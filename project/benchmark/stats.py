"""Statistical comparison module for Factory Memory (WP7, subtask 7.3).

Uses numpy, pandas, and scipy to:
- Average N runs per query per system (excluding degraded runs and reporting counts)
- Compute paired comparisons (multi vs baseline) per tier and overall:
  - Mean difference (multi - baseline)
  - Paired bootstrap 95% CI (10,000 resamples, fixed seed)
  - Wilcoxon signed-rank p-value (handles all-ties/zero diffs safely)
  - Paired Cohen's d effect size
  - McNemar test + paired bootstrap for binary metrics
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple
import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

CONTINUOUS_OR_RATE_METRICS = [
    "behaviour_match",
    "top1_root_cause_match",
    "top3_root_cause_hit",
    "cause_family_match",
    "safety_coverage",
    "must_not_violations",
    "already_tried_repeated",
    "citation_validity",
    "groundedness",
    "latency_ms",
    "llm_calls",
    "cost_proxy",
]

BINARY_METRICS = {
    "behaviour_match",
    "top1_root_cause_match",
    "top3_root_cause_hit",
    "cause_family_match",
}


def paired_bootstrap_ci(
    diffs: np.ndarray,
    *,
    n_resamples: int = 10_000,
    seed: int = 42,
    alpha: float = 0.05,
) -> Tuple[float, float]:
    """Compute paired bootstrap (1 - alpha) confidence interval for the mean difference."""
    n = len(diffs)
    if n == 0:
        return (0.0, 0.0)
    if n == 1 or np.all(diffs == diffs[0]):
        val = float(np.mean(diffs))
        return (val, val)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(n_resamples, n))
    boot_means = diffs[idx].mean(axis=1)
    lo = float(np.quantile(boot_means, alpha / 2.0))
    hi = float(np.quantile(boot_means, 1.0 - alpha / 2.0))
    return (round(lo, 6), round(hi, 6))


def safe_wilcoxon(diffs: np.ndarray) -> Optional[float]:
    """Wilcoxon signed-rank two-sided p-value, handling all-ties / all-zero safely."""
    nz = diffs[diffs != 0]
    if len(nz) == 0:
        return 1.0
    try:
        res = scipy_stats.wilcoxon(diffs, zero_method="wilcox", alternative="two-sided")
        p = float(res.pvalue)
        return None if np.isnan(p) else round(p, 6)
    except Exception:
        return None


def safe_cohens_d(diffs: np.ndarray) -> Optional[float]:
    """Paired Cohen's d effect size (mean(diff) / std(diff, ddof=1))."""
    if len(diffs) < 2:
        return None
    sd = float(np.std(diffs, ddof=1))
    if sd == 0.0:
        return 0.0
    return round(float(np.mean(diffs)) / sd, 6)


def safe_mcnemar(multi_bin: np.ndarray, base_bin: np.ndarray) -> Optional[float]:
    """Exact binomial McNemar p-value on discordant pairs (b, c)."""
    m = (multi_bin >= 0.5).astype(int)
    b_arr = (base_bin >= 0.5).astype(int)
    n01 = int(np.sum((m == 1) & (b_arr == 0)))
    n10 = int(np.sum((m == 0) & (b_arr == 1)))
    n_disc = n01 + n10
    if n_disc == 0:
        return 1.0
    res = scipy_stats.binomtest(min(n01, n10), n=n_disc, p=0.5, alternative="two-sided")
    return round(float(res.pvalue), 6)


