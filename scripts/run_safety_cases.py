"""Evaluation runner for safety verification agent on adversarial safety test set.

Runs test cases from tests/agents/adversarial_safety.json, compares expected
vs actual verdicts, and reports hazard recall, unflagged hazard count, and latency.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv()

from project.agents.a7_safety import SafetyAgent
from project.core.llm import FakeLLM, get_llm
from project.core.state import create_initial_state

ADVERSARIAL_CASES_PATH = PROJECT_ROOT / "tests" / "agents" / "adversarial_safety.json"


def run_evaluation(use_real_llm: bool = False, model_override: str = None, thinking_override: str = None):
    if not ADVERSARIAL_CASES_PATH.exists():
        print(f"[-] Cases file not found: {ADVERSARIAL_CASES_PATH}")
        sys.exit(1)

    with open(ADVERSARIAL_CASES_PATH, "r", encoding="utf-8") as f:
        cases = json.load(f)

    if use_real_llm:
        if "FACTORY_FAKE_LLM" in os.environ:
            del os.environ["FACTORY_FAKE_LLM"]
        if model_override:
            os.environ["GEMINI_MODEL_SAFETY"] = model_override
        if thinking_override:
            os.environ["GEMINI_THINKING_SAFETY"] = thinking_override
        llm = get_llm(model=model_override, thinking=thinking_override)
    else:
        os.environ["FACTORY_FAKE_LLM"] = "1"
        llm = FakeLLM()

    agent = SafetyAgent(llm_client=llm)
    backend_name = getattr(llm, "model_name", "fake")

    print("\n" + "=" * 115)
    print(" FACTORY MEMORY — Safety Verification Agent Evaluation (Adversarial Safety Cases)")
    print(f" LLM Backend: {backend_name.upper()} | Cases: {len(cases)}")
    print("=" * 115)
    print(f"{'Case ID':<13} | {'Expected':<8} | {'Actual':<8} | {'Match?':<6} | {'Rules Evaluated':<20} | {'Latency':<9} | {'Description':<38}")
    print("-" * 115)

    results = []
    total_cases = len(cases)
    matches = 0
    unsafe_cases = 0
    unsafe_flagged = 0
    unflagged_hazards = 0
    latencies = []

    for c in cases:
        cid = c.get("case_id", "DEV-CASE")
        desc = c.get("description", "")
        expected = c.get("expected_verdict", "approve").lower()
        is_unsafe = expected in ("veto", "amend")
        if is_unsafe:
            unsafe_cases += 1

        state = create_initial_state(
            query=c.get("query", desc),
            machine_type=c.get("machine_type", "unspecified"),
            trace_id=f"TR-{cid}",
            session_id=f"SES-{cid}",
        )
        state["candidate_steps"] = c.get("steps", [])
        state["hazard_ids"] = c.get("hazard_ids", [])
        state["selected_hypothesis"] = {
            "fault": c.get("query", desc),
            "confidence": 0.85,
            "hazards": c.get("hazard_ids", []),
        }

        t0 = time.perf_counter()
        update = agent.process(state)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        latencies.append(elapsed_ms)

        verdict_dict = update.get("safety_verdict") or {}
        actual = verdict_dict.get("verdict", "unknown").lower()
        rule_ids = verdict_dict.get("rule_ids", [])
        rules_str = ",".join(rule_ids[:3]) if rule_ids else "None"

        match = (actual == expected)
        if match:
            matches += 1

        if is_unsafe:
            if actual in ("veto", "amend"):
                unsafe_flagged += 1
            else:
                unflagged_hazards += 1

        match_str = "YES" if match else "NO *"
        desc_trunc = desc[:36] + ".." if len(desc) > 38 else desc
        print(f"{cid:<13} | {expected.upper():<8} | {actual.upper():<8} | {match_str:<6} | {rules_str:<20} | {elapsed_ms:>7.1f}ms | {desc_trunc:<38}")

        results.append({
            "case_id": cid,
            "description": desc,
            "expected": expected,
            "actual": actual,
            "match": match,
            "latency_ms": elapsed_ms,
            "rules": rule_ids,
            "reason": verdict_dict.get("reason", ""),
        })

    hazard_recall = (unsafe_flagged / unsafe_cases) * 100.0 if unsafe_cases else 100.0
    accuracy = (matches / total_cases) * 100.0
    avg_latency = sum(latencies) / len(latencies) if latencies else 0.0

    print("-" * 115)
    print(f"[*] Total Cases:         {total_cases}")
    print(f"[*] Overall Match Rate:  {matches}/{total_cases} ({accuracy:.1f}%)")
    print(f"[*] Unsafe Cases:        {unsafe_cases}")
    print(f"[*] Hazard Recall:       {unsafe_flagged}/{unsafe_cases} ({hazard_recall:.1f}%)")
    print(f"[*] Unflagged Hazards:   {unflagged_hazards} (unsafe cases incorrectly approved)")
    print(f"[*] Average Latency:     {avg_latency:.1f}ms per evaluation")
    print("=" * 115 + "\n")

    out_file = PROJECT_ROOT / "logs" / "safety_eval_results.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump({
            "backend": backend_name,
            "total_cases": total_cases,
            "accuracy": accuracy,
            "hazard_recall": hazard_recall,
            "unflagged_hazards": unflagged_hazards,
            "results": results,
        }, f, indent=2)

    return {
        "accuracy": accuracy,
        "hazard_recall": hazard_recall,
        "unflagged_hazards": unflagged_hazards,
        "avg_latency": avg_latency,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run safety verification evaluation on adversarial dataset.")
    parser.add_argument("--real-llm", action="store_true", help="Run with real configured LLM instead of FakeLLM")
    parser.add_argument("--model", type=str, default=None, help="Optional model override (e.g. gemini-3.5-flash)")
    parser.add_argument("--thinking", type=str, default=None, help="Optional thinking override (e.g. minimal)")
    args = parser.parse_args()

    run_evaluation(use_real_llm=args.real_llm, model_override=args.model, thinking_override=args.thinking)
