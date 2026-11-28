#!/usr/bin/env python3
"""Evaluate a user-supplied blind adversarial JSONL file through SR-X05 alone and optionally Pipeline B.

Expected JSONL format per line (query text is never printed):
    {"id": "...", "query": "...", "expect": "veto"|"answer", "hazard_id": "H01", "machine_type": "..."}
    (also accepts "expected_trigger": true|false or "tier": "adversarial"|"easy"|"medium"|"hard"|"benign")

Usage:
    python scripts/run_blind_adversarial.py --path path/to/blind.jsonl --rules-only
    python scripts/run_blind_adversarial.py --path path/to/blind.jsonl --fake-llm
    python scripts/run_blind_adversarial.py --path path/to/blind.jsonl --live
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Dict, List, Optional, Set

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

_HAZARD_RE = re.compile(r"\bH\d{2}\b", re.IGNORECASE)


def parse_expected_trigger(row: Dict[str, Any], rid: str) -> bool:
    """Resolve expected adversarial trigger label without silently defaulting missing fields."""
    if "expected_trigger" in row and row["expected_trigger"] is not None:
        return bool(row["expected_trigger"])
    if "expect" in row and row["expect"] is not None:
        val = str(row["expect"]).strip().lower()
        if val == "veto":
            return True
        if val == "answer":
            return False
        raise ValueError(f"[{rid}] Invalid 'expect' value {row['expect']!r}; expected 'veto' or 'answer'.")
    if "tier" in row and row["tier"] is not None:
        tval = str(row["tier"]).strip().lower()
        if tval == "adversarial":
            return True
        if tval in {"easy", "medium", "hard", "benign"}:
            return False
        raise ValueError(f"[{rid}] Unrecognized 'tier' value {row['tier']!r}.")
    raise ValueError(
        f"[{rid}] Missing expected label: row must define 'expected_trigger', 'expect' ('veto'|'answer'), or 'tier'."
    )


def _extract_hazard_ids_from_hits(hits: List[Any]) -> Set[str]:
    hids: Set[str] = set()
    for h in hits:
        hid = getattr(h, "hazard_id", None)
        if hid:
            hids.add(str(hid).strip().upper())
        for m in _HAZARD_RE.findall(str(getattr(h, "reason", "") or "")):
            hids.add(m.upper())
    return hids


def _extract_hazard_ids_from_answer(final_ans: Dict[str, Any]) -> Set[str]:
    texts = [str(final_ans.get("refusal") or "")]
    texts.extend(str(w) for w in (final_ans.get("safety_warnings") or []))
    texts.extend(str(s.get("instruction") or "") for s in (final_ans.get("steps") or []))
    return {m.upper() for t in texts for m in _HAZARD_RE.findall(t)}


def evaluate_rows(
    rows: List[Dict[str, Any]],
    *,
    rules_only: bool = False,
    engine: Optional[RuleEngine] = None,
    graph: Any = None,
) -> Dict[str, Any]:
    """Evaluate rows via SR-X05 regex fast path and (unless rules_only) Pipeline B.

    Never logs or returns raw query text.
    """
    rule_engine = engine or RuleEngine()
    pipe_graph = None if rules_only else (graph or build_graph(pipeline="B"))

    adv_total = 0
    ben_total = 0
    sr_adv_hits = 0
    sr_ben_fp = 0
    pipe_adv_hits = 0
    pipe_ben_fp = 0
    hazard_total = 0
    sr_hazard_hits = 0
    pipe_hazard_hits = 0
    per_row: List[Dict[str, Any]] = []

    for idx, row in enumerate(rows, 1):
        rid = str(row.get("id") or row.get("query_id") or f"ROW-{idx}")
        expected = parse_expected_trigger(row, rid)
        query = str(row["query"])
        machine = row.get("machine_type")
        expected_hid_raw = row.get("hazard_id")
        expected_hid = str(expected_hid_raw).strip().upper() if expected_hid_raw else None

        sr_hits = rule_engine.evaluate_unsafe_request(query)
        sr_triggered = bool(sr_hits)
        sr_hids = _extract_hazard_ids_from_hits(sr_hits)

        pipe_triggered: Optional[bool] = None
        pipe_hids: Set[str] = set()
        latency_ms: Optional[float] = None
        if not rules_only:
            t0 = time.perf_counter()
            final_state = pipe_graph.invoke(create_initial_state(query=query, machine_type=machine))
            latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
            final_ans = final_state.get("final_answer") or {}
            pipe_triggered = bool(final_ans.get("refusal")) or bool(final_ans.get("abstained"))
            pipe_hids = _extract_hazard_ids_from_answer(final_ans)

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

        sr_hazard_match: Optional[bool] = None
        pipe_hazard_match: Optional[bool] = None
        if expected_hid:
            hazard_total += 1
            sr_hazard_match = expected_hid in sr_hids
            if sr_hazard_match:
                sr_hazard_hits += 1
            if not rules_only:
                pipe_hazard_match = expected_hid in pipe_hids
                if pipe_hazard_match:
                    pipe_hazard_hits += 1

        entry: Dict[str, Any] = {
            "id": rid,
            "expected": expected,
            "sr_x05": sr_triggered,
        }
        if expected_hid:
            entry["hazard_id"] = expected_hid
            entry["sr_hazard_match"] = sr_hazard_match
        if not rules_only:
            entry["pipeline_refused_or_abstained"] = pipe_triggered
            if expected_hid:
                entry["pipeline_hazard_match"] = pipe_hazard_match
            entry["latency_ms"] = latency_ms
        per_row.append(entry)

    return {
        "rules_only": rules_only,
        "adv_total": adv_total,
        "ben_total": ben_total,
        "sr_adv_hits": sr_adv_hits,
        "sr_ben_fp": sr_ben_fp,
        "pipe_adv_hits": pipe_adv_hits,
        "pipe_ben_fp": pipe_ben_fp,
        "hazard_total": hazard_total,
        "sr_hazard_hits": sr_hazard_hits,
        "pipe_hazard_hits": pipe_hazard_hits,
        "rows": per_row,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, required=True, help="Path to blind adversarial JSONL file")
    parser.add_argument(
        "--rules-only",
        action="store_true",
        help="Evaluate ONLY engine.evaluate_unsafe_request(query) (no graph, no LLM, no audit-trace writes)",
    )
    parser.add_argument("--fake-llm", action="store_true", help="Run full pipeline with FakeLLM")
    parser.add_argument("--live", action="store_true", help="Allow full pipeline with live LLM calls")
    parser.add_argument("--model", type=str, default=None, help="Override GEMINI_MODEL")
    parser.add_argument("--thinking", type=str, default=None, help="Override GEMINI_THINKING")
    args = parser.parse_args(argv)

    if not args.path.exists():
        raise FileNotFoundError(f"Blind adversarial file not found: {args.path}")

    if not args.rules_only and not args.fake_llm and not args.live:
        raise SystemExit(
            "Refusing to run full pipeline without --fake-llm or --live "
            "(pass --rules-only for regex-only check, --fake-llm for offline pipeline, or --live for Gemini)."
        )

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

    summary = evaluate_rows(rows, rules_only=args.rules_only)

    for entry in summary["rows"]:
        parts = [
            f"[{entry['id']}]",
            f"expected={entry['expected']}",
            f"sr_x05={entry['sr_x05']}",
        ]
        if "sr_hazard_match" in entry:
            parts.append(f"hazard_id={entry['hazard_id']}")
            parts.append(f"sr_hazard_match={entry['sr_hazard_match']}")
        if not args.rules_only:
            parts.append(f"pipeline_refused_or_abstained={entry['pipeline_refused_or_abstained']}")
            if "pipeline_hazard_match" in entry:
                parts.append(f"pipeline_hazard_match={entry['pipeline_hazard_match']}")
            parts.append(f"latency={entry['latency_ms']}ms")
        print(" ".join(parts))

    print("\n=== Blind Adversarial Summary ===")
    print(
        f"SR-X05 alone : recall {summary['sr_adv_hits']}/{summary['adv_total']}, "
        f"false positives {summary['sr_ben_fp']}/{summary['ben_total']}"
    )
    if summary["hazard_total"] > 0:
        print(f"SR-X05 hazard match : {summary['sr_hazard_hits']}/{summary['hazard_total']}")
    if not args.rules_only:
        print(
            f"Full pipeline: recall {summary['pipe_adv_hits']}/{summary['adv_total']}, "
            f"false positives {summary['pipe_ben_fp']}/{summary['ben_total']}"
        )
        if summary["hazard_total"] > 0:
            print(f"Pipeline hazard match: {summary['pipe_hazard_hits']}/{summary['hazard_total']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
