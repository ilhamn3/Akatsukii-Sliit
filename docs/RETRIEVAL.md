# K6: Agent 6 hybrid retrieval (vector + graph, with failed attempts)

Owner: Kavibarath S. Reviewer: Ilham MM. Branch: `wp4/retrieval-agent`.
Subtasks 4.4 and 4.5. Stacked on K5 (PR #14), then K4 (#13) and K3 (#12).
Retarget to `main` as those merge.

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

`RetrievalAgent` is a BaseAgent node. It writes `retrieved_episodes` (Evidence dicts),
`graph_subgraph_nodes`, `failed_attempts_retrieved` (readable strings with episode and
evidence IDs) and `retrieval_confidence` (max score). I left Ilham's `config.yaml`
Pipeline B untouched. The proven wiring (tested with a config override in
`tests/store/test_retrieval.py`) is:

```yaml
      - name: "RetrievalAgent"
        module: "project.agents.a6_retrieval"
        class: "RetrievalAgent"
        enabled: true
        stub: false
    edges:
      - from: "DiagnosticAgent"
        condition: "check_clarification"
        on_true: "__end__"
        on_false: "RetrievalAgent"
      - from: "RetrievalAgent"
        to: "<Orchestrator>"
```

The agent needs `FACTORY_MEMORY_STORAGE` (activation script). If the store is
unavailable, the failure is traced as AGENT_ERROR and the retrieval fields stay empty.
The Orchestrator should then abstain. `project/core/stubs/retrieval_stub.py` can be
retired when Ilham switches over.

## Development check (synthetic; not a benchmark)

```powershell
. .\scripts\Activate-FactoryMemory.ps1
python -m scripts.retrieval_dev_eval --local-files-only
```

The script builds 40 catalogue-derived SYNTHETIC verified episodes. Each has symptoms
and alarm codes, root cause, valid fixes as steps, wrong fixes as failed attempts and
hazard controls as warnings. They are written through Agent 4 into
`.local-data/retrieval-dev` (the loader is named as verifier; no human review). Each
fault's differently worded `lay_description` is the query. Results come from code and
are saved to `logs/retrieval_dev_eval.json`.

25 Nov 2026, real MiniLM, 40 queries:

| Scope | Mode | hit@1 | hit@3 | MRR |
|---|---|---:|---:|---:|
| Within machine (8 candidates) | vector only | 0.950 | 1.000 | 0.975 |
| | graph only | 1.000 | 1.000 | 1.000 |
| | **hybrid** | 0.975 | 1.000 | 0.988 |
| All machines (40 candidates) | vector only | 0.925 | 1.000 | 0.963 |
| | graph only | 0.925 | 1.000 | 0.963 |
| | **hybrid** | **0.975** | 1.000 | **0.988** |

How to read this:
- Hybrid beats either signal alone across machines, and every gold episode is in the
  top 3 in every mode.
- Within one machine, graph-only is slightly higher. These queries share catalogue
  vocabulary, which favours lexical matching. I did not retune the weights to this.
- This is a self-consistency check on synthetic data the team wrote. It is not
  retrieval accuracy on real narratives. The frozen test set was not used.

Ilham's DEV split (`tests/agents/dev_queries.json`) has no episode-level gold, so it is
inspection only. All five display-name machine types resolved. Top-1 matched the
plausible catalogue fault for the specific queries:

| Query | Top-1 fault | Score |
|---|---|---:|
| DEV-02 ram chatter | P01 slow ram descent | 0.57 |
| DEV-03 ALM-304 | I03 barrel temperature runaway | 0.72 |
| DEV-04 belt slipping | C02 belt slipping at the drive | 0.63 |
| DEV-05 oil carryover | A02 excess oil carryover | 0.62 |
| DEV-09 turret | L06 turret fails to index | 0.59 |

The vague queries (DEV-06/07/08 "something feels strange") scored 0.28-0.42, which is
below the 0.55 abstention threshold.

## Limits

- Real verified team episodes don't exist yet, so real retrieval quality is unmeasured.
- Lexical graph matching is English-only with crude stemming. Synonyms rely on the
  vector signal.
- Every query reads one full store snapshot and rebuilds the index and graph. That is
  fine for this project's small dataset, but it is not a high-throughput design.
- Symptoms are not linked to catalogue fault IDs. Retrieval returns verified episodes,
  not catalogue entries.

## Tests

`tests/store/test_retrieval.py` (12 tests) uses real SQLite/FAISS/NetworkX and a
test-only hash embedder. It covers:
- contract signature and ordering;
- failed attempts, outcome and citations;
- machine filtering before ranking, and cross-machine search;
- alarm-code graph traversal;
- graph-only-adds-support;
- the asset hop;
- empty store and invalid input;
- agent blackboard output and traces, including the failure path;
- Pipeline B with A6 inserted by config;
- the dev-check metrics.
