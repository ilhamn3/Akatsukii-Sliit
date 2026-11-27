# Kavibarath S: Work Plan, Progress Tracker and PR Log

**Team Akatsukii · SLIIT · Innodata AI Catalyst '26 (University Track)**  
**Role:** Ingestion and knowledge-store lead · **Share:** 30% (30 of 100 planning points) · **PR reviewer:** Ilham MM  
**Repo:** https://github.com/ilhamn3/Akatsukii-Sliit  
**Deadlines:** MVP / project plan **29 Nov 2026, 11:59 PM** · Grand Finale **7 Dec 2026**

**What you own:** Capture and Transcription (1), Knowledge Extraction (2), Validation and Curation (3), Knowledge Graph (4), Retrieval (6), knowledge store, review-queue backend, hardening.

> **Rule for every PR:** update the **Done %** column in section 1 of this file, add a row to the **PR log** (section 7), run `python scripts/progress.py --write`, and commit the result **in the same PR**. Reviewer checks that the claimed % matches the evidence.

## 1. Progress tracker

<!-- PROGRESS:START -->
**Overall: 23.2 / 30 points · 77% complete**

| WP | Title | Weight | Earned | WP % |
|---|---|---:|---:|---:|
| WP3 | Ingestion agents 1-4 and review queue | 18 | 11.5 | 64% |
| WP4 | Knowledge store and retrieval | 10 | 9.7 | 97% |
| WP10 | Hardening and unit tests | 2 | 2 | 100% |
<!-- PROGRESS:END -->

**How to update:** edit only the **Done %** column below (0-100). Points earned = Pts x Done %. A subtask is 100% only when it meets its "done when" in section 3 and has been reviewed. Partial progress is fine (25 / 50 / 75%).

#### WP3 – Ingestion agents 1-4 and review queue (18 pts)

| ID | Subtask | Pts | Due | Done % |
|---|---|---|---|---|
| 3.1 | Agent 1: Whisper transcription (timestamped transcript) | 3 | 24 Nov | 75% |
| 3.2 | Agent 1: video frame sampling and unclear-audio flagging | 1 | 24 Nov | 75% |
| 3.3 | Agent 2: structured extraction (steps, tools, root cause, failed attempts, warnings) | 4 | 24 Nov | 50% |
| 3.4 | Agent 3: schema/consistency validation, dedup, confidence scoring | 4 | 26 Nov | 50% |
| 3.5 | Agent 4: entity resolution and graph writes | 3 | 26 Nov | 75% |
| 3.6 | Review-queue backend for low-confidence records | 2 | 26 Nov | 75% |
| 3.7 | Extraction precision / recall / F1 numbers exported to the harness | 1 | 27 Nov | 75% |

#### WP4 – Knowledge store and retrieval (10 pts)

| ID | Subtask | Pts | Due | Done % |
|---|---|---|---|---|
| 4.1 | SQLite tables for verified records | 2 | 26 Nov | 100% |
| 4.2 | Vector index (ChromaDB or FAISS) | 2 | 26 Nov | 100% |
| 4.3 | Knowledge graph (NetworkX) of machines, faults, causes, fixes, hazards | 2 | 26 Nov | 100% |
| 4.4 | Agent 6: hybrid vector + graph retrieval | 3 | 27 Nov | 90% |
| 4.5 | Retrieval returns failed attempts and outcomes | 1 | 27 Nov | 100% |

#### WP10 – Hardening and unit tests (2 pts)

| ID | Subtask | Pts | Due | Done % |
|---|---|---|---|---|
| 10.1 | Retries, timeouts and fallbacks in ingestion and store modules | 1 | 28 Nov | 100% |
| 10.2 | pytest for ingestion and store modules | 1 | 28 Nov | 100% |

## 2. Files and folders you own

Proposed repo layout (adjust if the existing repo differs, but keep the competition-required files: `project/main.py`, `project/requirements.txt`, `README.md`, `docs/Proposal.docx`, `docs/Workflow.md`, `docs/PROJECT_DETAILS.md`, `project-demo.mp4`).

