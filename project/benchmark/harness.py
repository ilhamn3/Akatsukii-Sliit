"""Unified benchmark harness for Factory Memory (WP7, subtask 7.2).

Supports running the multi-agent system (`project.core.api.run_query`),
the single-agent baseline (`project.benchmark.baseline_single_agent.run_baseline_query`),
or both, on `dev` or `test` splits with resumable caching, deterministic metrics,
paired statistics, and optional extraction/human-study ingestion.

Usage:
    python -m project.benchmark.harness --system both --split dev --runs 1 --out results/bench/dev_dry --resume
    python -m project.benchmark.harness --system both --split test --runs 3 --out results/bench/final --final --resume
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv
    _env = Path(__file__).resolve().parents[2] / ".env"
    if _env.exists():
        load_dotenv(dotenv_path=_env)
except ImportError:
    pass

from project.benchmark.baseline_single_agent import get_baseline_prompt_hash, run_baseline_query
from project.benchmark.metrics import load_faults_catalogue, score_query_result
from project.benchmark.stats import aggregate_per_query, compare_systems, summarize_single_system

ROOT = Path(__file__).resolve().parents[2]
TEST_SET_DIR = ROOT / "project" / "data" / "test_set"
VERIFY_SCRIPT = TEST_SET_DIR / "build_test_set.py"

MULTI_VERSION_FILES = [
    ROOT / "project" / "agents" / "a5_diagnostic.py",
    ROOT / "project" / "agents" / "a7_safety.py",
    ROOT / "project" / "agents" / "a8_orchestrator.py",
    ROOT / "project" / "core" / "api.py",
    ROOT / "project" / "core" / "config.yaml",
    ROOT / "project" / "core" / "prompts" / "diagnostic_v1.md",
    ROOT / "project" / "core" / "prompts" / "orchestrator_v1.md",
    ROOT / "project" / "core" / "safety_rules" / "rules.yaml",
    ROOT / "project" / "core" / "safety_rules" / "unsafe_request_patterns.yaml",
]


def compute_system_hash(system_name: str) -> str:
    """Compute short SHA-256 hash for cache invalidation when system code/prompts change."""
    if system_name == "baseline":
        return get_baseline_prompt_hash()
    h = hashlib.sha256()
    for p in MULTI_VERSION_FILES:
        if p.exists():
            h.update(p.read_bytes())
    return h.hexdigest()[:12]


def _infer_machine(query: str) -> Optional[str]:
    q_low = query.lower()
    if "lathe" in q_low:
        return "cnc_lathe"
    if "press" in q_low and "compressor" not in q_low:
        return "hydraulic_press"
    if "compressor" in q_low:
        return "air_compressor"
    if "conveyor" in q_low:
        return "conveyor_line"
    if "moulding" in q_low or "mould" in q_low:
        return "injection_moulding"
    return None


def _git_commit_short() -> str:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=5,
        )
        return res.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def verify_frozen_test_set() -> tuple[bool, str]:
    """Run build_test_set.py --verify and return (ok, output)."""
    res = subprocess.run(
        [sys.executable, str(VERIFY_SCRIPT), "--verify"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=15,
    )
    output = (res.stdout or "").strip() or (res.stderr or "").strip()
    return (res.returncode == 0), output


def load_split(split: str, *, final: bool) -> tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Load dev or test queries/gold. Enforces --final guard on test split."""
    if split == "test" and not final:
        raise PermissionError(
            "Refusing to read frozen test set (test_queries.jsonl / test_gold.jsonl) without --final."
        )
    q_path = TEST_SET_DIR / f"{split}_queries.jsonl"
    g_path = TEST_SET_DIR / f"{split}_gold.jsonl"
    queries = [json.loads(l) for l in q_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    golds = {
        g["query_id"]: g
        for g in (json.loads(l) for l in g_path.read_text(encoding="utf-8").splitlines() if l.strip())
    }
    return queries, golds


def load_cache(raw_path: Path, system_name: str, code_hash: str) -> Dict[tuple[str, int], Dict[str, Any]]:
    """Load non-degraded cached records keyed by (query_id, run) matching system & code_hash."""
    cached: Dict[tuple[str, int], Dict[str, Any]] = {}
    if not raw_path.exists():
        return cached
    for line in raw_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            if (
                rec.get("system") == system_name
                and rec.get("code_hash") == code_hash
                and not rec.get("degraded")
                and rec.get("query_id")
                and rec.get("run") is not None
            ):
                cached[(str(rec["query_id"]), int(rec["run"]))] = rec
        except Exception:
            continue
    return cached


def _append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")


def _run_multi_once(query: str, machine_type: Optional[str]) -> Dict[str, Any]:
    from project.core.api import run_query

    t0 = time.perf_counter()
    res = run_query(query=query, machine_type=machine_type)
    latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)

    trace_events = res.get("trace_events") or []
    flags = res.get("flags") or {}
    llm_calls = sum(
        1 for r in trace_events
        if r.get("llm") not in (None, "fallback")
        and r.get("event") in ("AGENT_COMPLETED", "CITATION_VALIDATED", "PLAN_REVISED")
    )
    toks = [r.get("tokens") for r in trace_events if r.get("tokens") is not None]
    token_count = int(sum(toks)) if toks else None

    llm_check = str(flags.get("llm_check") or "")
    degraded = bool(
        res.get("error") is not None
        or llm_check.startswith("skipped")
        or any(r.get("decision") == "FALLBACK_USED" or r.get("llm") == "fallback" for r in trace_events)
    )
    err = None
    if res.get("error"):
        err = f"{res['error'].get('type')}: {res['error'].get('message')}"
    elif degraded:
        err = f"Degraded ({llm_check or 'fallback used'})"

    return {
        "final_answer": res.get("final_answer"),
        "evidence": res.get("evidence") or [],
        "trace_id": res.get("trace_id"),
        "clarification_needed": bool(res.get("clarification_needed")),
        "latency_ms": latency_ms,
        "llm_calls": llm_calls,
        "token_count": token_count,
        "degraded": degraded,
        "error": err,
    }


