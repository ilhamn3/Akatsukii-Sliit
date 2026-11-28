"""K6 follow-up: hybrid retrieval over episodes ingested by the real Pipeline A.

Ilham's #21 review kept 4.4 at 90% until retrieval was evaluated on episodes that went
through the real pipeline rather than the oracle store of ground-truth records. Here:

  A2 drafts (real Agent 2 output, Groq gpt-oss-120b, prompt v5; 12 dev + 50 eval episodes)
  -> A3 validate_draft -> ReviewQueue.enqueue -> approve (no reviewer edits)
  -> A4 GraphAgent -> KnowledgeStore (SQLite + MiniLM/FAISS + NetworkX)
  -> A6 HybridRetriever on the 20 DEV queries (query text only).

Drafts that A3 blocks stay pending, as they would without a reviewer's correction. The
same retriever is also run on an oracle store of the SAME source episodes' gold records,
which separates the effect of extraction quality from retrieval itself.

Pipeline B's LLM agents (diagnosis, orchestration, safety LLM) are not run: the team's
Gemini quota is reserved for the final benchmark. RetrievalNode calls this retriever.

Scoring: pipeline episodes have their own IDs (EP-<source hash>), so a hit is a retrieved
episode whose SOURCE episode has the query's gold fault (generator labels, scoring only).
Queries whose fault is not in the store are "unanswerable"; for them the useful outcome
is a low top score (A8 abstains below `abstention_similarity_threshold`).
No weights or thresholds are tuned; the frozen test split is never read.

    python -m project.evaluation.retrieval_pipeline_eval --output-dir logs/retrieval_pipeline_eval
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
TEST_SET = ROOT / "project/data/test_set"
LABELS = ROOT / "project/data/expanded/n400/labels.jsonl"
GROUND_TRUTH = ROOT / "project/data/episodes/ground_truth.jsonl"
EVAL_DRAFTS = ROOT / "project/evaluation/results/2026-11-27_eval_groq-gpt-oss-120b_v5"
DEV_DRAFTS = ROOT / "project/evaluation/results/2026-11-27_dev_groq-gpt-oss-120b/predictions_dev_extraction_v5.jsonl"
REVIEWER = "simulated-reviewer (pipeline retrieval evaluation; approved as extracted, no edits)"
MODES = ("vector_only", "graph_only", "hybrid")
MACHINE_MODES = ("none", "detected_from_query")
SCHEMA = "retrieval_pipeline_eval/v1"


def _jsonl(path: Path) -> list[dict]:
    if path.name.startswith("test_"):
        raise ValueError(f"Refusing to read the frozen test split: {path.name}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_drafts(dev: Path = DEV_DRAFTS) -> list[dict]:
    """Final row per episode with a draft: [{episode_id, draft}] (dev first, then eval)."""
    rows = []
    for path in ([dev] if dev.exists() else []) + [next(EVAL_DRAFTS.glob("predictions_*.jsonl"))]:
        last = {}
        for row in _jsonl(path):
            last[row["episode_id"]] = row
        rows += [{"episode_id": e, "draft": r["draft"], "split": "dev" if path == dev else "eval"}
                 for e, r in last.items() if r.get("draft")]
    return rows


def dev_cases(labels: dict[str, str]) -> list[dict]:
    gold = {g["query_id"]: g for g in _jsonl(TEST_SET / "dev_gold.jsonl")}
    return [{"query_id": q["query_id"], "tier": q["tier"], "query": q["query"],
             "fault": gold[q["query_id"]]["fault_id"], "gold_machine": gold[q["query_id"]]["machine_type"]}
            for q in _jsonl(TEST_SET / "dev_queries.jsonl")]


def ingest(drafts: list[dict], workdir: Path, embedder) -> tuple[object, dict[str, str], dict]:
    """Real A3 -> queue -> approve -> A4 -> store. Returns (store, stored id -> source id, stats)."""
    from project.agents.a3_validation import episode_ids, validate_draft
    from project.core.extraction_schemas import DraftRepairRecord
    from project.core.validation_schemas import ReviewBlocked
    from project.store.knowledge_store import KnowledgeStore
    from project.store.review_queue import ReviewQueue

    store = KnowledgeStore(workdir / "pipeline.sqlite3", embedder=embedder)
    queue = ReviewQueue(workdir / "queue.sqlite3", store=store)
    to_source, blocked = {}, {}
    for row in drafts:
        report = validate_draft(row["draft"], trace_id="TR-K6-PIPE", queue=queue, store=store)
        item = queue.enqueue(row["draft"], report, trace_id="TR-K6-PIPE")
        try:
            episode = queue.approve(item.item_id, reviewer=REVIEWER, trace_id="TR-K6-PIPE")
            to_source[episode.episode_id] = row["episode_id"]
        except ReviewBlocked as exc:
            for issue in exc.issues:
                key = f"{issue.code}@{issue.field_path or '-'}"
                blocked[key] = blocked.get(key, 0) + 1
            to_source.setdefault(episode_ids(DraftRepairRecord.model_validate(row["draft"]))[0], None)
    approved = {k: v for k, v in to_source.items() if v}
    return store, approved, {"drafts": len(drafts), "approved": len(approved),
                             "blocked": len(drafts) - len(approved), "blocking_issues": blocked}


def oracle_store(source_ids: set[str], workdir: Path, embedder):
    """Gold records of the same source episodes, written through A4 (comparison only)."""
    from project.agents.a4_graph import GraphAgent
    from project.store.knowledge_store import KnowledgeStore
    sys.path.insert(0, str(ROOT))
    from scripts.retrieval_dev_eval import ground_truth_episodes

    store = KnowledgeStore(workdir / "oracle.sqlite3", embedder=embedder)
    writer = GraphAgent(store)
    rows = [r for r in _jsonl(GROUND_TRUTH) if r["episode_id"] in source_ids]
    for episode in ground_truth_episodes(rows):
        writer.put_verified(episode, trace_id="TR-K6-ORACLE")
    return store, {e: e for e in source_ids}


def evaluate(store, to_source: dict[str, str], cases: list[dict], labels: dict[str, str], *,
             abstain_below: float, depth: int = 10) -> dict:
    from project.agents.a4_graph import default_resolver
    from project.agents.a6_retrieval import HybridRetriever

    resolver = default_resolver()
    retrievers = {"vector_only": HybridRetriever(store, graph_weight=0.0, asset_weight=0.0),
                  "graph_only": HybridRetriever(store, use_vector=False),
                  "hybrid": HybridRetriever(store)}
    stored_faults = {labels[s] for s in to_source.values()}
    answerable = [c for c in cases if c["fault"] in stored_faults]
    unanswerable = [c for c in cases if c["fault"] not in stored_faults]
    detected = {c["query_id"]: resolver.resolve_machine(c["query"]).entity_id for c in cases}
    out = {"stored_episodes": len(to_source), "stored_faults": len(stored_faults),
           "answerable": [c["query_id"] for c in answerable],
           "unanswerable": [c["query_id"] for c in unanswerable], "results": {}}
    for machine_mode in MACHINE_MODES:
        for name in MODES:
            ranks, top_answerable, top_unanswerable = [], [], []
            for case in cases:
                machine = detected[case["query_id"]] if machine_mode == "detected_from_query" else None
                hits = retrievers[name].retrieve(case["query"], machine, k=depth, trace_id="TR-K6-PIPE-Q")
                top = round(float(hits[0].similarity_score), 4) if hits else 0.0
                if case in answerable:
                    ranks.append(next((i + 1 for i, h in enumerate(hits)
                                       if labels.get(to_source.get(h.episode_id, ""), "") == case["fault"]), None))
                    top_answerable.append(top)
                else:
                    top_unanswerable.append(top)
            n = len(ranks)
            out["results"][f"{machine_mode}/{name}"] = {
                "hit@1": round(sum(r == 1 for r in ranks) / n, 4) if n else None,
                "hit@3": round(sum(r is not None and r <= 3 for r in ranks) / n, 4) if n else None,
                f"mrr@{depth}": round(sum(1 / r for r in ranks if r) / n, 4) if n else None,
                "misses_at_3": [c["query_id"] for c, r in zip(answerable, ranks) if r is None or r > 3],
                "rank_by_query": {c["query_id"]: r for c, r in zip(answerable, ranks)},
                "answerable_top_score_min": min(top_answerable) if top_answerable else None,
                "answerable_below_abstention": sum(s < abstain_below for s in top_answerable),
                "unanswerable_top_scores": top_unanswerable,
                "unanswerable_below_abstention": sum(s < abstain_below for s in top_unanswerable),
            }
    return out


def run(*, embedder=None, dev: Path = DEV_DRAFTS, log=print) -> dict:
    from project.core.registry import load_config
    if embedder is None:
        from project.store.embeddings import get_cached_embedder
        embedder = get_cached_embedder()
    labels = {r["episode_id"]: r["fault_id"] for r in _jsonl(LABELS)}
    cases = dev_cases(labels)
    abstain_below = float(load_config()["system"]["abstention_similarity_threshold"])
    drafts = load_drafts(dev)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        store, to_source, ingestion = ingest(drafts, tmp, embedder)
        log(f"ingestion: {ingestion}")
        pipeline = evaluate(store, to_source, cases, labels, abstain_below=abstain_below)
        log(f"pipeline hybrid: {pipeline['results']['none/hybrid']}")
        ostore, omap = oracle_store(set(to_source.values()), tmp, embedder)
        oracle = evaluate(ostore, omap, cases, labels, abstain_below=abstain_below)
        log(f"oracle (same episodes) hybrid: {oracle['results']['none/hybrid']}")
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                                text=True, check=True).stdout.strip()
    except Exception:
        commit = None
    return {"schema_version": SCHEMA, "split": "dev", "synthetic_data_only": True,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "code_commit": commit,
            "embedding_model": getattr(embedder, "model_id", type(embedder).__name__),
            "drafts": {"dev": sum(d["split"] == "dev" for d in drafts), "eval": sum(d["split"] == "eval" for d in drafts),
                       "model": "openai/gpt-oss-120b via Groq", "prompt": "extraction_v5"},
            "abstention_similarity_threshold": abstain_below, "reviewer": REVIEWER,
            "ingestion": ingestion, "pipeline": pipeline, "oracle_same_episodes": oracle}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "logs" / "retrieval_pipeline_eval")
    args = parser.parse_args(argv)
    report = run()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    out = args.output_dir / "retrieval_pipeline_eval.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
