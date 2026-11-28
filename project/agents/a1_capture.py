"""Local Whisper capture with timestamped evidence and bounded frame sampling.

Run from the repository root:
    python -m project.agents.a1_capture --source recording.wav

Whisper is loaded lazily, so importing the agent needs no model download.
Only media processing uses FFmpeg; no audio is sent to an API.
"""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from project.agents.base import BaseAgent
from project.core.capture_schemas import (
    CaptureRequest, CaptureResult, TranscriptSegment, VideoFrame,
)
from project.core.state import BlackboardState, create_initial_state


NOISE_FRAME_SECONDS = 0.02
# Background-noise flag threshold on the estimated SNR (dB). Chosen on 48 development clips
# (project/evaluation/noise_flag_eval.py, 28 Nov 2026), never on the 12 committed clips:
# clean 60.0 (capped), 20 dB noise 25.7-26.2, 15 dB 20.7-21.2, 10 dB 15.8-16.3. Rule fixed
# before measuring: midpoint of lowest clean and highest 15 dB estimate, rounded down.
# Synthetic voices leave digital silence, so real recordings need this rechecked.
NOISE_SNR_THRESHOLD_DB = 40.0


def estimate_snr_db(samples, sample_rate: int = 16000) -> float | None:
    """Signal-to-noise estimate from the audio itself, independent of Whisper's confidence.

    Splits the audio into 20 ms frames and compares the loud frames (90th percentile RMS,
    speech) with the quiet ones (10th percentile, the floor between words). Digital silence
    is capped at 60 dB. Returns None for clips too short to measure. A rough indicator of
    steady background noise, not a calibrated SNR.
    """
    import numpy as np

    x = np.asarray(samples, dtype=np.float64).ravel()
    n = max(1, int(sample_rate * NOISE_FRAME_SECONDS))
    if x.size < n * 20:
        return None
    rms = np.sqrt((x[: x.size // n * n].reshape(-1, n) ** 2).mean(axis=1))
    loud, quiet = float(np.percentile(rms, 90)), float(np.percentile(rms, 10))
    if loud <= 0:
        return None
    return round(min(60.0, 20 * math.log10(loud / max(quiet, loud * 1e-3))), 1)


def default_noise_estimate(source) -> float | None:
    """SNR estimate for a media file via Whisper's FFmpeg loader; None if it cannot be read."""
    try:
        import whisper
        return estimate_snr_db(whisper.load_audio(str(source)))
    except Exception:
        return None


def _binary(name):
    executable = shutil.which(name)
    if executable is None:
        raise RuntimeError(f"{name} is required. Install FFmpeg and add its bin directory to PATH.")
    return executable


def _media_command(args):
    """Bound external media commands and keep Windows helper windows hidden."""
    result = subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise RuntimeError(f"Media processing failed: {result.stderr[-1000:]}")
    return result.stdout


def sample_video_frames(source, request, capture_id):
    """Sample at regular seek offsets, capped by max_frames.

    Timestamps are requested media offsets (nearest decoded video frame), not
    an OCR claim or a guarantee that a sampled frame contains a useful detail.
    Each run has its own directory, preventing stale/colliding frame results.
    """
    ffmpeg, ffprobe = _binary("ffmpeg"), _binary("ffprobe")
    info = json.loads(_media_command([
        ffprobe, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=codec_type:format=duration", "-of", "json", str(source),
    ]))
    if not info.get("streams"):
        raise ValueError("Video input contains no video stream")
    duration = float(info.get("format", {}).get("duration", 0))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Video input has no finite positive duration")
    root = Path(request.frame_output_dir)
    root.mkdir(parents=True, exist_ok=True)
    output_dir = Path(tempfile.mkdtemp(prefix=f"{capture_id}-", dir=root))
    frames = []
    for index in range(request.max_frames):
        timestamp = index * request.frame_interval_seconds
        if timestamp >= duration:
            break
        destination = output_dir / f"frame-{index:04d}.jpg"
        _media_command([
            ffmpeg, "-nostdin", "-v", "error", "-ss", str(timestamp),
            "-i", str(source), "-map", "0:v:0", "-frames:v", "1",
            "-q:v", "2", "-y", str(destination),
        ])
        if not destination.is_file() or destination.stat().st_size == 0:
            raise RuntimeError(f"No frame decoded at {timestamp} seconds")
        frames.append(VideoFrame(
            evidence_id=f"{capture_id}-F{index:04d}",
            timestamp_seconds=timestamp, path=str(destination.resolve()),
        ))
    return frames


def normalize_transcript(raw, capture_id):
    """Preserve dubious speech and mark it; never silently turn it into certainty."""
    segments, flags = [], []
    previous_start = -1.0
    for index, item in enumerate(raw.get("segments", [])):
        text = item.get("text", "").strip()
        if not text:
            flags.append("empty_segment")
            continue
        segment = TranscriptSegment(
            evidence_id=f"{capture_id}-S{index:04d}",
            start_seconds=item["start"], end_seconds=item["end"], text=text,
            avg_logprob=item.get("avg_logprob"),
            no_speech_prob=item.get("no_speech_prob"),
            compression_ratio=item.get("compression_ratio"),
        )
        if segment.start_seconds < previous_start:
            raise ValueError("Transcript segments are out of chronological order")
        previous_start = segment.start_seconds
        if any(value is None for value in (
            segment.avg_logprob, segment.no_speech_prob, segment.compression_ratio,
        )):
            segment.quality_flags.append("missing_quality_metrics")
        if segment.avg_logprob is not None and segment.avg_logprob < -1.0:
            segment.quality_flags.append("low_log_probability")
        if segment.no_speech_prob is not None and segment.no_speech_prob > 0.6:
            segment.quality_flags.append("possible_non_speech")
        if segment.compression_ratio is not None and segment.compression_ratio > 2.4:
            segment.quality_flags.append("repetitive_text")
        segments.append(segment)
    if not segments:
        flags.append("no_speech_detected")
    text = " ".join(segment.text for segment in segments)
    if raw.get("text", "").strip() and not segments:
        # Keep un-timestamped text visible, but explicitly route it to review.
        text = raw["text"].strip()
        flags.append("missing_timestamps")
    return text, segments, sorted(set(flags))


class CaptureAgent(BaseAgent):
    """Agent 1. Reads capture_request and writes JSON-compatible capture_result."""

    def __init__(self, trace_logger=None, model=None, frame_sampler=None, noise_estimator=None):
        super().__init__(
            name="CaptureAgent", role="Capture and transcription", pipeline="A",
            trace_logger=trace_logger,
        )
        self._model = model
        self._frame_sampler = frame_sampler or sample_video_frames
        self._noise_estimator = noise_estimator or default_noise_estimate

    def run(self, state: BlackboardState) -> BlackboardState:
        # A failed retry must not leave a previous recording's successful output.
        state["capture_result"] = None
        return super().run(state)

    def _transcribe(self, source, language):
        if self._model is None:
            _binary("ffmpeg")
            try:
                import whisper
            except ImportError as exc:
                raise RuntimeError("Install openai-whisper in the project environment") from exc
            self._model = whisper.load_model(
                os.getenv("WHISPER_MODEL", "base"),
                device=os.getenv("WHISPER_DEVICE", "cpu"),
                download_root="whisper_cache",
            )
        return self._model.transcribe(
            str(source), language=language, task="transcribe", fp16=False,
            verbose=None, temperature=0.0,
        )

    def process(self, state: BlackboardState):
        request = CaptureRequest.model_validate(state.get("capture_request", {}))
        source = Path(request.source_path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"Recording not found: {source}")
        if source.stat().st_size == 0:
            raise ValueError("Recording is empty")
        digest = hashlib.sha256()
        with source.open("rb") as recording:
            for chunk in iter(lambda: recording.read(1024 * 1024), b""):
                digest.update(chunk)
        fingerprint = digest.hexdigest()
        capture_id = f"CAP-{fingerprint[:24]}"
        raw = self._transcribe(source, request.language)
        text, segments, flags = normalize_transcript(raw, capture_id)
        # Whisper can stay confident under steady background noise, so the audio itself is
        # checked too; a noisy capture is routed to review like any other quality flag.
        snr = self._noise_estimator(source)
        if snr is not None and snr < NOISE_SNR_THRESHOLD_DB:
            flags = sorted(set(flags) | {"background_noise"})
        frames = []
        if request.media_type == "video":
            frames = self._frame_sampler(source, request, capture_id)
        result = CaptureResult(
            capture_id=capture_id, source_path=str(source), source_sha256=fingerprint,
            media_type=request.media_type, language=raw.get("language") or request.language,
            text=text, segments=segments, frames=frames, quality_flags=flags, estimated_snr_db=snr,
            review_required=bool(flags or any(s.quality_flags for s in segments)),
        )
        return {
            "capture_result": result.model_dump(mode="json"),
            "decision": "CAPTURE_REVIEW_REQUIRED" if result.review_required else "CAPTURE_COMPLETED",
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--media-type", choices=["audio", "video"], default="audio")
    parser.add_argument("--output", default="logs/capture_result.json")
    args = parser.parse_args(argv)
    state = create_initial_state(query="")
    state["capture_request"] = {"source_path": args.source, "media_type": args.media_type}
    result = CaptureAgent().run(state)
    if result.get("capture_result") is None:
        print("\n".join(result.get("errors", [])), file=sys.stderr)
        return 1
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result["capture_result"], indent=2), encoding="utf-8")
    print(f"Capture saved to {destination}; review_required={result['capture_result']['review_required']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
