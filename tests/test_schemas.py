"""Unit tests for Factory Memory core Pydantic schemas.

Validates that all core schemas (RepairRecord, AgentMessage, Hypothesis,
Evidence, SafetyVerdict, FinalAnswer) validate correct data and properly
reject malformed or out-of-range inputs.
"""

import pytest
from pydantic import ValidationError
from project.core.schemas import (
    ActionStep,
    AgentMessage,
    Evidence,
    FailedAttempt,
    FinalAnswer,
    Hypothesis,
    RepairRecord,
    RepairStep,
    SafetyVerdict,
)


def test_valid_repair_record():
    """Verify that a complete, valid RepairRecord validates cleanly."""
    record = RepairRecord(
        machine_type="CNC Lathe (Turning Center)",
        machine_id="CNC-L03",
        symptoms=[
            "High-pitched 85dB whining noise at >2800 RPM",
            "Chuck clamp pressure fluctuating below 15 bar",
        ],
        steps=[
            RepairStep(
                step_number=1,
                description="Execute LOTO at main breaker panel PB-02.",
                timestamp="00:45",
                evidence_id="EV-CNC-0014-S1",
            ),
            RepairStep(
                step_number=2,
                description="Bleed hydraulic clamp accumulator down to 0 bar.",
                timestamp="01:30",
                evidence_id="EV-CNC-0014-S2",
            ),
            RepairStep(
                step_number=3,
                description="Replace rotary union cartridge (Part #RU-8842-B) and torque to 45 Nm.",
                timestamp="04:15",
                evidence_id="EV-CNC-0014-S3",
            ),
        ],
        tools=["Torque wrench (20-100 Nm)", "Hex key set", "Pressure gauge (0-60 bar)"],
        parts=["Rotary union cartridge RU-8842-B", "Buna-N O-ring set"],
        root_cause="Rotary union seal wear leading to hydraulic micro-leakage and cavitation.",
        failed_attempts=[
            FailedAttempt(
                action="Tightened the chuck regulator screw directly without bleeding accumulator.",
                outcome="Relief valve needle stripped; pressure fluctuation worsened.",
                why_failed="Trapped 25 bar accumulator pressure caused needle seat galling under load.",
            )
        ],
        safety_warnings=[
            "MANDATORY: LOTO at breaker PB-02.",
            "Discharge 25 bar hydraulic accumulator before loosening union collar.",
        ],
        confidence=0.94,
        evidence_id="EV-CNC-0014",
        source="technician_audio_debrief",
    )

    assert record.machine_type == "CNC Lathe (Turning Center)"
    assert len(record.steps) == 3
    assert len(record.failed_attempts) == 1
    assert record.confidence == 0.94
    assert record.steps[0].evidence_id == "EV-CNC-0014-S1"


def test_repair_record_invalid_confidence():
    """Verify that confidence out of [0.0, 1.0] raises a ValidationError."""
    with pytest.raises(ValidationError):
        RepairRecord(
            machine_type="CNC Lathe",
            symptoms=["Whining noise"],
            steps=[RepairStep(step_number=1, description="Inspect unit")],
            root_cause="Bearing failure",
            confidence=1.5,  # Out of range!
            evidence_id="EV-001",
            source="manual",
        )


def test_agent_message():
    """Verify AgentMessage creation and default timestamp assignment."""
    msg = AgentMessage(
        sender="DiagnosticAgent",
        receiver="RetrievalAgent",
        type="DIAGNOSTIC_HYPOTHESIS",
        payload={
            "machine_type": "Hydraulic Press",
            "top_hypothesis": "Proportional pilot spool varnishing",
            "confidence": 0.88,
        },
        trace_id="TR-20261121-001",
    )

    assert msg.sender == "DiagnosticAgent"
    assert msg.receiver == "RetrievalAgent"
    assert msg.payload["confidence"] == 0.88
    assert msg.ts is not None
    assert "T" in msg.ts  # ISO format string


