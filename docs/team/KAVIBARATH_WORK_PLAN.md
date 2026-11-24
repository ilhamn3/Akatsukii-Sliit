# Kavibarath S: Work Plan, Progress Tracker and PR Log

**Team Akatsukii · SLIIT · Innodata AI Catalyst '26 (University Track)**  
**Role:** Ingestion and knowledge-store lead · **Share:** 30% (30 of 100 planning points) · **PR reviewer:** Ilham MM  
**Repo:** https://github.com/ilhamn3/Akatsukii-Sliit  
**Deadlines:** MVP / project plan **29 Nov 2026, 11:59 PM** · Grand Finale **7 Dec 2026**

**What you own:** Capture and Transcription (1), Knowledge Extraction (2), Validation and Curation (3), Knowledge Graph (4), Retrieval (6), knowledge store, review-queue backend, hardening.

> **Rule for every PR:** update the **Done %** column in section 1 of this file, add a row to the **PR log** (section 7), run `python scripts/progress.py --write`, and commit the result **in the same PR**. Reviewer checks that the claimed % matches the evidence.

## 1. Progress tracker

<!-- PROGRESS:START -->
**Overall: 13 / 30 points · 43% complete**

| WP | Title | Weight | Earned | WP % |
|---|---|---:|---:|---:|
| WP3 | Ingestion agents 1-4 and review queue | 18 | 8.5 | 47% |
| WP4 | Knowledge store and retrieval | 10 | 4.5 | 45% |
| WP10 | Hardening and unit tests | 2 | 0 | 0% |
<!-- PROGRESS:END -->

**How to update:** edit only the **Done %** column below (0-100). Points earned = Pts x Done %. A subtask is 100% only when it meets its "done when" in section 3 and has been reviewed. Partial progress is fine (25 / 50 / 75%).

#### WP3 – Ingestion agents 1-4 and review queue (18 pts)

| ID | Subtask | Pts | Due | Done % |
|---|---|---|---|---|
| 3.1 | Agent 1: Whisper transcription (timestamped transcript) | 3 | 24 Nov | 75% |
| 3.2 | Agent 1: video frame sampling and unclear-audio flagging | 1 | 24 Nov | 75% |
| 3.3 | Agent 2: structured extraction (steps, tools, root cause, failed attempts, warnings) | 4 | 24 Nov | 50% |
| 3.4 | Agent 3: schema/consistency validation, dedup, confidence scoring | 4 | 26 Nov | 50% |
| 3.5 | Agent 4: entity resolution and graph writes | 3 | 26 Nov | 0% |
| 3.6 | Review-queue backend for low-confidence records | 2 | 26 Nov | 75% |
| 3.7 | Extraction precision / recall / F1 numbers exported to the harness | 1 | 27 Nov | 0% |

#### WP4 – Knowledge store and retrieval (10 pts)

| ID | Subtask | Pts | Due | Done % |
|---|---|---|---|---|
| 4.1 | SQLite tables for verified records | 2 | 26 Nov | 75% |
| 4.2 | Vector index (ChromaDB or FAISS) | 2 | 26 Nov | 75% |
| 4.3 | Knowledge graph (NetworkX) of machines, faults, causes, fixes, hazards | 2 | 26 Nov | 75% |
| 4.4 | Agent 6: hybrid vector + graph retrieval | 3 | 27 Nov | 0% |
| 4.5 | Retrieval returns failed attempts and outcomes | 1 | 27 Nov | 0% |

#### WP10 – Hardening and unit tests (2 pts)

| ID | Subtask | Pts | Due | Done % |
|---|---|---|---|---|
| 10.1 | Retries, timeouts and fallbacks in ingestion and store modules | 1 | 28 Nov | 0% |
| 10.2 | pytest for ingestion and store modules | 1 | 28 Nov | 0% |

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
| K4 (PR number pending; stacked on #12) | 25 Nov 2026 | [WP3] Agent 3: validation, dedup, confidence and review-queue backend | 3.4 (0->50%), 3.6 (0->75%) | Rule-based A3 (grounding, quantity/unit, consistency, exact and semantic dedup), uncalibrated transparent confidence, Pipeline A handoff, SQLite review queue with audited approve/reject and reviewer corrections, publishing to K3. Every draft needs human approval. 150 tests and pip check pass; cached-MiniLM synthetic queue -> approve -> search run passes. Evidence: docs/VALIDATION_REVIEW.md. Score calibration, representative data, UI integration and review pending | 32% -> 43% (9.5/30 -> 13/30 pts) | Ilham MM (pending) |

*Example row:* `| #4 | 22 Nov | [WP1] Repo scaffold and Pydantic schemas | 1.1 (0->100%), 1.2 (0->100%) | Folder structure, .gitignore, schemas | 0% -> 17% | Kavibarath S |` *(delete this example line if you copy it).*
