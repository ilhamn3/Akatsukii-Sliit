"""K6 retrieval evaluation on the DEV split only (never the frozen test split).

Corpus: an "oracle store" of the 400 synthetic episodes' ground-truth RepairRecords
(project/data/episodes/ground_truth.jsonl), written through Agent 4 into a
dedicated store. This measures retrieval in isolation from extraction errors.
It is for this component check only and is never used by the benchmark system,
because that file is scoring-only data for agents.

Queries: project/data/test_set/dev_queries.jsonl, query text only; that is all a
system under test may see. dev_gold.jsonl is read only to score results: a hit
is any episode in `supporting_episode_ids` (episodes with the gold fault). The
machine is either not given, or detected from the query text with the K5 resolver.

Weights are the config defaults and are not tuned here; test_queries.jsonl and
test_gold.jsonl are refused.

    python -m scripts.retrieval_dev_eval --local-files-only
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys

from project.agents.a4_graph import GraphAgent, default_resolver
from project.agents.a6_retrieval import HybridRetriever
from project.store.models import VerifiedEpisode

ROOT = Path(__file__).resolve().parents[1]
TEST_SET = ROOT / "project" / "data" / "test_set"
GROUND_TRUTH = ROOT / "project" / "data" / "episodes" / "ground_truth.jsonl"
MODES = ("vector_only", "graph_only", "hybrid")
MACHINE_MODES = ("none", "detected_from_query")


def _jsonl(path: Path) -> list[dict]:
    if path.name.startswith("test_"):
        raise ValueError(f"Refusing to read the frozen test split: {path.name}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def catalogue_episodes(faults: list[dict], hazards: list[dict]) -> list[VerifiedEpisode]:
    """One synthetic verified episode per catalogue fault (unit-test fixture)."""
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


def ground_truth_episodes(rows: list[dict]) -> list[VerifiedEpisode]:
    """Oracle store: ground-truth records of the synthetic episodes (eval only)."""
    episodes = []
    for row in rows:
        record = row["record"]
        episodes.append(VerifiedEpisode.model_validate({
            "episode_id": row["episode_id"], "status": "verified", "record": record,
            "verified_by": "synthetic-ground-truth-loader (oracle store for retrieval evaluation; not a human review)",
            "verified_at": "2026-11-25T00:00:00+00:00", "outcome": None,
            "provenance": {
                "capture_id": f"CAP-{row['episode_id']}", "source_type": record["source"],
                "source_path": "project/data/episodes/ground_truth.jsonl",
                "source_sha256": hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest(),
                "evidence_spans": [{"field_path": "symptoms.0", "evidence_id": record["evidence_id"],
                                    "quote": record["symptoms"][0], "start_seconds": 0, "end_seconds": 0}],
            },
        }))
    return episodes


def dev_cases() -> list[dict]:
    gold = {g["query_id"]: g for g in _jsonl(TEST_SET / "dev_gold.jsonl")}
    return [{"query_id": q["query_id"], "tier": q["tier"], "query": q["query"],
             "gold": set(gold[q["query_id"]]["supporting_episode_ids"]),
             "gold_machine": gold[q["query_id"]]["machine_type"]}
            for q in _jsonl(TEST_SET / "dev_queries.jsonl")]


def evaluate(store, cases: list[dict], *, depth: int = 10) -> dict:
    """hit@1, hit@3, MRR@depth per retrieval mode and machine mode (computed by code)."""
    resolver = default_resolver()
    retrievers = {"vector_only": HybridRetriever(store, graph_weight=0.0, asset_weight=0.0),
                  "graph_only": HybridRetriever(store, use_vector=False),
                  "hybrid": HybridRetriever(store)}
    scored = [c for c in cases if c["gold"]]
    detected = {c["query_id"]: resolver.resolve_machine(c["query"]).entity_id for c in scored}
    report = {"queries": len(cases), "scored": len(scored),
              "weights": {"graph": retrievers["hybrid"].graph_weight, "asset": retrievers["hybrid"].asset_weight},
              "machine_detection": {
                  "detected": sum(v is not None for v in detected.values()),
                  "correct": sum(detected[c["query_id"]] == c["gold_machine"] for c in scored),
                  "wrong": sum(detected[c["query_id"]] not in (None, c["gold_machine"]) for c in scored)},
              "results": {}}
    for machine_mode in MACHINE_MODES:
        for name in MODES:
            ranks, by_tier = [], {}
            for case in scored:
                machine = detected[case["query_id"]] if machine_mode == "detected_from_query" else None
                hits = retrievers[name].retrieve(case["query"], machine, k=depth, trace_id="TR-RETRIEVAL-DEV")
                rank = next((i + 1 for i, h in enumerate(hits) if h.episode_id in case["gold"]), None)
                ranks.append(rank)
                by_tier.setdefault(case["tier"], []).append(rank)
            n = len(ranks)
            report["results"][f"{machine_mode}/{name}"] = {
                "hit@1": round(sum(r == 1 for r in ranks) / n, 4) if n else None,
                "hit@3": round(sum(r is not None and r <= 3 for r in ranks) / n, 4) if n else None,
                f"mrr@{depth}": round(sum(1 / r for r in ranks if r) / n, 4) if n else None,
                "hit@3_by_tier": {t: round(sum(r is not None and r <= 3 for r in rs) / len(rs), 4)
                                  for t, rs in sorted(by_tier.items())},
                "misses_at_3": [c["query_id"] for c, r in zip(scored, ranks) if r is None or r > 3],
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
        db = storage_root() / "retrieval-dev" / "oracle-dev.sqlite3"
        db.unlink(missing_ok=True)  # Dedicated synthetic oracle store, rebuilt each run.
        store = KnowledgeStore(db, embedder=MiniLMEmbedder(local_files_only=args.local_files_only))
        writer = GraphAgent(store)
        for episode in ground_truth_episodes(_jsonl(GROUND_TRUTH)):
            writer.put_verified(episode, trace_id="TR-RETRIEVAL-DEV-LOAD")
        report = {"split": "dev", "synthetic_data_only": True, "oracle_store": True, "database": str(db),
                  "embedding_model": store.embedder.model_id, **evaluate(store, dev_cases())}
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
