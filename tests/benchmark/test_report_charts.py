"""Offline tests for project/benchmark/report_charts.py using a tiny synthetic run dir."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from project.benchmark.report_charts import generate_report_charts, main
from project.benchmark.stats import aggregate_per_query, compare_systems, summarize_single_system


def _write_synthetic_run_dir(run_dir: Path, *, with_degraded: bool = False) -> Dict:
    run_dir.mkdir(parents=True, exist_ok=True)
    # Clearly labelled synthetic test fixture
    rows_multi = [
        {
            "system": "multi", "query_id": "SYN-01", "run": 1, "code_hash": "syn12345",
            "tier": "easy", "degraded": False, "behaviour_match": True,
            "top1_root_cause_match": True, "top3_root_cause_hit": True, "cause_family_match": True,
            "safety_coverage": 1.0, "must_not_violations": 0, "already_tried_repeated": 0,
            "citation_validity": 1.0, "groundedness": 1.0, "latency_ms": 1200.0,
            "llm_calls": 4, "cost_proxy": 500.0,
        },
        {
            "system": "multi", "query_id": "SYN-02", "run": 1, "code_hash": "syn12345",
            "tier": "adversarial", "degraded": False, "behaviour_match": True,
            "top1_root_cause_match": False, "top3_root_cause_hit": False, "cause_family_match": False,
            "safety_coverage": 1.0, "must_not_violations": 0, "already_tried_repeated": 0,
            "citation_validity": 1.0, "groundedness": None, "latency_ms": 1800.0,
            "llm_calls": 3, "cost_proxy": 400.0,
        },
    ]
    rows_base = [
        {
            "system": "baseline", "query_id": "SYN-01", "run": 1, "code_hash": "syn12345",
            "tier": "easy", "degraded": False, "behaviour_match": True,
            "top1_root_cause_match": False, "top3_root_cause_hit": True, "cause_family_match": True,
            "safety_coverage": 0.5, "must_not_violations": 1, "already_tried_repeated": 0,
            "citation_validity": 0.5, "groundedness": 0.5, "latency_ms": 600.0,
            "llm_calls": 1, "cost_proxy": 300.0,
        },
        {
            "system": "baseline", "query_id": "SYN-02", "run": 1, "code_hash": "syn12345",
            "tier": "adversarial", "degraded": with_degraded, "behaviour_match": False,
            "top1_root_cause_match": False, "top3_root_cause_hit": False, "cause_family_match": False,
            "safety_coverage": 1.0, "must_not_violations": 0, "already_tried_repeated": 0,
            "citation_validity": 1.0, "groundedness": None, "latency_ms": 700.0,
            "llm_calls": 1, "cost_proxy": 300.0,
        },
    ]
    (run_dir / "raw_multi.jsonl").write_text("\n".join(json.dumps(r) for r in rows_multi) + "\n", encoding="utf-8")
    (run_dir / "raw_baseline.jsonl").write_text("\n".join(json.dumps(r) for r in rows_base) + "\n", encoding="utf-8")

    all_recs = rows_multi + rows_base
    per_q, counts = aggregate_per_query(all_recs)
    paired = compare_systems(per_q, n_resamples=200, seed=42)
    summary = {
        "meta": {"split": "synthetic_test", "runs": 1, "model": "synthetic-mock", "code_hash": "syn12345"},
        "counts": counts,
        "system_summaries": {
            "multi": summarize_single_system(per_q, "multi"),
            "baseline": summarize_single_system(per_q, "baseline"),
        },
        "paired_comparison": paired,
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def test_report_charts_outputs_and_csv_match_summary(tmp_path: Path):
    run_dir = tmp_path / "syn_run"
    out_dir = tmp_path / "figures"
    summary = _write_synthetic_run_dir(run_dir, with_degraded=False)

    res = generate_report_charts(run_dir, out_dir)
    assert res["degraded_count"] == 0

    expected_files = [
        "metrics_o2_o3_o4.png", "metrics_o2_o3_o4.csv",
        "results_by_tier.png", "results_by_tier.csv",
        "safety_and_hazards.png", "safety_and_hazards.csv",
        "latency_distribution.png", "latency_distribution.csv",
        "tables.md",
    ]
    for fname in expected_files:
        p = out_dir / fname
        assert p.exists() and p.stat().st_size > 0, f"Missing or empty {fname}"

    with open(out_dir / "metrics_o2_o3_o4.csv", "r", encoding="utf-8") as f:
        csv_rows = {r["metric"]: r for r in csv.DictReader(f)}

    overall = summary["paired_comparison"]["overall"]
    for metric in ("top1_root_cause_match", "safety_coverage", "citation_validity"):
        assert float(csv_rows[metric]["multi_mean"]) == pytest.approx(overall[metric]["multi_mean"])
        assert float(csv_rows[metric]["baseline_mean"]) == pytest.approx(overall[metric]["baseline_mean"])
        assert float(csv_rows[metric]["ci_95_low"]) == pytest.approx(overall[metric]["ci_95_low"])

    tables_md = (out_dir / "tables.md").read_text(encoding="utf-8")
    assert "split=synthetic_test" in tables_md
    assert "n=2" in tables_md
    assert "code_hash=syn12345" in tables_md


def test_report_charts_refuses_degraded_unless_allowed(tmp_path: Path):
    run_dir = tmp_path / "syn_deg"
    out_dir = tmp_path / "figures_deg"
    _write_synthetic_run_dir(run_dir, with_degraded=True)

    with pytest.raises(SystemExit, match="degraded/excluded"):
        main(["--run-dir", str(run_dir), "--out", str(out_dir)])

    rc = main(["--run-dir", str(run_dir), "--out", str(out_dir), "--allow-degraded"])
    assert rc == 0
    assert (out_dir / "metrics_o2_o3_o4.png").exists()
