"""Real FFmpeg integration checks; skip when local media tools are absent.

Fixtures are generated test patterns and silence, not repair recordings.
These tests do not load Whisper or download model weights.
"""

from pathlib import Path
import shutil
import wave

import pytest

from project.agents.a1_capture import _media_command, sample_video_frames
from project.core.capture_schemas import CaptureRequest


pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="Activate an environment with FFmpeg and ffprobe for media integration tests",
)


def test_samples_actual_decoded_video_frames(tmp_path):
    source = tmp_path / "synthetic clip.mp4"
    _media_command([
        shutil.which("ffmpeg"), "-nostdin", "-v", "error", "-f", "lavfi",
        "-i", "color=c=blue:s=160x120:r=10", "-t", "2.5", "-c:v", "mpeg4",
        "-y", str(source),
    ])
    request = CaptureRequest(
        source_path=str(source), media_type="video", frame_interval_seconds=1,
        max_frames=10, frame_output_dir=str(tmp_path / "frames"),
    )
    frames = sample_video_frames(source, request, "CAP-INTEGRATION")
    assert [f.timestamp_seconds for f in frames] == [0, 1, 2]
    for frame in frames:
        contents = Path(frame.path).read_bytes()
        assert contents.startswith(b"\xff\xd8") and contents.endswith(b"\xff\xd9")


def test_actual_audio_only_file_has_no_video_stream(tmp_path):
    source = tmp_path / "silence.wav"
    with wave.open(str(source), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\x00\x00" * 16000)
    request = CaptureRequest(source_path=str(source), media_type="video")
    with pytest.raises(ValueError, match="no video stream"):
        sample_video_frames(source, request, "CAP-AUDIO-ONLY")


def test_actual_corrupt_video_reports_decoder_failure(tmp_path):
    source = tmp_path / "corrupt.mp4"
    source.write_bytes(b"This is not an MP4 file")
    request = CaptureRequest(source_path=str(source), media_type="video")
    with pytest.raises(RuntimeError, match="Media processing failed"):
        sample_video_frames(source, request, "CAP-CORRUPT")
