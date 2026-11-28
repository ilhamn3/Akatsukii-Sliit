"""Offline tests for WP7 single-agent baseline, metrics, harness, and statistics."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import numpy as np
import pytest

from project.benchmark.baseline_single_agent import run_baseline_query
from project.benchmark.harness import load_cache, load_split, main as harness_main
from project.benchmark.metrics import (
    already_tried_repeated,
    behaviour_match,
    cause_family_match,
    citation_validity,
    groundedness,
    must_not_violations,
    score_query_result,
    top3_root_cause_hit,
)
from project.benchmark.stats import (
    aggregate_per_query,
    compare_systems,
    paired_bootstrap_ci,
    safe_cohens_d,
    safe_mcnemar,
    safe_wilcoxon,
)
from project.core.llm import FakeLLM
from project.core.schemas import Evidence, FinalAnswer


class _DummyRetriever:
    def retrieve(self, query, machine_type, k, machine_id=None, trace_id=None):
        return [
            Evidence(
                evidence_id="EV-EP-0001-S1",
                episode_id="EP-0001",
                source_type="vector",
                content="Blocked intake filter caused continuous compressor run.",
                similarity_score=0.92,
                metadata={
                    "record": {
                        "root_cause": "blocked air intake filter",
                        "outcome": "worked",
                        "failed_attempts": [],
                        "safety_warnings": ["Vent receiver to zero pressure."],
                    }
                },
            )
        ]


def test_metrics_behaviour_veto_requires_refusal_and_control():
    req = [{"hazard_id": "H05", "control": "Vent the receiver and lines to zero pressure before servicing."}]
    # Refusal alone without control text fails veto behaviour_match
    ans_no_ctrl = {"abstained": True, "refusal": "Cannot do that.", "safety_warnings": [], "steps": []}
    assert behaviour_match("veto", ans_no_ctrl, False, req) is False
    # Refusal + control text passes
    ans_ok = {
        "abstained": True,
        "refusal": "Refused: must vent the receiver and lines to zero pressure before servicing.",
        "safety_warnings": ["H05: Vent receiver to zero pressure."],
        "steps": [],
    }
    assert behaviour_match("veto", ans_ok, False, req) is True


def test_metrics_top3_cause_family_groundedness_and_constraints():
    ans = {
        "diagnosed_cause": "bearing wear",
        "alternative_hypotheses": [
            {"cause": "bearing wear", "confidence": 0.5, "cause_family": "wear"},
            {"cause": "blocked air intake filter", "confidence": 0.4, "cause_family": "clogging"},
        ],
        "steps": [
            {"step_number": 1, "instruction": "Replace the intake filter element", "evidence_ids": ["EV-EP-0001-S1"]},
            {"step_number": 2, "instruction": "Do not raise the pressure switch setpoint", "evidence_ids": ["EV-EP-0001-S1"]},
        ],
        "safety_warnings": ["Raised the pressure switch setpoint is forbidden."],
        "abstained": False,
    }
    evidence = [{"evidence_id": "EV-EP-0001-S1", "episode_id": "EP-0001"}]

    assert top3_root_cause_hit(ans, "blocked air intake filter") is True
    assert cause_family_match(ans, "clogging") is True
    assert citation_validity(ans, evidence) == 1.0
    assert groundedness(ans, evidence, ["EP-0001"]) == 1.0
    assert groundedness(ans, evidence, ["EP-9999"]) == 0.0
    # must_not_violations scans steps AND safety_warnings
    assert must_not_violations(ans, ["raised the pressure switch setpoint"]) == 1
    assert already_tried_repeated(ans, ["replace the intake filter element"]) == 1


def test_stats_on_synthetic_arrays_and_all_ties():
    # All-ties safety check
    ties = np.array([0.0, 0.0, 0.0, 0.0])
    assert safe_wilcoxon(ties) == 1.0
    assert safe_cohens_d(ties) == 0.0
    assert paired_bootstrap_ci(ties) == (0.0, 0.0)

    # Known positive difference: multi = [1, 1, 1, 1, 0, 1], base = [0, 0, 1, 0, 0, 0]
    m = np.array([1.0, 1.0, 1.0, 1.0, 0.0, 1.0])
    b = np.array([0.0, 0.0, 1.0, 0.0, 0.0, 0.0])
    diffs = m - b
    assert np.isclose(np.mean(diffs), 4.0 / 6.0)
    lo, hi = paired_bootstrap_ci(diffs, n_resamples=2000, seed=42)
    assert lo > 0.0 and hi <= 1.0
    assert safe_wilcoxon(diffs) < 0.15
    assert safe_mcnemar(m, b) == 0.125

    # Aggregate & compare_systems with degraded exclusion
    records = [
        {"system": "multi", "query_id": "Q1", "tier": "easy", "run": 1, "degraded": False, "behaviour_match": 1.0, "top1_root_cause_match": 1.0, "top3_root_cause_hit": 1.0, "cause_family_match": 1.0, "safety_coverage": 1.0, "must_not_violations": 0, "already_tried_repeated": 0, "citation_validity": 1.0, "groundedness": 1.0, "latency_ms": 100.0, "llm_calls": 4, "cost_proxy": 400.0},
        {"system": "multi", "query_id": "Q1", "tier": "easy", "run": 2, "degraded": True, "behaviour_match": 0.0, "top1_root_cause_match": 0.0, "top3_root_cause_hit": 0.0, "cause_family_match": 0.0, "safety_coverage": 0.0, "must_not_violations": 0, "already_tried_repeated": 0, "citation_validity": 0.0, "groundedness": 0.0, "latency_ms": 999.0, "llm_calls": 0, "cost_proxy": 0.0},
        {"system": "baseline", "query_id": "Q1", "tier": "easy", "run": 1, "degraded": False, "behaviour_match": 0.0, "top1_root_cause_match": 0.0, "top3_root_cause_hit": 0.0, "cause_family_match": 0.0, "safety_coverage": 0.5, "must_not_violations": 0, "already_tried_repeated": 0, "citation_validity": 1.0, "groundedness": 1.0, "latency_ms": 50.0, "llm_calls": 1, "cost_proxy": 100.0},
    ]
    df, counts = aggregate_per_query(records)
    assert counts["degraded_runs"] == 1
    assert counts["non_degraded_runs"] == 2
    comp = compare_systems(df, n_resamples=500, seed=42)
    assert comp["paired_query_count"] == 1
    assert comp["overall"]["behaviour_match"]["mean_diff"] == 1.0


def test_final_guard_blocks_test_split_without_final_flag(tmp_path):
    with pytest.raises(PermissionError):
        load_split("test", final=False)
    rc = harness_main(["--system", "baseline", "--split", "test", "--out", str(tmp_path)])
    assert rc == 2


def test_resume_cache_keys_on_system_and_hash(tmp_path):
    raw = tmp_path / "raw_baseline.jsonl"
    rows = [
        {"system": "baseline", "query_id": "DQ-01", "run": 1, "code_hash": "abc123", "degraded": False},
        {"system": "baseline", "query_id": "DQ-02", "run": 1, "code_hash": "abc123", "degraded": True},
        {"system": "baseline", "query_id": "DQ-03", "run": 1, "code_hash": "oldhash", "degraded": False},
    ]
    raw.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    cached = load_cache(raw, "baseline", "abc123")
    assert ("DQ-01", 1) in cached
    assert ("DQ-02", 1) not in cached  # degraded excluded
    assert ("DQ-03", 1) not in cached  # stale hash excluded


def test_baseline_refuses_fake_llm_by_default_and_validates_schema(capsys):
    with pytest.raises(RuntimeError, match="refuses FakeLLM"):
        run_baseline_query(
            query="Compressor belt squeals",
            allow_fake=False,
            retriever=_DummyRetriever(),
            llm_client=FakeLLM(),
        )

    res = run_baseline_query(
        query="Compressor belt squeals",
        machine_type="air_compressor",
        allow_fake=True,
        retriever=_DummyRetriever(),
        llm_client=FakeLLM(),
    )
    captured = capsys.readouterr()
    assert "BASELINE WARNING" in captured.out
    assert res["degraded"] is False
    assert res["llm_calls"] == 1
    ans = FinalAnswer.model_validate(res["final_answer"])
    assert ans.steps[0].evidence_ids == ["EV-EP-0001-S1"]


def test_step_level_evidence_ids_for_multi_and_baseline():
    """Verify citation_validity and groundedness accept step-level IDs (-S<n>) for both multi and baseline."""
    # Multi-agent api.run_query returns episode-level summary without metadata.steps
    multi_evidence = [{"evidence_id": "EV-CMP-0010", "episode_id": "EP-0010"}]
    # Baseline returns full normalized evidence with metadata.steps
    base_evidence = [
        {
            "evidence_id": "EV-CMP-0010",
            "episode_id": "EP-0010",
            "metadata": {"steps": [{"evidence_id": "EV-CMP-0010-S1", "step": "Check belt tension"}]},
        }
    ]
    ans = {
        "steps": [
            {"step_number": 1, "instruction": "Check belt tension", "evidence_ids": ["EV-CMP-0010-S1"]},
            {"step_number": 2, "instruction": "Inspect pulley alignment", "evidence_ids": ["EV-CMP-0010-S2"]},
        ],
        "abstained": False,
    }
    assert citation_validity(ans, multi_evidence) == 1.0
    assert groundedness(ans, multi_evidence, ["EP-0010"]) == 1.0
    assert groundedness(ans, multi_evidence, ["EP-9999"]) == 0.0
    assert citation_validity(ans, base_evidence) == 1.0
    assert groundedness(ans, base_evidence, ["EP-0010"]) == 1.0


def test_gemini_429_daily_vs_rpm_handling(monkeypatch):
    """Daily-quota 429 raises DailyQuotaExhaustedError immediately; per-minute 429 retries with backoff."""
    from unittest.mock import MagicMock
    from project.core.llm import (
        DailyQuotaExhaustedError,
        GeminiLLMClient,
        is_daily_quota_exhausted,
        reset_daily_quota_flag,
        set_global_rpm,
    )

    reset_daily_quota_flag()
    set_global_rpm(None)

    sleep_calls = []
    monkeypatch.setattr("project.core.llm.time.sleep", lambda s: sleep_calls.append(s))

    # 1. Daily quota (RPD) 429: NO retry, NO sleep, raises DailyQuotaExhaustedError immediately
    client = GeminiLLMClient(api_key="test-key-123", model="gemini-3.5-flash-lite", max_retries=3)
    mock_genai = MagicMock()
    mock_genai.models.generate_content.side_effect = RuntimeError(
        "429 RESOURCE_EXHAUSTED: GenerateRequestsPerDayPerProjectPerModel-FreeTier exceeded (RPD)"
    )
    monkeypatch.setattr(client, "_get_client", lambda: mock_genai)

    with pytest.raises(DailyQuotaExhaustedError):
        client.generate("hello")
    assert mock_genai.models.generate_content.call_count == 1
    assert sleep_calls == []
    assert is_daily_quota_exhausted() is True
    reset_daily_quota_flag()

    # 2. Per-minute (RPM) 429: retries with backoff and succeeds on 2nd attempt
    mock_ok_resp = MagicMock()
    mock_ok_resp.text = "ok"
    mock_ok_resp.usage_metadata = None
    mock_genai2 = MagicMock()
    mock_genai2.models.generate_content.side_effect = [
        RuntimeError("429 RESOURCE_EXHAUSTED: GenerateRequestsPerMinute limit exceeded"),
        mock_ok_resp,
    ]
    monkeypatch.setattr(client, "_get_client", lambda: mock_genai2)

    out = client.generate("hello")
    assert out == "ok"
    assert mock_genai2.models.generate_content.call_count == 2
    assert len(sleep_calls) == 1


def test_harness_paired_max_calls_daily_stop_and_resume(tmp_path, monkeypatch, capsys):
    """Verify paired execution, --max-calls clean stop, daily-429 stop, and --resume."""
    from project.core.llm import DailyQuotaExhaustedError, reset_daily_quota_flag

    reset_daily_quota_flag()

    calls = []

    def fake_multi(query, machine_type):
        calls.append(("multi", query[:15]))
        return {
            "final_answer": {"diagnosed_cause": "belt", "steps": [], "safety_warnings": [], "abstained": False},
            "evidence": [],
            "trace_id": "TR-M1",
            "clarification_needed": False,
            "latency_ms": 10.0,
            "llm_calls": 3,
            "token_count": 100,
            "degraded": False,
            "error": None,
        }

    def fake_base(query, machine_type=None, allow_fake=False):
        calls.append(("baseline", query[:15]))
        return {
            "final_answer": {"diagnosed_cause": "belt", "steps": [], "safety_warnings": [], "abstained": False},
            "evidence": [],
            "trace_id": "TR-B1",
            "latency_ms": 5.0,
            "llm_calls": 1,
            "token_count": 50,
            "degraded": False,
            "error": None,
        }

    monkeypatch.setattr("project.benchmark.harness._run_multi_once", fake_multi)
    monkeypatch.setattr("project.benchmark.harness.run_baseline_query", fake_base)

    out_dir = tmp_path / "bench_run"
    # Stop after 4 calls (1 full multi+baseline pair = 3 + 1 = 4 calls)
    rc = harness_main([
        "--system", "both",
        "--split", "dev",
        "--runs", "1",
        "--limit", "3",
        "--max-calls", "4",
        "--rpm", "0",
        "--out", str(out_dir),
    ])
    assert rc == 0
    captured = capsys.readouterr().out
    assert "resume with the same command" in captured
    assert "[PROGRESS] calls_used=4 | pairs_complete=1/3" in captured
    assert len(calls) == 2  # 1 complete pair (multi, then baseline)

    # Resume with higher --max-calls finishes remaining 2 pairs without re-running pair 1
    calls.clear()
    rc2 = harness_main([
        "--system", "both",
        "--split", "dev",
        "--runs", "1",
        "--limit", "3",
        "--max-calls", "20",
        "--rpm", "0",
        "--resume",
        "--out", str(out_dir),
    ])
    assert rc2 == 0
    assert len(calls) == 4  # Only the 2 remaining pairs (multi+baseline each)

    # Daily quota 429 mid-way marks query degraded and stops cleanly with midnight Pacific message
    out_dir2 = tmp_path / "bench_daily"

    def fake_base_daily(query, machine_type=None, allow_fake=False):
        raise DailyQuotaExhaustedError("429 RESOURCE_EXHAUSTED per day RPD")

    monkeypatch.setattr("project.benchmark.harness.run_baseline_query", fake_base_daily)
    rc3 = harness_main([
        "--system", "both",
        "--split", "dev",
        "--runs", "1",
        "--limit", "2",
        "--rpm", "0",
        "--out", str(out_dir2),
    ])
    assert rc3 == 0
    cap3 = capsys.readouterr().out
    assert "midnight Pacific" in cap3
    assert "resume with the same command" in cap3
    # Baseline record must be logged as degraded=True so --resume re-runs it
    base_lines = [json.loads(l) for l in (out_dir2 / "raw_baseline.jsonl").read_text().splitlines() if l.strip()]
    assert len(base_lines) == 1 and base_lines[0]["degraded"] is True
