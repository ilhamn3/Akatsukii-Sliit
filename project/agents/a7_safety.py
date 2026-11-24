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

        # 2. Initialize Faults Catalogue for hazard mapping
        self.faults_map: Dict[str, Dict[str, Any]] = {}
        f_path = Path(faults_json_path or DEFAULT_FAULTS_JSON)
        if f_path.exists():
            try:
                with open(f_path, "r", encoding="utf-8") as f:
                    fault_entries = json.load(f)
                    for fe in fault_entries:
                        fid = fe.get("fault_id")
                        if fid:
                            self.faults_map[fid.lower()] = fe
                            self.faults_map[fe.get("name", "").lower()] = fe
            except Exception as e:
                logger.warning("Could not index faults.json: %s", e)

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
        """Extract hazard IDs associated with the fault from catalogue."""
        hazards: List[str] = []
        fid = hypothesis.get("fault_id")
        if fid and str(fid).lower() in self.faults_map:
            hazards.extend(self.faults_map[str(fid).lower()].get("hazards", []))

        fault_desc = hypothesis.get("fault", "").lower()
        for k, v in self.faults_map.items():
            if k in fault_desc or k in query.lower():
                for h in v.get("hazards", []):
                    if h not in hazards:
                        hazards.append(h)
        return hazards

    def process(self, state: BlackboardState) -> Dict[str, Any]:
        """Execute two-stage safety verification (deterministic rules first, then LLM check)."""
        raw_query = state.get("raw_query") or ""
        machine_type = state.get("machine_type") or "unspecified"
        hypothesis = state.get("selected_hypothesis") or {}
        fault_desc = hypothesis.get("fault", "Unspecified fault")

        # 1. Extract candidate repair steps
        candidate_steps: List[Any] = state.get("candidate_steps") or []
        if not candidate_steps:
            # Fallback: check if valid fixes are available in fault catalogue for demonstration
            fid = hypothesis.get("fault_id")
            if fid and str(fid).lower() in self.faults_map:
                candidate_steps = self.faults_map[str(fid).lower()].get("valid_fixes", [])
            elif fault_desc in self.faults_map:
                candidate_steps = self.faults_map[fault_desc].get("valid_fixes", [])
            else:
                # Default baseline steps to inspect
                candidate_steps = [
                    f"Perform initial safety lockout and physical inspection for {fault_desc}",
                    "Inspect affected mechanical and electrical subassemblies",
                    "Replace worn components and confirm machine calibration under load",
                ]

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

        det_has_veto = any(h.action == "veto" for h in rule_hits)
        det_has_amend = any(h.action == "amend" for h in rule_hits)

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
                llm_check_status = "skipped"

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

        # Format ActionSteps
        norm_steps_list = self.rule_engine._normalize_steps(candidate_steps)
        action_steps: List[ActionStep] = []
        if is_approved:
            for i, st in enumerate(norm_steps_list, 1):
                action_steps.append(
                    ActionStep(step_number=i, instruction=st, evidence_ids=[f"EV-STEP-{i:02d}"])
                )
        elif final_verdict == "amend":
            # Prepend or include suggested amendments
            combined_steps = list(all_amendments) + norm_steps_list
            for i, st in enumerate(combined_steps, 1):
                action_steps.append(
                    ActionStep(step_number=i, instruction=st, evidence_ids=[f"EV-AMEND-{i:02d}"])
                )

        safety_warnings = (
            [f"SAFETY VETO: {combined_reason}"] if final_verdict == "veto"
            else ([f"SAFETY AMENDMENT: {combined_reason}"] + all_amendments if final_verdict == "amend"
                  else ["Safety Verified: Deterministic safety invariants and procedural controls verified."])
        )

        final_answer_obj = FinalAnswer(
            machine_type=machine_type,
            symptoms=[raw_query],
            diagnosed_cause=fault_desc,
            steps=action_steps,
            safety_warnings=safety_warnings,
            abstained=(final_verdict == "veto"),
            abstention_reason=f"Plan Vetoed for Safety: {combined_reason}" if final_verdict == "veto" else None,
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
            "safety_violations": det_reasons + llm_concerns,
            "safety_loop_count": current_loop,
            "escalated_to_supervisor": escalate,
            "safety_verdict": verdict_obj.model_dump(),
            "final_answer": final_answer_obj.model_dump(),
            "decision": f"SAFETY_{final_verdict.upper()}",
        }
