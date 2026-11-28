# K6: Agent 6 hybrid retrieval (vector + graph, with failed attempts)

Owner: Kavibarath S. Reviewer: Ilham MM. Subtasks 4.4 and 4.5. Merged as PR #15.
Review follow-ups (flat metadata, dev-split evaluation, latency) are on
`wp4/review-followups`. Pipeline B calls this engine through Ilham's `RetrievalNode`
(`project/core/stubs/retrieval_node.py`, backend `real`).

## Contract

Implements `RetrievalEngine` from `project/core/contracts.py`:

```python
from project.agents.a6_retrieval import HybridRetriever

engine = HybridRetriever()          # uses KnowledgeStore() under the activated storage root
hits = engine.retrieve("Drive belt slipping under load", "Conveyor Line (Automated Material Handling)", k=3)
# optional keywords (contract-compatible): machine_id="Line 2", trace_id="TR-..."
```

It returns `list[Evidence]` ordered by `similarity_score` in [0, 1]. Each item has the
verified episode's primary `evidence_id`, its `episode_id`, `source_type`
("vector" or "graph", whichever signal contributed more) and readable `content`:
symptoms, root cause, cited fix steps, failed attempts, warnings and outcome.

`metadata` gives the Orchestrator what it needs for grounding and conflict ranking:

| Key | Content |
|---|---|
| `verified` | Always `true`: only human-approved episodes are stored |
| `recorded_at`, `recorded_at_source` | ISO time used for recency. It is the verification time (`"verified_at"`), because the pipeline has no capture/repair timestamp yet |
| `root_cause`, `machine_type` | Flat copies from the record |
| `outcome_status` | `worked` / `failed` / `unknown`, computed with A8's own `orchestration_helpers.classify_outcome` keyword rules, so A6 and A8 classify identically |
| `kind`, `step_order` | `"episode"` and `null`: evidence is episode-level, and steps carry their own order and IDs in `steps` |
| `steps` | Ordered steps with their step-level `evidence_id` and timestamp (cite these in `ActionStep.evidence_ids`) |
| `failed_attempts` | `action`, `outcome`, `why_failed` for every failed attempt (4.5) |
| `outcome` | Overall repair outcome (4.5) |
| `safety_warnings`, `catalogue_hazards` | Warnings and the K5 hazard IDs they may involve |
| `verified_by`, `verified_at`, `provenance` | Human verification and source evidence, incl. `reviewer_supplied_fields` |
| `record` | Full shared `RepairRecord` |
| `retrieval` | `vector_cosine`, `graph_score`, `matched_terms`/`codes`/`nodes`, `asset_match`, weights, `machine_filter`, `cross_machine`, machine resolution |

## How it ranks

1. **Machine filter.** The free-text `machine_type` is resolved with K5. For example
   "CNC Lathe (Turning Center)" becomes `cnc_lathe` and "Rotary Screw Air Compressor"
   becomes `air_compressor`. The filter is applied *before* ranking. If the machine
   is unresolved ("unspecified", None), A6 searches all machines and sets
   `cross_machine: true`.
2. **Vector.** MiniLM cosine between the query and each episode's observed symptoms
   (FAISS exact inner product), from one consistent store snapshot.
3. **Graph.** Traversal from the machine node through fault -> cause -> fix and the
   episode's failed attempts. The score is query-term coverage of the reached labels:
   a match on a fault/symptom node counts 1.0, a cause 0.75, a fix or failed attempt
   0.5. An alarm code on the path (ALM-410, SV-024) counts as full support.
   Optional `machine_id` adds an asset hop (machine -> asset -> episode).
4. **Fusion.**

   `score = 1 - (1 - cosine) x (1 - 0.5 x graph) x (1 - 0.1 x asset)`

   Graph and asset evidence can only *add* support. A record with no graph match keeps
   exactly its cosine, so the Orchestrator's abstention threshold (0.55) still reads
   on the similarity scale. The weights are in `config.yaml` under `retrieval:`. They
   were chosen before evaluation and are not tuned.

A6 ranks by relevance only. Ranking conflicting records by outcome, senior
verification and recency, and deciding to abstain, belong to the Orchestrator
(Ilham, 5.6). A6 hands over everything those decisions need.

## Pipeline B wiring (for Ilham's Orchestrator PR)

Ilham's `RetrievalNode` (PR #20) calls `HybridRetriever().retrieve(...)` in Pipeline B
and writes `evidence` / `retrieved_episodes` / `retrieval_confidence`. My
`RetrievalAgent` (same module) is an equivalent standalone node; it is not registered
in `config.yaml`. Both need the store. If the store is unavailable the failure is
traced and no evidence is returned, so the Orchestrator abstains.