| Path | What it is |
|---|---|
| `project/agents/a1_capture.py` | Agent 1 – Whisper transcription and frame sampling |
| `project/agents/a2_extraction.py` | Agent 2 – structured knowledge extraction |
| `project/agents/a3_validation.py` | Agent 3 – validation, dedup, confidence scoring |
| `project/agents/a4_graph.py` | Agent 4 – entity resolution and graph writes |
| `project/agents/a6_retrieval.py` | Agent 6 – hybrid vector + graph retrieval |
| `project/store/ (sqlite_db.py, vector_index.py, graph.py, review_queue.py)` | SQLite, ChromaDB/FAISS, NetworkX graph, review-queue backend |
| `tests/ingestion/, tests/store/` | pytest for ingestion and store modules |

## 3. Planned PRs and PR descriptions

One PR per row, small and focused. Branch from `main`, title format `[WP<N>] what`, reviewer **Ilham MM**. Use the PR template (`.github/pull_request_template.md`) and start from the description below.

| PR | Branch | Subtasks | Pts | Merge by |
|---|---|---|---|---|
| K1 | `wp3/capture-transcription` | 3.1, 3.2 | 4 | 24 Nov |
| K2 | `wp3/extraction-agent` | 3.3 | 4 | 24 Nov |
| K3 | `wp4/knowledge-store` | 4.1, 4.2, 4.3 | 6 | 26 Nov |
| K4 | `wp3/validation-review-queue` | 3.4, 3.6 | 6 | 26 Nov |
| K5 | `wp3/graph-agent` | 3.5 | 3 | 26 Nov |
| K6 | `wp4/retrieval-agent` | 4.4, 4.5 | 4 | 27 Nov |
| K7 | `wp3/extraction-metrics` | 3.7 | 1 | 27 Nov |
| K8 | `wp10/hardening-tests` | 10.1, 10.2 | 2 | 28 Nov |

### K1: [WP3] Agent 1: Whisper transcription and frame sampling

- **Branch:** `wp3/capture-transcription` · **Merge by:** 24 Nov · **Subtasks:** 3.1, 3.2 · **Reviewer:** Ilham MM
- **Summary:** Implements Agent 1: audio to timestamped transcript, key-frame sampling for video, and flagging of unclear audio.
- **Done when:** Works on Tharshanth's sample narratives/audio; output validates against Ilham's schema; low-quality audio is flagged, not silently dropped.

### K2: [WP3] Agent 2: structured knowledge extraction

- **Branch:** `wp3/extraction-agent` · **Merge by:** 24 Nov · **Subtasks:** 3.3 · **Reviewer:** Ilham MM
- **Summary:** Implements Agent 2: transcript to a draft record with machine, symptoms, steps, tools, parts, root cause, failed attempts and safety warnings, each with source timestamps.
- **Done when:** Draft record validates against the schema on all sample narratives; failed attempts and warnings are captured; handoff appears in the trace.

### K3: [WP4] SQLite store, vector index and knowledge graph

- **Branch:** `wp4/knowledge-store` · **Merge by:** 26 Nov · **Subtasks:** 4.1, 4.2, 4.3 · **Reviewer:** Ilham MM
- **Summary:** Builds the three stores and a single store interface. Includes the retrieval interface (function signatures) Ilham needs by 26 Nov.
- **Done when:** A verified record is written to all three stores; queries by machine, by symptom and by graph relation work; interface documented in a docstring/README for Ilham.

### K4: [WP3] Agent 3: validation, dedup, confidence and review-queue backend

- **Branch:** `wp3/validation-review-queue` · **Merge by:** 26 Nov · **Subtasks:** 3.4, 3.6 · **Reviewer:** Ilham MM
- **Summary:** Implements Agent 3 and the review-queue backend. (Team checkpoint is 25 Nov.)
- **Done when:** Inconsistent or duplicate records are caught; each record has a confidence score; low-confidence records land in the queue and can be approved/rejected through the backend API for Kajanan's UI.
- **K2 review follow-up:** A3 must map `DraftRepairRecord` to the shared `RepairRecord` while retaining evidence spans and provenance. Missing required facts must remain reviewable rather than being invented to satisfy the target schema.

