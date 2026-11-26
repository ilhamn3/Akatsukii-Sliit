#!/usr/bin/env python3
"""Load synthetic verified repair episodes into the KnowledgeStore (idempotent).

Loads `project/data/expanded/<size>/episodes.jsonl` if present; otherwise falls back
to `project/data/episodes/ground_truth.jsonl` for n100/n400. Uses `GraphAgent.put_verified`
so machine, asset, and hazard entity links are resolved for A6 graph retrieval.

Usage:
    python scripts/load_expanded_store.py --size n400
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from project.agents.a4_graph import GraphAgent
from project.store.embeddings import MiniLMEmbedder, storage_root
from project.store.knowledge_store import KnowledgeStore
from project.store.models import VerifiedEpisode

SIZE_LIMITS = {"n100": 100, "n400": 400, "n2000": 2000}


def _to_verified_episode(row: Dict[str, Any], source_path: str) -> VerifiedEpisode:
    """Validate a VerifiedEpisode row or wrap a ground_truth.jsonl row into one."""
    if row.get("status") == "verified" and "provenance" in row:
        return VerifiedEpisode.model_validate(row)

    record = dict(row["record"])
    ep_id = row["episode_id"]
    symptoms = record.get("symptoms") or ["unspecified symptom"]
    first_step_eid = (record.get("steps") or [{}])[0].get("evidence_id") or f"{ep_id}-S0"
    sha = hashlib.sha256(json.dumps(row, sort_keys=True).encode("utf-8")).hexdigest()

    return VerifiedEpisode.model_validate({
        "episode_id": ep_id,
        "status": "verified",
        "verified_by": "expanded-store-loader",
        "verified_at": "2026-11-25T00:00:00+00:00",
        "outcome": f"Fault cleared; {record.get('root_cause', 'repair completed')}.",
        "record": record,
        "provenance": {
            "capture_id": f"CAP-{ep_id}",
            "source_path": source_path,
            "source_sha256": sha,
            "source_type": record.get("source", "synthetic_episode"),
            "evidence_spans": [
                {
                    "field_path": "symptoms.0",
                    "evidence_id": first_step_eid,
                    "quote": symptoms[0],
                    "start_seconds": 0,
                    "end_seconds": 0,
                }
            ],
        },
    })


def load_store(size: str, db_path: Path | None = None, local_files_only: bool = False) -> Dict[str, int]:
    """Load episodes for `size` into KnowledgeStore idempotently and return counts."""
    expanded_path = ROOT / "project" / "data" / "expanded" / size / "episodes.jsonl"
    fallback_path = ROOT / "project" / "data" / "episodes" / "ground_truth.jsonl"

    if expanded_path.exists():
        src_path = expanded_path
        limit = None
    elif fallback_path.exists() and size in ("n100", "n400"):
        src_path = fallback_path
        limit = SIZE_LIMITS[size]
    else:
        raise FileNotFoundError(
            f"Expanded dataset not found at {expanded_path} (and no fallback for size={size})."
        )

    store = KnowledgeStore(
        db_path or (storage_root() / "store" / "repairs.sqlite3"),
        embedder=MiniLMEmbedder(local_files_only=local_files_only),
    )
    writer = GraphAgent(store)

    rows: List[Dict[str, Any]] = []
    with open(src_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
            if limit is not None and len(rows) >= limit:
                break

    existing_before = len(store.snapshot(trace_id="TR-LOAD-COUNT-BEFORE")[0])
    loaded = 0
    for row in rows:
        ep = _to_verified_episode(row, str(src_path.relative_to(ROOT)).replace("\\", "/"))
        writer.put_verified(ep, trace_id="TR-LOAD-EXPANDED")
        loaded += 1

    total_after = len(store.snapshot(trace_id="TR-LOAD-COUNT-AFTER")[0])
    return {
        "source_rows": len(rows),
        "processed": loaded,
        "existing_before": existing_before,
        "total_in_store": total_after,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", choices=["n100", "n400", "n2000"], default="n400")
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--local-files-only", action="store_true")
    args = parser.parse_args()

    counts = load_store(args.size, db_path=args.db, local_files_only=args.local_files_only)
    print(json.dumps({"size": args.size, **counts}, indent=2))


if __name__ == "__main__":
    main()
