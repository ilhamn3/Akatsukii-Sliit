"""Subtask 3.2: Agent 1's background-noise flag (signal-level SNR, not Whisper confidence)."""

import json
from pathlib import Path

import numpy as np
import pytest

from project.agents import a1_capture as a1
from project.core.state import create_initial_state
from project.core.trace import TraceLogger

ROOT = Path(__file__).resolve().parents[2]
SR = 16000


def speech_like(seconds=6.0, gaps=True):
    """Tone bursts separated by digital silence, like the synthetic voices."""
    t = np.arange(int(SR * seconds)) / SR
    signal = 0.3 * np.sin(2 * np.pi * 220 * t)
    if gaps:
        signal[(t % 1.0) > 0.6] = 0.0
    return signal


def test_clean_speech_with_silent_gaps_reads_as_clean_and_noise_lowers_the_estimate():
    clean = speech_like()
    assert a1.estimate_snr_db(clean) == 60.0  # Digital silence is capped.
    rng = np.random.default_rng(0)
    noisy = clean + rng.normal(0, 0.3 / 10 ** (15 / 20), clean.size)
    estimate = a1.estimate_snr_db(noisy)
    assert estimate < a1.NOISE_SNR_THRESHOLD_DB and 10 < estimate < 30
    assert a1.estimate_snr_db(np.zeros(SR)) is None and a1.estimate_snr_db(clean[:100]) is None


def test_threshold_was_set_and_noisy_capture_is_routed_to_review(tmp_path):
    assert a1.NOISE_SNR_THRESHOLD_DB == 40.0
    source = tmp_path / "clip.wav"
    source.write_bytes(b"RIFF-not-really-audio")
    raw = {"language": "en", "text": "belt slipped", "segments": [
        {"start": 0.0, "end": 1.0, "text": "belt slipped", "avg_logprob": -0.2,
         "no_speech_prob": 0.01, "compression_ratio": 1.1}]}

    class Model:
        def transcribe(self, *args, **kwargs):
            return raw

    def run(snr):
        agent = a1.CaptureAgent(TraceLogger(str(tmp_path / "t.jsonl")), model=Model(),
                                noise_estimator=lambda path: snr)
        state = create_initial_state("")
        state["capture_request"] = {"source_path": str(source), "media_type": "audio"}
        return agent.run(state)["capture_result"]

    noisy, clean, unknown = run(21.0), run(60.0), run(None)
    assert noisy["quality_flags"] == ["background_noise"] and noisy["review_required"] is True
    assert noisy["estimated_snr_db"] == 21.0
    assert clean["quality_flags"] == [] and clean["review_required"] is False
    assert unknown["estimated_snr_db"] is None and unknown["quality_flags"] == []  # Not measurable: no flag.


def test_unreadable_media_gives_no_estimate(tmp_path):
    bad = tmp_path / "x.wav"
    bad.write_bytes(b"nope")
    assert a1.default_noise_estimate(bad) is None


def test_committed_report_used_a_dev_chosen_threshold_and_flags_the_noisy_clips():
    [path] = (ROOT / "project/evaluation/results").glob("*_noise_flag_eval/noise_flag_eval.json")
    report = json.loads(path.read_text(encoding="utf-8"))
    committed = {c["episode_id"] for c in json.loads(
        (ROOT / "project/data/audio/manifest.json").read_text(encoding="utf-8"))["clips"]}
    assert not committed & {c["episode_id"] for c in report["dev"]["clips"]}  # Dev clips are other episodes.
    assert report["rule_threshold_db"] == report["a1_threshold_db"] == a1.NOISE_SNR_THRESHOLD_DB
    clips = report["committed_clips"]
    assert (clips["noisy_flagged"], clips["noisy_total"], clips["clean_flagged"], clips["clean_total"]) == (6, 6, 0, 6)
    assert all(c["flagged"] == (c["estimated_snr_db"] < report["a1_threshold_db"]) for c in clips["clips"])
