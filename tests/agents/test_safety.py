"""Comprehensive unit and adversarial tests for SafetyRule engine and SafetyAgent (PR I4).

Offline tests using FakeLLM and deterministic verification:
1. Every one of the 16 hazards H01-H16 has a rule and dedicated test.
2. Adversarial cases (skip lockout, open hydraulic under pressure, reach in rotating spindle, live fuse change, bypass interlock) are VETOED.
3. Compliant procedures are APPROVED.
4. Invariant: Rule Veto + LLM "Approve" -> STILL VETO.
5. Unknown hazard class -> Flagged for review / Veto (SR-X04).
6. Missing catalogue hazard in rule loader -> raises ValueError loudly.
7. Audit trace contains verdict, rule ids, and loop count.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from project.agents.a7_safety import SafetyAgent, SafetyReviewOutput
from project.core.llm import FakeLLM
from project.core.safety_rules.rule_engine import (
    DEFAULT_HAZARDS_JSON,
    RuleEngine,
    RuleHit,
    SafetyRule,
    load_safety_rules,
)
from project.core.schemas import SafetyVerdict
from project.core.state import create_initial_state
from project.core.trace import TraceLogger, read_trace


class FakeApproveLLM(FakeLLM):
    """Fake LLM that always attempts to approve everything, to test rule override."""

    def generate_structured(self, prompt: str, response_model: Any, system_prompt: str = None) -> Any:
        return response_model.model_validate({
            "is_safe": True,
            "verdict": "approve",
            "concerns": [],
            "suggested_amendments": [],
            "reason": "Fake LLM says everything is fine.",
        })


class FakeVetoLLM(FakeLLM):
    """Fake LLM that vetoes subtler issues not covered by deterministic rules."""

    def generate_structured(self, prompt: str, response_model: Any, system_prompt: str = None) -> Any:
        return response_model.model_validate({
            "is_safe": False,
            "verdict": "veto",
            "concerns": ["Subtle ergonomic and chemical exposure hazard detected."],
            "suggested_amendments": ["Provide forced ventilation and respirator."],
            "reason": "Subtle toxicity hazard identified during deep safety review.",
        })


# ==============================================================================
# 1. Loader & Catalogue Coverage Tests
# ==============================================================================

def test_safety_rule_loader_covers_all_catalogue_hazards():
    """Verify that every hazard in hazards.json (H01-H16) has a defined rule."""
    rules = load_safety_rules()
    with open(DEFAULT_HAZARDS_JSON, "r", encoding="utf-8") as f:
        catalogue = json.load(f)

    hazard_ids = {h["hazard_id"] for h in catalogue}
    assert len(hazard_ids) == 16, "Catalogue should contain exactly 16 hazards H01-H16"

    rule_hazard_ids = {r.hazard_id for r in rules if r.hazard_id}
    for hid in hazard_ids:
        assert hid in rule_hazard_ids, f"Hazard {hid} missing from safety rules!"


def test_safety_rule_loader_fails_loudly_on_missing_hazard(tmp_path):
    """Test that loader raises ValueError if a catalogue hazard has no rule."""
    fake_hazards = tmp_path / "hazards.json"
    fake_hazards.write_text(
        json.dumps([
            {"hazard_id": "H99_NEW", "name": "Novel Hazard", "required_control": "Wear special suit.", "keywords": []}
        ]),
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as excinfo:
        load_safety_rules(hazards_json_path=fake_hazards)
    assert "H99_NEW" in str(excinfo.value)


# ==============================================================================
# 2. Rule Coverage for Every One of the 16 Hazards H01-H16
# ==============================================================================

@pytest.mark.parametrize(
    "hazard_id, unsafe_step, safe_step",
    [
        ("H01", "Open pressurized hydraulic hose without depressurising", "Depressurise hydraulic line and verify zero bar on gauge"),
        ("H02", "Work live on 400V electrical terminals without LOTO", "Isolate electrical disconnect, apply LOTO and prove dead with multimeter"),
        ("H03", "Reach hand into press die without safety blocks", "Insert mechanical safety block under die and lock out"),
        ("H04", "Drain boiling oil without gloves immediately", "Allow oil to cool below 40C and wear heat-resistant protective gloves"),
        ("H05", "Disconnect pressurized pneumatic air line", "Vent receiver tank to zero pressure dump valve"),
        ("H06", "Reach in while spindle still coasting", "Verify spindle at full stop and zero speed before opening guard"),
        ("H07", "Face injection nozzle while purging without shield", "Wear face shield and heat-resistant gloves, stand clear of nozzle"),
        ("H08", "Clear jammed package while conveyor belt is running", "Press e-stop, lock out conveyor drive and clear jam"),
        ("H09", "Ignore slippery oil puddle on floor", "Clean spill immediately using absorbent pads and non-slip footwear"),
        ("H10", "Feel along high pressure hose with bare hand for leak", "Depressurise circuit and use cardboard to inspect for hydraulic leaks"),
        ("H11", "Pull hot stringy swarf with bare hands", "Use brass rake and wear cut-resistant gloves to clear chips"),
        ("H12", "Walk under suspended hydraulic ram without blocks", "Support ram mechanically with certified safety blocks"),
        ("H13", "Touch VFD DC bus immediately without measuring voltage", "Wait drive discharge time and measure DC bus voltage 0V"),
        ("H14", "Plug safety relief valve with steel pipe cap", "Replace with certified safety relief valve set to rated pressure"),
        ("H15", "Jumper safety gate interlock switch", "Restore interlock switch with no bypass and obtain supervisor sign-off"),
        ("H16", "Mop water pooling under live energized 415V panel", "Isolate electrical supply and de-energise before drying area"),
    ],
)
def test_each_hazard_rule_triggers_and_protects(hazard_id: str, unsafe_step: str, safe_step: str):
    """Ensure every hazard H01-H16 has a working rule that vetoes unsafe and approves safe."""
    engine = RuleEngine()

    # Unsafe evaluation -> VETO
    hits_unsafe = engine.evaluate(steps=[unsafe_step], context={"hazard_ids": [hazard_id]})
    assert any(h.action == "veto" for h in hits_unsafe), f"Hazard {hazard_id} failed to veto: '{unsafe_step}'"

    # Safe evaluation -> No veto
    hits_safe = engine.evaluate(steps=[safe_step], context={"hazard_ids": [hazard_id]})
    assert not any(h.action == "veto" for h in hits_safe), f"Hazard {hazard_id} incorrectly vetoed safe step: '{safe_step}'"


# ==============================================================================
# 3. Specific Adversarial Cases
# ==============================================================================

def test_adversarial_open_hydraulic_without_pressure_release():
    agent = SafetyAgent(llm_client=FakeLLM())
    state = create_initial_state(query="Hydraulic line replacement", machine_type="CNC Lathe (Turning Center)")
    state["candidate_steps"] = ["Open the hydraulic line without releasing pressure to drain fluid"]
    state["hazard_ids"] = ["H01"]

    res = agent.process(state)
    assert res["safety_approved"] is False
    assert res["decision"] == "SAFETY_VETO"
    assert "SR-H01" in res["safety_verdict"]["rule_ids"]


def test_adversarial_skip_lockout():
    agent = SafetyAgent(llm_client=FakeLLM())
    state = create_initial_state(query="Motor maintenance", machine_type="Conveyor Line")
    state["candidate_steps"] = ["Skip lockout because work takes two minutes", "Tighten motor terminal screws"]
    state["hazard_ids"] = ["H02"]

    res = agent.process(state)
    assert res["safety_approved"] is False
    assert res["decision"] == "SAFETY_VETO"
    assert "SR-H02" in res["safety_verdict"]["rule_ids"]


def test_adversarial_bypass_door_interlock():
    agent = SafetyAgent(llm_client=FakeLLM())
    state = create_initial_state(query="Door switch inspection", machine_type="CNC Lathe (Turning Center)")
    state["candidate_steps"] = ["Bypass the door interlock with a jumper wire to keep running"]
    state["hazard_ids"] = ["H15"]

    res = agent.process(state)
    assert res["safety_approved"] is False
    assert res["decision"] == "SAFETY_VETO"
    assert any(r in res["safety_verdict"]["rule_ids"] for r in ("SR-H15", "SR-X02"))


def test_adversarial_reach_in_while_spindle_coasting():
    agent = SafetyAgent(llm_client=FakeLLM())
    state = create_initial_state(query="Part measurement", machine_type="CNC Lathe (Turning Center)")
    state["candidate_steps"] = ["Reach in while spindle still coasting to catch the workpiece"]
    state["hazard_ids"] = ["H06"]

    res = agent.process(state)
    assert res["safety_approved"] is False
    assert res["decision"] == "SAFETY_VETO"
    assert "SR-H06" in res["safety_verdict"]["rule_ids"]


def test_adversarial_replace_fuse_live_400v():
    agent = SafetyAgent(llm_client=FakeLLM())
    state = create_initial_state(query="Fuse replacement", machine_type="Injection-Moulding Machine")
    state["candidate_steps"] = ["Replace fuse on 400 V panel live using insulated pliers"]
    state["hazard_ids"] = ["H02"]

    res = agent.process(state)
    assert res["safety_approved"] is False
    assert res["decision"] == "SAFETY_VETO"
    assert any(r in res["safety_verdict"]["rule_ids"] for r in ("SR-H02", "SR-X01"))


# ==============================================================================
# 4. Invariant: Rule Veto + LLM "Approve" -> STILL VETO
# ==============================================================================

def test_rule_veto_cannot_be_downgraded_by_llm_approval():
    """Critical safety invariant: Even if the LLM claims a procedure is safe, a deterministic rule veto stands."""
    agent = SafetyAgent(llm_client=FakeApproveLLM())
    state = create_initial_state(query="Hydraulic repair", machine_type="Hydraulic Press")
    state["candidate_steps"] = ["Open the hydraulic line without releasing pressure"]
    state["hazard_ids"] = ["H01"]

    res = agent.process(state)
    assert res["safety_approved"] is False
    assert res["decision"] == "SAFETY_VETO"
    assert "DETERMINISTIC VETO" in res["safety_verdict"]["reason"]


def test_llm_can_veto_even_if_rules_pass():
    """LLM deep check can add concerns or veto subtle issues even if deterministic rules pass."""
    agent = SafetyAgent(llm_client=FakeVetoLLM())
    state = create_initial_state(query="Compliant mechanical inspection", machine_type="Lathe")
    state["candidate_steps"] = [
        "Lock out the machine and verify zero speed",
        "Inspect tool holder screws",
    ]
    state["hazard_ids"] = []

    res = agent.process(state)
    assert res["safety_approved"] is False
    assert res["decision"] == "SAFETY_VETO"
    assert "LLM SAFETY VETO" in res["safety_verdict"]["reason"]


# ==============================================================================
# 5. Unknown Hazard Class Mandatory Review (SR-X04)
# ==============================================================================

def test_unknown_hazard_class_triggers_veto_and_escalation():
    agent = SafetyAgent(llm_client=FakeLLM())
    state = create_initial_state(query="Exotic chemical leak", machine_type="Reactor")
    state["candidate_steps"] = ["Inspect pipeline connection"]
    state["hazard_ids"] = ["H99_UNKNOWN_CORROSIVE_GAS"]

    res = agent.process(state)
    assert res["safety_approved"] is False
    assert res["escalated_to_supervisor"] is True
    assert "SR-X04" in res["safety_verdict"]["rule_ids"]


# ==============================================================================
# 6. Trace Logging Verification
# ==============================================================================

def test_safety_agent_trace_logging(tmp_path):
    log_file = tmp_path / "test_trace.jsonl"
    logger = TraceLogger(log_path=log_file)
    agent = SafetyAgent(trace_logger=logger, llm_client=FakeLLM())

    state = create_initial_state(
        query="Safe hydraulic service",
        machine_type="CNC Lathe",
        trace_id="TR-SAFETY-001",
        session_id="SES-SAFETY-001",
    )
    state["candidate_steps"] = [
        "Isolate power and apply LOTO.",
        "Depressurise line and verify zero bar.",
        "Replace hydraulic seal.",
    ]
    state["hazard_ids"] = ["H01"]

    agent.process(state)

    all_traces = read_trace(path=log_file)
    traces = [t for t in all_traces if t.get("trace_id") == "TR-SAFETY-001"]
    assert len(traces) >= 1
    safety_trace = traces[-1]
    assert safety_trace["agent"] == "SafetyAgent"
    assert safety_trace["event"] == "AGENT_COMPLETED"
    assert "SAFETY_APPROVE" in safety_trace["decision"]
    assert "output_digest" in safety_trace
    assert safety_trace["output_digest"]["verdict"] == "approve"
