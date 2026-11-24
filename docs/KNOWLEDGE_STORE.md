# K3: Verified knowledge store

Owner: Kavibarath S. Reviewer: Ilham MM. Branch: `wp4/knowledge-store`.

K3 was started from reviewed K2 commit 8ced194 with main f735884 already merged.
PR #7 was not yet in main at branch creation. Until K2 is merged, a K3 draft PR
should target wp3/extraction-agent to show only K3 changes; after K2 merges, sync
main and retarget the K3 PR to main. Do not merge K2 independently of its owner.

## Boundary and verification

The store accepts a `VerifiedEpisode` envelope containing the team's unchanged
`RepairRecord`, explicit status=verified, reviewer identity/time and provenance.
It rejects draft status and missing verification/provenance. It does not authenticate
reviewers or infer approval from confidence. Only the future trusted A3/review service
should call the write interface. JSON saying verified is not proof of human approval.

A3/K4 owns DraftRepairRecord -> RepairRecord conversion. Unknown required root
cause/failed-attempt reasons cannot be invented to satisfy RepairRecord. Keep those
records in the review queue until resolvable. The K2 live demo remains an unverified
draft and is not imported by this implementation.

The additive envelope preserves the capture ID, source path/hash/type, original
draft evidence spans, extraction model/prompt version when available, reviewer and
verification timestamp. Span paths retain their draft-field meaning; A3 must preserve
the mapping through conversion. Overall outcome is a separate nullable envelope field
because the shared RepairRecord does not currently define it. K5 owns alias/catalogue
resolution; the store only normalizes case and whitespace for category comparison.

## Architecture and consistency

- SQLite stores full envelope JSON, unique episode/evidence IDs, normalized machine
  labels, normalized embedding vectors, embedding identity/dimension and audit events.
- FAISS IndexFlatIP uses normalized vectors for exact cosine similarity of symptoms.
  Default embeddings are local CPU all-MiniLM-L6-v2 (384 dimensions), pinned to
  c9745ed1d9f207416be6d2e6f8de32d1f16199bf. No LLM API key is required.
- NetworkX MultiDiGraph links machine -> observed fault/symptom -> verified cause ->
  completed fix -> episode warning, plus failed attempts and overall outcomes.
  Non-machine nodes and every edge carry episode context, avoiding cross-episode
  causal joins. These links reflect stored statements, not inferred causal discovery.

SQLite is the only durable authority. FAISS and NetworkX projections are constructed
from committed snapshots on reads and restart, rather than independently serialized
files that can disagree after a crash. A write runs in a SQLite transaction and builds
both projections before commit. Projection failures roll back the row and vector.
Repeated identical writes are idempotent; corrections replace projections on the next
read. Changing an episode's primary evidence ID is rejected, as is reusing it for a
different episode. A different embedding identity/dimension requires an explicit
migration/reindex; there is no silent mixing of vector spaces.

This deliberately favors correctness for the project's small dataset: each read
loads a snapshot, each write rebuilds projections, and embedding happens under the
write lock. This is not a high-throughput multi-writer service. SQLite lock timeouts
surface as errors; K8 owns broader operational retry policy. Audit events for writes
commit with the record. Failed writes record an error type after rollback when the
database is available. Read events record snapshot access, not downstream completion.

## Interface for Ilham and K4-K6

```python
from project.store.knowledge_store import KnowledgeStore

store = KnowledgeStore()  # Requires E: activation; creates no model download yet.
store.put_verified(verified_episode, trace_id="TR-INGEST-001")
episode = store.get("EP-001", trace_id="TR-LOOKUP-001")
records = store.by_machine("conveyor_line", trace_id="TR-MACHINE-001")
hits = store.search_symptoms("belt slips under load", machine_type="conveyor_line",
                             k=5, min_score=0.2, trace_id="TR-RETRIEVE-001")
related = store.related("hazard", "Isolate electrical power before repair",
                         trace_id="TR-GRAPH-001")
graph = store.graph_snapshot(trace_id="TR-GRAPH-002")
events = store.audit_events()
```

`get` returns a VerifiedEpisode or None; `by_machine` returns VerifiedEpisodes.
Vector/graph queries return the shared Evidence schema with source type, episode ID,
primary evidence ID, full repair record, failed attempts, outcome and provenance.
Machine filtering precedes top-k selection. Graph labels match exactly after
case/whitespace normalization. Query results/snapshots are independent copies.
Cosine similarity is not confidence; raw cosine is retained in metadata and negative
matches are excluded by the nonnegative threshold. K6 owns combined ranking and
abstention; no benchmark retrieval-quality claim is made by K3.
MiniLM is an English-oriented development baseline; multilingual retrieval and
long-input truncation behavior still need evaluation against the team's data.

The embedder is injectable through a model_id, dimension and encode(texts) interface.
Tests use a labelled tiny test embedder and actual SQLite, FAISS and NetworkX. They
do not download models or substitute fake databases/indexes.

## Setup and demonstration

```powershell
Set-Location 'E:\Factory memory\Akatsukii-Sliit'
. .\scripts\Activate-FactoryMemory.ps1
python -m pip install -r project/requirements-store.txt
python -m pytest tests/store -q
python -m scripts.store_demo
```

The first demo downloads the pinned MiniLM weights to E:. It inserts two hand-authored
synthetic verified envelopes in `.local-data/store-demo/repairs.sqlite3`, queries all
three stores and prints a summary. Fixture hashes, identities and timestamps are
synthetic, not recordings or real reviewer attestations. No production records or
API credentials are needed. The normal store defaults to `.local-data/store/repairs.sqlite3`.
The exact root comes from FACTORY_MEMORY_STORAGE set by the activation script.

After downloading once, demonstrate a restart using cached weights only:

```powershell
python -m scripts.store_demo --local-files-only
```

Do not commit databases, weights, package caches or runtime outputs. K4/K5 integration,
team-record validation and Ilham's store-interface review remain pending.

## Observed development validation (24 Nov 2026)

The full repository suite passed 95 tests (15 new store tests). Dependency checks
passed. The real pinned MiniLM demo stored two synthetic episodes; machine lookup
and the graph warning query each returned the conveyor episode. Symptom search
ranked conveyor first (cosine about 0.708) and compressor second (about 0.138).
These are development smoke results, not retrieval accuracy measurements.
The separate-process rerun with `--local-files-only` also passed, recovering both
episodes and the same ranking from SQLite with cached weights. The local summary
is saved in logs/store_demo_restart.json; repeat inserts produced STORE_NOOP events.

## References

- [FAISS cosine metric guidance](https://github.com/facebookresearch/faiss/wiki/MetricType-and-distances)
- [SentenceTransformer local model/encoding interface](https://sbert.net/docs/package_reference/sentence_transformer/model.html)
- [Pinned MiniLM revision and Apache-2.0 model card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/commit/c9745ed1d9f207416be6d2e6f8de32d1f16199bf)
