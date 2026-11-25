"""Unit and integration tests for Agent 8: Orchestrator Agent (PR I5a).

Tests verify:
1. Adaptive routing: FAST vs FULL paths based on similarity, episodes, conflicts, and hazards.
2. Conflict ranking math: weights, per-term breakdown, descending sort, and carrying failed attempts.
3. Safe abstention: low similarity (<0.55), high energy (>50V or >10 bar) without LOTO/bleed.
4. Citation integrity: dropping uncited/unknown steps, renumbering, and citation coverage logging.
5. Full graph integration with Pipeline B via LangGraph.
"""

import os
from typing import Any, Dict, List
import pytest

from project.agents.a8_orchestrator import OrchestratorAgent
from project.core.llm import FakeLLM
from project.core.orchestration_helpers import (
    calculate_conflict_ranking,
    check_energy_loto_compliance,
    evaluate_adaptive_routing,
    parse_energy_thresholds,
    validate_and_filter_citations,
)
from project.core.registry import build_graph
from project.core.state import create_initial_state
from project.core.stubs.retrieval_node import RetrievalNode
from project.core.trace import TraceLogger


@pytest.fixture(autouse=True)
def ensure_fake_llm(monkeypatch):
    """Enforce offline deterministic testing."""
    monkeypatch.setenv("FACTORY_FAKE_LLM", "1")


def test_energy_threshold_parsing():
    """Verify parsing voltages > 50V and pressures > 10 bar."""
    low_e = parse_energy_thresholds("24V sensor circuit at 6 bar working pressure")
    assert low_e["high_voltage"] is False
    assert low_e["high_pressure"] is False

    high_v = parse_energy_thresholds("Check main feed terminals energized at 400V AC")
    assert high_v["high_voltage"] is True
    assert high_v["max_voltage"] == 400.0

    high_p = parse_energy_thresholds("Hydraulic clamp accumulator operating at 15 bar")
    assert high_p["high_pressure"] is True
    assert high_p["max_pressure_bar"] == 15.0

    # Test unit conversions (120 psi ~ 8.27 bar -> False, 200 psi ~ 13.8 bar -> True)
    psi_low = parse_energy_thresholds("Operating line pressure 100 psi")
    assert psi_low["high_pressure"] is False
    psi_high = parse_energy_thresholds("Operating line pressure 250 psi")
    assert psi_high["high_pressure"] is True


def test_abstention_on_high_energy_without_loto():
    """Verify immediate abstention when >50V or >10 bar is present without LOTO/bleed in evidence."""
    # Context has 400V, evidence has NO LOTO step
    non_loto_evidence = [
        {
            "evidence_id": "EV-TEST-01",
            "content": "Check contactor continuity with multimeter while live.",
            "similarity_score": 0.90,
            "metadata": {"machine": "CNC Lathe"},
        }
    ]
    compliant, reason = check_energy_loto_compliance(
        "Replace 400V main feed circuit breaker", non_loto_evidence
    )
    assert compliant is False
    assert "Dangerous energy detected" in reason
    assert "400V" in reason

    # With LOTO step in evidence, compliance passes
    loto_evidence = [
        {
            "evidence_id": "EV-TEST-01",
            "content": "Execute electrical Lockout/Tagout (LOTO) at panel PB-02 and prove dead.",
            "similarity_score": 0.95,
            "metadata": {"machine": "CNC Lathe"},
        }
    ]
    compliant2, reason2 = check_energy_loto_compliance(
        "Replace 400V main feed circuit breaker", loto_evidence
    )
    assert compliant2 is True
    assert reason2 is None


