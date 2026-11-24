"""Tests for the human-study session materials (WP8)."""
import csv
import io
import json

import pytest

from project.human_study import make_materials as mm


@pytest.fixture(scope="module")
def files():
    return mm.build()


def read_csv(data: bytes):
    return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))


def test_committed_materials_are_current(files):
    for name, data in files.items():
        assert (mm.OUT / name).read_bytes() == data, f"{name} is stale; rerun make_materials.py"


def test_packets_cover_all_60_queries_once(files):
    ids = [r["query_id"] for p in mm.PACKETS for r in read_csv(files[f"packet_{p}.csv"])]
    test_ids = [json.loads(l)["query_id"] for l in (mm.DATA / "test_set" / "test_queries.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sorted(ids) == sorted(test_ids)


def test_each_packet_has_the_same_tier_mix(files):
    gold = {json.loads(l)["query_id"]: json.loads(l)["tier"]
            for l in (mm.DATA / "test_set" / "test_gold.jsonl").read_text(encoding="utf-8").splitlines()}
    for p in mm.PACKETS:
        tiers = [gold[r["query_id"]] for r in read_csv(files[f"packet_{p}.csv"])]
        assert {t: tiers.count(t) for t in set(tiers)} == mm.PER_PACKET


def test_participant_materials_hide_answers(files):
    for p in mm.PACKETS:
        assert "tier" not in read_csv(files[f"packet_{p}.csv"])[0]
    blob = b"".join(files.values()).decode("utf-8", "ignore")
    for leak in ("fault_id", "root_cause\":", "ground_truth", "narrative_coverage", "expected_behaviour"):
        assert leak not in blob


def test_repair_log_lists_every_narrative(files):
    page = files["repair_log.html"].decode("utf-8")
    assert page.count('<td class="id">EP-') == 400


def test_extraction_uses_one_audio_and_one_log(files):
    narr = {json.loads(l)["episode_id"]: json.loads(l)["source"]
            for l in (mm.DATA / "episodes" / "narratives.jsonl").read_text(encoding="utf-8").splitlines()}
    rows = read_csv(files["extraction_sheet.csv"])
    assert sorted(narr[r["episode_id"]] for r in rows) == ["audio_debrief", "shift_log"]


def test_practice_uses_dev_split(files):
    assert "DQ-" in files["practice.md"].decode("utf-8")
    assert "TQ-" not in files["practice.md"].decode("utf-8")
