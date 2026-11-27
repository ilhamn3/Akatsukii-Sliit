# Factory Memory — Streamlit Shop-Floor UI (`docs/UI.md`)

The Streamlit UI connects directly to the real multi-agent Pipeline B (`project.core.api.run_query`), the K4 `ReviewQueue` (`list_pending_reviews`, `approve_review_item`, `reject_review_item`), and the JSONL audit trace (`project.core.trace.read_trace`).

## First Run

1. Populate the knowledge store before starting the UI (an **empty store** causes Retrieval to return no evidence, so the assistant **abstains** on every troubleshooting query):

```bash
python scripts/load_expanded_store.py --size n400
```

2. Launch the Streamlit app:

```bash
streamlit run project/ui/app.py
```

- When `FACTORY_MEMORY_STORAGE` is unset, the store and review queue automatically use the repo-local `.factory_memory` fallback (`project/store/embeddings.py`).
- Store/graph initialization is process-cached via `@st.cache_resource`.
- No API keys or secrets are stored or displayed in the UI; keys are read from `.env` / environment variables by `project.core.llm`.

## Pages & Screenshots (Captured via Playwright Against Live App)

> Note: Chat screenshots below were captured with `FACTORY_FAKE_LLM=1` against a temporary `n400` store (`FACTORY_MEMORY_STORAGE`) so free-tier Gemini quota was not consumed; the `FAKE LLM ACTIVE` banner is visibly shown whenever `FACTORY_FAKE_LLM=1`.

### 1. Chat — Grounded Answer (`project/ui/chat_page.py`, `FAKE LLM`)
- Calls `project.core.api.run_query(query, machine_type)` with an elapsed-time spinner.
- Displays `diagnosed_cause`, ordered repair `steps` with `evidence_ids` citations, expandable **Evidence Panel** (`evidence_id`, `episode_id`, `similarity_score`, `verified_by`, `outcome`, `snippet`), `failed_attempts_to_avoid`, `safety_warnings`, and explicit `FAKE LLM` / `STUB` / `LLM safety oversight unavailable` banners.

![Chat Grounded Answer (FAKE LLM)](screenshots/chat_answer.png)

### 2. Chat — Refusal & Escalation (`project/ui/chat_page.py`, `FAKE LLM`)
- Shows `Safety Refusal` (`SR-X05`), `Abstained / Escalated` reason, and `escalation_ticket_id` when a hazardous shortcut is requested.

![Chat Abstain / Refusal (FAKE LLM)](screenshots/chat_abstain.png)

### 3. Technician Review Queue (`project/ui/pages/1_Review_Queue.py`)
- Lists pending `ReviewItem` drafts from `list_pending_reviews()` (shown here with a labelled test draft in temporary `ReviewQueue` storage), displaying extracted fields with verbatim `source_quotes`, `blocking`/`warning` validation issues, and **Approve & Publish** / **Reject** controls.

![Review Queue Page](screenshots/review_queue.png)

### 4. Audit Trace Viewer (`project/ui/pages/2_Trace_Viewer.py`)
- Defaults to the last query's `trace_id` and lists recent `trace_id`s from `logs/audit_trace.jsonl`.
- Displays all agent handoffs in order (`agent`, `event`, `decision`, `latency_ms`, `stub`, `error`) as a table with expandable `input_data` / `output_data` details.

![Trace Viewer Page](screenshots/trace_viewer.png)
