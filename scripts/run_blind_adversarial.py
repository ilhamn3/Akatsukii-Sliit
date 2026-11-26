#!/usr/bin/env python3
"""Evaluate a user-supplied blind adversarial JSONL file through SR-X05 alone and the full Pipeline B.

Expected JSONL format per line:
    {"id": "...", "query": "...", "expected_trigger": true|false, "machine_type": "..."} (machine_type optional)

Usage:
    python scripts/run_blind_adversarial.py --path tests/fixtures/adversarial_blind.jsonl
    python scripts/run_blind_adversarial.py --path path/to/blind.jsonl --fake-llm
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

from project.core.registry import build_graph
from project.core.safety_rules.rule_engine import RuleEngine
from project.core.state import create_initial_state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, required=True, help="Path to blind adversarial JSONL file")
    parser.add_argument("--fake-llm", action="store_true", help="Run full pipeline with FakeLLM")
    parser.add_argument("--model", type=str, default=None, help="Override GEMINI_MODEL")
    parser.add_argument("--thinking", type=str, default=None, help="Override GEMINI_THINKING")
    args = parser.parse_args()

    if not args.path.exists():
        raise FileNotFoundError(f"Blind adversarial file not found: {args.path}")

    if args.fake_llm:
        os.environ["FACTORY_FAKE_LLM"] = "1"
    if args.model:
        os.environ["GEMINI_MODEL"] = args.model
    if args.thinking:
        os.environ["GEMINI_THINKING"] = args.thinking

    rows: List[Dict[str, Any]] = [
        json.loads(line)
        for line in args.path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    engine = RuleEngine()
    graph = build_graph(pipeline="B")

    adv_total = 0
    ben_total = 0
    sr_adv_hits = 0
    sr_ben_fp = 0
    pipe_adv_hits = 0
    pipe_ben_fp = 0

    for idx, row in enumerate(rows, 1):
        rid = row.get("id") or f"ROW-{idx}"
        query = row["query"]
        expected = bool(row.get("expected_trigger", row.get("tier") == "adversarial"))
        machine = row.get("machine_type")

        sr_triggered = bool(engine.evaluate_unsafe_request(query))
        t0 = time.perf_counter()
        final_state = graph.invoke(create_initial_state(query=query, machine_type=machine))
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        final_ans = final_state.get("final_answer") or {}
        pipe_triggered = bool(final_ans.get("refusal")) or bool(final_ans.get("abstained"))

        if expected:
            adv_total += 1
            if sr_triggered:
                sr_adv_hits += 1
            if pipe_triggered:
                pipe_adv_hits += 1
        else:
            ben_total += 1
            if sr_triggered:
                sr_ben_fp += 1
            if pipe_triggered:
                pipe_ben_fp += 1

        print(
            f"[{rid}] expected={expected} sr_x05={sr_triggered} "
            f"pipeline_refused_or_abstained={pipe_triggered} latency={latency_ms}ms"
        )

    print("\n=== Blind Adversarial Summary ===")
    print(f"SR-X05 alone : recall {sr_adv_hits}/{adv_total}, false positives {sr_ben_fp}/{ben_total}")
    print(f"Full pipeline: recall {pipe_adv_hits}/{adv_total}, false positives {pipe_ben_fp}/{ben_total}")


if __name__ == "__main__":
    main()
