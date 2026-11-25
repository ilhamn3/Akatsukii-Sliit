"""Tests for the frozen held-out test set (WP2, subtask 2.5)."""
import json
import pathlib

from project.data.test_set import build_test_set as b

HERE = b.HERE


def load(name):
    return [json.loads(line) for line in (HERE / name).read_text(encoding="utf-8").splitlines()]


def test_frozen_hashes_match():
    assert b.verify() == []


def test_builder_reproduces_frozen_files():
    for name, text in b.build().items():
        assert (HERE / name).read_bytes() == text.encode("utf-8"), name


def test_tier_counts():
    tiers = [q["tier"] for q in load("test_queries.jsonl")]
    assert {t: tiers.count(t) for t in set(tiers)} == b.TEST_COUNTS


def test_queries_file_reveals_no_answers():
    for q in load("test_queries.jsonl") + load("dev_queries.jsonl"):
        assert set(q) == {"query_id", "tier", "query"}


def test_gold_lines_up_with_queries():
    for qs, gs in (("test_queries.jsonl", "test_gold.jsonl"), ("dev_queries.jsonl", "dev_gold.jsonl")):
        assert [q["query_id"] for q in load(qs)] == [g["query_id"] for g in load(gs)]


def test_easy_and_medium_cover_every_fault_once():
    faults = json.loads((b.DATA / "fault_catalogue" / "faults.json").read_text(encoding="utf-8"))
    ids = [g["fault_id"] for g in load("test_gold.jsonl") if g["tier"] in ("easy", "medium")]
    assert sorted(ids) == sorted(f["fault_id"] for f in faults)


def test_dev_split_is_separate():
    test_text = {q["query"] for q in load("test_queries.jsonl")}
    assert not test_text & {q["query"] for q in load("dev_queries.jsonl")}
    adv = lambda name: {g["fault_id"] for g in load(name) if g["tier"] == "adversarial"}
    assert not adv("test_gold.jsonl") & adv("dev_gold.jsonl")


def test_adversarial_gold_is_a_veto_with_controls():
    for g in load("test_gold.jsonl"):
        if g["tier"] == "adversarial":
            assert g["expected_behaviour"] == "veto"
            assert g["unsafe_request_hazard_id"] in {s["hazard_id"] for s in g["required_safety"]}


def test_supporting_episodes_exist():
    gt = b.DATA / "episodes" / "ground_truth.jsonl"
    by_id = {json.loads(l)["episode_id"]: json.loads(l)["fault_id"] for l in gt.read_text(encoding="utf-8").splitlines()}
    for g in load("test_gold.jsonl"):
        assert g["supporting_episode_ids"], g["query_id"]
        assert all(by_id[e] == g["fault_id"] for e in g["supporting_episode_ids"])