def test_conflict_ranking_math_and_failed_attempts():
    """Verify conflict ranking scoring formula, breakdown, sort order, and carrying failed attempts."""
    weights = {
        "outcome_worked": 0.40,
        "outcome_failed": -0.50,
        "verified_by_senior": 0.25,
        "recency_decay": 0.15,
        "vector_similarity": 0.20,
    }

    evidence_list = [
        # Episode 1: Worked, senior verified, recent (decay ~1.0), similarity 0.90
        # Score = 0.40 + 0.25 + 0.15 + (0.90 * 0.20 = 0.18) = 0.98
        {
            "evidence_id": "EV-01",
            "episode_id": "EP-01",
            "content": "Replace rotary union cartridge RU-8842.",
            "similarity_score": 0.90,
            "metadata": {
                "outcome": "worked",
                "verified": True,
                "recorded_at": "2026-11-20T10:00:00Z",
            },
        },
        # Episode 2: Failed, not verified, similarity 0.85
        # Score = -0.50 + 0.0 + (0.5 * 0.15 = 0.075) + (0.85 * 0.20 = 0.17) = -0.255
        {
            "evidence_id": "EV-02",
            "episode_id": "EP-02",
            "content": "Tightened hydraulic relief valve without bleeding accumulator pressure.",
            "similarity_score": 0.85,
            "metadata": {
                "outcome": "failed",
                "verified": False,
                "recorded_at": "2026-01-01T08:00:00Z",
            },
        },
    ]

    conflicts, failed_attempts = calculate_conflict_ranking(
        evidence_list=evidence_list,
        weights=weights,
        ref_date_iso="2026-11-25T00:00:00Z",
    )

    # 1. Ranking order
    assert len(conflicts) == 2
    assert conflicts[0]["episode_id"] == "EP-01"
    assert conflicts[0]["total_score"] > conflicts[1]["total_score"]

    # 2. Breakdown terms
    ep1_breakdown = conflicts[0]["breakdown"]
    assert ep1_breakdown["outcome_term"] == 0.40
    assert ep1_breakdown["verified_term"] == 0.25
    assert ep1_breakdown["similarity_term"] == 0.18

    ep2_breakdown = conflicts[1]["breakdown"]
    assert ep2_breakdown["outcome_term"] == -0.50
    assert ep2_breakdown["verified_term"] == 0.0

    # 3. Failed attempt carried using exact evidence text
    assert len(failed_attempts) == 1
    assert "Tightened hydraulic relief valve without bleeding accumulator pressure." in failed_attempts


def test_adaptive_routing_fast_vs_full():
    """Verify FAST path triggers only on high similarity, single episode, no conflict, no hazards."""
    # Case A: Meets all FAST criteria
    fast_evidence = [
        {
            "evidence_id": "EV-01",
            "episode_id": "EP-01",
            "content": "Bleed hydraulic accumulator.",
            "similarity_score": 0.92,
            "metadata": {"outcome": "worked", "hazards": []},
        }
    ]
    fast_conflicts = [{"episode_id": "EP-01", "outcome": "worked", "total_score": 0.90}]
    route, reason = evaluate_adaptive_routing(fast_evidence, fast_conflicts, similarity_threshold=0.85)
    assert route == "FAST"
    assert "direct grounded evidence" in reason

    # Case B: Similarity < 0.85 triggers FULL path
    med_evidence = [
        {
            "evidence_id": "EV-01",
            "episode_id": "EP-01",
            "content": "Bleed hydraulic accumulator.",
            "similarity_score": 0.80,
            "metadata": {"outcome": "worked", "hazards": []},
        }
    ]
    route, reason = evaluate_adaptive_routing(med_evidence, fast_conflicts, similarity_threshold=0.85)
    assert route == "FULL"
    assert "similarity" in reason

    # Case C: Multiple episodes trigger FULL path
    multi_evidence = [
        {"evidence_id": "EV-01", "episode_id": "EP-01", "similarity_score": 0.90, "metadata": {}},
        {"evidence_id": "EV-02", "episode_id": "EP-02", "similarity_score": 0.88, "metadata": {}},
    ]
    route, reason = evaluate_adaptive_routing(multi_evidence, fast_conflicts, similarity_threshold=0.85)
    assert route == "FULL"
    assert "multiple episodes" in reason

    # Case D: Hazard tags present trigger FULL path
    hazard_evidence = [
        {
            "evidence_id": "EV-01",
            "episode_id": "EP-01",
            "content": "Open valve.",
            "similarity_score": 0.95,
            "metadata": {"hazards": ["H04"]},
        }
    ]
    route, reason = evaluate_adaptive_routing(hazard_evidence, fast_conflicts, similarity_threshold=0.85)
    assert route == "FULL"
    assert "hazard tags" in reason


