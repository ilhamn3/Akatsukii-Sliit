"""K1 audio evaluation: WER scoring and reporting (no Whisper model needed)."""

import json
from pathlib import Path

import pytest

from project.evaluation import capture_metrics as cm

ROOT = Path(__file__).resolve().parents[2]


def test_word_errors_count_substitutions_deletions_insertions():
    ref, hyp = "the lathe four alarm".split(), "the late four alarm again".split()
    errors = cm.word_errors(ref, hyp)
    assert errors == {"reference_words": 4, "substitutions": 1, "deletions": 0, "insertions": 1, "errors": 2}
    assert cm.wer(errors) == 0.5
    assert cm.word_errors(["a", "b"], [])["deletions"] == 2 and cm.wer(cm.word_errors([], [])) is None


def test_primary_wer_uses_whisper_normalizer_and_basic_keeps_fillers_and_numbers():
    reference = "Uh, it was throwing the seven one five alarm."
    hypothesis = "It was throwing the 715 alarm."
    assert cm.wer(cm.word_errors(cm.whisper_words(reference), cm.whisper_words(hypothesis))) == 0.0
    assert cm.wer(cm.word_errors(cm.basic_words(reference), cm.basic_words(hypothesis))) > 0.3


def test_score_and_aggregate_report_noisy_flagging_from_a1_output():
    clips = [{"file": "clips/A_clean.wav", "episode_id": "A", "noise": "none", "voice": "v", "rate": 0,
              "duration_seconds": 10.0, "reference_transcript": "belt slipped"},
             {"file": "clips/B_noisy.wav", "episode_id": "B", "noise": "plant noise", "voice": "v", "rate": 0,
              "duration_seconds": 10.0, "reference_transcript": "belt slipped"}]
    seg = {"avg_logprob": -0.2, "no_speech_prob": 0.01, "quality_flags": []}
    rows = [cm.score_clip(clips[0], {"text": "belt slipped", "segments": [seg], "review_required": False}, 1.0),
            cm.score_clip(clips[1], {"text": "felt slipped", "review_required": True,
                                     "segments": [{**seg, "quality_flags": ["low_log_probability"]}]}, 2.0)]
    summary = cm.aggregate(rows)
    assert summary["all"]["wer_pooled"] == 0.25 and summary["noisy"]["wer_pooled"] == 0.5
    assert summary["noisy_flagging"] == {"noisy_flagged": 1, "noisy_total": 1, "clean_flagged": 0, "clean_total": 1}
    assert rows[1]["segment_flags"] == ["low_log_probability"] and rows[1]["real_time_factor"] == 0.2


def test_committed_audio_report_matches_its_clips_and_manifest():
    """The committed numbers are recomputable from the committed transcripts."""
    [report_path] = (ROOT / "project/evaluation/results").glob("*_audio_whisper-*/capture_metrics_audio.json")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    manifest = json.loads((ROOT / "project/data/audio/manifest.json").read_text(encoding="utf-8"))
    assert [c["file"] for c in report["clips"]] == [c["file"] for c in manifest["clips"]]
    for row, clip in zip(report["clips"], manifest["clips"]):
        assert row["reference"] == clip["reference_transcript"]
        again = cm.score_clip(clip, {"text": row["transcript"], "segments": [], "review_required": False}, 1.0)
        assert again["wer"] == row["wer"] and again["wer_basic"] == row["wer_basic"]
    assert report["summary"]["all"]["clips"] == 12 and report["summary"]["all"]["failed"] == 0
    assert report["synthetic_data_only"] is True
    assert cm.markdown_table(report).count("\n| `EP-") == 12
