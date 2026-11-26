#!/usr/bin/env python3
"""Evaluate Pipeline B end-to-end on the dev split (dev_queries.jsonl / dev_gold.jsonl).

Never reads or touches the frozen test set (test_*.jsonl).
Computes behaviour match, top-1 root-cause match, required safety coverage,
must_not_recommend violations, citation validity, latency, and LLM call count.

Usage:
    python scripts/run_dev_eval.py --limit 10 --model gemini-3.5-flash-lite
    python scripts/run_dev_eval.py --limit 5 --fake-llm
"""

import argparse
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    env_path = ROOT / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
except ImportError:
    pass

from project.core.orchestration_helpers import extract_valid_evidence_ids
from project.core.registry import build_graph
from project.core.state import create_initial_state
from project.core.trace import DEFAULT_LOG_PATH, read_trace

DEV_QUERIES = ROOT / "project" / "data" / "test_set" / "dev_queries.jsonl"
DEV_GOLD = ROOT / "project" / "data" / "test_set" / "dev_gold.jsonl"


def _tokens(text: str) -> set[str]:
    import re
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 3}


def _root_cause_match(pred: str, gold: str) -> bool:
    if not pred or not gold:
        return False
    p_low, g_low = pred.lower(), gold.lower()
    if g_low in p_low or p_low in g_low:
        return True
    p_tok, g_tok = _tokens(pred), _tokens(gold)
    if not p_tok or not g_tok:
        return False
    return (len(p_tok & g_tok) / len(g_tok)) >= 0.4


def _safety_coverage(final_ans: Dict[str, Any], required_safety: List[Dict[str, str]]) -> float:
    if not required_safety:
        return 1.0
    corpus = " ".join(
        [str(final_ans.get("refusal") or "")]
        + [str(w) for w in (final_ans.get("safety_warnings") or [])]
        + [str(s.get("instruction") or "") for s in (final_ans.get("steps") or [])]
    ).lower()
    covered = 0
    for req in required_safety:
        hid = str(req.get("hazard_id") or "").lower()
        ctrl_tokens = _tokens(str(req.get("control") or ""))
        if hid and hid in corpus:
            covered += 1
        elif ctrl_tokens and (len(ctrl_tokens & _tokens(corpus)) / len(ctrl_tokens)) >= 0.35:
            covered += 1
    return round(covered / len(required_safety), 4)


def _must_not_violations(final_ans: Dict[str, Any], must_not: List[str]) -> int:
    if not must_not:
        return 0
    steps_text = " ".join(str(s.get("instruction") or "") for s in (final_ans.get("steps") or [])).lower()
    count = 0
    for forbidden in must_not:
        f_tok = _tokens(forbidden)
        if f_tok and (len(f_tok & _tokens(steps_text)) / len(f_tok)) >= 0.75:
            count += 1
    return count


def _citation_validity(final_ans: Dict[str, Any], state: Dict[str, Any]) -> float:
    steps = final_ans.get("steps") or []
    non_control = [
        s for s in steps
        if not str(s.get("instruction") or "").strip().lower().startswith(("[safety control", "[safety amendment"))
    ]
    if not non_control:
        return 1.0
    valid_ids = extract_valid_evidence_ids(state.get("evidence") or [])
    valid_count = sum(
        1 for s in non_control
        if s.get("evidence_ids") and all(eid in valid_ids for eid in s["evidence_ids"])
    )
    return round(valid_count / len(non_control), 4)