## Dev-split evaluation (synthetic; not a benchmark)

```powershell
. .\scripts\Activate-FactoryMemory.ps1
python -m scripts.retrieval_dev_eval --local-files-only
```

The setup:
- **Queries:** the 20 dev-split queries in `project/data/test_set/dev_queries.jsonl`,
  5 per tier. The retriever sees **query text only**. `dev_gold.jsonl` is used only to
  score. The script refuses to open `test_queries.jsonl` or `test_gold.jsonl`.
- **Machine:** either not given, or detected from the query text with the K5 resolver.
  Detection was 20/20 correct, with 0 wrong.
- **Corpus:** an *oracle store* of the 400 synthetic episodes' ground-truth records
  (`project/data/episodes/ground_truth.jsonl`), written through Agent 4. This isolates
  retrieval from extraction errors. It is used for this check only and never by the
  benchmark system, because that file is scoring-only data for agents.
- **Scoring:** a hit is any episode listed in the query's `supporting_episode_ids`, i.e.
  an episode of the correct fault. The weights are the config defaults and were not tuned.

25 Nov 2026, real MiniLM, results computed by code (`logs/retrieval_dev_eval.json`):

| Machine given | Mode | hit@1 | hit@3 | MRR@10 | Miss at 3 |
|---|---|---:|---:|---:|---|
| none | vector only | 0.95 | 0.95 | 0.958 | DQ-07 (medium) |
| none | graph only | 0.95 | 0.95 | 0.950 | DQ-13 (hard) |
| none | **hybrid** | **1.00** | **1.00** | **1.000** | none |
| detected from query | vector only | 0.95 | 0.95 | 0.958 | DQ-07 |
| detected from query | graph only | 0.95 | 0.95 | 0.950 | DQ-13 |
| detected from query | **hybrid** | **1.00** | **1.00** | **1.000** | none |

How to read this:
- Each single signal misses a different query, and the fused score recovers both.
- n = 20, and the queries and episodes come from the same seeded templates and
  catalogue vocabulary. Expect real narratives, and the frozen test set, to score lower.
  These numbers say the retriever and its contract work; they are not a benchmark result.

An earlier check in this document (catalogue `lay_description` queries) has been
**withdrawn**. The frozen test set's easy tier is built from those descriptions, so
results on them are too close to the test set to report. No weights were tuned on it.

## Retrieval on episodes ingested by the real pipeline (28 Nov 2026)

Ilham's #21 review kept 4.4 at 90% until retrieval was checked on episodes that went
through the real pipeline instead of the oracle store. Path:

```
real A2 drafts (Groq gpt-oss-120b, prompt v5; 12 dev + 50 eval episodes)
  -> A3 validate_draft -> ReviewQueue.enqueue -> approve (as extracted, no reviewer edits)
  -> A4 GraphAgent -> KnowledgeStore (SQLite + MiniLM/FAISS + NetworkX)
  -> A6 HybridRetriever on the 20 dev queries (query text only)
```

- **Command:** `python -m project.evaluation.retrieval_pipeline_eval`. Results:
  `project/evaluation/results/2026-11-28_retrieval_pipeline_eval/` (code `fd75460`).
- **Ingestion:** 56 of 62 drafts approved as extracted. 6 stayed pending, because A3
  blocked them (missing root cause or `why_failed`, or a machine named only by a part)
  and no reviewer filled the gap. The store holds 56 episodes of 30 faults.
- **Scoring:** a hit is a retrieved episode whose *source* episode has the query's gold
  fault (pipeline episodes have their own IDs). 15 dev queries have their fault in the
  store; for the other 5 the right outcome is a low top score, since A8 abstains below
  `abstention_similarity_threshold` (0.55).
- **Comparison:** the same retriever on an oracle store of the **same 56 episodes'**
  gold records. The difference is the effect of ingesting real narrations.
- Not run: Pipeline B's LLM agents (diagnosis, orchestration, safety LLM), because the
  Gemini quota is reserved for the final benchmark. `RetrievalNode` calls this retriever.
  No weights or thresholds were tuned; the test split is refused.

