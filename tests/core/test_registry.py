"""Unit tests for Factory Memory Agent Registry and dynamic graph construction."""

import copy
from pathlib import Path
import pytest
from project.core.registry import build_graph, load_config
from project.core.state import create_initial_state
from project.core.trace import TraceLogger, read_trace


def test_load_default_config():
    """Verify loading default config.yaml."""
    cfg = load_config()
    assert "pipelines" in cfg
    assert "B" in cfg["pipelines"]
    assert "system" in cfg
    assert cfg["system"]["safety_max_revision_loops"] == 2


def test_build_and_run_two_agent_graph(tmp_path):
    """Verify that a two-agent dummy graph executes sequentially and logs trace lines."""
    log_file = tmp_path / "test_trace.jsonl"
    logger = TraceLogger.get_instance(str(log_file))

    initial_state = create_initial_state(
        query="Spindle whining noise",
        machine_type="CNC Lathe",
        trace_id="TR-GRAPH-001",
    )

    graph = build_graph(pipeline="B")
    final_state = graph.invoke(initial_state)

    # Verify state progression across both dummy agents
    assert len(final_state["ranked_hypotheses"]) > 0
    assert final_state["safety_approved"] is True
    assert final_state["final_answer"] is not None
    assert final_state["final_answer"]["diagnosed_cause"] is not None

    # Verify agent messages
    assert len(final_state["messages"]) >= 2
    senders = [m["sender"] for m in final_state["messages"]]
    assert "DummyDiagnosticAgent" in senders
    assert "DummySafetyAgent" in senders


def test_add_third_agent_via_config_only(tmp_path):
    """Verify that a third agent can be added strictly by config without editing graph code."""
    base_cfg = load_config()
    custom_cfg = copy.deepcopy(base_cfg)

    # Add third agent to pipeline B configuration
    audit_agent_entry = {
        "name": "DummyAuditAgent",
        "role": "Post-processing audit verification",
        "pipeline": "B",
        "module": "project.core.stubs.dummy_agents",
        "class": "DummyAuditAgent",
        "enabled": True,
        "order": 3,
    }
    custom_cfg["pipelines"]["B"]["agents"].append(audit_agent_entry)

    # Rewire edges: Diagnostic -> Safety -> Audit -> END
    custom_cfg["pipelines"]["B"]["edges"] = [
        {"from": "DummyDiagnosticAgent", "to": "DummySafetyAgent"},
        {"from": "DummySafetyAgent", "to": "DummyAuditAgent"},
        {"from": "DummyAuditAgent", "to": "__end__"},
    ]

    # Build graph with custom config override (zero graph code modification)
    graph = build_graph(pipeline="B", config_override=custom_cfg)

    initial_state = create_initial_state(
        query="High temperature alarm",
        machine_type="Injection-Moulding",
        trace_id="TR-3AGENT-001",
    )
    final_state = graph.invoke(initial_state)

    # Verify that the third agent ran and mutated the state
    assert final_state.get("metadata", {}).get("audit_verified") is True
    senders = [m["sender"] for m in final_state["messages"]]
    assert "DummyAuditAgent" in senders


def test_stub_agent_trace_flag_and_pipeline_detection(tmp_path):
    """Verify that stub agents are detected and write 'stub': true to trace logs."""
    from project.core.registry import is_pipeline_stub
    from project.core.stubs.dummy_agents import DummyDiagnosticAgent, DummySafetyAgent

    diag = DummyDiagnosticAgent()
    safety = DummySafetyAgent()
    assert diag.is_stub is True, "DummyDiagnosticAgent must have is_stub=True"
    assert safety.is_stub is True, "DummySafetyAgent must have is_stub=True"

    # Verify pipeline detection
    assert is_pipeline_stub("B") is True, "Pipeline B with dummy agents must be flagged as stub"

    # Test trace log entries contain 'stub': True
    log_file = tmp_path / "stub_trace.jsonl"
    logger = TraceLogger(str(log_file))
    diag.trace_logger = logger

    state = create_initial_state(query="Stub test query", machine_type=None)
    assert state["machine_type"] == "unspecified", "Unspecified machine must be handled cleanly"

    # Run agent and inspect logged record
    state = diag.run(state)
    records = read_trace(str(log_file))
    assert len(records) >= 2  # START and COMPLETED
    for r in records:
        assert r.get("stub") is True, "Trace records emitted by stub agents must contain 'stub': True"
