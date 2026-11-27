# K8: retries, timeouts, fallbacks and tests (ingestion and store)

Owner: Kavibarath S. Reviewer: Ilham MM. Branch: `wp10/hardening-tests`.
Subtasks 10.1 and 10.2. Stacked on K7 (#23), then #22.

The done-when criteria, and the evidence for each:

| Criterion | Evidence |
|---|---|
| A simulated API timeout does not crash the pipeline | `test_simulated_api_timeout_does_not_crash_pipeline_a` runs the real Pipeline A graph with a provider that times out. The graph completes; the root cause is the only error; A3 records `SKIPPED_UPSTREAM_FAILURE` |
| Failures are written to the trace log | Every failing agent writes `AGENT_ERROR` with the error type and message (BaseAgent). Store and queue failures add `STORE_ERROR` / `APPROVE_FAILED` audit events. K7 run failures are kept per episode in the predictions file |
| pytest passes on a clean checkout | A fresh `git clone` of this branch, with `FACTORY_MEMORY_STORAGE` unset and no caches or `.venv`, using the repo's Python 3.12 environment: **249 passed, 3 skipped**. The skips are the FFmpeg media tests, which skip by design when FFmpeg isn't on PATH; in the activated environment they run |

## What each module does on failure

| Module | Failure | Behaviour |
|---|---|---|
| A1 capture | FFmpeg/ffprobe missing or hanging | Clear `RuntimeError`; subprocess timeout 30 s; traced. Downstream agents record `SKIPPED_UPSTREAM_FAILURE` instead of a second error |
| Shared LLM client (`core/llm.py`, Ilham's) | Transport error / timeout | 3 attempts with backoff; 30 s (OpenAI-compatible) or 60 s (Gemini) timeout. A2 does **not** multiply these retries |
| A2 extraction | Invalid output: bad JSON, schema error, or a quote that doesn't match its segment | **One corrective retry** (2 calls in total, `EXTRACTION_MAX_ATTEMPTS`, 1-5). The retry sends the validator's own message back as data, with the same segments. A draft that only succeeded on retry gets the review reason `extraction_retried_after_invalid_output` and decision `EXTRACTION_DRAFT_CREATED_AFTER_2_ATTEMPTS`. After the last attempt the error is traced and no stale draft is left |
| A2 extraction | Upstream capture failed | `SKIPPED_UPSTREAM_FAILURE`; the model is not called |
| A3 validation | No draft because upstream failed | `SKIPPED_UPSTREAM_FAILURE` (a missing draft with no upstream error is still an error) |
| A3 validation | Store unreadable (e.g. SQLite locked) during the exact-duplicate check | Warning `store_dedup_unavailable`; the draft is still queued. Approval re-checks the store before publishing, so no duplicate can slip through |
| A3 validation | Embedding model or store unavailable during the semantic duplicate check | Warning `semantic_dedup_unavailable` with the error type; the draft is still queued for human review |
| Review queue | Store write fails during approval | Item stays `pending`; `APPROVE_FAILED` event with the error type; nothing is half-published |
| K3 store | Concurrent writers | SQLite `BEGIN IMMEDIATE` plus a 30 s busy timeout serialize writes. The revision counter and the audit log stay consistent; tested with 4 threads x 5 writes |
| A6 retrieval | Embedding model can't load or encode (offline, missing weights) | Falls back to graph-only ranking. Every result carries `retrieval.degraded: ["vector_unavailable:<Error>"]`. Scores stay <= the graph weight (0.5), so the Orchestrator tends to abstain |
| A6 retrieval | Store unavailable | The exception propagates; Ilham's `RetrievalNode` traces `RETRIEVAL_ERROR` and returns no evidence, so the Orchestrator abstains |
| K7 extraction runs | Rejected key (401) / every episode failing | The run stops at the first rejection; no metrics are written for an all-failed run; `--retry-errors` resumes |

## Recommendation for the shared LLM client (Ilham's file, not changed here)

`OpenAILLMClient._call_api_with_retry` retries **every** exception three times,
including non-retryable ones such as 401 (invalid key) and 400 (bad request). The
openai SDK also retries internally (its default `max_retries` is 2). In the K7 run with
a revoked key, this cost three calls per episode before failing. Suggestions:
- retry only timeouts, connection errors, 429 and 5xx;
- pass `max_retries=0` to the SDK so there is one retry policy;
- keep the existing 30 s timeout.

## Tests

`tests/ingestion/test_hardening.py` (9 tests) covers:
- the corrective retry and its feedback;
- bounded and configurable attempts;
- timeouts not multiplied by A2;
- a Pipeline A timeout, and a capture failure, each traced once while downstream agents skip;
- the normal pipeline still reaching the queue;
- dedup fallbacks keeping drafts reviewable;
- the graph-only retrieval fallback;
- concurrent store writers.

The ingestion and store suites (capture, extraction, validation, review queue, graph
agent, knowledge store, retrieval, extraction metrics, hardening) use real SQLite,
FAISS, NetworkX and LangGraph. They use only test clients and test embedders, so they
need no API key or model download.

## Not covered

- Whisper transcription of a very long recording has no wall-clock timeout; it runs
  locally and is bounded by file size.
- A failure to write the trace file itself (e.g. disk full) would still raise; the
  TraceLogger is shared code.
- `tests/ui` needs `streamlit`, which is not installed in this environment.
