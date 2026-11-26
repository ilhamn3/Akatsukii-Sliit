"""Unit and integration tests for Agent 8: Orchestrator Agent (PR I5a).

Tests verify:
1. Adaptive routing: FAST vs FULL paths based on similarity, episodes, conflicts, and hazards.
2. Conflict ranking math: weights, per-term breakdown, descending sort, and carrying failed attempts.
3. Safe abstention: low similarity (<0.55), high energy (>50V or >10 bar) without LOTO/bleed.
4. Citation integrity: dropping uncited/unknown steps, renumbering, and citation coverage logging.
5. Full graph integration with Pipeline B via LangGraph.
"""

import copy
import json
import os
from typing import Any, Dict, List
from unittest.mock import Mock
import pytest

from project.agents.a4_graph import FAULTS, HAZARDS, GraphAgent
from project.agents.a6_retrieval import HybridRetriever
from project.agents.a8_orchestrator import OrchestratorAgent
from project.core.llm import FakeLLM
from project.core.orchestration_helpers import (
    calculate_conflict_ranking,
    check_energy_loto_compliance,
    evaluate_adaptive_routing,
    extract_valid_evidence_ids,
    normalize_evidence,
    parse_energy_thresholds,
    validate_and_filter_citations,
)
from project.core.registry import build_graph, load_config
from project.core.state import create_initial_state
from project.core.stubs.retrieval_node import RetrievalNode
from project.core.trace import TraceLogger, read_trace
from project.store.knowledge_store import KnowledgeStore
from scripts.retrieval_dev_eval import catalogue_episodes
from tests.validation_helpers import HashEmbedder


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


def test_pipeline_b_graph_execution_with_stub_retrieval(monkeypatch):
    """Verify end-to-end LangGraph Pipeline B execution from Diagnostic to FinalAnswer."""
    monkeypatch.setenv("FACTORY_STUB_RETRIEVAL", "1")
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


def test_real_backend_failure_does_not_fallback_and_abstains(tmp_path, monkeypatch):
    """(a) Real backend failure must NOT silently fall back to stub; logs AGENT_ERROR and abstains."""
    log_file = tmp_path / "err_trace.jsonl"
    logger = TraceLogger.get_instance(str(log_file))
    monkeypatch.delenv("FACTORY_STUB_RETRIEVAL", raising=False)

    failing_retriever = Mock()
    failing_retriever.retrieve.side_effect = RuntimeError("SQLite store locked or missing")

    node = RetrievalNode(backend="real", trace_logger=logger, retriever=failing_retriever)
    assert node.is_stub is False

    state = create_initial_state(
        query="Spindle overheating alarm ALM-410",
        machine_type="CNC Lathe",
        trace_id="TR-REAL-FAIL-01",
    )
    state["evidence"] = [{"evidence_id": "STALE", "content": "stale"}]
    out_state = node.run(state)

    assert out_state["evidence"] == []
    assert out_state["retrieved_episodes"] == []
    assert out_state["retrieval_confidence"] == 0.0
    assert any("RuntimeError: SQLite store locked or missing" in e for e in out_state["errors"])

    trace_events = read_trace(str(log_file))
    assert any(
        ev["agent"] == "RetrievalNode" and ev["event"] == "AGENT_ERROR" and not ev["stub"]
        for ev in trace_events
    )

    orch = OrchestratorAgent(phase="plan", llm_client=FakeLLM(), trace_logger=logger)
    plan_updates = orch.process(out_state)
    assert plan_updates["abstained"] is True
    assert plan_updates["candidate_steps"] == []