### K5: [WP3] Agent 4: entity resolution and graph writes

- **Branch:** `wp3/graph-agent` · **Merge by:** 26 Nov · **Subtasks:** 3.5 · **Reviewer:** Ilham MM
- **Summary:** Implements Agent 4: resolves aliases (e.g. "hyd. press 3" = "Press-03") and writes verified records into the graph and indexes.
- **Done when:** Alias test cases resolve to one entity; graph shows linked machine -> fault -> cause -> fix -> hazard; in-memory NetworkX is enough (no Neo4j needed).
- **K2 review follow-up:** Resolve A2's free-text `machine_type` to catalogue IDs: `cnc_lathe`, `hydraulic_press`, `injection_moulding`, `conveyor_line`, `air_compressor`.

### K6: [WP4] Agent 6: hybrid retrieval including failed attempts

- **Branch:** `wp4/retrieval-agent` · **Merge by:** 27 Nov · **Subtasks:** 4.4, 4.5 · **Reviewer:** Ilham MM
- **Summary:** Implements Agent 6: hybrid vector + graph search returning similar past repairs, their outcomes and failed attempts.
- **Done when:** For dev-split queries the right records appear in the top results; each result carries an evidence ID; output validates against the Orchestrator's input contract.

### K7: [WP3] Extraction P/R/F1 export for the benchmark harness

- **Branch:** `wp3/extraction-metrics` · **Merge by:** 27 Nov · **Subtasks:** 3.7 · **Reviewer:** Ilham MM
- **Summary:** Adds a script that scores Agent 2 output against ground truth and exports field-level precision, recall and F1.
- **Done when:** Script runs from the command line on the labelled set and produces a file Kajanan's harness can read; numbers come from code, not typed by hand.

### K8: [WP10] Retries, timeouts, fallbacks and pytest

- **Branch:** `wp10/hardening-tests` · **Merge by:** 28 Nov · **Subtasks:** 10.1, 10.2 · **Reviewer:** Ilham MM
- **Summary:** Adds retries, timeouts and fallbacks to ingestion/store modules and a pytest suite for them.
- **Done when:** A simulated API timeout does not crash the pipeline; pytest passes on a clean checkout; failures are written to the trace log.

## 4. Handoffs

**You deliver**

| By | To | What |
|---|---|---|
| 22 Nov (stubs) | Ilham | Store/retrieval interface stubs agreed against Ilham's schemas |
| 26 Nov | Ilham | Store and retrieval interface (blocks Orchestrator integration) |
| 27 Nov | Kajanan | Extraction F1 numbers for the harness |
| 26 Nov | Kajanan | Review-queue backend API for the review page |

**You depend on**

| By | From | What |
|---|---|---|
| 22 Nov | Ilham | Schemas and message contracts |
| 24 Nov | Tharshanth | Sample episodes and audio |

## 5. Your timeline

| Date | Plan |
|---|---|
| 21-22 Nov | Set up Whisper, SQLite, ChromaDB; draft Agent 1 against schema stubs |
| 23-24 Nov | Agents 1-2 working on sample narratives |
| 25-26 Nov | Agents 3-4; review-queue backend; store. **Checkpoint 25 Nov** |
| 27 Nov | Retrieval agent (6); extraction F1 to harness |
| 28 Nov | Hardening and pytest |
| 29 Nov | Module documentation |
| 30 Nov - 6 Dec | Ingestion Q&A preparation |
| 7 Dec | Support integration and the data side |

## 6. Rules and competition items you own

- Owns the extraction quality numbers (field-level precision, recall, F1) that go to Kajanan's harness.
- Innodata/competition materials are confidential: do not upload them to unauthorised services.
- **Cut line** (only if behind at the 25-26 Nov checkpoint): (1) Agent 3 uses rules only, no LLM critic; (2) Agent 4 uses in-memory NetworkX only, no Neo4j.
- Never cut: trace logging of every handoff from your agents.
- Original work built during the competition; reused components and pre-existing code must be disclosed.
- Declare every AI model, service and coding assistant you use (send your list to Tharshanth for `docs/PROJECT_DETAILS.md`).
- **K2 review follow-up:** The final benchmark must use one team-agreed LLM provider/model. Continue using `get_llm_client()`; the Groq development smoke tests do not select the final benchmark provider.
- Everyone must be able to explain the full architecture and benchmark method at the finale Q&A, not only their own modules.

