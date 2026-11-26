"""Agent 7: Safety Verification Agent (Deterministic Rules + LLM Review).

Enforces mandatory industrial safety controls based on catalogue hazards H01-H16
and cross-cutting energy/interlock rules. Deterministic rule hits always take precedence
and cannot be downgraded by LLM outputs.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from project.agents.base import BaseAgent
from project.core.llm import LLMClient, get_llm
from project.core.safety_rules.rule_engine import RuleEngine, RuleHit
from project.core.schemas import ActionStep, AgentMessage, FinalAnswer, SafetyVerdict
from project.core.state import BlackboardState

logger = logging.getLogger(__name__)

DEFAULT_FAULTS_JSON = (
    Path(__file__).resolve().parent.parent / "data" / "fault_catalogue" / "faults.json"
)

SAFETY_SYSTEM_PROMPT = """You are a Senior Industrial Plant Safety Engineer verifying candidate maintenance procedures.
Your mandate is zero-incident plant safety. Verify that proposed mechanical/electrical repair steps do not introduce hazards such as:
1. Stored hydraulic/pneumatic energy or failure to depressurise/bleed to 0 bar.
2. Electrical arc-flash or electrocution; failure to lock out / tag out (LOTO) and prove dead.
3. Mechanical crush or pinch points; failure to place safety blocks under rams or platens.
4. Hot surfaces, boiling fluid, or molten plastic.
5. Rotating components; reaching into spindles/chucks before full stop and zero-speed verification.
6. Guard, interlock, or safety switch bypasses or jumpers.
7. Checking high-pressure hydraulic lines with bare hands instead of cardboard.