def aggregate_per_query(records: List[Dict[str, Any]]) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Average N runs per query per system, excluding degraded runs and reporting counts."""
    if not records:
        return pd.DataFrame(), {"total_runs": 0, "degraded_runs": 0, "non_degraded_runs": 0}

    df = pd.DataFrame(records)
    total_runs = len(df)
    degraded_mask = df["degraded"].fillna(False).astype(bool)
    degraded_runs = int(degraded_mask.sum())
    clean = df[~degraded_mask].copy()

    counts = {
        "total_runs": int(total_runs),
        "degraded_runs": degraded_runs,
        "non_degraded_runs": int(len(clean)),
        "degraded_by_system": {
            str(sys_name): int(grp["degraded"].fillna(False).astype(bool).sum())
            for sys_name, grp in df.groupby("system")
        },
        "total_by_system": {
            str(sys_name): int(len(grp))
            for sys_name, grp in df.groupby("system")
        },
    }

    if clean.empty:
        return pd.DataFrame(), counts

    # Ensure numeric types for aggregation
    for col in CONTINUOUS_OR_RATE_METRICS:
        if col in clean.columns:
            clean[col] = pd.to_numeric(clean[col], errors="coerce")

    agg_cols = [c for c in CONTINUOUS_OR_RATE_METRICS if c in clean.columns]
    grouped = (
        clean.groupby(["system", "query_id", "tier"], as_index=False)[agg_cols]
        .mean()
    )
    return grouped, counts


def compare_systems(
    per_query_df: pd.DataFrame,
    *,
    n_resamples: int = 10_000,
    seed: int = 42,
) -> Dict[str, Any]:
    """Run paired comparisons (multi vs baseline) overall and per tier."""
    if per_query_df.empty:
        return {"overall": {}, "by_tier": {}, "paired_query_count": 0}

    multi_df = per_query_df[per_query_df["system"] == "multi"].set_index("query_id")
    base_df = per_query_df[per_query_df["system"] == "baseline"].set_index("query_id")
    common_ids = sorted(set(multi_df.index) & set(base_df.index))

    if not common_ids:
        return {"overall": {}, "by_tier": {}, "paired_query_count": 0}

    def _compare_subset(qids: Sequence[str]) -> Dict[str, Any]:
        out: Dict[str, Any] = {"n_paired": len(qids)}
        m_sub = multi_df.loc[list(qids)]
        b_sub = base_df.loc[list(qids)]

        for metric in CONTINUOUS_OR_RATE_METRICS:
            if metric not in m_sub.columns or metric not in b_sub.columns:
                continue
            pair = pd.DataFrame({"m": m_sub[metric], "b": b_sub[metric]}).dropna()
            if pair.empty:
                continue
            m_vals = pair["m"].to_numpy(dtype=float)
            b_vals = pair["b"].to_numpy(dtype=float)
            diffs = m_vals - b_vals
            ci_lo, ci_hi = paired_bootstrap_ci(diffs, n_resamples=n_resamples, seed=seed)
            entry: Dict[str, Any] = {
                "n": int(len(diffs)),
                "multi_mean": round(float(np.mean(m_vals)), 6),
                "baseline_mean": round(float(np.mean(b_vals)), 6),
                "mean_diff": round(float(np.mean(diffs)), 6),
                "ci_95_low": ci_lo,
                "ci_95_high": ci_hi,
                "wilcoxon_p": safe_wilcoxon(diffs),
                "cohens_d": safe_cohens_d(diffs),
            }
            if metric in BINARY_METRICS:
                entry["mcnemar_p"] = safe_mcnemar(m_vals, b_vals)
            out[metric] = entry
        return out

    overall = _compare_subset(common_ids)
    by_tier: Dict[str, Any] = {}
    tiers = sorted(set(multi_df.loc[common_ids, "tier"].astype(str)))
    for tier in tiers:
        tier_ids = [qid for qid in common_ids if str(multi_df.loc[qid, "tier"]) == tier]
        if tier_ids:
            by_tier[tier] = _compare_subset(tier_ids)

    return {
        "paired_query_count": len(common_ids),
        "overall": overall,
        "by_tier": by_tier,
    }


def summarize_single_system(per_query_df: pd.DataFrame, system_name: str) -> Dict[str, Any]:
    """Compute per-system summary means (after averaging N runs per query)."""
    sub = per_query_df[per_query_df["system"] == system_name]
    if sub.empty:
        return {"n_queries": 0, "metrics": {}}
    metrics_out: Dict[str, Any] = {}
    for metric in CONTINUOUS_OR_RATE_METRICS:
        if metric not in sub.columns:
            continue
        vals = sub[metric].dropna().to_numpy(dtype=float)
        if len(vals) == 0:
            continue
        metrics_out[metric] = {
            "n": int(len(vals)),
            "mean": round(float(np.mean(vals)), 6),
            "std": round(float(np.std(vals, ddof=1)), 6) if len(vals) > 1 else 0.0,
        }
    return {"n_queries": int(len(sub)), "metrics": metrics_out}