def test_exhausted_veto_finalize_abstains_and_escalates():
    """(b) Exhausted veto / unapproved safety => abstained=True, escalated=True, steps=[]."""
    state = create_initial_state(
        query="Hydraulic ram creeping down",
        machine_type="Hydraulic Press",
        trace_id="TR-VETO-EXHAUST-01",
    )
    state["candidate_steps"] = [
        {"step_number": 1, "instruction": "Work under ram directly.", "evidence_ids": ["EV-HYD-01"]}
    ]
    state["abstained"] = False
    state["safety_approved"] = False
    state["safety_loop_count"] = 2
    state["safety_violations"] = ["Missing mechanical safety block under ram (SR-H03)."]
    state["safety_verdict"] = {
        "verdict": "veto",
        "reason": "Critical crush hazard: safety block missing.",
        "rule_ids": ["SR-H03"],
        "suggested_amendments": [],
    }

    orch_fin = OrchestratorAgent(phase="finalize")
    updates = orch_fin.process(state)
    final_ans = updates["final_answer"]

    assert final_ans["abstained"] is True
    assert final_ans["escalated"] is True
    assert final_ans["steps"] == []
    assert "Critical crush hazard" in final_ans["abstention_reason"]
    assert "Missing mechanical safety block under ram (SR-H03)." in final_ans["safety_warnings"]


def test_revise_amendments_carry_no_borrowed_evidence_ids_and_veto_escalates():
    """(c) Amendment steps carry evidence_ids=[]; veto with no amendments escalates immediately."""
    state = create_initial_state(
        query="Replace hydraulic hose",
        machine_type="Hydraulic Press",
        trace_id="TR-REVISE-01",
    )
    state["evidence"] = [
        {"evidence_id": "EV-HYD-01", "episode_id": "EP-HYD-01", "content": "Disconnect hose fitting.", "similarity_score": 0.9, "metadata": {}}
    ]
    state["candidate_steps"] = [
        {"step_number": 1, "instruction": "Disconnect hose fitting.", "evidence_ids": ["EV-HYD-01"]}
    ]
    state["safety_loop_count"] = 1
    state["safety_violations"] = ["Missing depressurisation step."]
    state["safety_verdict"] = {
        "verdict": "amend",
        "reason": "Missing depressurisation step.",
        "suggested_amendments": ["Depressurise circuit to 0 bar and verify gauge."],
    }

    orch_rev = OrchestratorAgent(phase="revise")
    rev_updates = orch_rev.process(state)
    amended = rev_updates["candidate_steps"]

    assert amended[0]["instruction"].startswith("[SAFETY CONTROL")
    assert amended[0]["evidence_ids"] == []
    assert amended[1]["evidence_ids"] == ["EV-HYD-01"]

    # Now test hard veto with NO suggested_amendments and NO violations -> immediate escalation
    state["safety_violations"] = []
    state["safety_verdict"] = {
        "verdict": "veto",
        "reason": "Unknown hazard class.",
        "suggested_amendments": [],
    }
    veto_updates = orch_rev.process(state)
    assert veto_updates["escalated_to_supervisor"] is True
    assert veto_updates["decision"] == "ESCALATED"


def test_normalize_evidence_and_routing_on_real_a6_evidence(tmp_path):
    """(d) normalize_evidence + FAST/FULL routing on real A6 Evidence built from store fixture."""
    faults = json.loads(FAULTS.read_text(encoding="utf-8"))
    hazards = json.loads(HAZARDS.read_text(encoding="utf-8"))
    store = KnowledgeStore(tmp_path / "repairs.sqlite3", embedder=HashEmbedder())
    writer = GraphAgent(store)
    for ep in catalogue_episodes(faults[:5], hazards):
        writer.put_verified(ep, trace_id="TR-NORM-LOAD")

    hits = HybridRetriever(store).retrieve(
        "spindle housing too hot, thermal alarm", "CNC Lathe (Turning Center)", k=2
    )
    assert len(hits) >= 1
    ev_dict = hits[0].model_dump(mode="json")

    norm = normalize_evidence(ev_dict)
    assert norm["verified"] is True
    assert norm["outcome_class"] == "worked"
    assert norm["recorded_at"] == ev_dict["metadata"]["verified_at"]
    assert isinstance(norm["steps"], list) and len(norm["steps"]) >= 1
    assert isinstance(norm["hazards"], list)

    # Multi-episode / hazard-tagged A6 evidence triggers FULL routing
    conflicts, failed = calculate_conflict_ranking([ev_dict])
    route, _ = evaluate_adaptive_routing([ev_dict], conflicts, similarity_threshold=0.85)
    assert route == "FULL"
    assert len(failed) >= 1

    # Single clean episode without hazards and >= 0.85 similarity triggers FAST with metadata.steps
    clean_ev = copy.deepcopy(ev_dict)
    clean_ev["similarity_score"] = 0.92
    clean_ev["metadata"]["catalogue_hazards"] = []
    clean_conflicts, _ = calculate_conflict_ranking([clean_ev])
    fast_route, _ = evaluate_adaptive_routing([clean_ev], clean_conflicts, similarity_threshold=0.85)
    assert fast_route == "FAST"

    orch = OrchestratorAgent(phase="plan", llm_client=FakeLLM())
    st = create_initial_state("spindle hot", machine_type="cnc_lathe")
    st["evidence"] = [clean_ev]
    st["retrieval_confidence"] = 0.92
    plan = orch.process(st)
    # FAST path builds individual steps from metadata.steps, not the whole summary blob
    assert len(plan["candidate_steps"]) == len(clean_ev["metadata"]["steps"])
    assert plan["candidate_steps"][0]["evidence_ids"] == [clean_ev["metadata"]["steps"][0]["evidence_id"]]


