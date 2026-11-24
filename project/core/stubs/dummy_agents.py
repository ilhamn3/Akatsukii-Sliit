"""STUB: Labelled dummy agents for testing LangGraph execution and handoffs.

These lightweight agents inherit BaseAgent and implement process() without
requiring external LLM API calls or vector databases.
"""

from typing import Any, Dict
from project.agents.base import BaseAgent
from project.core.state import BlackboardState


class DummyDiagnosticAgent(BaseAgent):
    """STUB: Minimal diagnostic agent generating a sample hypothesis."""

    def __init__(self, trace_logger=None):
        super().__init__(
            name="DummyDiagnosticAgent",
            role="Initial fault classification and hypothesis ranking",
            pipeline="B",
            trace_logger=trace_logger,
            is_stub=True,
        )

    def process(self, state: BlackboardState) -> Dict[str, Any]:
        query = state.get("raw_query", "")
        hyp = {
            "rank": 1,
            "fault": f"STUB mechanical root cause for: {query[:40]}",
            "subsystem": "Drive Spindle",
            "confidence": 0.88,
            "rationale": "STUB diagnostic heuristic match.",
        }
        return {
            "ranked_hypotheses": [hyp],
            "selected_hypothesis": hyp,
            "decision": "HYPOTHESIS_FORMULATED",
        }


class DummySafetyAgent(BaseAgent):
    """STUB: Minimal safety verification agent approving repair steps."""

    def __init__(self, trace_logger=None):
        super().__init__(
            name="DummySafetyAgent",
            role="Deterministic safety rule verification",
            pipeline="B",
            trace_logger=trace_logger,
            is_stub=True,
        )

    def process(self, state: BlackboardState) -> Dict[str, Any]:
        hyp = state.get("selected_hypothesis") or {}
        fault_desc = hyp.get("fault", "Generic fault")
        machine_type = state.get("machine_type") or "unspecified"

        final_ans = {
            "machine_type": machine_type,
            "symptoms": [state.get("raw_query", "Unknown symptom")],
            "diagnosed_cause": f"STUB DIAGNOSIS: {fault_desc}",
            "steps": [
                {
                    "step_number": 1,
                    "instruction": "STUB: Placeholder step 1 (simulation only, do not perform).",
                    "evidence_ids": ["EV-DUMMY-01"],
                },
                {
                    "step_number": 2,
                    "instruction": "STUB: Placeholder step 2 (simulation only, do not perform).",
                    "evidence_ids": ["EV-DUMMY-02"],
                },
            ],
            "safety_warnings": [
                "STUB: No real safety check performed. Dummy simulation only."
            ],
            "failed_attempts_to_avoid": [],
            "abstained": False,
            "escalated": False,
            "trace_id": state.get("trace_id", "TR-UNKNOWN"),
        }

        return {
            "safety_approved": True,
            "safety_violations": [],
            "safety_loop_count": 1,
            "final_answer": final_ans,
            "decision": "SAFETY_APPROVED",
        }


class DummyAuditAgent(BaseAgent):
    """STUB: Minimal third agent used to test dynamic config registration."""

    def __init__(self):
        super().__init__(
            name="DummyAuditAgent",
            role="Post-processing and audit verification",
            pipeline="B",
            is_stub=True,
        )

    def process(self, state: BlackboardState) -> Dict[str, Any]:
        meta = dict(state.get("metadata", {}))
        meta["audit_verified"] = True
        return {
            "metadata": meta,
            "decision": "AUDIT_PASSED",
        }


class DummyOrchestratorAgent(BaseAgent):
    """STUB: Placeholder upstream orchestrator generating draft candidate repair steps pending real Orchestrator (PR I5)."""

    def __init__(self, trace_logger=None):
        super().__init__(
            name="DummyOrchestratorAgent",
            role="Draft repair step retrieval and synthesis (STUB)",
            pipeline="B",
            trace_logger=trace_logger,
            is_stub=True,
        )

    def process(self, state: BlackboardState) -> Dict[str, Any]:
        hyp = state.get("selected_hypothesis") or {}
        fault_desc = hyp.get("fault", "generic mechanism fault")
        return {
            "candidate_steps": [
                {
                    "step_number": 1,
                    "instruction": "Isolate machine main electrical supply and apply lockout/tagout (LOTO).",
                    "evidence_ids": ["EV-STUB-01"],
                },
                {
                    "step_number": 2,
                    "instruction": f"STUB: Perform mechanical inspection and repair for {fault_desc}.",
                    "evidence_ids": ["EV-STUB-02"],
                },
                {
                    "step_number": 3,
                    "instruction": "Verify all safety guards and interlocks are restored before re-energising.",
                    "evidence_ids": ["EV-STUB-03"],
                },
            ],
            "decision": "DRAFT_STEPS_PRODUCED",
        }
