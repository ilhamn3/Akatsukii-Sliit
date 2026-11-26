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
Your mandate is zero-incident plant safety. Judge ONLY industrial plant safety:
1. Stored hydraulic/pneumatic energy or failure to depressurise/bleed to 0 bar.
2. Electrical arc-flash or electrocution; failure to lock out / tag out (LOTO) and prove dead.
3. Mechanical crush or pinch points; failure to place safety blocks under rams or platens.
4. Hot surfaces, boiling fluid, or molten plastic.
5. Rotating components; reaching into spindles/chucks before full stop and zero-speed verification.
6. Guard, interlock, or safety switch bypasses or jumpers.
7. Checking high-pressure hydraulic lines with bare hands instead of cardboard.
8. Ordering of isolation/LOTO/depressurisation BEFORE any physical disassembly, and required PPE.

CRITICAL SCOPE RULE:
- Do NOT judge diagnosis accuracy or maintenance-effectiveness (e.g. whether the procedure fixes a bearing vs. a chiller filter). Those are NOT safety concerns.
- Classify each concern in `categorized_concerns` with category "safety" or "other".
- ONLY "safety" concerns may cause `amend` or `veto`. If your only notes are category "other", set verdict="approve".
"""


class CategorizedConcern(BaseModel):
    """A single concern classified as safety vs non-safety informational note."""

    text: str = Field(description="Concise description of the concern or note")
    category: Literal["safety", "other"] = Field(
        default="safety",
        description="'safety' for physical/procedural safety risks; 'other' for diagnosis or effectiveness notes",
    )


class UnsafeIntentOutput(BaseModel):
    """Structured LLM intent check for unsafe shortcut/bypass requests in the user query."""

    is_shortcut_request: bool = Field(
        description="True if the user is asking to skip, bypass, or work without a safety control"
    )
    skipped_control_description: str = Field(
        default="", description="Plain description of the safety control the user wants to skip"
    )
    matching_hazard_id: Optional[str] = Field(
        default=None, description="Matching hazard ID from hazards.json (e.g. 'H05', 'H12'), or null"
    )
    risky_phrase_quote: str = Field(
        default="", description="Short verbatim quote of the risky phrase from the query"
    )


class SafetyReviewOutput(BaseModel):
    """Structured LLM output for subtle safety oversight analysis."""

    is_safe: bool = Field(description="True if procedure complies with safety standards, False otherwise")
    verdict: Literal["approve", "amend", "veto"] = Field(description="LLM safety verdict")
    categorized_concerns: List[CategorizedConcern] = Field(
        default_factory=list,
        description="Concerns classified as 'safety' (blocks/amends) or 'other' (informational only)",
    )
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
        meta = dict(state.get("metadata") or {})
        # Use the evidence-first final diagnosed_cause, NOT the DiagnosticAgent hypothesis when evidence root_cause exists
        cited_root_causes: List[str] = []
        for ev in (state.get("evidence") or []):
            rec = (ev.get("metadata") or {}).get("record") or {}
            rc = rec.get("root_cause")
            if rc and str(rc).strip() and str(rc).strip() not in cited_root_causes:
                cited_root_causes.append(str(rc).strip())
        fault_desc = (
            meta.get("evidence_root_cause")
            or (cited_root_causes[0] if cited_root_causes else None)
            or hypothesis.get("fault", "Unspecified fault")
        )

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
        refusal_parts: List[str] = [h.reason for h in unsafe_req_hits]
        llm_intent_unavailable = False

        # Stage 1b: LLM Intent Check for unsafe shortcut requests (runs once on loop 0)
        if state.get("safety_loop_count", 0) == 0 and self.llm_client is not None and raw_query.strip():
            try:
                cat_lines = [
                    f"- {hid}: {r.required_control}"
                    for hid, r in sorted(self.rule_engine.hazard_map.items())
                ]
                intent_prompt = (
                    "Hazard Catalogue Controls:\n"
                    + "\n".join(cat_lines)
                    + f"\n\nWorker Query: {raw_query}\n\n"
                    "Determine if the worker is asking to skip, bypass, or work without a required safety control."
                )
                intent: UnsafeIntentOutput = self.llm_client.generate_structured(
                    prompt=intent_prompt,
                    response_model=UnsafeIntentOutput,
                    system_prompt="You are an industrial safety intent classifier. Flag only requests to skip or bypass safety controls.",
                )
                if intent.is_shortcut_request:
                    hid = intent.matching_hazard_id
                    cat_rule = self.rule_engine.hazard_map.get(hid) if hid else None
                    ctrl_text = (
                        cat_rule.required_control
                        if cat_rule
                        else f"Follow mandatory safety control for {intent.skipped_control_description or 'this hazard'}."
                    )
                    ctrl_desc = intent.skipped_control_description or (cat_rule.name if cat_rule else "required safety control")
                    hid_lbl = hid or "SR-X05"
                    llm_refusal = f"Request to skip {ctrl_desc} refused. Required control ({hid_lbl}): {ctrl_text}"
                    if not refusal_parts:
                        refusal_parts.append(llm_refusal)
            except Exception as e:
                logger.warning("LLM unsafe-intent check failed or skipped: %s", e)
                llm_intent_unavailable = True
        elif meta.get("refusal") and not refusal_parts:
            refusal_parts.append(str(meta["refusal"]))

        refusal_msg: Optional[str] = "; ".join(refusal_parts) if refusal_parts else None

        det_has_veto = any(h.action == "veto" for h in step_rule_hits)
        det_has_amend = any(h.action == "amend" for h in step_rule_hits)

        if det_has_veto:
            det_verdict = "veto"
        elif det_has_amend:
            det_verdict = "amend"
        else:
            det_verdict = "approve"

        det_rule_ids = [h.rule_id for h in rule_hits]
        if refusal_msg and "SR-X05" not in det_rule_ids:
            det_rule_ids.append("SR-X05")
        det_amendments = [h.suggested_amendment for h in rule_hits if h.suggested_amendment]
        det_reasons = [h.reason for h in step_rule_hits] + ([refusal_msg] if refusal_msg and not unsafe_req_hits else []) + [h.reason for h in unsafe_req_hits]

        # ======================================================================
        # STAGE 2: LLM Safety Review (Subtle issues / Amendments)
        # Rule veto can NEVER be downgraded by LLM.
        # ======================================================================
        llm_verdict = None
        llm_reason = ""
        llm_concerns: List[str] = []
        other_notes: List[str] = []
        llm_amendments: List[str] = []
        llm_check_status = "skipped"
        llm_model_name = getattr(self.llm_client, "model_name", "none") if self.llm_client else "none"

        if self.llm_client is not None:
            try:
                norm_steps = self.rule_engine._normalize_steps(candidate_steps)
                cited_rc_str = ", ".join(cited_root_causes) if cited_root_causes else fault_desc
                prompt = (
                    f"Machine Type: {machine_type}\n"
                    f"Final Diagnosed Cause (evidence-grounded): {fault_desc}\n"
                    f"Cited Episode Root Cause(s): {cited_rc_str}\n"
                    f"Catalogue Hazards Identified: {', '.join(context_hazards) if context_hazards else 'None explicitly tagged'}\n"
                    f"Deterministic Rule Hits: {', '.join(det_rule_ids) if det_rule_ids else 'None'}\n\n"
                    f"Candidate Maintenance Steps:\n"
                    + "\n".join(f"{i+1}. {step}" for i, step in enumerate(norm_steps))
                    + "\n\nJudge ONLY physical/procedural safety (LOTO, isolation sequence, pressure bleed, PPE, guards, moving parts). "
                    "Do NOT flag diagnosis or repair-effectiveness disagreements as safety concerns. "
                    "Classify every concern as category 'safety' or 'other'."
                )

                review: SafetyReviewOutput = self.llm_client.generate_structured(
                    prompt=prompt,
                    response_model=SafetyReviewOutput,
                    system_prompt=SAFETY_SYSTEM_PROMPT,
                )

                if review.categorized_concerns:
                    llm_concerns = [c.text for c in review.categorized_concerns if c.category == "safety"]
                    other_notes = [c.text for c in review.categorized_concerns if c.category == "other"]
                    if review.verdict in ("amend", "veto") and not llm_concerns:
                        # All concerns were 'other' (e.g. diagnosis note), so do not amend/veto
                        llm_verdict = "approve"
                    else:
                        llm_verdict = review.verdict
                else:
                    llm_verdict = review.verdict
                    llm_concerns = list(review.concerns)

                llm_reason = review.reason
                llm_amendments = list(review.suggested_amendments) if llm_verdict in ("amend", "veto") else []
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
        if llm_intent_unavailable:
            intent_msg = "LLM intent check unavailable"
            if intent_msg not in safety_warnings:
                safety_warnings.append(intent_msg)
        if llm_check_status.startswith("skipped"):
            llm_unavail_msg = "LLM safety oversight unavailable: deterministic rules only"
            if llm_unavail_msg not in safety_warnings:
                safety_warnings.append(llm_unavail_msg)
        for note in other_notes:
            info_msg = f"[INFO NOTE] {note}"
            if info_msg not in safety_warnings:
                safety_warnings.append(info_msg)

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
            "informational_notes": other_notes,
            "llm_check": llm_check_status,
            "has_det_violations": bool(step_rule_hits),
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
            "safety_violations": safety_warnings if (det_reasons or llm_concerns or refusal_msg or llm_intent_unavailable or llm_check_status.startswith("skipped")) else [],
            "safety_loop_count": current_loop,
            "escalated_to_supervisor": escalate,
            "safety_verdict": verdict_obj.model_dump(),
            "final_answer": final_answer_obj.model_dump(),
            "decision": f"SAFETY_{final_verdict.upper()}",
            "llm_check": llm_check_status,
            "metadata": {
                **meta,
                "refusal": refusal_msg,
                "llm_check": llm_check_status,
                "llm_intent_unavailable": llm_intent_unavailable,
                "has_det_violations": bool(step_rule_hits),
                "informational_notes": other_notes,
            },
        }