def test_hypothesis():
    """Verify Hypothesis schema and confidence bounds."""
    hyp = Hypothesis(
        rank=1,
        fault="Rotary union seal wear causing hydraulic micro-leakage",
        subsystem="Main Spindle & Chuck Clamp",
        confidence=0.89,
        rationale="Matches symptom pattern of whining + pressure drop.",
    )
    assert hyp.rank == 1
    assert hyp.confidence == 0.89

    with pytest.raises(ValidationError):
        Hypothesis(
            rank=0,  # Must be >= 1
            fault="Fault",
            subsystem="Subsystem",
            confidence=0.5,
        )


def test_evidence():
    """Verify Evidence schema and similarity bounds."""
    ev = Evidence(
        evidence_id="EV-CNC-0014-S2",
        episode_id="EP-CNC-0014",
        source_type="vector",
        content="Bleed hydraulic accumulator down to 0 bar via manual valve V-12.",
        similarity_score=0.92,
        metadata={"machine": "CNC Lathe", "tech": "Dave"},
    )
    assert ev.evidence_id == "EV-CNC-0014-S2"
    assert ev.similarity_score == 0.92


def test_safety_verdict():
    """Verify SafetyVerdict literals: approve, amend, veto."""
    v_approve = SafetyVerdict(
        verdict="approve",
        reason="All LOTO and accumulator discharge protocols satisfied.",
        rule_ids=["RULE-LOTO-01", "RULE-HYD-PRESSURE-02"],
    )
    assert v_approve.verdict == "approve"

    v_veto = SafetyVerdict(
        verdict="veto",
        reason="Missing LOTO isolation on 480V circuit breaker.",
        rule_ids=["RULE-LOTO-01"],
        suggested_amendments=["Insert LOTO step at PB-02 prior to enclosure entry."],
    )
    assert v_veto.verdict == "veto"
    assert len(v_veto.suggested_amendments) == 1

    # Invalid verdict literal must raise error
    with pytest.raises(ValidationError):
        SafetyVerdict(
            verdict="ignore",  # Not in ("approve", "amend", "veto")
            reason="Unknown",
        )


def test_final_answer():
    """Verify FinalAnswer with action steps and citations."""
    ans = FinalAnswer(
        machine_type="CNC Lathe (Turning Center)",
        symptoms=["Whining noise", "Chuck pressure fluctuation"],
        diagnosed_cause="Rotary union seal wear",
        steps=[
            ActionStep(
                step_number=1,
                instruction="Lockout/Tagout main breaker PB-02.",
                evidence_ids=["EV-CNC-0014-S1"],
            ),
            ActionStep(
                step_number=2,
                instruction="Bleed 25 bar accumulator to 0 bar.",
                evidence_ids=["EV-CNC-0014-S2"],
            ),
        ],
        safety_warnings=["MANDATORY: LOTO at PB-02.", "Discharge hydraulic accumulator."],
        failed_attempts_to_avoid=[
            "Do not turn regulator screw without bleeding accumulator pressure."
        ],
        abstained=False,
        escalated=False,
        trace_id="TR-20261121-001",
    )
    assert not ans.abstained
    assert len(ans.steps) == 2
    assert ans.steps[0].evidence_ids == ["EV-CNC-0014-S1"]


def test_final_answer_abstention():
    """Verify FinalAnswer can abstain with empty steps if abstained=True."""
    ans = FinalAnswer(
        machine_type="Unknown Machine",
        symptoms=["Unprecedented vibration"],
        diagnosed_cause="Unknown",
        steps=[],
        abstained=True,
        abstention_reason="Retrieval similarity score (0.38) below 0.55 threshold.",
        trace_id="TR-ABSTAIN-001",
    )
    assert ans.abstained is True
    assert ans.abstention_reason is not None
    assert len(ans.steps) == 0


def test_final_answer_empty_steps_without_abstention_fails():
    """Verify that non-abstained FinalAnswer cannot have empty steps."""
    with pytest.raises(ValidationError):
        FinalAnswer(
            machine_type="CNC Lathe",
            symptoms=["Whining noise"],
            diagnosed_cause="Bearing wear",
            steps=[],  # Empty without abstained=True!
            abstained=False,
            escalated=False,
            trace_id="TR-FAIL-001",
        )