## 7. PR log (add one row per PR, newest at the bottom)

K2 review (24 Nov): Ilham reported 76 passing tests and approved the code subject
to adding the PR #7 row below. Approval explicitly covers the additive
`state.py` draft_record and `config.yaml` Capture -> Extraction handoff changes.
The required log row is now included. Representative team-sample validation is
still pending, so subtask 3.3 stays at 50%. Non-blocking follow-ups are recorded
under K4, K5 and the benchmark rules above.
After merging origin/main at f735884, all 80 tests and pip check passed locally.

| PR # | Date | Title | Subtasks | Work done in this PR | My progress before -> after | Reviewer |
|---|---|---|---|---|---|---|
| [#5](https://github.com/ilhamn3/Akatsukii-Sliit/pull/5) (K1 draft) | 22 Nov 2026 | [WP3] Agent 1: Whisper transcription and frame sampling | 3.1 (0->75%), 3.2 (0->75%) | Capture implementation, schemas, registry handoff and local setup; 52 tests pass plus actual Whisper/FFmpeg checks on synthetic speech, video and silence. Evidence: docs/CAPTURE_SETUP.md and local logs/capture_smoke/. Team samples and review pending | 0% -> 10% (3/30 pts) | Ilham MM (pending) |
| [#7](https://github.com/ilhamn3/Akatsukii-Sliit/pull/7) (K2) | 24 Nov 2026 | [WP3] Agent 2: source-linked knowledge extraction | 3.3 (0->50%) | Draft schema, extraction agent, quote/timestamp provenance, A1 -> A2 handoff, traced failures, API diagnostics and setup docs; 76 tests passed before main sync and in Ilham's review. Live Groq 120B / prompt v3 synthetic smoke check passed. Evidence: docs/EXTRACTION_SETUP.md; representative samples pending | 10% -> 17% (3/30 -> 5/30 pts) | Ilham MM (code approved; requested log row added) |
| [#12](https://github.com/ilhamn3/Akatsukii-Sliit/pull/12) (K3) | 24 Nov 2026 | [WP4] Verified knowledge store and query interface | 4.1, 4.2, 4.3 (0->75% each) | SQLite envelopes/provenance and audit events, FAISS symptom search, episode-scoped NetworkX graph, atomic writes and documented K4-K6 interface. 95 tests passed at ed90444; after merging main 6c5fd72 on 25 Nov, 126 tests and pip check pass and the cached MiniLM synthetic demo reproduces. Base is main (K2 merged). Evidence: docs/KNOWLEDGE_STORE.md. Integration, representative data and reviewer acceptance pending | 17% -> 32% (5/30 -> 9.5/30 pts) | Ilham MM (pending) |
| [#13](https://github.com/ilhamn3/Akatsukii-Sliit/pull/13) (K4; builds on #12) | 25 Nov 2026 | [WP3] Agent 3: validation, dedup, confidence and review-queue backend | 3.4 (0->50%), 3.6 (0->75%) | Rule-based A3 (grounding, quantity/unit, consistency, exact and semantic dedup), uncalibrated transparent confidence, Pipeline A handoff, SQLite review queue with audited approve/reject and reviewer corrections, publishing to K3. Every draft needs human approval. 150 tests and pip check pass; cached-MiniLM synthetic queue -> approve -> search run passes. Evidence: docs/VALIDATION_REVIEW.md. Score calibration, representative data, UI integration and review pending | 32% -> 43% (9.5/30 -> 13/30 pts) | Ilham MM (pending) |
| [#14](https://github.com/ilhamn3/Akatsukii-Sliit/pull/14) (K5; builds on #13) | 25 Nov 2026 | [WP3] Agent 4: entity resolution and graph writes | 3.5 (0->75%) | Deterministic machine/asset/hazard resolution from a curated vocabulary plus SafetyAgent rule keywords; unknown, ambiguous and conflicting labels stay blocked in review; approvals publish through traced GraphAgent with canonical IDs and raw text kept in provenance; shared asset and catalogue-hazard graph nodes; A3 dedup uses canonical machines. 183 tests and pip check pass; cached-MiniLM synthetic approve -> canonical store/graph run passes. Evidence: docs/ENTITY_GRAPH.md. Vocabulary check on team recordings/asset names and review pending | 43% -> 51% (13/30 -> 15.25/30 pts) | Ilham MM (pending) |
| [#15](https://github.com/ilhamn3/Akatsukii-Sliit/pull/15) (K6) | 25 Nov 2026 | [WP4] Agent 6: hybrid retrieval including failed attempts | 4.4 (0->75%), 4.5 (0->75%) | RetrievalEngine contract: K5 machine resolution and filter-before-rank, MiniLM/FAISS symptom cosine fused (support-only) with NetworkX traversal and alarm-code/asset hops; Evidence carries failed attempts, outcome, step-level evidence IDs, verification and provenance; RetrievalAgent node and proven Pipeline B wiring. 195 tests and pip check pass. The synthetic catalogue check in this PR was later withdrawn (its queries resembled the test set's easy tier); see the follow-up row. Evidence: docs/RETRIEVAL.md | 51% -> 61% (15.25/30 -> 18.25/30 pts) | Ilham MM (approved, merged) |
| [#21](https://github.com/ilhamn3/Akatsukii-Sliit/pull/21) (K3-K6 review follow-ups) | 25 Nov 2026 | [WP4] Review follow-ups: store caching, flat evidence metadata, dev-split retrieval evaluation | 4.1-4.3 (75->100% each), 4.4 (75->100%), 4.5 (75->100%) | Addresses Ilham's #12-#15 reviews. Revision-keyed projection cache: 2,000 writes in ~31 s instead of ~444 ms per write and rising; queries ~40-70 ms steady instead of 665 ms. Flat `verified`, `recorded_at` (+source), `root_cause`, `outcome_status` (A8's own rules), `kind`/`step_order` metadata, checked against A8's normalize_evidence. Dev-split-only evaluation (test split refused) over an oracle store: hybrid hit@1 20/20, single signals 19/20 (synthetic, not a benchmark). A4 note in Workflow.md. 230 tests pass with and without FACTORY_MEMORY_STORAGE. Done-when criteria met and #12/#15 approved | 61% -> 69% (18.25/30 -> 20.75/30 pts) | Ilham MM (approved, merged; asked 4.4 -> 90%, see next row) |
| [#22](https://github.com/ilhamn3/Akatsukii-Sliit/pull/22) (4.4 tracker correction) | 26 Nov 2026 | [WP4] Set 4.4 to 90% per #21 review | 4.4 (100->90%) | Reviewer correction: hybrid retrieval has only been evaluated on the synthetic oracle store, not yet through the real pipeline (Pipeline A ingestion -> verified store -> Pipeline B). Remaining 10%: retrieval evaluated on episodes ingested by the real pipeline. Progress recomputed with scripts/progress.py | 69% -> 68% (20.75/30 -> 20.45/30 pts) | Ilham MM (requested) |
| [#23](https://github.com/ilhamn3/Akatsukii-Sliit/pull/23) (K7; builds on #22) | 26 Nov 2026 | [WP3] Extraction P/R/F1 export for the benchmark harness | 3.7 (0->75%) | Code-driven field-level precision/recall/F1 (8 O1 fields): gold = what each synthetic narrative says (narrative_coverage); deterministic one-to-one token-Dice matching (0.5, plus exact), K5 machine resolution; narrative-to-capture adapter (new `text` media type); resumable Agent 2 runner on shared get_llm_client() that refuses FakeLLM; seeded dev/eval splits; human-form scoring; `extraction_metrics/v1` JSON + per-episode JSONL for the harness. 243 tests pass; oracle check F1 1.0 on 400 episodes. Development live run (Groq gpt-oss-120b, prompt v3, eval split of 50): micro F1 0.833 (P 0.848, R 0.818) at threshold 0.5, exact-match F1 0.606, 3 episodes failed A2's own checks and count as misses; outputs committed under project/evaluation/results/ for the harness. The run stops on a rejected key and never reports an all-failed run. Evidence: docs/EXTRACTION_METRICS.md. Harness ingestion, final team model and review pending | 68% -> 71% (20.45/30 -> 21.2/30 pts) | Ilham MM (pending) |
| [#24](https://github.com/ilhamn3/Akatsukii-Sliit/pull/24) (K8; builds on #23) | 26 Nov 2026 | [WP10] Retries, timeouts, fallbacks and pytest | 10.1 (0->100%), 10.2 (0->100%) | A2 makes one bounded corrective retry on invalid output (not on transport errors, which the shared client retries), configurable 1-5, flagged for reviewers; downstream agents skip with SKIPPED_UPSTREAM_FAILURE instead of cascading errors; A3 dedup failures (locked store, unavailable embeddings) become review warnings and the draft is still queued; A6 falls back to graph-only retrieval flagged `degraded`; concurrent store writers tested. Done-when met: a simulated API timeout runs through the real Pipeline A graph without crashing, failures are traced, and a fresh clean-checkout clone without FACTORY_MEMORY_STORAGE gives 249 passed, 3 skipped (FFmpeg media tests by design). 252 tests pass in the activated environment. Shared-client retry recommendation for core/llm.py documented. Evidence: docs/HARDENING.md | 71% -> 77% (21.2/30 -> 23.2/30 pts) | Ilham MM (pending) |
| [#27](https://github.com/ilhamn3/Akatsukii-Sliit/pull/27) (root-cause prompt; also lands #23 and #24 on main) | 27 Nov 2026 | [WP3] Extraction prompt v5 as default after dev-split comparison | 3.3 (50%, unchanged), 3.7 (75%, unchanged) | Prompt work on the 12 dev episodes only (eval and test splits untouched). v4: any wording presenting a cause as the finding counts as a stated root cause; hedged or unstated causes stay null. v5 adds clean values with exact quotes and keeps failed actions out of steps. Dev result, Groq gpt-oss-120b, 0 failures in both runs: root cause F1 0.957 -> 1.000, micro F1 0.887 -> 0.874 (single-item differences in steps, symptoms, tools and parts, documented). v5 is now the default; v3 stays selectable. Runner tooling: provider vs output failure labels, compare refuses incomplete runs, stop on rate limit, per-call token counts, optional `--wait-on-rate-limit`. 264 tests pass. No % change: 3.3 still needs team samples, and the reported v5 eval-split number is not run yet. Evidence: docs/EXTRACTION_METRICS.md | 77% -> 77% (23.2/30 pts) | Ilham MM (merged) |
| #TBD (v5 eval result) | 27 Nov 2026 | [WP3] Record the v5 eval-split extraction result | 3.3 (50%, unchanged), 3.7 (75%, unchanged) | The one eval-split run with v5 (Groq gpt-oss-120b, same 50 episodes as v3, no prompt change after it): micro F1 0.833 -> 0.897 (P 0.869, R 0.927), exact-match 0.606 -> 0.683, root cause 0.864 -> 0.990, 0 failures (v3 had 3). On the 47 episodes both runs completed, 0.864 -> 0.890, so most of the gain is the prompt and the rest is the K8 retry removing failures. Above the O1 target of 0.85, on synthetic data with a development provider. Outputs committed under project/evaluation/results/ (final row per episode only; superseded rate-limit rows held provider account details). Fix: `score` uses the last row per episode, as `run` does (re-scoring a log with superseded failures counted them twice); a test re-scores every committed results folder to its reported numbers. 283 tests pass. No % change: 3.3 still needs team samples, and 3.7 still needs harness ingestion, the team's final model and review. Evidence: docs/EXTRACTION_METRICS.md | 77% -> 77% (23.2/30 pts) | Ilham MM (pending) |

*Example row:* `| #4 | 22 Nov | [WP1] Repo scaffold and Pydantic schemas | 1.1 (0->100%), 1.2 (0->100%) | Folder structure, .gitignore, schemas | 0% -> 17% | Kavibarath S |` *(delete this example line if you copy it).*