Be rigorous. Never approve dangerous shortcuts. You may suggest procedural amendments or recommend a veto.
"""


class SafetyReviewOutput(BaseModel):
    """Structured LLM output for subtle safety oversight analysis."""

    is_safe: bool = Field(description="True if procedure complies with safety standards, False otherwise")
    verdict: Literal["approve", "amend", "veto"] = Field(description="LLM safety verdict")
    concerns: List[str] = Field(default_factory=list, description="Specific safety risks or missing prerequisites")
    suggested_amendments: List[str] = Field(
        default_factory=list, description="Specific procedural fixes to make the steps safe"
    )
    reason: str = Field(description="Technical rationale for the safety recommendation")


class SafetyAgent(BaseAgent):
    """Agent 7: Industrial Safety Verification Agent."""

    def __init__(
        self,
        trace_logger=None,
        llm_client: Optional[LLMClient] = None,
        rule_engine: Optional[RuleEngine] = None,
        faults_json_path: Optional[Path] = None,
    ):
        super().__init__(
            name="SafetyAgent",
            role="Deterministic safety rule verification and LLM oversight",
            pipeline="B",
            trace_logger=trace_logger,
            is_stub=False,
        )

        # 1. Initialize Deterministic Rule Engine
        self.rule_engine = rule_engine or RuleEngine()

        # 2. Initialize Faults Catalogue strictly for hazard mapping (NEVER for steps)
        self.faults_hazard_map: Dict[str, List[str]] = {}
        f_path = Path(faults_json_path or DEFAULT_FAULTS_JSON)
        if f_path.exists():
            try:
                with open(f_path, "r", encoding="utf-8") as f:
                    fault_entries = json.load(f)
                    for fe in fault_entries:
                        fid = fe.get("fault_id")
                        hazards = fe.get("hazards", [])
                        if fid:
                            self.faults_hazard_map[fid.lower()] = list(hazards)
                            self.faults_hazard_map[fe.get("name", "").lower()] = list(hazards)
            except Exception as e:
                logger.warning("Could not index hazards from faults.json: %s", e)

        # 3. Resolve LLM client (with per-agent model override support)
        if llm_client is not None:
            self.llm_client = llm_client
        else:
            safety_model = os.getenv("GEMINI_MODEL_SAFETY") or os.getenv("GEMINI_MODEL")
            safety_thinking = os.getenv("GEMINI_THINKING_SAFETY") or os.getenv("GEMINI_THINKING")
            try:
                self.llm_client = get_llm(model=safety_model, thinking=safety_thinking)
            except Exception as e:
                logger.warning(
                    "SafetyAgent LLM init failed (%s); operating in deterministic-only mode.", e
                )
                self.llm_client = None

    def _extract_fault_hazards(self, hypothesis: Dict[str, Any], query: str) -> List[str]:
        """Extract hazard IDs associated with the fault from catalogue (hazards field only)."""
        hazards: List[str] = []
        fid = hypothesis.get("fault_id")
        if fid and str(fid).lower() in self.faults_hazard_map:
            hazards.extend(self.faults_hazard_map[str(fid).lower()])

        fault_desc = hypothesis.get("fault", "").lower()
        for k, h_list in self.faults_hazard_map.items():
            if k in fault_desc or k in query.lower():
                for h in h_list:
                    if h not in hazards:
                        hazards.append(h)
        return hazards

    def process(self, state: BlackboardState) -> Dict[str, Any]:
        """Execute two-stage safety verification (deterministic rules first, then LLM check)."""
        raw_query = state.get("raw_query") or ""
        machine_type = state.get("machine_type") or "unspecified"
        hypothesis = state.get("selected_hypothesis") or {}
        fault_desc = hypothesis.get("fault", "Unspecified fault")

        # 1. Candidate steps must come ONLY from state (never generated, never from faults.json)
        candidate_steps: List[Any] = state.get("candidate_steps") or []

        # If there are no candidate steps in state, explicitly reject/abstain
        if not candidate_steps:
            current_loop = state.get("safety_loop_count", 0) + 1
            verdict_obj = SafetyVerdict(
                verdict="veto",
                reason="no candidate steps to verify",
                rule_ids=[],
                suggested_amendments=None,
            )
            final_answer_obj = FinalAnswer(
                machine_type=machine_type,
                symptoms=[raw_query],
                diagnosed_cause=fault_desc,
                steps=[],
                safety_warnings=["NO STEPS TO VERIFY: Candidate step list is empty; safety verification cannot proceed."],
                abstained=True,
                abstention_reason="no candidate steps to verify",
                escalated=False,
                trace_id=state.get("trace_id", "TR-UNKNOWN"),
            )
            output_digest = {
                "verdict": "veto",
                "rule_ids": [],
                "reason": "no candidate steps to verify",
                "safety_approved": "False",
                "safety_loop_count": current_loop,
                "escalated_to_supervisor": "False",
                "llm_check": "skipped",
                "llm_model": getattr(self.llm_client, "model_name", "none") if self.llm_client else "none",
            }
            if self.trace_logger:
                self.trace_logger.log(
                    trace_id=state.get("trace_id", "TR-DEV"),
                    session_id=state.get("session_id", "SES-DEV"),
                    agent=self.name,
                    stub=False,
                    event="AGENT_COMPLETED",
                    input_data={"query": raw_query, "machine": machine_type},
                    output_data=output_digest,
                    decision="NO_STEPS_TO_VERIFY",
                    llm=None,
                )
            return {
                "safety_approved": False,
                "safety_violations": ["Missing candidate steps: SafetyAgent cannot verify an empty procedure."],
                "safety_loop_count": current_loop,
                "escalated_to_supervisor": False,
                "safety_verdict": verdict_obj.model_dump(),
                "final_answer": final_answer_obj.model_dump(),
                "decision": "NO_STEPS_TO_VERIFY",
            }

        # 2. Extract context hazards
        context_hazards = list(state.get("hazard_ids") or [])
        fault_hazards = self._extract_fault_hazards(hypothesis, raw_query)
        for fh in fault_hazards:
            if fh not in context_hazards:
                context_hazards.append(fh)

        context = {
            "hazard_ids": context_hazards,
            "machine_type": machine_type,
            "query": raw_query,
            "fault": fault_desc,
        }

        # ======================================================================
        # STAGE 1: Deterministic Safety Rules Evaluation (ALWAYS FIRST)
        # ======================================================================
        rule_hits = self.rule_engine.evaluate(steps=candidate_steps, context=context)

        # Separate SR-X05 (unsafe shortcut request in query) from step-level rule hits
        unsafe_req_hits = [h for h in rule_hits if h.rule_id == "SR-X05"]
        step_rule_hits = [h for h in rule_hits if h.rule_id != "SR-X05"]
        refusal_msg: Optional[str] = (
            "; ".join(h.reason for h in unsafe_req_hits) if unsafe_req_hits else None
        )

        det_has_veto = any(h.action == "veto" for h in step_rule_hits)
        det_has_amend = any(h.action == "amend" for h in step_rule_hits)

        if det_has_veto:
            det_verdict = "veto"
        elif det_has_amend:
            det_verdict = "amend"
        else:
            det_verdict = "approve"

        det_rule_ids = [h.rule_id for h in rule_hits]
        det_amendments = [h.suggested_amendment for h in rule_hits if h.suggested_amendment]
        det_reasons = [h.reason for h in rule_hits]

        # ======================================================================
        # STAGE 2: LLM Safety Review (Subtle issues / Amendments)
        # Rule veto can NEVER be downgraded by LLM.
        # ======================================================================
        llm_verdict = None
        llm_reason = ""
        llm_concerns: List[str] = []
        llm_amendments: List[str] = []
        llm_check_status = "skipped"
        llm_model_name = getattr(self.llm_client, "model_name", "none") if self.llm_client else "none"

        if self.llm_client is not None:
            try:
                norm_steps = self.rule_engine._normalize_steps(candidate_steps)
                prompt = (
                    f"Machine Type: {machine_type}\n"
                    f"Diagnosed Fault: {fault_desc}\n"
                    f"Catalogue Hazards Identified: {', '.join(context_hazards) if context_hazards else 'None explicitly tagged'}\n"
                    f"Deterministic Rule Hits: {', '.join(det_rule_ids) if det_rule_ids else 'None'}\n\n"
                    f"Candidate Maintenance Steps:\n"
                    + "\n".join(f"{i+1}. {step}" for i, step in enumerate(norm_steps))
                    + "\n\nReview this maintenance procedure for subtle hazards, missing isolation/LOTO steps, or PPE omissions. "
                    "Provide your structured verdict (approve, amend, or veto)."
                )

                review: SafetyReviewOutput = self.llm_client.generate_structured(
                    prompt=prompt,
                    response_model=SafetyReviewOutput,
                    system_prompt=SAFETY_SYSTEM_PROMPT,
                )

                llm_verdict = review.verdict
                llm_reason = review.reason
                llm_concerns = review.concerns
                llm_amendments = review.suggested_amendments
                llm_check_status = "performed"
            except Exception as e:
                logger.warning("LLM safety review check failed or skipped: %s", e)
                err_str = str(e).lower()
                if "429" in err_str or "resource_exhausted" in err_str:
                    llm_check_status = "skipped_429"
                elif "timeout" in err_str or "timed out" in err_str:
                    llm_check_status = "skipped_timeout"
                else:
                    llm_check_status = "skipped_error"

        # ======================================================================
        # Combine Verdicts: Invariant - Deterministic Veto is IMMUTABLE
        # ======================================================================
        if det_verdict == "veto":
            final_verdict: Literal["approve", "amend", "veto"] = "veto"
            combined_reason = (
                "DETERMINISTIC VETO: " + "; ".join(det_reasons)
                + (f" | Additional LLM Notes: {llm_reason}" if llm_reason else "")
            )
        elif llm_verdict == "veto":
            final_verdict = "veto"
            combined_reason = f"LLM SAFETY VETO: {llm_reason}"
        elif det_verdict == "amend" or llm_verdict == "amend":
            final_verdict = "amend"
            amend_notes = det_reasons + ([f"LLM: {llm_reason}"] if llm_reason else [])
            combined_reason = "PROCEDURAL AMENDMENT REQUIRED: " + "; ".join(amend_notes)
        else:
            final_verdict = "approve"
            combined_reason = (
                "Safety requirements satisfied. All deterministic controls verified compliant"
                + (f" and LLM safety review cleared ({llm_reason})." if llm_reason else ".")
            )

        all_rule_ids = list(dict.fromkeys(det_rule_ids))
        all_amendments = list(dict.fromkeys(det_amendments + llm_amendments))

        verdict_obj = SafetyVerdict(
            verdict=final_verdict,
            reason=combined_reason,
            rule_ids=all_rule_ids,
            suggested_amendments=all_amendments or None,
        )

        # 3. Update Blackboard State
        current_loop = state.get("safety_loop_count", 0) + 1
        max_loops = 2

        is_approved = final_verdict == "approve"
        escalate = (final_verdict == "veto" and current_loop >= max_loops) or ("SR-X04" in all_rule_ids)

        # Format ActionSteps - preserve original step text and upstream evidence_ids strictly
        action_steps: List[ActionStep] = []
        if is_approved:
            for i, st in enumerate(candidate_steps, 1):
                if isinstance(st, dict):
                    instruction = str(st.get("instruction") or st.get("action") or "").strip()
                    ev_ids = list(st.get("evidence_ids") or [])
                elif isinstance(st, ActionStep):
                    instruction = st.instruction.strip()
                    ev_ids = list(st.evidence_ids)
                else:
                    instruction = str(st).strip()
                    ev_ids = []
                action_steps.append(
                    ActionStep(step_number=i, instruction=instruction, evidence_ids=ev_ids)
                )
        elif final_verdict == "amend":
            current_idx = 1
            for amend in all_amendments:
                action_steps.append(
                    ActionStep(step_number=current_idx, instruction=f"[SAFETY AMENDMENT] {amend}", evidence_ids=[])
                )
                current_idx += 1
            for st in candidate_steps:
                if isinstance(st, dict):
                    instruction = str(st.get("instruction") or st.get("action") or "").strip()
                    ev_ids = list(st.get("evidence_ids") or [])
                elif isinstance(st, ActionStep):
                    instruction = st.instruction.strip()
                    ev_ids = list(st.evidence_ids)
                else:
                    instruction = str(st).strip()
                    ev_ids = []
                action_steps.append(
                    ActionStep(step_number=current_idx, instruction=instruction, evidence_ids=ev_ids)
                )
                current_idx += 1

        safety_warnings = (
            [f"SAFETY VETO: {combined_reason}"] if final_verdict == "veto"
            else ([f"SAFETY AMENDMENT: {combined_reason}"] + all_amendments if final_verdict == "amend"
                  else ["Safety Verified: Deterministic safety invariants and procedural controls verified."])
        )
        if refusal_msg and refusal_msg not in safety_warnings:
            safety_warnings.insert(0, refusal_msg)
        if llm_check_status.startswith("skipped"):
            llm_unavail_msg = "LLM safety oversight unavailable: deterministic rules only"
            if llm_unavail_msg not in safety_warnings:
                safety_warnings.append(llm_unavail_msg)

        final_answer_obj = FinalAnswer(
            machine_type=machine_type,
            symptoms=[raw_query],
            diagnosed_cause=fault_desc,
            steps=action_steps,
            safety_warnings=safety_warnings,
            abstained=(final_verdict == "veto"),
            abstention_reason=f"Plan Vetoed for Safety: {combined_reason}" if final_verdict == "veto" else None,
            refusal=refusal_msg,
            escalated=escalate,
            trace_id=state.get("trace_id", "TR-UNKNOWN"),
        )

        output_digest = {
            "verdict": final_verdict,
            "rule_ids": all_rule_ids,
            "reason": combined_reason[:150] + ("..." if len(combined_reason) > 150 else ""),
            "safety_approved": str(is_approved),
            "safety_loop_count": current_loop,
            "escalated_to_supervisor": str(escalate),
            "llm_check": llm_check_status,
            "llm_model": llm_model_name,
        }

        # Update trace logger explicitly
        if self.trace_logger:
            self.trace_logger.log(
                trace_id=state.get("trace_id", "TR-DEV"),
                session_id=state.get("session_id", "SES-DEV"),
                agent=self.name,
                stub=False,
                event="AGENT_COMPLETED",
                input_data={"query": raw_query, "machine": machine_type},
                output_data=output_digest,
                decision=f"SAFETY_{final_verdict.upper()}",
                llm=llm_model_name if llm_check_status == "performed" else None,
            )

        return {
            "safety_approved": is_approved,
            "safety_violations": safety_warnings if (det_reasons or llm_concerns or refusal_msg or llm_check_status.startswith("skipped")) else [],
            "safety_loop_count": current_loop,
            "escalated_to_supervisor": escalate,
            "safety_verdict": verdict_obj.model_dump(),
            "final_answer": final_answer_obj.model_dump(),
            "decision": f"SAFETY_{final_verdict.upper()}",
            "llm_check": llm_check_status,
            "metadata": {
                **dict(state.get("metadata") or {}),
                "refusal": refusal_msg,
                "llm_check": llm_check_status,
            },
        }
