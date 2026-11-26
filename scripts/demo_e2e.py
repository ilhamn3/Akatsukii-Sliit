#!/usr/bin/env python3
"""End-to-end Factory Memory demo: Pipeline A (Capture -> Extract -> Validate -> Approve -> A4 Store)
followed by Pipeline B (`project.core.api.run_query`) retrieving and citing the newly approved episode.

Usage:
    python scripts/demo_e2e.py
    python scripts/demo_e2e.py --fake-llm
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import uuid

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode-index", type=int, default=0, help="Index in narratives.jsonl (default: 0 = EP-0001)")
    parser.add_argument("--model", type=str, default=None, help="Override GEMINI_MODEL")
    parser.add_argument("--thinking", type=str, default="minimal", help="Override GEMINI_THINKING")
    parser.add_argument("--fake-llm", action="store_true", help="Use FakeLLM for offline plumbing check")
    args = parser.parse_args()

    if args.model:
        os.environ["GEMINI_MODEL"] = args.model
    if args.thinking:
        os.environ["GEMINI_THINKING"] = args.thinking
    if args.fake_llm:
        os.environ["FACTORY_FAKE_LLM"] = "1"
        print("*** FAKE LLM - CANNED OUTPUT - NOT REAL EXTRACTION/DIAGNOSIS ***")

    # Use an isolated demo storage directory so repeated runs are clean and reproducible
    demo_storage = ROOT / ".factory_memory" / "demo_e2e"
    if demo_storage.exists():
        shutil.rmtree(demo_storage)
    demo_storage.mkdir(parents=True, exist_ok=True)
    os.environ["FACTORY_MEMORY_STORAGE"] = str(demo_storage)

    from project.agents.a2_extraction import ExtractionAgent
    from project.agents.a3_validation import ReviewBlocked, ValidationAgent
    from project.core.api import approve_review_item, clear_api_caches, run_query
    from project.core.capture_schemas import CaptureResult, TranscriptSegment
    from project.core.state import create_initial_state
    from project.store.knowledge_store import KnowledgeStore
    from project.store.review_queue import ReviewQueue

    clear_api_caches()
    narratives_path = ROOT / "project" / "data" / "episodes" / "narratives.jsonl"
    rows = [json.loads(l) for l in narratives_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    row = rows[args.episode_index]

    ep_id = row["episode_id"]
    narrative_text = row["narrative"]
    machine_type = row["machine_type"]
    src_file = demo_storage / f"{ep_id}_narrative.txt"
    src_file.write_text(narrative_text, encoding="utf-8")
    sha = hashlib.sha256(narrative_text.encode("utf-8")).hexdigest()

    # Stage 1: Capture envelope (synthetic narrative -> CaptureResult)
    t1 = f"TR-E2E-CAP-{uuid.uuid4().hex[:6].upper()}"
    capture = CaptureResult(
        capture_id=f"CAP-{ep_id}",
        source_path=str(src_file),
        source_sha256=sha,
        media_type="audio",
        language="en",
        text=narrative_text,
        review_required=False,
        segments=[
            TranscriptSegment(
                evidence_id=f"EV-{ep_id}-SEG1",
                start_seconds=0.0,
                end_seconds=25.0,
                text=narrative_text,
            )
        ],
    )
    print(f"[Stage 1: Capture] episode={ep_id} trace_id={t1}")

    # Stage 2: Extraction (A2)
    t2 = f"TR-E2E-EXT-{uuid.uuid4().hex[:6].upper()}"
    st2 = create_initial_state("", machine_type=machine_type, trace_id=t2)
    st2["capture_result"] = capture.model_dump(mode="json")
    ext_agent = ExtractionAgent()
    st2 = ext_agent.run(st2)
    if not st2.get("draft_record"):
        raise RuntimeError(f"ExtractionAgent failed: {st2.get('errors')}")
    print(f"[Stage 2: Extraction A2] trace_id={t2} model={ext_agent.llm_name}")

    # Stage 3: Validation & Review Queue (A3)
    t3 = f"TR-E2E-VAL-{uuid.uuid4().hex[:6].upper()}"
    store = KnowledgeStore()
    queue = ReviewQueue(store=store)
    st3 = create_initial_state("", machine_type=machine_type, trace_id=t3)
    st3["draft_record"] = st2["draft_record"]
    val_agent = ValidationAgent(queue=queue, store=store)
    st3 = val_agent.run(st3)
    review_item_id = st3.get("review_item_id")
    if not review_item_id:
        raise RuntimeError(f"ValidationAgent failed: {st3.get('errors')}")
    print(f"[Stage 3: Validation A3] trace_id={t3} review_item_id={review_item_id}")

    # Stage 4: Human Approval as "demo-reviewer" -> A4 GraphAgent -> KnowledgeStore
    t4 = f"TR-E2E-APP-{uuid.uuid4().hex[:6].upper()}"
    try:
        approved = approve_review_item(
            review_item_id,
            reviewer="demo-reviewer",
            note="Approved in demo_e2e.py",
            trace_id=t4,
        )
    except ReviewBlocked as blk:
        # Provide minimal reviewer corrections only for missing required fields in extracted draft
        corrections = {}
        for issue in blk.issues:
            if issue.field_path == "safety_warnings":
                corrections["safety_warnings"] = ["Stop and lock out machine before maintenance."]
            elif issue.field_path == "outcome":
                corrections["outcome"] = "Fault resolved; returned to production."
            elif issue.field_path == "machine_type":
                corrections["machine_type"] = machine_type
        approved = approve_review_item(
            review_item_id,
            reviewer="demo-reviewer",
            corrections=corrections or None,
            note="Approved with reviewer corrections in demo_e2e.py",
            trace_id=t4,
        )
    print(f"[Stage 4: Approval & A4 Store Write] trace_id={t4} episode_id={approved['episode_id']} verified_by={approved['verified_by']}")

    # Stage 5: Pipeline B query via public api.run_query()
    symptoms = approved["record"].get("symptoms") or ["chain jumps on the sprocket"]
    query = f"Need help with {machine_type}: {symptoms[0]}"
    res = run_query(query=query, machine_type=machine_type, machine_id=row.get("machine_id"))
    print(f"[Stage 5: Pipeline B api.run_query] trace_id={res['trace_id']} error={res['error']}")
    print("  • Retrieved Evidence IDs:", [e["episode_id"] for e in res["evidence"]])
    if res["final_answer"]:
        print("  • Diagnosed Cause:", res["final_answer"].get("diagnosed_cause"))
        for st in res["final_answer"].get("steps", []):
            print(f"    {st['step_number']}. {st['instruction']} [Citations: {st['evidence_ids']}]")

    if args.fake_llm:
        print("*** FAKE LLM - CANNED OUTPUT - NOT REAL EXTRACTION/DIAGNOSIS ***")


if __name__ == "__main__":
    main()