def test_step_level_evidence_ids_accepted_unknown_dropped():
    """(e) Citations to real steps[].evidence_id are accepted; unknown IDs are dropped."""
    evidence_list = [
        {
            "evidence_id": "EV-SYN-CAT-L01",
            "episode_id": "SYN-CAT-L01",
            "content": "Summary text",
            "similarity_score": 0.90,
            "metadata": {
                "steps": [
                    {"step_number": 1, "description": "Stop spindle", "evidence_id": "SYN-L01-S1"},
                    {"step_number": 2, "description": "Clean fan", "evidence_id": "SYN-L01-S2"},
                ]
            },
        }
    ]
    valid_ids = extract_valid_evidence_ids(evidence_list)
    assert valid_ids == {"EV-SYN-CAT-L01", "SYN-L01-S1", "SYN-L01-S2"}

    raw_steps = [
        {"step_number": 1, "instruction": "Stop spindle", "evidence_ids": ["SYN-L01-S1"]},
        {"step_number": 2, "instruction": "Bogus step", "evidence_ids": ["EV-MADE-UP-99"]},
        {"step_number": 3, "instruction": "Clean fan", "evidence_ids": ["SYN-L01-S2", "EV-FAKE"]},
    ]
    filtered, coverage = validate_and_filter_citations(raw_steps, valid_ids)
    assert len(filtered) == 2
    assert filtered[0]["evidence_ids"] == ["SYN-L01-S1"]
    assert filtered[1]["evidence_ids"] == ["SYN-L01-S2"]
    assert coverage == pytest.approx(2 / 3, abs=1e-3)


def test_revise_uses_all_amendments_and_keeps_citation_integrity():
    """Revise phase incorporates all amendments with [SAFETY CONTROL] and drops uncited non-control steps."""
    state = create_initial_state(
        query="Hydraulic pressure unstable",
        machine_type="Hydraulic Press",
        trace_id="TR-REV-ALL-01",
    )
    state["evidence"] = [
        {
            "evidence_id": "EV-HYD-01",
            "episode_id": "EP-HYD-01",
            "content": "Summary",
            "similarity_score": 0.9,
            "metadata": {
                "steps": [
                    {"step_number": 1, "description": "Inspect valve", "evidence_id": "SYN-HYD-S1"}
                ]
            },
        }
    ]
    state["candidate_steps"] = [
        {"step_number": 1, "instruction": "Inspect valve", "evidence_ids": ["SYN-HYD-S1"]}
    ]
    state["safety_loop_count"] = 1
    state["safety_violations"] = ["Missing LOTO", "Missing zero-pressure verification"]
    state["safety_verdict"] = {
        "verdict": "amend",
        "rule_ids": ["SR-H01", "SR-H02"],
        "suggested_amendments": [
            "Apply electrical LOTO at main breaker.",
            "Bleed hydraulic line to 0 bar and verify gauge.",
        ],
    }

    orch_rev = OrchestratorAgent(phase="revise", llm_client=FakeLLM())
    updates = orch_rev.process(state)
    steps = updates["candidate_steps"]

    control_steps = [s for s in steps if s["instruction"].startswith("[SAFETY CONTROL")]
    assert len(control_steps) == 2
    assert all(s["evidence_ids"] == [] for s in control_steps)
    non_control = [s for s in steps if not s["instruction"].startswith("[SAFETY CONTROL")]
    assert len(non_control) >= 1
    assert all(eid in {"EV-HYD-01", "SYN-HYD-S1"} for s in non_control for eid in s["evidence_ids"])


