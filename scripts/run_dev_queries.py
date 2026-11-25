#!/usr/bin/env python3
"""Run dev queries through DiagnosticAgent and summarize performance and diagnostics.

Executes the 10 offline dev queries from tests/agents/dev_queries.json,
writes detailed structured outputs to logs/dev_run_diagnostic.json,
and prints a tabular console summary.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

# Ensure project root is on sys.path
repo_root = str(Path(__file__).resolve().parent.parent)
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

# Load environment
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
    else:
        load_dotenv()
except ImportError:
    pass

from project.agents.a5_diagnostic import DiagnosticAgent
from project.core.llm import FakeLLM, OpenAILLMClient, get_llm
from project.core.state import create_initial_state

DEV_QUERIES_PATH = Path(repo_root) / "tests" / "agents" / "dev_queries.json"
OUTPUT_LOG_PATH = Path(repo_root) / "logs" / "dev_run_diagnostic.json"


def run_dev_queries(force_fake: bool = False) -> List[Dict[str, Any]]:
    if force_fake:
        os.environ["FACTORY_FAKE_LLM"] = "1"

    # Determine LLM backend
    llm_backend = "unknown"
    llm_client = None
    try:
        llm_client = get_llm(force_fake=force_fake)
        llm_backend = getattr(llm_client, "model_name", "unknown")
    except ValueError as e:
        print(f"[ERROR] Cannot initialize LLM client: {e}", file=sys.stderr)
        print("Use --fake-llm to run with deterministic offline FakeLLM simulation.", file=sys.stderr)
        sys.exit(1)

    print("=" * 105)
    print(" FACTORY MEMORY — DiagnosticAgent Dev Query Evaluation (10 Dev Queries)")
    print(f" LLM Backend: {llm_backend.upper()} ({'SIMULATED / OFFLINE' if llm_backend == 'fake' else 'REAL API'})")
    print("=" * 105)

    if not DEV_QUERIES_PATH.exists():
        raise FileNotFoundError(f"Dev queries file not found: {DEV_QUERIES_PATH}")

    with open(DEV_QUERIES_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
        queries = data.get("dev_queries", [])

    agent = DiagnosticAgent(llm_client=llm_client)

    results = []
    # Print table header
    header = (
        f"{'ID':<7} | {'Machine':<20} | {'Clarify?':<8} | {'Fallback?':<9} | {'Conf':<5} | "
        f"{'Latency':<9} | {'Model':<15} | {'Error':<15} | {'Top Fault Hypothesis / Question':<35}"
    )
    print(header)
    print("-" * 140)

    for q in queries:
        qid = q["id"]
        machine = q.get("machine_type") or "unspecified"
        query_text = q["query"]

        state = create_initial_state(
            query=query_text,
            machine_type=q.get("machine_type"),
            trace_id=f"TR-DEV-{qid}",
        )

        t0 = time.perf_counter()
        out_state = agent.run(state)
        latency_ms = (time.perf_counter() - t0) * 1000.0

        clarify_needed = out_state.get("clarification_needed", False)
        clarify_str = "YES" if clarify_needed else "NO"

        hypotheses = out_state.get("ranked_hypotheses", [])
        top_hyp = hypotheses[0] if hypotheses else None

        # Determine fallback presence
        decision = out_state.get("decision", "")
        err_msg = out_state.get("error") or ""
        if not err_msg and out_state.get("errors"):
            err_msg = "; ".join(out_state["errors"])

        is_fallback = (
            decision == "FALLBACK_DIAGNOSIS"
            or bool(err_msg)
            or any("fallback" in (h.get("fault", "").lower()) for h in hypotheses)
        )
        fallback_str = "YES" if is_fallback else "NO"
        err_display = (err_msg[:12] + "...") if len(err_msg) > 15 else (err_msg or "None")

        conf_str = f"{top_hyp['confidence']:.2f}" if top_hyp else "N/A"
        summary_text = (
            out_state.get("clarification_question")
            if clarify_needed
            else (top_hyp["fault"] if top_hyp else "None")
        )
        if len(summary_text) > 35:
            summary_text = summary_text[:32] + "..."

        m_display = machine if len(machine) <= 20 else machine[:17] + "..."
        model_display = llm_backend if len(llm_backend) <= 15 else llm_backend[:12] + "..."

        tokens = out_state.get("tokens") or getattr(llm_client, "last_token_count", None)

        print(
            f"{qid:<7} | {m_display:<20} | {clarify_str:<8} | {fallback_str:<9} | {conf_str:<5} | "
            f"{latency_ms:>7.1f}ms | {model_display:<15} | {err_display:<15} | {summary_text:<35}"
        )

        results.append({
            "id": qid,
            "query": query_text,
            "machine_type": q.get("machine_type"),
            "clarification_needed": clarify_needed,
            "clarification_question": out_state.get("clarification_question"),
            "ranked_hypotheses": hypotheses,
            "latency_ms": round(latency_ms, 2),
            "llm_backend": llm_backend,
            "is_fallback": is_fallback,
            "error": err_msg or None,
            "tokens": tokens,
        })

    real_answers = sum(1 for r in results if not r["clarification_needed"] and not r["is_fallback"])
    clarifications = sum(1 for r in results if r["clarification_needed"] and not r["is_fallback"])
    fallbacks = sum(1 for r in results if r["is_fallback"])

    print("-" * 140)
    print(f"[*] SUMMARY: Real answers: {real_answers}/10 | Clarifications: {clarifications}/10 | Fallbacks: {fallbacks}/10")
    print(f"[*] Results written to: {OUTPUT_LOG_PATH.resolve()}")
    if llm_backend == "fake":
        print("*** FAKE LLM - CANNED OUTPUT - NOT REAL DIAGNOSIS ***")
    print("=" * 140)

    # Save output json
    OUTPUT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {
                "run_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "llm_backend": llm_backend,
                "is_fake": llm_backend == "fake",
                "total_queries": len(results),
                "real_answers": real_answers,
                "clarifications": clarifications,
                "fallbacks": fallbacks,
                "results": results,
            },
            f,
            indent=2,
        )

    return results


def main():
    parser = argparse.ArgumentParser(description="Evaluate DiagnosticAgent on dev queries")
    parser.add_argument(
        "--fake-llm",
        action="store_true",
        help="Force offline execution using FakeLLM",
    )
    args = parser.parse_args()
    run_dev_queries(force_fake=args.fake_llm)


if __name__ == "__main__":
    main()
