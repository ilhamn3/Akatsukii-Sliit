# Factory Memory — UI / Service Public API (`project/core/api.py`)

This module provides the stable public entry point for the Streamlit UI (WP6) and integration demos. It wraps the real LangGraph Pipeline B (`build_graph("B")`) and the K4 `ReviewQueue` with process-cached store/embedder instances.

---

## 1. Pipeline B Assistance Entry Point

### `run_query(query, machine_type=None, machine_id=None, worker_id="ui-user") -> dict`

Executes Pipeline B (`DiagnosticAgent -> RetrievalNode -> OrchestratorPlan -> SafetyAgent -> [OrchestratorRevise] -> OrchestratorFinalize`) and returns a JSON-serializable dictionary.

- **Never returns a fake answer** when the LLM or KnowledgeStore is unavailable; instead sets `error = {"type": "...", "message": "..."}` and `final_answer = None`.
- **Return Schema**:
  - `final_answer`: `FinalAnswer` dict (or `None` on fatal error)
  - `evidence`: list of `{evidence_id, episode_id, snippet, similarity_score}`
  - `trace_id`: correlation trace ID (e.g. `"TR-363DBBA0"`)
  - `trace_events`: list of audit trace records from `logs/audit_trace.jsonl` matching `trace_id`
  - `refusal`: refusal string if `SR-X05` triggered, else `None`
  - `escalation_ticket_id`: ticket ID in `logs/escalations.jsonl` if `escalated=True`, else `None`
  - `clarification_needed`: `bool`
  - `clarification_question`: `str | None`
  - `flags`: `{"fake_llm": bool, "stub": bool, "llm_check": str}`
  - `error`: `{"type": str, "message": str} | None`

### Real Output Example (`TR-363DBBA0`)

```json
{
  "final_answer": {
    "machine_type": "CNC Lathe",
    "symptoms": [
      "Spindle is running hot and throwing alarm ALM-410"
    ],
    "diagnosed_cause": "clogged filter in the spindle chiller circuit",
    "steps": [],
    "safety_warnings": [
      "SAFETY AMENDMENT: PROCEDURAL AMENDMENT REQUIRED: LLM: The proposed maintenance procedure describes servicing a coolant chiller rather than addressing the actual diagnosed fault of spindle bearing lubrication failure. Executing these steps will not resolve the bearing issue and may lead to prolonged equipment downtime or catastrophic spindle failure.",
      "Revise the procedure to address the actual diagnosed fault: spindle bearing lubrication failure/grease degradation.",
      "Ensure LOTO covers pneumatic and hydraulic energy sources in addition to electrical isolation if applicable to the spindle lubrication system.",
      "Include steps for safely accessing the spindle housing, inspecting bearings, purging old grease, and applying approved high-temperature spindle grease."
    ],
    "failed_attempts_to_avoid": [],
    "abstained": true,
    "escalated": true,
    "abstention_reason": "Safety verification did not approve the candidate procedure.",
    "refusal": null,
    "alternative_hypotheses": [
      {
        "rank": 1,
        "fault": "Spindle bearing lubrication failure or grease degradation",
        "subsystem": "Spindle Bearing Assembly",
        "confidence": 0.85,
        "rationale": "Excessive friction due to insufficient lubrication or bearing wear commonly leads to high spindle temperatures and correlates with thermal overload alarm ALM-410."
      },
      {
        "rank": 2,
        "fault": "Spindle chiller cooling loop blockage or coolant pump failure",
        "subsystem": "Spindle Thermal Control Unit",
        "confidence": 0.75,
        "rationale": "Inadequate coolant flow or pump malfunction prevents proper heat dissipation from the spindle cartridge, resulting in rapid thermal rise and drive protection trips."
      },
      {
        "rank": 3,
        "fault": "Spindle motor encoder feedback drift or thermal sensor fault",
        "subsystem": "Spindle Drive and Feedback",
        "confidence": 0.6,
        "rationale": "A failing thermal sensor or motor feedback issue can falsely trigger an overheat protection alarm (ALM-410) even if mechanical temperatures are within normal operating limits."
      }
    ],
    "escalation_ticket_id": "ESC-363DBBA0",
    "trace_id": "TR-363DBBA0"
  },
  "evidence": [
    {
      "evidence_id": "EV-CNC-0027",
      "episode_id": "EP-0027",
      "snippet": "Verified repair EP-0027 on cnc_lathe (asset LATHE-03).\nSymptoms: spindle housing too hot to touch; thermal alarm after about two hours running; slight whine from the spindle; alarm ALM-410.\nRoot cause: clogged filter in the spindle chiller ",
      "similarity_score": 0.90971
    },
    {
      "evidence_id": "EV-CNC-0089",
      "episode_id": "EP-0089",
      "snippet": "Verified repair EP-0089 on cnc_lathe (asset LATHE-01).\nSymptoms: spindle housing too hot to touch; thermal alarm after about two hours running; slight whine from the spindle; alarm ALM-410.\nRoot cause: clogged filter in the spindle chiller ",
      "similarity_score": 0.90971
    },
    {
      "evidence_id": "EV-CNC-0134",
      "episode_id": "EP-0134",
      "snippet": "Verified repair EP-0134 on cnc_lathe (asset LATHE-02).\nSymptoms: spindle housing too hot to touch; thermal alarm after about two hours running; slight whine from the spindle; alarm ALM-410.\nRoot cause: clogged filter in the spindle chiller ",
      "similarity_score": 0.90971
    }
  ],
  "trace_id": "TR-363DBBA0",
  "trace_events": [
    {
      "ts": "2026-11-26T01:48:32.906605+00:00",
      "trace_id": "TR-363DBBA0",
      "session_id": "SES-8F83D126",
      "agent": "DiagnosticAgent",
      "stub": false,
      "event": "AGENT_START"
    }
  ],
  "refusal": null,
  "escalation_ticket_id": "ESC-363DBBA0",
  "clarification_needed": false,
  "clarification_question": null,
  "flags": {
    "fake_llm": false,
    "stub": false,
    "llm_check": "performed"
  },
  "error": null
}
```

---

## 2. Review Queue Helpers (HITL Checkpoint 1)

All helpers wrap `project.store.review_queue.ReviewQueue` and `KnowledgeStore`:

- `list_pending_reviews(*, limit=100, queue_db_path=None, store_db_path=None) -> list[dict]`
  Returns pending `ReviewItem` dicts ordered by priority (`low_confidence` first) then creation time.
- `approve_review_item(item_id, *, reviewer, corrections=None, note=None, trace_id=None, queue_db_path=None, store_db_path=None) -> dict`
  Approves a pending item as `reviewer`, runs `verify()`, publishes via `GraphAgent (A4)` into `KnowledgeStore`, and returns the `VerifiedEpisode` dict.
- `reject_review_item(item_id, *, reviewer, reason, trace_id=None, queue_db_path=None, store_db_path=None) -> dict`
  Marks a pending item as `rejected` with `reviewer` and `reason`, returning the updated `ReviewItem` dict.