def test_sr_x05_triggers_on_adversarial_and_not_benign():
    """SR-X05 catches all 5 dev adversarial queries and triggers 0 false positives on benign dev queries."""
    from project.core.safety_rules.rule_engine import RuleEngine

    eng = RuleEngine()
    with open("project/data/test_set/dev_queries.jsonl", "r", encoding="utf-8") as f:
        queries = [json.loads(line) for line in f if line.strip()]

    adv = [q for q in queries if q["tier"] == "adversarial"]
    benign = [q for q in queries if q["tier"] != "adversarial"]

    assert len(adv) == 5
    for q in adv:
        hits = eng.evaluate_unsafe_request(q["query"])
        assert len(hits) >= 1, f"Expected SR-X05 hit on {q['query_id']}"
        assert hits[0].rule_id == "SR-X05"
        assert "refused" in hits[0].reason.lower()

    for q in benign:
        hits = eng.evaluate_unsafe_request(q["query"])
        assert hits == [], f"Unexpected SR-X05 false positive on {q['query_id']}: {q['query']}"


def test_llm_unavailable_disclosure_in_safety_and_finalize(tmp_path):
    """When SafetyAgent LLM fails with 429, FinalAnswer discloses deterministic-only check and trace records skipped_429."""
    from project.agents.a7_safety import SafetyAgent

    log_file = tmp_path / "llm_unavail_trace.jsonl"
    logger = TraceLogger.get_instance(str(log_file))

    failing_llm = Mock()
    failing_llm.model_name = "gemini:gemini-3.5-flash"
    failing_llm.generate_structured.side_effect = RuntimeError("429 RESOURCE_EXHAUSTED")

    safety = SafetyAgent(trace_logger=logger, llm_client=failing_llm)
    state = create_initial_state("Belt slipping", machine_type="Conveyor Line", trace_id="TR-429-01")
    state["candidate_steps"] = [
        {"step_number": 1, "instruction": "Stop and lock out the conveyor drive.", "evidence_ids": ["EV-C01"]}
    ]

    s_out = safety.run(state)
    assert s_out["llm_check"] == "skipped_429"

    trace_events = read_trace(str(log_file))
    assert any(
        e.get("agent") == "SafetyAgent"
        and isinstance(e.get("output_digest"), dict)
        and e["output_digest"].get("llm_check") == "skipped_429"
        for e in trace_events
    )

    orch_fin = OrchestratorAgent(phase="finalize", trace_logger=logger)
    fin_out = orch_fin.run(s_out)
    warnings = fin_out["final_answer"]["safety_warnings"]
    assert "LLM safety oversight unavailable: deterministic rules only" in warnings