| Store | Machine | Mode | hit@1 | hit@3 | MRR@10 | Miss at 3 |
|---|---|---|---:|---:|---:|---|
| Pipeline | none | vector only | 0.73 | 0.80 | 0.793 | DQ-06, DQ-13, DQ-20 |
| Pipeline | none | graph only | 0.87 | 0.93 | 0.889 | DQ-13 |
| Pipeline | none | **hybrid** | **0.80** | **0.93** | **0.856** | DQ-13 |
| Pipeline | detected | **hybrid** | 0.80 | 0.93 | 0.874 | DQ-13 |
| Oracle, same episodes | none | hybrid | 1.00 | 1.00 | 1.000 | none |

Abstention at 0.55 (hybrid):

| Store | Answerable queries scoring below 0.55 | Unanswerable queries scoring below 0.55 |
|---|---:|---:|
| Pipeline | 2 of 15 | 4 of 5 |
| Oracle, same episodes | 0 of 15 | 2 of 5 |

What the gap is:
- **All three slips are one fault (A04), with one stored episode (EP-0323).** Its
  technician only said the compressor "keeps starting and stopping every few seconds"
  (written "comp" in the draft). DQ-13 asks about "hissing at the unloader", which the
  narration never mentions, so no ingested record can match it; the oracle record has
  it because it holds the catalogue's full symptom list. DQ-03 and DQ-20 still find the
  episode at ranks 2 and 3.
- So the drop from the oracle is about what technicians actually say, not a retrieval
  or extraction error: A2 extracted EP-0323's narrated symptom and root cause correctly.
- Narrated records also make scores lower overall: unanswerable queries are abstained
  on more often (4 of 5 instead of 2 of 5), but 2 answerable queries also fall below
  0.55. The threshold was set on the oracle store and is not retuned here.
- Hybrid ranks better than vector-only on ingested records (hit@3 0.93 vs 0.80).
  Graph-only is slightly better at hit@1 here (0.87 vs 0.80) with the same hit@3, but
  its scores are not on the cosine scale, so it cannot drive abstention; n = 15 is too
  small to separate the two. The weights were not changed.

## Latency at 2,000 records

Ilham's #12 review asked for cached projections before the 2,000-record scalability run.

1. SQLite keeps a `revision` counter, bumped in the same transaction as every write.
2. A process-wide cache keyed by store path and embedding model holds:
   - the parsed episodes;
   - the FAISS index and the graph, built lazily;
   - A6's per-node term index.
3. Any write, from any process, changes the revision, and the next read rebuilds.
4. Writes validate only the new episode's projection, which is per-episode by design.
5. A6 reads its retrieval settings from config only when the file changes.

Measured on this laptop with the test-only hash embedder (so MiniLM time is excluded),
using the 400 episodes replicated to 2,000. Each query used a new retriever and store
instance, as `RetrievalNode` does:

| | Before (main 93e9b20) | After |
|---|---|---|
| Writes | 1,016 written in 240 s, then stopped; last 100 averaged 444 ms each and rising | all 2,000 in 25-31 s, ~13 ms each, flat |
| Query, all machines | 665 ms (at 1,016 records) | ~70 ms steady; ~2.6 s once after a write (rebuild) |
| Query, one machine | not measured | ~40 ms steady |

Every read still appends an audit event.

## Limits

- Real verified team episodes don't exist yet, so real retrieval quality is unmeasured.
  The 28 Nov pipeline check uses real Agent 2 output on synthetic narrations (n = 15
  answerable dev queries), which is closer, but still synthetic.
- Lexical graph matching is English-only with crude stemming. Synonyms rely on the
  vector signal.
- The first query after a write rebuilds the projections (~2.6 s at 2,000 records).
  Load data before serving queries, or warm up with one query.
- `recorded_at` is the verification time, not the repair date. Adding a capture or
  repair date to the pipeline would make recency ranking meaningful.
- Symptoms are not linked to catalogue fault IDs. Retrieval returns verified episodes,
  not catalogue entries.

## Tests

`tests/store/test_retrieval.py` (17 tests) uses real SQLite/FAISS/NetworkX and a
test-only hash embedder. The follow-ups added tests for:
- flat metadata, read back through A8's `normalize_evidence`;
- the shared cache and its cross-instance invalidation;
- reads staying independent of the cache;
- constant work per write;
- dev-split-only loading, with test files refused.

The original tests cover:
- contract signature and ordering;
- failed attempts, outcome and citations;
- machine filtering before ranking, and cross-machine search;
- alarm-code graph traversal;
- graph-only-adds-support;
- the asset hop;
- empty store and invalid input;
- agent blackboard output and traces, including the failure path;
- Pipeline B with the real retriever through `RetrievalNode`;
- the dev-check metrics.
