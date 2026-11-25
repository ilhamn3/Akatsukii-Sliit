"""Tests for the seeded episode generator (WP2, subtasks 2.2 and 2.3)."""
import json

import pytest

from project.data import generator as g


@pytest.fixture(scope="module")
def files():
    return g.generate(g.DEFAULT_SEED, g.DEFAULT_N)


def rows(files, name):
    return [json.loads(line) for line in files[name].splitlines()]


def test_same_seed_is_byte_identical(files):
    assert g.generate(g.DEFAULT_SEED, g.DEFAULT_N) == files


def test_different_seed_differs(files):
    assert g.generate(g.DEFAULT_SEED + 1, g.DEFAULT_N)["narratives.jsonl"] != files["narratives.jsonl"]


def test_committed_files_match_generator(files):
    for name, text in files.items():
        assert (g.DEFAULT_OUT / name).read_bytes() == text.encode("utf-8"), f"{name} is stale; rerun generator.py"


def test_counts(files):
    narr = rows(files, "narratives.jsonl")
    assert len(narr) == 400
    assert len({r["technician_id"] for r in narr}) == 12
    assert len({t["fault_id"] for t in rows(files, "ground_truth.jsonl")}) == 40


def test_narratives_hold_no_labels(files):
    for r in rows(files, "narratives.jsonl"):
        assert set(r) == {"episode_id", "date", "technician_id", "shift", "machine_type", "machine_id", "source", "narrative"}


def test_ground_truth_validates_against_repair_record(files):
    schemas = pytest.importorskip("project.core.schemas")
    for t in rows(files, "ground_truth.jsonl"):
        schemas.RepairRecord.model_validate(t["record"])


def test_spoken_style_features_present(files):
    audio = [r["narrative"] for r in rows(files, "narratives.jsonl") if r["source"] == "audio_debrief"]
    assert sum(any(f + "," in n for f in g.FILLERS) for n in audio) > len(audio) * 0.5
    assert any("LOTO" in n for n in audio)
    truths = rows(files, "ground_truth.jsonl")
    assert sum(bool(t["narrative_coverage"]["steps_omitted"]) for t in truths) > 100
    assert sum(not t["narrative_coverage"]["safety_step_mentioned"] for t in truths) > 40


def test_audio_steps_have_increasing_timestamps(files):
    for t in rows(files, "ground_truth.jsonl"):
        if t["record"]["source"] != "audio_debrief":
            continue
        stamps = [s["timestamp"] for s in t["record"]["steps"] if s["timestamp"]]
        assert stamps == sorted(stamps)
        assert len(stamps) == len(t["narrative_coverage"]["steps_mentioned"])