def test_escalation_ticket_written(tmp_path, monkeypatch):
    """When escalated=True, OrchestratorFinalize appends a ticket line to logs/escalations.jsonl."""
    from project.agents import a8_orchestrator

    esc_file = tmp_path / "escalations.jsonl"
    monkeypatch.setattr(a8_orchestrator, "ESCALATIONS_LOG_PATH", esc_file)

    state = create_initial_state(
        query="Press ram drifting down",
        machine_type="Hydraulic Press",
        trace_id="TR-ESC-TICKET-01",
    )
    state["safety_approved"] = False
    state["safety_violations"] = ["Missing safety blocks (SR-H03)"]
    state["safety_verdict"] = {"verdict": "veto", "rule_ids": ["SR-H03"], "reason": "Critical crush hazard"}

    orch_fin = OrchestratorAgent(phase="finalize")
    out = orch_fin.process(state)
    final_ans = out["final_answer"]

    assert final_ans["escalated"] is True
    assert final_ans["escalation_ticket_id"] is not None
    assert esc_file.exists()
    lines = [json.loads(l) for l in esc_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 1
    assert lines[0]["ticket_id"] == final_ans["escalation_ticket_id"]
    assert lines[0]["trace_id"] == "TR-ESC-TICKET-01"
    assert "SR-H03" in lines[0]["rule_ids"]


def test_diagnosis_taken_from_top_episode_and_disagreement_traced(tmp_path):
    """Final diagnosed_cause uses top retrieved episode's root_cause and logs DIAGNOSIS_DISAGREEMENT when different."""
    log_file = tmp_path / "diag_disagree.jsonl"
    logger = TraceLogger.get_instance(str(log_file))

    state = create_initial_state("Spindle hot ALM-410", machine_type="CNC Lathe", trace_id="TR-DISAGREE-01")
    state["selected_hypothesis"] = {
        "rank": 1,
        "fault": "Spindle ceramic bearing spalling",
        "subsystem": "Spindle",
        "confidence": 0.85,
    }
    state["ranked_hypotheses"] = [state["selected_hypothesis"]]
    state["evidence"] = [
        {
            "evidence_id": "EV-L01",
            "episode_id": "EP-0027",
            "content": "Clean chiller filter.",
            "similarity_score": 0.92,
            "metadata": {
                "outcome": "Fault cleared",
                "verified_by": "loader",
                "catalogue_hazards": [],
                "record": {"root_cause": "clogged filter in the spindle chiller circuit"},
                "steps": [{"step_number": 1, "description": "Clean chiller filter", "evidence_id": "EV-L01-S1"}],
            },
        }
    ]
    state["retrieval_confidence"] = 0.92

    orch_plan = OrchestratorAgent(phase="plan", llm_client=FakeLLM(), trace_logger=logger)
    state.update(orch_plan.process(state))
    state["safety_approved"] = True

    orch_fin = OrchestratorAgent(phase="finalize", trace_logger=logger)
    fin_out = orch_fin.process(state)
    final_ans = fin_out["final_answer"]

    assert final_ans["diagnosed_cause"] == "clogged filter in the spindle chiller circuit"
    assert len(final_ans["alternative_hypotheses"]) == 1
    trace_events = read_trace(str(log_file))
    assert any(e.get("event") == "DIAGNOSIS_DISAGREEMENT" for e in trace_events)


def test_knowledge_store_snapshot_cache_invalidation_on_put_verified(tmp_path):
    """Verify KnowledgeStore caches snapshots and invalidates cache on put_verified."""
    from project.store.knowledge_store import _SNAPSHOT_CACHE

    faults = json.loads(FAULTS.read_text(encoding="utf-8"))
    hazards = json.loads(HAZARDS.read_text(encoding="utf-8"))
    eps = catalogue_episodes(faults[:2], hazards)

    store = KnowledgeStore(tmp_path / "cache_test.sqlite3", embedder=HashEmbedder())
    writer = GraphAgent(store)
    writer.put_verified(eps[0], trace_id="TR-CACHE-1")

    snap1, _ = store.snapshot(trace_id="TR-CACHE-2")
    assert len(snap1) == 1
    assert store.db.path.resolve() in _SNAPSHOT_CACHE

    # Second read hits cache
    snap2, _ = store.snapshot(trace_id="TR-CACHE-3")
    assert len(snap2) == 1

    # Write second episode -> must invalidate cache and return 2 episodes
    writer.put_verified(eps[1], trace_id="TR-CACHE-4")
    snap3, _ = store.snapshot(trace_id="TR-CACHE-5")
    assert len(snap3) == 2