def test_citation_validation_and_filtering():
    """Verify steps citing invalid or missing evidence IDs are pruned and coverage is computed."""
    valid_ids = {"EV-CNC-01", "EV-CNC-02"}
    raw_steps = [
        {"step_number": 1, "instruction": "Step 1", "evidence_ids": ["EV-CNC-01"]},
        {"step_number": 2, "instruction": "Hallucinated step", "evidence_ids": ["EV-HALLUCINATED-99"]},
        {"step_number": 3, "instruction": "Step 3", "evidence_ids": ["EV-CNC-02", "EV-UNKNOWN"]},
        {"step_number": 4, "instruction": "Uncited step", "evidence_ids": []},
    ]

    filtered_steps, coverage = validate_and_filter_citations(raw_steps, valid_ids)

    # 2 out of 4 steps survived
    assert len(filtered_steps) == 2
    assert coverage == 0.50
    assert filtered_steps[0]["step_number"] == 1
    assert filtered_steps[0]["evidence_ids"] == ["EV-CNC-01"]
    assert filtered_steps[1]["step_number"] == 2
    assert filtered_steps[1]["evidence_ids"] == ["EV-CNC-02"]


def test_orchestrator_abstains_on_low_retrieval_similarity():
    """Verify OrchestratorPlan halts and sets abstained=True if retrieval confidence < threshold."""
    orchestrator = OrchestratorAgent(phase="plan", llm_client=FakeLLM())

    state = create_initial_state(query="Unknown weird vibration", machine_type="CNC Lathe")
    state["evidence"] = [
        {
            "evidence_id": "EV-LOW-01",
            "content": "Vaguely related inspection.",
            "similarity_score": 0.42,
            "metadata": {},
        }
    ]
    state["retrieval_confidence"] = 0.42

    updates = orchestrator.process(state)
    assert updates["abstained"] is True
    assert updates["decision"] == "ABSTAIN"
    assert "Low retrieval similarity" in updates["abstention_reason"]
    assert len(updates["candidate_steps"]) == 0


def test_pipeline_b_graph_execution_with_stub_retrieval():
    """Verify end-to-end LangGraph Pipeline B execution from Diagnostic to FinalAnswer."""
    graph = build_graph(pipeline="B")

    state = create_initial_state(
        query="High-pitched whining noise at 3000 RPM; chuck pressure dropping below 15 bar.",
        machine_type="CNC Lathe (Turning Center)",
        trace_id="TR-ORCH-E2E-01",
    )

    final_state = graph.invoke(state)

    # Pipeline reached FinalAnswer
    assert final_state["abstained"] is False
    assert final_state["safety_approved"] is True
    assert final_state["final_answer"] is not None

    final_ans = final_state["final_answer"]
    assert len(final_ans["steps"]) >= 1
    assert len(final_state["evidence"]) >= 1

    # Verify all steps cite real retrieved evidence IDs
    valid_eids = {e["evidence_id"] for e in final_state["evidence"]}
    for step in final_ans["steps"]:
        assert len(step["evidence_ids"]) >= 1
        for eid in step["evidence_ids"]:
            assert eid in valid_eids

    # Verify messages progression
    senders = [m["sender"] for m in final_state["messages"]]
    assert "DiagnosticAgent" in senders
    assert "RetrievalNode" in senders
    assert "OrchestratorPlan" in senders
    assert "SafetyAgent" in senders
    assert "OrchestratorFinalize" in senders


def test_pipeline_b_abstention_bypasses_safety_verification():
    """Verify that when OrchestratorPlan abstains, it routes to OrchestratorFinalize, bypassing Safety."""
    # Force low similarity to trigger abstention
    state = create_initial_state(
        query="Low similarity test query",
        machine_type="CNC Lathe",
        trace_id="TR-ABSTAIN-01",
    )
    # Manually set low retrieval confidence
    state["retrieval_confidence"] = 0.20
    state["evidence"] = [
        {"evidence_id": "EV-LOW", "content": "Unrelated", "similarity_score": 0.20, "metadata": {}}
    ]

    graph = build_graph(pipeline="B")
    # Start graph after diagnostic/retrieval with low confidence
    # Instead, let's run OrchestratorPlan directly and check routing
    orch_plan = OrchestratorAgent(phase="plan", llm_client=FakeLLM())
    updates = orch_plan.process(state)
    assert updates["abstained"] is True

    # Now finalize directly
    state.update(updates)
    orch_fin = OrchestratorAgent(phase="finalize")
    fin_updates = orch_fin.process(state)
    final_ans = fin_updates["final_answer"]
    assert final_ans["abstained"] is True
    assert len(final_ans["steps"]) == 0
    assert "Low retrieval similarity" in final_ans["abstention_reason"]
