"""Tests for the audio subset (2.4) and expanded stores (2.6)."""
import hashlib
import json
import wave

import pytest

from project.data import expand_stores as xs
from project.data import generator as g

AUDIO = g.HERE / "audio"


@pytest.fixture(scope="module")
def stores():
    return xs.build()


def rows(text):
    return [json.loads(line) for line in text.splitlines()]


def test_expanded_files_match_builder(stores):
    for rel, text in stores.items():
        assert (xs.OUT / rel).read_bytes() == text.encode("utf-8"), f"{rel} is stale; rerun expand_stores.py"


def test_sizes_and_fault_coverage(stores):
    for size in xs.SIZES:
        m = json.loads(stores[f"n{size}/manifest.json"])
        assert m["episodes"] == size
        assert m["faults_covered"] == 40


def test_stores_are_nested(stores):
    ids = {s: [e["episode_id"] for e in rows(stores[f"n{s}/episodes.jsonl"])] for s in xs.SIZES}
    assert set(ids[100]) <= set(ids[400]) <= set(ids[2000])
    assert len(set(ids[2000])) == 2000


def test_n400_is_the_base_ground_truth(stores):
    base = rows((g.DEFAULT_OUT / "ground_truth.jsonl").read_text(encoding="utf-8"))
    env = rows(stores["n400/episodes.jsonl"])
    assert [e["record"] for e in env] == [t["record"] for t in base]


def test_evidence_ids_unique_in_n2000(stores):
    ev = [e["record"]["evidence_id"] for e in rows(stores["n2000/episodes.jsonl"])]
    assert len(set(ev)) == len(ev)


def test_envelopes_validate(stores):
    schemas = pytest.importorskip("project.core.schemas")
    for e in rows(stores["n100/episodes.jsonl"]):
        assert e["status"] == "verified" and e["provenance"]["evidence_spans"]
        schemas.RepairRecord.model_validate(e["record"])
    models = pytest.importorskip("project.store.models")  # present once the knowledge store (PR #12) merges
    for size in xs.SIZES:
        for e in rows(stores[f"n{size}/episodes.jsonl"]):
            models.VerifiedEpisode.model_validate(e)


def test_audio_clips_match_manifest():
    manifest = json.loads((AUDIO / "manifest.json").read_text(encoding="utf-8"))
    narratives = {r["episode_id"]: r for r in rows((g.DEFAULT_OUT / "narratives.jsonl").read_text(encoding="utf-8"))}
    assert len(manifest["clips"]) == 12
    assert len({c["machine_type"] for c in manifest["clips"]}) == 5
    assert {c["noise"] == "none" for c in manifest["clips"]} == {True, False}
    for c in manifest["clips"]:
        path = AUDIO / c["file"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == c["sha256"]
        with wave.open(str(path), "rb") as w:
            assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)
            assert abs(w.getnframes() / w.getframerate() - c["duration_seconds"]) < 0.01
        assert c["reference_transcript"] == narratives[c["episode_id"]]["narrative"]
        assert narratives[c["episode_id"]]["source"] == "audio_debrief"
