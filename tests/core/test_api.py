"""Offline unit tests for project.core.api (UI entry point) and dev_eval resume cache."""

import json
from pathlib import Path
from unittest.mock import Mock
import pytest

from project.core import api
from scripts.run_dev_eval import append_resume_cache, load_resume_cache


@pytest.fixture(autouse=True)
def reset_api_cache():
    api.clear_api_caches()
    yield
    api.clear_api_caches()


def test_run_query_returns_error_object_when_store_or_llm_unavailable(monkeypatch):
    """When graph/store/LLM raises, api.run_query returns a structured error object (never a fake answer)."""
    monkeypatch.delenv("FACTORY_FAKE_LLM", raising=False)
    monkeypatch.delenv("FACTORY_STUB_RETRIEVAL", raising=False)

    failing_graph = Mock()
    failing_graph.invoke.side_effect = RuntimeError("KnowledgeStore SQLite database unavailable")
    monkeypatch.setattr(api, "_get_cached_graph", lambda pipeline="B": failing_graph)

    res = api.run_query("Spindle overheating ALM-410", machine_type="CNC Lathe")

    assert res["final_answer"] is None
    assert res["evidence"] == []
    assert res["trace_id"].startswith("TR-")
    assert isinstance(res["trace_events"], list)
    assert res["error"] is not None
    assert res["error"]["type"] == "RuntimeError"
    assert "KnowledgeStore SQLite database unavailable" in res["error"]["message"]
    assert res["flags"]["llm_check"] == "unavailable"


def test_run_query_returns_trace_events_and_flags(monkeypatch, tmp_path):
    """api.run_query reuses Pipeline B, returns trace_events for trace_id, evidence, and flags."""
    monkeypatch.setenv("FACTORY_FAKE_LLM", "1")
    monkeypatch.setenv("FACTORY_STUB_RETRIEVAL", "1")

    res = api.run_query(
        "Spindle high-pitched whining noise at 3000 RPM; chuck pressure dropping below 15 bar.",
        machine_type="CNC Lathe (Turning Center)",
    )

    assert res["error"] is None
    assert res["final_answer"] is not None
    assert res["flags"]["fake_llm"] is True
    assert res["flags"]["stub"] is True
    assert len(res["evidence"]) >= 1
    assert "snippet" in res["evidence"][0]
    assert res["trace_id"].startswith("TR-")
    assert len(res["trace_events"]) >= 1
    assert all(ev["trace_id"] == res["trace_id"] for ev in res["trace_events"])


def test_dev_eval_resume_cache_skips_degraded_and_keeps_good(tmp_path):
    """Verify run_dev_eval resume cache loads non-degraded records and ignores degraded ones."""
    cache_file = tmp_path / "dev_eval_cache.jsonl"
    good_rec = {"query_id": "DQ-01", "degraded": False, "behaviour_match": True, "latency_ms": 1200.0}
    bad_rec = {"query_id": "DQ-02", "degraded": True, "behaviour_match": False, "latency_ms": 5000.0}

    append_resume_cache(cache_file, good_rec)
    append_resume_cache(cache_file, bad_rec)

    loaded = load_resume_cache(cache_file)
    assert "DQ-01" in loaded
    assert loaded["DQ-01"]["behaviour_match"] is True
    assert "DQ-02" not in loaded


def test_dev_eval_resume_cache_respects_version_hash(tmp_path):
    """Cached entries from an older code/prompt version_hash must never be reused."""
    from scripts.run_dev_eval import compute_code_version_hash

    v_now = compute_code_version_hash()
    assert len(v_now) == 12

    cache_file = tmp_path / "versioned_cache.jsonl"
    stale = {"query_id": "DQ-01", "version_hash": "oldhash00000", "degraded": False}
    fresh = {"query_id": "DQ-02", "version_hash": v_now, "degraded": False}

    append_resume_cache(cache_file, stale)
    append_resume_cache(cache_file, fresh)

    loaded = load_resume_cache(cache_file, version_hash=v_now)
    assert "DQ-01" not in loaded
    assert "DQ-02" in loaded
