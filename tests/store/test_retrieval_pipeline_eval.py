"""K6 retrieval on pipeline-ingested episodes: ingestion path, scoring and the committed report."""

import json
from pathlib import Path

import pytest

from project.evaluation import retrieval_pipeline_eval as rp
from tests.validation_helpers import HashEmbedder

ROOT = Path(__file__).resolve().parents[2]


def test_test_split_is_refused():
    with pytest.raises(ValueError, match="frozen test split"):
        rp._jsonl(rp.TEST_SET / "test_queries.jsonl")


def test_drafts_go_through_the_real_queue_and_are_scored_by_source_fault(tmp_path):
    labels = {r["episode_id"]: r["fault_id"] for r in rp._jsonl(rp.LABELS)}
    drafts = [d for d in rp.load_drafts() if d["split"] == "eval"][:6]
    store, to_source, stats = rp.ingest(drafts, tmp_path, HashEmbedder())
    assert stats["approved"] + stats["blocked"] == 6 == stats["drafts"]
    assert set(to_source.values()) <= {d["episode_id"] for d in drafts}
    assert all(key.startswith("EP-") and key not in labels for key in to_source)  # Pipeline IDs, not gold IDs.
    episode = store.get(next(iter(to_source)), trace_id="T")
    assert episode.verified_by == rp.REVIEWER and episode.status == "verified"
    cases = rp.dev_cases(labels)
    report = rp.evaluate(store, to_source, cases, labels, abstain_below=0.55)
    assert set(report["answerable"]) | set(report["unanswerable"]) == {c["query_id"] for c in cases}
    assert set(report["results"]) == {f"{m}/{r}" for m in rp.MACHINE_MODES for r in rp.MODES}


def test_committed_report_states_its_conditions():
    [path] = (ROOT / "project/evaluation/results").glob("*_retrieval_pipeline_eval/retrieval_pipeline_eval.json")
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["split"] == "dev" and report["synthetic_data_only"] is True
    assert report["drafts"] == {"dev": 12, "eval": 50, "model": "openai/gpt-oss-120b via Groq",
                                "prompt": "extraction_v5"}
    assert report["ingestion"]["approved"] + report["ingestion"]["blocked"] == 62
    hybrid = report["pipeline"]["results"]["none/hybrid"]
    assert set(hybrid["rank_by_query"]) == set(report["pipeline"]["answerable"])
    assert report["oracle_same_episodes"]["stored_episodes"] == report["pipeline"]["stored_episodes"]
