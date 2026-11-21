"""Unit tests for Factory Memory BlackboardState."""

import pytest
from project.core.state import BlackboardState, create_initial_state


def test_create_initial_state_defaults():
    """Verify default initialization of BlackboardState."""
    state = create_initial_state(
        query="Hydraulic ram leaking oil at rod seal",
    )

    assert state["raw_query"] == "Hydraulic ram leaking oil at rod seal"
    assert state["machine_type"] == "unspecified"
    assert state["machine_id"] is None
    assert state["session_id"].startswith("SES-")
    assert state["trace_id"].startswith("TR-")
    assert state["clarification_needed"] is False
    assert state["safety_approved"] is False
    assert state["safety_loop_count"] == 0
    assert state["abstained"] is False
    assert state["escalated"] is False
    assert isinstance(state["ranked_hypotheses"], list)
    assert isinstance(state["messages"], list)
    assert isinstance(state["errors"], list)
    assert isinstance(state["audit_trace"], list)


def test_create_initial_state_custom_ids():
    """Verify custom session and trace IDs are preserved."""
    state = create_initial_state(
        query="Compressor overheating trip",
        machine_type="Air Compressor",
        machine_id="CMP-09",
        session_id="SES-CUSTOM-123",
        trace_id="TR-CUSTOM-999",
        worker_id="tech-bob",
    )

    assert state["session_id"] == "SES-CUSTOM-123"
    assert state["trace_id"] == "TR-CUSTOM-999"
    assert state["machine_id"] == "CMP-09"
    assert state["worker_id"] == "tech-bob"
