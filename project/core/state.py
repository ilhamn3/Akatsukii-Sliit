"""Shared blackboard state for Factory Memory multi-agent workflows.

Implements BlackboardState as specified in docs/Workflow.md Section 4,
providing typed contracts for LangGraph state transitions across Pipeline A
and Pipeline B.
"""

from typing import Any, Dict, List, Optional, TypedDict
import uuid


class BlackboardState(TypedDict, total=False):
    """LangGraph shared blackboard state across all specialized agents."""

    # Session & Query Context
    session_id: str
    trace_id: str
    worker_id: str
    machine_type: str
    machine_id: Optional[str]
    raw_query: str

    # Pipeline A capture handoff (validated by core.capture_schemas).
    capture_request: Dict[str, Any]
    capture_result: Optional[Dict[str, Any]]
    draft_record: Optional[Dict[str, Any]]
    # A3 output (core.validation_schemas); every item awaits human review.
    validation_report: Optional[Dict[str, Any]]
    review_item_id: Optional[str]
    # A4 (K5) approval-time output: canonical VerifiedEpisode and entity links.
    verified_episode: Optional[Dict[str, Any]]
    entity_links: List[Dict[str, Any]]

    # Diagnostic & Clarification State
    clarification_turns: int
    clarification_needed: bool
    clarification_question: Optional[str]

    # Diagnostic Hypotheses
    ranked_hypotheses: List[Dict[str, Any]]
    selected_hypothesis: Optional[Dict[str, Any]]

    # Retrieval State
    evidence: List[Dict[str, Any]]
    retrieved_episodes: List[Dict[str, Any]]
    graph_subgraph_nodes: List[str]
    failed_attempts_retrieved: List[str]
    failed_attempts_to_avoid: List[str]
    retrieval_confidence: float

    # Safety Verification & Veto State
    candidate_steps: List[Dict[str, Any]]
    safety_violations: List[str]
    safety_loop_count: int
    safety_approved: bool
    escalated_to_supervisor: bool

    # Conflict Resolution
    conflicts: List[Dict[str, Any]]

    # Synthesis & Delivery
    final_answer: Optional[Dict[str, Any]]
    abstained: bool
    escalated: bool
    abstention_reason: Optional[str]

    # Messaging, Errors & Observability
    messages: List[Dict[str, Any]]
    errors: List[str]
    audit_trace: List[Dict[str, Any]]
    metadata: Dict[str, Any]


def create_initial_state(
    query: str,
    machine_type: Optional[str] = None,
    machine_id: Optional[str] = None,
    worker_id: str = "worker-default",
    session_id: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> BlackboardState:
    """Instantiate a clean BlackboardState with required defaults."""
    sid = session_id or f"SES-{uuid.uuid4().hex[:8].upper()}"
    tid = trace_id or f"TR-{uuid.uuid4().hex[:8].upper()}"
    effective_machine = machine_type if machine_type else "unspecified"

    return {
        "session_id": sid,
        "trace_id": tid,
        "worker_id": worker_id,
        "machine_type": effective_machine,
        "machine_id": machine_id,
        "raw_query": query,
        "clarification_turns": 0,
        "clarification_needed": False,
        "clarification_question": None,
        "ranked_hypotheses": [],
        "selected_hypothesis": None,
        "evidence": [],
        "retrieved_episodes": [],
        "graph_subgraph_nodes": [],
        "failed_attempts_retrieved": [],
        "failed_attempts_to_avoid": [],
        "retrieval_confidence": 0.0,
        "candidate_steps": [],
        "safety_violations": [],
        "safety_loop_count": 0,
        "safety_approved": False,
        "escalated_to_supervisor": False,
        "conflicts": [],
        "final_answer": None,
        "abstained": False,
        "escalated": False,
        "abstention_reason": None,
        "messages": [],
        "errors": [],
        "audit_trace": [],
        "metadata": {},
    }