def _load_optional_extraction(extraction_json: Optional[Path] = None) -> Dict[str, Any]:
    if extraction_json is not None:
        candidates = [extraction_json] if extraction_json.exists() else []
    else:
        eval_dir = ROOT / "project" / "evaluation" / "results"
        candidates = sorted(eval_dir.glob("*/extraction_metrics_eval.json")) if eval_dir.exists() else []
    if not candidates:
        return {"status": "missing"}
    latest = candidates[-1]
    try:
        data = json.loads(latest.read_text(encoding="utf-8"))
        return {
            "status": "present",
            "path": str(latest.relative_to(ROOT)),
            "n_episodes": data.get("n_episodes"),
            "primary_macro_f1": (data.get("primary") or {}).get("macro_f1"),
            "primary_micro_f1": ((data.get("primary") or {}).get("micro") or {}).get("f1"),
            "exact_macro_f1": (data.get("exact_match") or {}).get("macro_f1"),
            "failed_episodes": len(data.get("failed_episodes") or []),
        }
    except Exception as exc:
        return {"status": f"error: {exc}"}


def _load_optional_human_study(human_csv: Optional[Path] = None) -> Dict[str, Any]:
    if human_csv is not None:
        candidates = [human_csv] if human_csv.exists() else []
    else:
        hs_dir = ROOT / "project" / "human_study"
        candidates = sorted(hs_dir.glob("*.csv")) if hs_dir.exists() else []
    if not candidates:
        return {"status": "missing"}
    latest = candidates[-1]
    try:
        with open(latest, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        return {
            "status": "present",
            "path": str(latest.relative_to(ROOT)),
            "n_rows": len(rows),
        }
    except Exception as exc:
        return {"status": f"error: {exc}"}


def write_markdown_report(
    md_path: Path,
    *,
    meta: Dict[str, Any],
    counts: Dict[str, Any],
    system_summaries: Dict[str, Any],
    comparison: Dict[str, Any],
    extraction_info: Dict[str, Any],
    human_info: Dict[str, Any],
) -> None:
    lines = [
        f"# Factory Memory — Benchmark Report ({meta['split'].upper()} Split)",
        "",
        f"- **Generated:** `{meta['generated_at']}`",
        f"- **Git Commit:** `{meta['git_commit']}`",
        f"- **Model:** `{meta['model']}` | **Thinking:** `{meta['thinking']}` | **Temperature:** `{meta['temperature']}`",
        f"- **Runs per query:** `{meta['runs']}` | **Final Mode:** `{meta['final']}` | **FROZEN.sha256:** `{meta['frozen_verify']}`",
        f"- **Degraded / Excluded Runs:** `{counts.get('degraded_runs', 0)}` of `{counts.get('total_runs', 0)}` "
        f"(by system: `{counts.get('degraded_by_system', {})}`)",
        "",
        "## Per-System Summary (Averaged Over Non-Degraded Runs Per Query)",
        "",
        "| Metric | " + " | ".join(system_summaries.keys()) + " |",
        "|---|" + "|".join("---:" for _ in system_summaries) + "|",
    ]
    metric_keys = [
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
    for mk in metric_keys:
        row_vals = []
        for sys_name, summary in system_summaries.items():
            m_entry = (summary.get("metrics") or {}).get(mk)
            if m_entry is None:
                row_vals.append("N/A")
            elif mk in ("latency_ms", "llm_calls", "cost_proxy", "must_not_violations", "already_tried_repeated"):
                row_vals.append(f"{m_entry['mean']:.2f}")
            else:
                row_vals.append(f"{m_entry['mean']:.2%}")
        lines.append(f"| `{mk}` | " + " | ".join(row_vals) + " |")

    if comparison.get("paired_query_count", 0) > 0:
        lines.extend([
            "",
            f"## Paired Comparison: Multi-Agent vs Single-Agent Baseline (n={comparison['paired_query_count']} paired queries)",
            "",
            "| Metric | Multi | Baseline | Diff (M-B) | 95% Bootstrap CI | Wilcoxon p | Cohen's d | McNemar p |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ])
        overall = comparison["overall"]
        for mk in metric_keys:
            st = overall.get(mk)
            if not st:
                continue
            mcn = f"{st['mcnemar_p']:.4f}" if st.get("mcnemar_p") is not None else "-"
            wil = f"{st['wilcoxon_p']:.4f}" if st.get("wilcoxon_p") is not None else "-"
            cd = f"{st['cohens_d']:.3f}" if st.get("cohens_d") is not None else "-"
            lines.append(
                f"| `{mk}` | {st['multi_mean']:.4f} | {st['baseline_mean']:.4f} | {st['mean_diff']:+.4f} | "
                f"[{st['ci_95_low']:+.4f}, {st['ci_95_high']:+.4f}] | {wil} | {cd} | {mcn} |"
            )

        lines.extend(["", "## Paired Behaviour Match by Tier", "", "| Tier | n | Multi | Baseline | Diff | Wilcoxon p |", "|---|---:|---:|---:|---:|---:|"])
        for tier, tdata in (comparison.get("by_tier") or {}).items():
            bm = tdata.get("behaviour_match")
            if bm:
                wil = f"{bm['wilcoxon_p']:.4f}" if bm.get("wilcoxon_p") is not None else "-"
                lines.append(
                    f"| {tier} | {bm['n']} | {bm['multi_mean']:.2%} | {bm['baseline_mean']:.2%} | {bm['mean_diff']:+.2%} | {wil} |"
                )

    lines.extend([
        "",
        "## Optional Upstream & Human Study Inputs",
        "",
        f"- **Extraction Metrics (A2):** `{extraction_info}`" if extraction_info.get("status") == "present" else "- **Extraction Metrics (A2):** `missing`",
        f"- **Human Study Results:** `{human_info}`" if human_info.get("status") == "present" else "- **Human Study Results:** `missing`",
    ])

    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--system", choices=["multi", "baseline", "both"], required=True)
    parser.add_argument("--split", choices=["dev", "test"], default="dev")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--final", action="store_true", help="Required to unlock frozen test_* files")
    parser.add_argument("--resume", action="store_true", help="Resume from raw JSONL cache")
    parser.add_argument("--fake-llm", action="store_true", help="Allow FakeLLM (for offline testing only)")
    parser.add_argument("--model", type=str, default=None)
    parser.add_argument("--thinking", type=str, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--extraction-json", type=Path, default=None)
    parser.add_argument("--human-csv", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.fake_llm:
        os.environ["FACTORY_FAKE_LLM"] = "1"
    if args.model:
        os.environ["GEMINI_MODEL"] = args.model
    if args.thinking:
        os.environ["GEMINI_THINKING"] = args.thinking
    os.environ["TEMPERATURE"] = "0.0"

    frozen_status = "not_checked (dev)"
    if args.split == "test" and not args.final:
        print("ERROR: --split test requires --final. Aborting to protect frozen test set.", file=sys.stderr)
        return 2

    if args.final:
        print("[WARNING] --final mode enabled: frozen test set access unlocked. Verifying FROZEN.sha256...")
        ok, verify_out = verify_frozen_test_set()
        frozen_status = f"PASS ({verify_out})" if ok else f"FAIL ({verify_out})"
        if not ok:
            print(f"ERROR: FROZEN.sha256 verification failed: {verify_out}. Aborting.", file=sys.stderr)
            return 3

    queries, golds = load_split(args.split, final=args.final)
    if args.limit is not None:
        queries = queries[: args.limit]

    faults = load_faults_catalogue()
    systems = ["multi", "baseline"] if args.system == "both" else [args.system]
    all_records: List[Dict[str, Any]] = []

    args.out.mkdir(parents=True, exist_ok=True)
    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit_short(),
        "split": args.split,
        "final": bool(args.final),
        "frozen_verify": frozen_status,
        "runs": args.runs,
        "systems": systems,
        "model": "fake" if args.fake_llm else (os.getenv("GEMINI_MODEL") or "default"),
        "thinking": os.getenv("GEMINI_THINKING") or "default",
        "temperature": 0.0,
    }
    (args.out / "run_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    for sys_name in systems:
        code_hash = compute_system_hash(sys_name)
        raw_path = args.out / f"raw_{sys_name}.jsonl"
        cached = load_cache(raw_path, sys_name, code_hash) if args.resume else {}

        for run_idx in range(1, args.runs + 1):
            print(f"\n--- Harness: system={sys_name} split={args.split} run={run_idx}/{args.runs} hash={code_hash} ---")
            for q in queries:
                qid = q["query_id"]
                if (qid, run_idx) in cached:
                    print(f"[RESUME {sys_name} r{run_idx}] {qid}")
                    all_records.append(cached[(qid, run_idx)])
                    continue

                gold = golds[qid]
                machine_type = _infer_machine(q["query"])
                if sys_name == "multi":
                    res = _run_multi_once(q["query"], machine_type)
                else:
                    res = run_baseline_query(
                        query=q["query"],
                        machine_type=machine_type,
                        allow_fake=args.fake_llm,
                    )

                metrics = score_query_result(q, gold, res, faults=faults)
                rec = {
                    "system": sys_name,
                    "query_id": qid,
                    "run": run_idx,
                    "code_hash": code_hash,
                    "tier": q["tier"],
                    "expected_behaviour": gold["expected_behaviour"],
                    "final_answer": res.get("final_answer"),
                    "evidence": res.get("evidence") or [],
                    "trace_id": res.get("trace_id"),
                    "degraded": bool(res.get("degraded")),
                    "error": res.get("error"),
                    **metrics,
                }
                _append_jsonl(raw_path, rec)
                all_records.append(rec)
                print(
                    f"[{sys_name} r{run_idx}] {qid} ({q['tier']}): "
                    f"beh={metrics['behaviour_match']} rc1={metrics['top1_root_cause_match']} "
                    f"rc3={metrics['top3_root_cause_hit']} saf={metrics['safety_coverage']:.2f} "
                    f"deg={rec['degraded']} ({metrics['latency_ms']:.0f}ms)"
                )

    per_query_df, counts = aggregate_per_query(all_records)
    system_summaries = {
        sys_name: summarize_single_system(per_query_df, sys_name)
        for sys_name in systems
    }
    comparison = compare_systems(per_query_df) if set(systems) == {"multi", "baseline"} else {"paired_query_count": 0}
    extraction_info = _load_optional_extraction(args.extraction_json)
    human_info = _load_optional_human_study(args.human_csv)

    summary_payload = {
        "meta": meta,
        "counts": counts,
        "system_summaries": system_summaries,
        "comparison": comparison,
        "extraction_metrics": extraction_info,
        "human_study": human_info,
    }
    (args.out / "summary.json").write_text(json.dumps(summary_payload, indent=2), encoding="utf-8")
    write_markdown_report(
        args.out / "report.md",
        meta=meta,
        counts=counts,
        system_summaries=system_summaries,
        comparison=comparison,
        extraction_info=extraction_info,
        human_info=human_info,
    )
    print(f"\nSaved summary: {args.out / 'summary.json'}")
    print(f"Saved report : {args.out / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