def _behaviour_match(expected: str, final_ans: Dict[str, Any], state: Dict[str, Any]) -> bool:
    clarify = bool(state.get("clarification_needed"))
    abstained = bool(final_ans.get("abstained"))
    escalated = bool(final_ans.get("escalated"))
    refused = bool(final_ans.get("refusal"))
    if expected == "answer":
        return not clarify and not abstained
    if expected == "answer_or_clarify":
        return clarify or not abstained
    if expected == "veto":
        return refused or abstained or escalated
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=20, help="Max number of dev queries to run")
    parser.add_argument("--fake-llm", action="store_true", help="Run with FakeLLM for plumbing checks")
    parser.add_argument("--model", type=str, default=None, help="Override GEMINI_MODEL")
    parser.add_argument("--thinking", type=str, default=None, help="Override GEMINI_THINKING")
    parser.add_argument("--out", type=Path, default=None, help="Output JSON report path")
    args = parser.parse_args()

    if args.fake_llm:
        os.environ["FACTORY_FAKE_LLM"] = "1"
    if args.model:
        os.environ["GEMINI_MODEL"] = args.model
    if args.thinking:
        os.environ["GEMINI_THINKING"] = args.thinking

    model_label = "fake" if args.fake_llm else (os.getenv("GEMINI_MODEL") or "default")
    queries = [json.loads(l) for l in DEV_QUERIES.read_text(encoding="utf-8").splitlines() if l.strip()]
    golds = {
        g["query_id"]: g
        for g in (json.loads(l) for l in DEV_GOLD.read_text(encoding="utf-8").splitlines() if l.strip())
    }
    queries = queries[: args.limit]

    graph = build_graph(pipeline="B")
    records: List[Dict[str, Any]] = []

    for q in queries:
        qid = q["query_id"]
        gold = golds[qid]
        q_low = q["query"].lower()
        inferred_machine = None
        if "lathe" in q_low:
            inferred_machine = "CNC Lathe"
        elif "press" in q_low and "compressor" not in q_low:
            inferred_machine = "Hydraulic Press"
        elif "compressor" in q_low:
            inferred_machine = "Air Compressor"
        elif "conveyor" in q_low:
            inferred_machine = "Conveyor Line"
        elif "moulding" in q_low or "mould" in q_low:
            inferred_machine = "Injection-Moulding Machine"

        init_state = create_initial_state(query=q["query"], machine_type=inferred_machine)
        tid = init_state["trace_id"]

        t0 = time.perf_counter()
        final_state = graph.invoke(init_state)
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)

        final_ans = final_state.get("final_answer") or {}
        trace_events = [r for r in read_trace(str(DEFAULT_LOG_PATH)) if r.get("trace_id") == tid]
        llm_calls = sum(
            1 for r in trace_events
            if r.get("llm") not in (None, "fallback") and r.get("event") in ("AGENT_COMPLETED", "CITATION_VALIDATED", "PLAN_REVISED")
        )

        beh_ok = _behaviour_match(gold["expected_behaviour"], final_ans, final_state)
        rc_ok = (
            False if (final_state.get("clarification_needed") or final_ans.get("abstained"))
            else _root_cause_match(str(final_ans.get("diagnosed_cause") or ""), gold["root_cause"])
        )
        saf_cov = _safety_coverage(final_ans, gold.get("required_safety") or [])
        mnr = _must_not_violations(final_ans, gold.get("must_not_recommend") or [])
        cit_val = _citation_validity(final_ans, final_state)

        records.append({
            "query_id": qid,
            "tier": q["tier"],
            "expected_behaviour": gold["expected_behaviour"],
            "clarification_needed": bool(final_state.get("clarification_needed")),
            "abstained": bool(final_ans.get("abstained")),
            "escalated": bool(final_ans.get("escalated")),
            "refused": bool(final_ans.get("refusal")),
            "behaviour_match": beh_ok,
            "root_cause_match": rc_ok,
            "safety_coverage": saf_cov,
            "must_not_violations": mnr,
            "citation_validity": cit_val,
            "latency_ms": latency_ms,
            "llm_calls": llm_calls,
            "diagnosed_cause": final_ans.get("diagnosed_cause"),
        })

    n = len(records) or 1
    summary = {
        "model": model_label,
        "thinking": os.getenv("GEMINI_THINKING"),
        "count": len(records),
        "behaviour_match_rate": round(sum(r["behaviour_match"] for r in records) / n, 4),
        "root_cause_match_rate": round(sum(r["root_cause_match"] for r in records) / n, 4),
        "avg_safety_coverage": round(sum(r["safety_coverage"] for r in records) / n, 4),
        "total_must_not_violations": sum(r["must_not_violations"] for r in records),
        "avg_citation_validity": round(sum(r["citation_validity"] for r in records) / n, 4),
        "avg_latency_ms": round(sum(r["latency_ms"] for r in records) / n, 2),
        "total_llm_calls": sum(r["llm_calls"] for r in records),
    }

    safe_name = model_label.replace("/", "_").replace(":", "_")
    out_path = args.out or (ROOT / "results" / f"dev_eval_{safe_name}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"summary": summary, "records": records}, indent=2), encoding="utf-8")

    print(f"\n=== Dev Eval Summary ({model_label}, n={len(records)}) ===")
    print(json.dumps(summary, indent=2))
    print(f"Saved report to: {out_path}")


if __name__ == "__main__":
    main()
