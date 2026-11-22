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
        f"{'ID':<7} | {'Machine':<25} | {'Clarify?':<8} | {'Conf':<5} | "
        f"{'Latency':<9} | {'Top Fault Hypothesis / Question':<40}"
    )
    print(header)
    print("-" * 105)

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

        conf_str = f"{top_hyp['confidence']:.2f}" if top_hyp else "N/A"
        summary_text = (
            out_state.get("clarification_question")
            if clarify_needed
            else (top_hyp["fault"] if top_hyp else "None")
        )
        if len(summary_text) > 40:
            summary_text = summary_text[:37] + "..."

        m_display = machine if len(machine) <= 25 else machine[:22] + "..."

        print(
            f"{qid:<7} | {m_display:<25} | {clarify_str:<8} | {conf_str:<5} | "
            f"{latency_ms:>7.1f}ms | {summary_text:<40}"
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
            "tokens_estimated": len(query_text.split()) + 45,  # Approx estimate
        })

    # Save output json
    OUTPUT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {
                "run_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "llm_backend": llm_backend,
                "is_fake": llm_backend == "fake",
                "total_queries": len(results),
                "clarifications_triggered": sum(1 for r in results if r["clarification_needed"]),
                "results": results,
            },
            f,
            indent=2,
        )

    print("-" * 105)
    print(f"[*] Results written to: {OUTPUT_LOG_PATH.resolve()}")
    if llm_backend == "fake":
        print("*** FAKE LLM - CANNED OUTPUT - NOT REAL DIAGNOSIS ***")
    print("=" * 105)

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
