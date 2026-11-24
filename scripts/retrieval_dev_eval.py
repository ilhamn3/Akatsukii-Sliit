"""K6 development retrieval check on catalogue-derived SYNTHETIC verified episodes.

Builds one synthetic verified episode per fault in the team fault catalogue
(symptoms + alarm codes, root cause, valid fixes, wrong fixes as failed attempts,
hazard controls) and writes them through Agent 4 into a dedicated store. It then
queries each fault's differently worded `lay_description` and reports hit@1,
hit@3 and MRR for vector-only, graph-only and hybrid scoring, within the machine
and across all machines.

The retrieval weights are the config defaults, fixed before this check, and
nothing is tuned against it. This is a self-consistency smoke check on synthetic
data, NOT a benchmark: the frozen test set is never used. The synthetic episodes
name a loader, not a human reviewer.

    python -m scripts.retrieval_dev_eval --local-files-only
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys

from project.agents.a4_graph import FAULTS, HAZARDS, GraphAgent
from project.agents.a6_retrieval import HybridRetriever
from project.store.models import VerifiedEpisode

ROOT = Path(__file__).resolve().parents[1]
DEV_QUERIES = ROOT / "tests" / "agents" / "dev_queries.json"


def catalogue_episodes(faults: list[dict], hazards: list[dict]) -> list[VerifiedEpisode]:
    controls = {h["hazard_id"]: h["required_control"] for h in hazards}
    episodes = []
    for fault in faults:
        fid = fault["fault_id"]
        symptoms = list(fault["symptoms"]) + [f"alarm {code}" for code in fault.get("error_codes", [])]
        episodes.append(VerifiedEpisode.model_validate({
            "episode_id": f"SYN-CAT-{fid}", "status": "verified",
            "verified_by": "synthetic-catalogue-loader (not a human review)",
            "verified_at": "2026-11-25T00:00:00+00:00",
            "outcome": "Fault cleared by the catalogue's valid fix (synthetic)",
            "record": {
                "machine_type": fault["machine_type"], "machine_id": None, "symptoms": symptoms,
                "steps": [{"step_number": i + 1, "description": step, "timestamp": None,
                           "evidence_id": f"SYN-{fid}-S{i + 1}"} for i, step in enumerate(fault["valid_fixes"])],
                "tools": fault["tools"], "parts": fault["parts"], "root_cause": fault["root_cause"],
                "failed_attempts": [{"action": w["action"], "outcome": "Fault persisted (synthetic wrong fix)",
                                     "why_failed": w["why_it_fails"]} for w in fault["wrong_fixes"]],
                "safety_warnings": [controls[h] for h in fault["hazards"]],
                "confidence": 1.0, "evidence_id": f"EV-SYN-CAT-{fid}", "source": "synthetic_catalogue",
            },
            "provenance": {
                "capture_id": f"CAP-SYN-CAT-{fid}", "source_type": "synthetic_catalogue",
                "source_path": "project/data/fault_catalogue/faults.json",
                "source_sha256": hashlib.sha256(json.dumps(fault, sort_keys=True).encode()).hexdigest(),
                "evidence_spans": [{"field_path": "symptoms.0", "evidence_id": f"SYN-{fid}-S0",
                                    "quote": symptoms[0], "start_seconds": 0, "end_seconds": 0}],
            },
        }))
    return episodes


def evaluate(store, faults: list[dict], *, trace_id="TR-RETRIEVAL-DEV") -> dict:
    modes = {"vector_only": HybridRetriever(store, graph_weight=0.0, asset_weight=0.0),
             "graph_only": HybridRetriever(store, use_vector=False),
             "hybrid": HybridRetriever(store)}
    report = {"queries": len(faults), "weights": {"graph": modes["hybrid"].graph_weight,
                                                 "asset": modes["hybrid"].asset_weight}, "results": {}}
    for scope in ("within_machine", "all_machines"):
        for name, retriever in modes.items():
            ranks = []
            for fault in faults:
                hits = retriever.retrieve(fault["lay_description"],
                                          fault["machine_type"] if scope == "within_machine" else None,
                                          k=len(faults), trace_id=trace_id)
                ids = [h.episode_id for h in hits]
                gold = f"SYN-CAT-{fault['fault_id']}"
                ranks.append(ids.index(gold) + 1 if gold in ids else None)
            n = len(ranks)
            report["results"][f"{scope}/{name}"] = {
                "hit@1": round(sum(r == 1 for r in ranks) / n, 4),
                "hit@3": round(sum(r is not None and r <= 3 for r in ranks) / n, 4),
                "mrr": round(sum(1 / r for r in ranks if r) / n, 4),
                "misses_at_3": [f["fault_id"] for f, r in zip(faults, ranks) if r is None or r > 3],
            }
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--local-files-only", action="store_true", help="Use cached MiniLM weights only")
    parser.add_argument("--output", default=str(ROOT / "logs" / "retrieval_dev_eval.json"))
    args = parser.parse_args(argv)
    try:
        from project.store.embeddings import MiniLMEmbedder, storage_root
        from project.store.knowledge_store import KnowledgeStore
        db = storage_root() / "retrieval-dev" / "repairs.sqlite3"
        db.unlink(missing_ok=True)  # Dedicated synthetic store, rebuilt each run.
        store = KnowledgeStore(db, embedder=MiniLMEmbedder(local_files_only=args.local_files_only))
        faults = json.loads(FAULTS.read_text(encoding="utf-8"))
        writer = GraphAgent(store)
        for episode in catalogue_episodes(faults, json.loads(HAZARDS.read_text(encoding="utf-8"))):
            writer.put_verified(episode, trace_id="TR-RETRIEVAL-DEV-LOAD")
        report = {"synthetic_data_only": True, "database": str(db),
                  "embedding_model": store.embedder.model_id, **evaluate(store, faults)}
        hybrid = HybridRetriever(store)
        report["dev_queries_top1"] = []  # Ilham's DEV split has no episode gold; inspection only.
        for query in json.loads(DEV_QUERIES.read_text(encoding="utf-8"))["dev_queries"]:
            hits = hybrid.retrieve(query["query"], query["machine_type"], k=1, trace_id="TR-RETRIEVAL-DEV-Q")
            report["dev_queries_top1"].append({
                "id": query["id"], "machine_type": query["machine_type"],
                "top": hits[0].episode_id if hits else None,
                "score": hits[0].similarity_score if hits else None,
                "cross_machine": hits[0].metadata["retrieval"]["cross_machine"] if hits else None})
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({k: v for k, v in report.items() if k != "dev_queries_top1"}, indent=2))
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
