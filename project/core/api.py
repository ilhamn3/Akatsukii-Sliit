"""Public UI / Service API for Factory Memory (unblocks WP6 Streamlit UI).

Wraps Pipeline B (`build_graph("B")`) and the K4 `ReviewQueue` with process-cached
graph/store instances, structured trace event retrieval, and explicit error objects
(never returning fake answers when the LLM or store is unavailable).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import uuid

from project.core.registry import build_graph, is_pipeline_stub
from project.core.state import create_initial_state
from project.core.trace import TraceLogger, read_trace

_GRAPH_CACHE: Dict[str, Any] = {}


def _get_cached_graph(pipeline: str = "B"):
    if pipeline not in _GRAPH_CACHE:
        _GRAPH_CACHE[pipeline] = build_graph(pipeline=pipeline)
    return _GRAPH_CACHE[pipeline]


def clear_api_caches() -> None:
    """Clear cached compiled graphs (useful in tests when env/config changes)."""
    _GRAPH_CACHE.clear()


def run_query(
    query: str,
    machine_type: Optional[str] = None,
    machine_id: Optional[str] = None,
    worker_id: str = "ui-user",
) -> Dict[str, Any]:
    """Execute Pipeline B for a troubleshooting query and return a UI-ready JSON dict.

    Never returns a fake answer when the LLM or store is unavailable; instead sets
    `error` to a structured error object and `final_answer` to None.
    """
    init_state = create_initial_state(
        query=query,
        machine_type=machine_type,
        machine_id=machine_id,
        worker_id=worker_id,
    )
    trace_id = init_state["trace_id"]
    is_fake_llm = os.getenv("FACTORY_FAKE_LLM") == "1"
    is_stub = is_pipeline_stub("B")

    try:
        graph = _get_cached_graph("B")
        final_state = graph.invoke(init_state)
    except Exception as exc:
        log_path = str(TraceLogger.get_instance().log_path)
        trace_events = [r for r in read_trace(log_path) if r.get("trace_id") == trace_id]
        return {
            "final_answer": None,
            "evidence": [],
            "trace_id": trace_id,
            "trace_events": trace_events,
            "refusal": None,
            "escalation_ticket_id": None,
            "clarification_needed": False,
            "clarification_question": None,
            "flags": {
                "fake_llm": is_fake_llm,
                "stub": is_stub,
                "llm_check": "unavailable",
            },
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
            },
        }

    log_path = str(TraceLogger.get_instance().log_path)
    trace_events = [r for r in read_trace(log_path) if r.get("trace_id") == trace_id]
    errors = list(final_state.get("errors") or [])

    # Surface fatal store/retrieval errors explicitly rather than disguising them
    if errors and not final_state.get("evidence") and not final_state.get("clarification_needed"):
        return {
            "final_answer": final_state.get("final_answer"),
            "evidence": [],
            "trace_id": trace_id,
            "trace_events": trace_events,
            "refusal": None,
            "escalation_ticket_id": (final_state.get("final_answer") or {}).get("escalation_ticket_id"),
            "clarification_needed": False,
            "clarification_question": None,
            "flags": {
                "fake_llm": is_fake_llm,
                "stub": is_stub,
                "llm_check": final_state.get("llm_check") or (final_state.get("metadata") or {}).get("llm_check"),
            },
            "error": {
                "type": "PipelineError",
                "message": "; ".join(errors),
            },
        }

    evidence_summary = [
        {
            "evidence_id": ev.get("evidence_id"),
            "episode_id": ev.get("episode_id"),
            "snippet": (ev.get("content") or "")[:240],
            "similarity_score": ev.get("similarity_score", 0.0),
        }
        for ev in (final_state.get("evidence") or [])
    ]

    final_ans = final_state.get("final_answer")
    llm_check = final_state.get("llm_check") or (final_state.get("metadata") or {}).get("llm_check") or "not_run"

    return {
        "final_answer": final_ans,
        "evidence": evidence_summary,
        "trace_id": trace_id,
        "trace_events": trace_events,
        "refusal": (final_ans or {}).get("refusal"),
        "escalation_ticket_id": (final_ans or {}).get("escalation_ticket_id"),
        "clarification_needed": bool(final_state.get("clarification_needed")),
        "clarification_question": final_state.get("clarification_question"),
        "flags": {
            "fake_llm": is_fake_llm,
            "stub": is_stub,
            "llm_check": llm_check,
        },
        "error": None,
    }


def _get_queue_and_store(
    queue_db_path: Optional[Path] = None,
    store_db_path: Optional[Path] = None,
):
    from project.store.knowledge_store import KnowledgeStore
    from project.store.review_queue import ReviewQueue

    store = KnowledgeStore(db_path=store_db_path)
    queue = ReviewQueue(db_path=queue_db_path, store=store)
    return queue, store


def list_pending_reviews(
    *,
    limit: int = 100,
    queue_db_path: Optional[Path] = None,
    store_db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """List pending review items from the K4 ReviewQueue."""
    queue, _ = _get_queue_and_store(queue_db_path=queue_db_path, store_db_path=store_db_path)
    items = queue.list_items(status="pending", limit=limit)
    return [item.model_dump(mode="json") for item in items]


def approve_review_item(
    item_id: str,
    *,
    reviewer: str,
    corrections: Optional[Dict[str, Any]] = None,
    note: Optional[str] = None,
    trace_id: Optional[str] = None,
    queue_db_path: Optional[Path] = None,
    store_db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Approve a pending review item as `reviewer`, publishing it to KnowledgeStore via A4."""
    tid = trace_id or f"TR-UI-APP-{uuid.uuid4().hex[:8].upper()}"
    queue, _ = _get_queue_and_store(queue_db_path=queue_db_path, store_db_path=store_db_path)
    episode = queue.approve(
        item_id,
        reviewer=reviewer,
        trace_id=tid,
        corrections=corrections,
        note=note,
    )
    return episode.model_dump(mode="json")


def reject_review_item(
    item_id: str,
    *,
    reviewer: str,
    reason: str,
    trace_id: Optional[str] = None,
    queue_db_path: Optional[Path] = None,
    store_db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Reject a pending review item as `reviewer` with a reason."""
    tid = trace_id or f"TR-UI-REJ-{uuid.uuid4().hex[:8].upper()}"
    queue, _ = _get_queue_and_store(queue_db_path=queue_db_path, store_db_path=store_db_path)
    item = queue.reject(item_id, reviewer=reviewer, reason=reason, trace_id=tid)
    return item.model_dump(mode="json")
