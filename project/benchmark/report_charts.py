"""Generate publication-ready PNG charts (200 dpi), CSVs, and Markdown tables from benchmark logs (WP7, 7.4).

Reads raw JSONL + summary.json produced by `project.benchmark.harness` and reuses
`project.benchmark.stats` for paired bootstrap CIs and Wilcoxon/McNemar p-values.

Usage:
    python -m project.benchmark.report_charts --run-dir results/bench/dev_dry --out results/bench/dev_dry/figures
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from project.benchmark.stats import aggregate_per_query, compare_systems

# Colour-blind-safe Okabe-Ito palette
COLOR_MULTI = "#0072B2"     # blue
COLOR_BASELINE = "#E69F00"  # orange
COLOR_ACCENT = "#009E73"    # bluish green
COLOR_WARN = "#D55E00"      # vermilion

O_METRICS: List[Tuple[str, str, str]] = [
    ("top1_root_cause_match", "O2", "Top-1 Root Cause"),
    ("top3_root_cause_hit", "O2", "Top-3 Root Cause"),
    ("cause_family_match", "O2", "Cause Family"),
    ("behaviour_match", "O3", "Behaviour Match"),
    ("safety_coverage", "O3", "Safety Coverage"),
    ("citation_validity", "O4", "Citation Validity"),
    ("groundedness", "O4", "Groundedness"),
]

TIER_ORDER = ["easy", "medium", "hard", "adversarial"]
TIER_METRICS = ["behaviour_match", "top3_root_cause_hit", "safety_coverage", "citation_validity"]


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_run_dir(run_dir: Path) -> Dict[str, Any]:
    """Load summary.json, run_meta.json (if present), and raw_<system>.jsonl from `run_dir`."""
    summary_path = run_dir / "summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"summary.json not found in {run_dir}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    run_meta: Dict[str, Any] = {}
    meta_path = run_dir / "run_meta.json"
    if meta_path.exists():
        run_meta = json.loads(meta_path.read_text(encoding="utf-8"))

    records: List[Dict[str, Any]] = []
    for raw_file in sorted(run_dir.glob("raw_*.jsonl")):
        records.extend(_load_jsonl(raw_file))
    if not records:
        raise FileNotFoundError(f"No raw_*.jsonl files found in {run_dir}")

    return {"summary": summary, "run_meta": run_meta, "records": records}


def _extract_header_meta(summary: Dict[str, Any], run_meta: Dict[str, Any], records: List[Dict[str, Any]]) -> Dict[str, Any]:
    meta = dict(summary.get("meta") or {})
    code_hashes = sorted({str(r.get("code_hash")) for r in records if r.get("code_hash")})
    code_hash = (
        meta.get("code_hash")
        or run_meta.get("code_hash")
        or (", ".join(code_hashes) if code_hashes else "unknown")
    )
    sys_summaries = summary.get("system_summaries") or {}
    n_queries = 0
    for s_info in sys_summaries.values():
        n_queries = max(n_queries, int(s_info.get("n_queries") or 0))
    if n_queries == 0:
        n_queries = len({str(r.get("query_id")) for r in records})

    return {
        "split": str(meta.get("split") or run_meta.get("split") or "unknown"),
        "n_queries": n_queries,
        "runs": int(meta.get("runs") or run_meta.get("runs") or 1),
        "model": str(meta.get("model") or run_meta.get("model") or "unknown"),
        "code_hash": str(code_hash),
    }


def _title_suffix(hmeta: Dict[str, Any]) -> str:
    return (
        f"split={hmeta['split']} | n={hmeta['n_queries']} | runs={hmeta['runs']} "
        f"| model={hmeta['model']} | code_hash={hmeta['code_hash']}"
    )


def _style_ax(ax: plt.Axes) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", linestyle="--", alpha=0.35)
    ax.set_axisbelow(True)


def _add_footer(fig: plt.Figure, degraded_count: int) -> None:
    if degraded_count > 0:
        fig.text(
            0.5,
            0.01,
            f"Degraded/excluded rows: {degraded_count} (excluded from averages; --allow-degraded active)",
            ha="center",
            va="bottom",
            fontsize=8,
            color="#555555",
        )


def _write_csv(path: Path, fieldnames: Sequence[str], rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(fieldnames))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _plot_o2_o3_o4(
    overall: Dict[str, Any],
    hmeta: Dict[str, Any],
    degraded_count: int,
    out_dir: Path,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for metric, obj, label in O_METRICS:
        entry = overall.get(metric)
        if not entry:
            continue
        rows.append(
            {
                "metric": metric,
                "objective": obj,
                "label": f"[{obj}] {label}",
                "n": entry.get("n", 0),
                "multi_mean": entry.get("multi_mean", 0.0),
                "baseline_mean": entry.get("baseline_mean", 0.0),
                "mean_diff": entry.get("mean_diff", 0.0),
                "ci_95_low": entry.get("ci_95_low", 0.0),
                "ci_95_high": entry.get("ci_95_high", 0.0),
                "wilcoxon_p": entry.get("wilcoxon_p"),
                "mcnemar_p": entry.get("mcnemar_p"),
            }
        )

    csv_path = out_dir / "metrics_o2_o3_o4.csv"
    _write_csv(
        csv_path,
        ["metric", "objective", "n", "multi_mean", "baseline_mean", "mean_diff", "ci_95_low", "ci_95_high", "wilcoxon_p", "mcnemar_p"],
        [{k: v for k, v in r.items() if k != "label"} for r in rows],
    )

    if not rows:
        return rows

    labels = [r["label"] for r in rows]
    x = np.arange(len(labels))
    width = 0.36

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.2), gridspec_kw={"width_ratios": [1.3, 1.0]})
    _style_ax(ax1)
    _style_ax(ax2)

    m_vals = [r["multi_mean"] for r in rows]
    b_vals = [r["baseline_mean"] for r in rows]
    ax1.bar(x - width / 2, m_vals, width, label="Multi-Agent", color=COLOR_MULTI)
    ax1.bar(x + width / 2, b_vals, width, label="Single-Agent Baseline", color=COLOR_BASELINE)
    ax1.set_xticks(x)
    ax1.set_xticklabels(labels, rotation=28, ha="right", fontsize=8.5)
    ax1.set_ylim(0, 1.12)
    ax1.set_ylabel("Mean Score (0–1)")
    ax1.set_title("System Means (O2, O3, O4)")
    ax1.legend(frameon=False, loc="lower right")

    diffs = np.array([r["mean_diff"] for r in rows], dtype=float)
    lo = np.array([max(0.0, r["mean_diff"] - r["ci_95_low"]) for r in rows], dtype=float)
    hi = np.array([max(0.0, r["ci_95_high"] - r["mean_diff"]) for r in rows], dtype=float)
    ax2.axhline(0.0, color="#444444", linewidth=1.0, linestyle=":")
    ax2.errorbar(x, diffs, yerr=[lo, hi], fmt="o", color=COLOR_MULTI, ecolor=COLOR_ACCENT, elinewidth=2, capsize=4)
    ax2.set_xticks(x)
    ax2.set_xticklabels(labels, rotation=28, ha="right", fontsize=8.5)
    ax2.set_ylabel("Paired Diff (Multi − Baseline, 95% CI)")
    ax2.set_title("Paired Bootstrap 95% CI")

    fig.suptitle(f"Objectives O2–O4: Multi vs Baseline\n{_title_suffix(hmeta)}", fontsize=11)
    _add_footer(fig, degraded_count)
    fig.tight_layout(rect=(0, 0.04 if degraded_count else 0, 1, 0.92))
    fig.savefig(out_dir / "metrics_o2_o3_o4.png", dpi=200)
    plt.close(fig)
    return rows


def _plot_by_tier(
    by_tier: Dict[str, Any],
    hmeta: Dict[str, Any],
    degraded_count: int,
    out_dir: Path,
) -> List[Dict[str, Any]]:
    tiers = [t for t in TIER_ORDER if t in by_tier] + [t for t in sorted(by_tier) if t not in TIER_ORDER]
    rows: List[Dict[str, Any]] = []
    for tier in tiers:
        t_data = by_tier[tier]
        for metric in TIER_METRICS:
            entry = t_data.get(metric)
            if not entry:
                continue
            rows.append(
                {
                    "tier": tier,
                    "metric": metric,
                    "n_paired": entry.get("n", 0),
                    "multi_mean": entry.get("multi_mean", 0.0),
                    "baseline_mean": entry.get("baseline_mean", 0.0),
                    "mean_diff": entry.get("mean_diff", 0.0),
                    "ci_95_low": entry.get("ci_95_low", 0.0),
                    "ci_95_high": entry.get("ci_95_high", 0.0),
                    "wilcoxon_p": entry.get("wilcoxon_p"),
                }
            )

    _write_csv(
        out_dir / "results_by_tier.csv",
        ["tier", "metric", "n_paired", "multi_mean", "baseline_mean", "mean_diff", "ci_95_low", "ci_95_high", "wilcoxon_p"],
        rows,
    )

    if not tiers:
        return rows

    fig, axes = plt.subplots(1, len(TIER_METRICS), figsize=(14, 4.5), sharey=True)
    if len(TIER_METRICS) == 1:
        axes = [axes]
    x = np.arange(len(tiers))
    width = 0.36
    for ax, metric in zip(axes, TIER_METRICS):
        _style_ax(ax)
        m_vals = [float((by_tier[t].get(metric) or {}).get("multi_mean", 0.0)) for t in tiers]
        b_vals = [float((by_tier[t].get(metric) or {}).get("baseline_mean", 0.0)) for t in tiers]
        ax.bar(x - width / 2, m_vals, width, label="Multi", color=COLOR_MULTI)
        ax.bar(x + width / 2, b_vals, width, label="Baseline", color=COLOR_BASELINE)
        ax.set_xticks(x)
        ax.set_xticklabels(tiers, rotation=20, ha="right", fontsize=8.5)
        ax.set_ylim(0, 1.12)
        ax.set_title(metric.replace("_", " ").title(), fontsize=9.5)
    axes[0].set_ylabel("Mean Score (0–1)")
    axes[-1].legend(frameon=False, loc="lower right")
    fig.suptitle(f"Results by Difficulty Tier\n{_title_suffix(hmeta)}", fontsize=11)
    _add_footer(fig, degraded_count)
    fig.tight_layout(rect=(0, 0.04 if degraded_count else 0, 1, 0.90))
    fig.savefig(out_dir / "results_by_tier.png", dpi=200)
    plt.close(fig)
    return rows


def _plot_safety_and_hazards(
    clean_records: List[Dict[str, Any]],
    sys_summaries: Dict[str, Any],
    hmeta: Dict[str, Any],
    degraded_count: int,
    out_dir: Path,
) -> List[Dict[str, Any]]:
    systems = [s for s in ("multi", "baseline") if s in sys_summaries or any(r.get("system") == s for r in clean_records)]
    rows: List[Dict[str, Any]] = []
    for sys_name in systems:
        sys_recs = [r for r in clean_records if r.get("system") == sys_name]
        cov_vals = [float(r["safety_coverage"]) for r in sys_recs if r.get("safety_coverage") is not None]
        mean_cov = float(np.mean(cov_vals)) if cov_vals else float((sys_summaries.get(sys_name, {}).get("metrics", {}).get("safety_coverage") or {}).get("mean", 0.0))
        incomplete_safety = sum(1 for v in cov_vals if v < 1.0)
        must_not_total = sum(int(r.get("must_not_violations") or 0) for r in sys_recs)
        unflagged_hazard_rows = sum(
            1 for r in sys_recs
            if (r.get("safety_coverage") is not None and float(r["safety_coverage"]) < 1.0) or int(r.get("must_not_violations") or 0) > 0
        )
        rows.append(
            {
                "system": sys_name,
                "n_runs": len(sys_recs),
                "safety_coverage_mean": round(mean_cov, 6),
                "incomplete_safety_count": incomplete_safety,
                "must_not_violations_total": must_not_total,
                "unflagged_hazard_rows": unflagged_hazard_rows,
            }
        )

    _write_csv(
        out_dir / "safety_and_hazards.csv",
        ["system", "n_runs", "safety_coverage_mean", "incomplete_safety_count", "must_not_violations_total", "unflagged_hazard_rows"],
        rows,
    )

    if not rows:
        return rows

    x = np.arange(len(rows))
    colors = [COLOR_MULTI if r["system"] == "multi" else COLOR_BASELINE for r in rows]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.5, 4.5))
    _style_ax(ax1)
    _style_ax(ax2)

    ax1.bar(x, [r["safety_coverage_mean"] for r in rows], width=0.5, color=colors)
    ax1.axhline(0.95, color=COLOR_ACCENT, linestyle="--", linewidth=1.2, label="O3 Target (0.95)")
    ax1.set_xticks(x)
    ax1.set_xticklabels([r["system"] for r in rows])
    ax1.set_ylim(0, 1.12)
    ax1.set_ylabel("Mean Safety Coverage (0–1)")
    ax1.set_title("Safety Coverage")
    ax1.legend(frameon=False, loc="lower right")

    ax2.bar(x, [r["unflagged_hazard_rows"] for r in rows], width=0.5, color=COLOR_WARN)
    ax2.set_xticks(x)
    ax2.set_xticklabels([r["system"] for r in rows])
    ax2.set_ylabel("Row Count")
    ax2.set_title("Unflagged / Incomplete Hazard Rows")

    fig.suptitle(f"Safety Coverage & Unflagged Hazard Count\n{_title_suffix(hmeta)}", fontsize=11)
    _add_footer(fig, degraded_count)
    fig.tight_layout(rect=(0, 0.04 if degraded_count else 0, 1, 0.90))
    fig.savefig(out_dir / "safety_and_hazards.png", dpi=200)
    plt.close(fig)
    return rows


def _plot_latency(
    clean_records: List[Dict[str, Any]],
    hmeta: Dict[str, Any],
    degraded_count: int,
    out_dir: Path,
) -> List[Dict[str, Any]]:
    systems = [s for s in ("multi", "baseline") if any(r.get("system") == s for r in clean_records)]
    rows: List[Dict[str, Any]] = []
    for sys_name in systems:
        lats = [float(r["latency_ms"]) for r in clean_records if r.get("system") == sys_name and r.get("latency_ms") is not None]
        if not lats:
            continue
        arr = np.asarray(lats, dtype=float)
        rows.append(
            {
                "system": sys_name,
                "n_runs": len(lats),
                "p50_ms": round(float(np.percentile(arr, 50)), 2),
                "p95_ms": round(float(np.percentile(arr, 95)), 2),
                "max_ms": round(float(np.max(arr)), 2),
                "mean_ms": round(float(np.mean(arr)), 2),
            }
        )

    _write_csv(
        out_dir / "latency_distribution.csv",
        ["system", "n_runs", "p50_ms", "p95_ms", "max_ms", "mean_ms"],
        rows,
    )

    if not rows:
        return rows

    x = np.arange(len(rows))
    width = 0.34
    fig, ax = plt.subplots(figsize=(8.2, 4.6))
    _style_ax(ax)

    ax.bar(x - width / 2, [r["p50_ms"] for r in rows], width, label="p50 (median)", color=COLOR_MULTI)
    ax.bar(x + width / 2, [r["p95_ms"] for r in rows], width, label="p95", color=COLOR_BASELINE)
    ax.set_xticks(x)
    ax.set_xticklabels([r["system"] for r in rows])
    ax.set_ylabel("Latency (ms)")
    ax.set_title(f"Latency Distribution (p50 / p95)\n{_title_suffix(hmeta)}", fontsize=11)
    ax.legend(frameon=False)
    _add_footer(fig, degraded_count)
    fig.tight_layout(rect=(0, 0.04 if degraded_count else 0, 1, 0.95))
    fig.savefig(out_dir / "latency_distribution.png", dpi=200)
    plt.close(fig)
    return rows


def _write_tables_md(
    out_dir: Path,
    hmeta: Dict[str, Any],
    degraded_count: int,
    o_rows: List[Dict[str, Any]],
    tier_rows: List[Dict[str, Any]],
    safety_rows: List[Dict[str, Any]],
    latency_rows: List[Dict[str, Any]],
) -> None:
    lines: List[str] = [
        f"# Benchmark Tables ({_title_suffix(hmeta)})",
        "",
        f"- **Split:** `{hmeta['split']}` · **n:** `{hmeta['n_queries']}` queries · **Runs:** `{hmeta['runs']}` · **Model:** `{hmeta['model']}` · **Code hash:** `{hmeta['code_hash']}` · **Degraded/excluded:** `{degraded_count}`",
        "",
        "## 1. Per-Metric Multi vs Baseline (95% Paired Bootstrap CI)",
        "",
        "| Metric | Obj | n | Multi | Baseline | Diff (M−B) | 95% CI | Wilcoxon p | McNemar p |",
        "|---|---|---:|---:|---:|---:|---|---:|---:|",
    ]
    for r in o_rows:
        wp = f"{r['wilcoxon_p']:.4f}" if r["wilcoxon_p"] is not None else "n/a"
        mp = f"{r['mcnemar_p']:.4f}" if r["mcnemar_p"] is not None else "n/a"
        lines.append(
            f"| `{r['metric']}` | {r['objective']} | {r['n']} | {r['multi_mean']:.4f} | {r['baseline_mean']:.4f} | "
            f"{r['mean_diff']:+.4f} | [{r['ci_95_low']:+.4f}, {r['ci_95_high']:+.4f}] | {wp} | {mp} |"
        )

    lines.extend([
        "",
        "## 2. Results by Tier",
        "",
        "| Tier | Metric | n | Multi | Baseline | Diff (M−B) | 95% CI | Wilcoxon p |",
        "|---|---|---:|---:|---:|---:|---|---:|",
    ])
    for r in tier_rows:
        wp = f"{r['wilcoxon_p']:.4f}" if r["wilcoxon_p"] is not None else "n/a"
        lines.append(
            f"| `{r['tier']}` | `{r['metric']}` | {r['n_paired']} | {r['multi_mean']:.4f} | {r['baseline_mean']:.4f} | "
            f"{r['mean_diff']:+.4f} | [{r['ci_95_low']:+.4f}, {r['ci_95_high']:+.4f}] | {wp} |"
        )

    lines.extend([
        "",
        "## 3. Safety Coverage & Unflagged Hazard Rows",
        "",
        "| System | Runs | Safety Coverage | Incomplete Safety Rows (<1.0) | Must-Not Violations | Unflagged/Incomplete Hazard Rows |",
        "|---|---:|---:|---:|---:|---:|",
    ])
    for r in safety_rows:
        lines.append(
            f"| `{r['system']}` | {r['n_runs']} | {r['safety_coverage_mean']:.4f} | {r['incomplete_safety_count']} | "
            f"{r['must_not_violations_total']} | {r['unflagged_hazard_rows']} |"
        )

    lines.extend([
        "",
        "## 4. Latency Distribution (ms)",
        "",
        "| System | Runs | p50 (ms) | p95 (ms) | max (ms) | mean (ms) |",
        "|---|---:|---:|---:|---:|---:|",
    ])
    for r in latency_rows:
        lines.append(
            f"| `{r['system']}` | {r['n_runs']} | {r['p50_ms']:.2f} | {r['p95_ms']:.2f} | {r['max_ms']:.2f} | {r['mean_ms']:.2f} |"
        )
    lines.append("")
    (out_dir / "tables.md").write_text("\n".join(lines), encoding="utf-8")


def generate_report_charts(
    run_dir: Path,
    out_dir: Path,
    *,
    allow_degraded: bool = False,
) -> Dict[str, Any]:
    """Generate PNG charts, CSVs, and tables.md from a benchmark `run_dir`."""
    loaded = load_run_dir(run_dir)
    summary = loaded["summary"]
    run_meta = loaded["run_meta"]
    records = loaded["records"]

    degraded_records = sum(1 for r in records if bool(r.get("degraded")))
    degraded_summary = int((summary.get("counts") or {}).get("degraded_runs") or 0)
    degraded_count = max(degraded_records, degraded_summary)

    if degraded_count > 0 and not allow_degraded:
        raise ValueError(
            f"Run directory {run_dir} contains {degraded_count} degraded/excluded row(s). "
            "Pass --allow-degraded to generate charts with a degraded-count footer."
        )

    clean_records = [r for r in records if not bool(r.get("degraded"))]
    hmeta = _extract_header_meta(summary, run_meta, records)

    # Reuse stats.py (or precomputed paired_comparison if identical); recomputing via stats.compare_systems ensures consistency
    per_query_df, _ = aggregate_per_query(records)
    paired_comp = summary.get("paired_comparison") or compare_systems(per_query_df)
    overall = paired_comp.get("overall") or {}
    by_tier = paired_comp.get("by_tier") or {}
    sys_summaries = summary.get("system_summaries") or {}

    out_dir.mkdir(parents=True, exist_ok=True)
    o_rows = _plot_o2_o3_o4(overall, hmeta, degraded_count, out_dir)
    tier_rows = _plot_by_tier(by_tier, hmeta, degraded_count, out_dir)
    safety_rows = _plot_safety_and_hazards(clean_records, sys_summaries, hmeta, degraded_count, out_dir)
    latency_rows = _plot_latency(clean_records, hmeta, degraded_count, out_dir)
    _write_tables_md(out_dir, hmeta, degraded_count, o_rows, tier_rows, safety_rows, latency_rows)

    return {
        "meta": hmeta,
        "degraded_count": degraded_count,
        "out_dir": str(out_dir),
        "o_rows": o_rows,
        "tier_rows": tier_rows,
        "safety_rows": safety_rows,
        "latency_rows": latency_rows,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True, help="Benchmark run directory containing summary.json and raw_*.jsonl")
    parser.add_argument("--out", type=Path, default=None, help="Output figures directory (default: <run-dir>/figures)")
    parser.add_argument("--allow-degraded", action="store_true", help="Allow runs containing degraded/excluded rows")
    args = parser.parse_args(argv)

    out_dir = args.out or (args.run_dir / "figures")
    try:
        res = generate_report_charts(args.run_dir, out_dir, allow_degraded=args.allow_degraded)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    print(f"Generated charts and tables in {res['out_dir']} ({_title_suffix(res['meta'])})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
