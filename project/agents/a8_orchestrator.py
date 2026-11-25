"""Agent 8: Orchestrator Agent (Repair Step Synthesis & Delivery).

Synthesizes safe, grounded repair action steps by reconciling diagnostic hypotheses
with retrieved historical evidence chunks. Enforces adaptive routing (FAST vs FULL),
cross-episode conflict ranking, strict citation validation, and safe abstention.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Set

from pydantic import BaseModel, Field

from project.agents.base import BaseAgent
from project.core.llm import LLMClient, get_llm
from project.core.orchestration_helpers import (
    DEFAULT_CONFLICT_WEIGHTS,
    calculate_conflict_ranking,
    check_energy_loto_compliance,
    evaluate_adaptive_routing,
    validate_and_filter_citations,
)
from project.core.registry import load_config
from project.core.schemas import ActionStep, FinalAnswer
from project.core.state import BlackboardState
from project.core.trace import TraceLogger

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parent.parent / "core" / "prompts" / "orchestrator_v1.md"


class SynthesizedStep(BaseModel):
    """Structured step schema produced during LLM orchestration."""

    step_number: int = Field(ge=1, description="1-indexed sequence number of the step")
    instruction: str = Field(..., min_length=3, description="Imperative instruction for the technician")
    evidence_ids: List[str] = Field(
        default_factory=list,
        description="Grounded evidence IDs supporting this instruction directly from retrieved evidence",
    )


class OrchestrationPlanOutput(BaseModel):
    """Structured schema expected from the LLM synthesis call."""

    diagnosed_cause: str = Field(..., min_length=3, description="Confirmed root cause or fault diagnosis")
    steps: List[SynthesizedStep] = Field(..., min_length=1, description="Synthesized action steps")
    safety_warnings: List[str] = Field(
        default_factory=list, description="Critical warnings or safety prerequisites"
    )


class OrchestratorAgent(BaseAgent):
    """Agent 8: Multi-phase Orchestrator (Plan, Revise, Finalize)."""

    def __init__(
        self,
        name: Optional[str] = None,
        phase: Literal["plan", "revise", "finalize"] = "plan",
        trace_logger: Optional[TraceLogger] = None,
        llm_client: Optional[LLMClient] = None,
        is_stub: bool = False,
    ):
        self.phase = phase
        resolved_name = name or f"Orchestrator{phase.capitalize()}"
        super().__init__(
            name=resolved_name,
            role=f"Orchestration and repair step synthesis ({phase} phase)",
            pipeline="B",
            trace_logger=trace_logger,
            is_stub=is_stub,
        )
        self.llm_client = llm_client

    def _get_llm(self) -> LLMClient:
        if self.llm_client is not None:
            return self.llm_client
        return get_llm()

    def _load_system_prompt(self) -> str:
        if PROMPT_PATH.exists():
            try:
                return PROMPT_PATH.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning("Could not read prompt from %s: %s", PROMPT_PATH, e)
        return (
            "You are the Senior Industrial Maintenance Orchestrator. "
            "Synthesize sequential repair steps strictly grounded in retrieved evidence. "
            "Every step must cite valid evidence_ids."
        )

    def process(self, state: BlackboardState) -> Dict[str, Any]:
        """Dispatch based on the configured orchestration phase."""
        if self.phase == "plan":
            return self._process_plan(state)
        elif self.phase == "revise":
            return self._process_revise(state)
        elif self.phase == "finalize":
            return self._process_finalize(state)
        else:
            raise ValueError(f"Unknown orchestration phase: {self.phase}")

    def _process_plan(self, state: BlackboardState) -> Dict[str, Any]:
        """Phase 1: Evaluate evidence, check energy safety, rank conflicts, route, and synthesize steps."""
        raw_query = state.get("raw_query", "")
        machine_type = state.get("machine_type", "unspecified")
        selected_hyp = state.get("selected_hypothesis") or {}
        evidence_list = state.get("evidence") or []
        retrieval_conf = float(state.get("retrieval_confidence", 0.0))

        # 0. Load thresholds from configuration
        cfg: Dict[str, Any] = {}
        try:
            cfg = load_config()
        except Exception:
            pass
        sim_threshold = float(cfg.get("system", {}).get("abstention_similarity_threshold", 0.55))
        weights = cfg.get("conflict_ranking_weights", DEFAULT_CONFLICT_WEIGHTS)

        # 1. Check Retrieval Confidence & Similarity Abstention Threshold
        if not evidence_list or retrieval_conf < sim_threshold:
            reason = (
                f"Low retrieval similarity ({retrieval_conf:.2f} < {sim_threshold:.2f}); "
                "insufficient grounded knowledge to proceed safely."
            )
            self.trace_logger.log(
                agent=self.name,
                event="ORCHESTRATOR_ABSTAIN",
                input_data={"retrieval_confidence": retrieval_conf, "threshold": sim_threshold},
                output_data={"reason": reason},
                decision="ABSTAIN",
                confidence=retrieval_conf,
                trace_id=state.get("trace_id", "TR-UNKNOWN"),
                session_id=state.get("session_id", "SES-UNKNOWN"),
                stub=self.is_stub,
            )
            return {
                "candidate_steps": [],
                "abstained": True,
                "abstention_reason": reason,
                "decision": "ABSTAIN",
            }

        # 2. Check Energy Thresholds and LOTO/Bleed Compliance
        context_text = f"{raw_query} {selected_hyp.get('fault', '')} {' '.join(e.get('content', '') for e in evidence_list)}"
        compliant, loto_reason = check_energy_loto_compliance(context_text, evidence_list)
        if not compliant:
            self.trace_logger.log(
                agent=self.name,
                event="ORCHESTRATOR_ABSTAIN",
                input_data={"context": context_text[:200]},
                output_data={"reason": loto_reason},
                decision="ABSTAIN",
                trace_id=state.get("trace_id", "TR-UNKNOWN"),
                session_id=state.get("session_id", "SES-UNKNOWN"),
                stub=self.is_stub,
            )
            return {
                "candidate_steps": [],
                "abstained": True,
                "abstention_reason": loto_reason,
                "decision": "ABSTAIN",
            }

        # 3. Calculate Cross-Episode Conflict Ranking
        conflicts, failed_attempts_to_avoid = calculate_conflict_ranking(evidence_list, weights)
        self.trace_logger.log(
            agent=self.name,
            event="CONFLICT_RANKING_COMPLETED",
            input_data={"evidence_count": len(evidence_list)},
            output_data={
                "conflicts_count": len(conflicts),
                "failed_attempts_count": len(failed_attempts_to_avoid),
                "top_ranked_episode": conflicts[0]["episode_id"] if conflicts else None,
            },
            decision="CONFLICTS_RANKED",
            trace_id=state.get("trace_id", "TR-UNKNOWN"),
            session_id=state.get("session_id", "SES-UNKNOWN"),
            stub=self.is_stub,
        )

        # 4. Evaluate Adaptive Routing (FAST vs FULL)
        routing, routing_reason = evaluate_adaptive_routing(evidence_list, conflicts, similarity_threshold=0.85)
        self.trace_logger.log(
            agent=self.name,
            event="ROUTING_DECIDED",
            input_data={"top_similarity": retrieval_conf, "episodes_count": len(conflicts)},
            output_data={"routing": routing, "reason": routing_reason},
            decision=routing,
            trace_id=state.get("trace_id", "TR-UNKNOWN"),
            session_id=state.get("session_id", "SES-UNKNOWN"),
            stub=self.is_stub,
        )

        # 5. Synthesize Candidate Steps
        candidate_steps: List[Dict[str, Any]] = []
        is_llm_fallback = False

        if routing == "FAST":
            # Direct evidence mapping without LLM
            # Prefer sorting by step_order if available in metadata
            sorted_ev = sorted(
                evidence_list,
                key=lambda x: int((x.get("metadata") or {}).get("step_order", 99)),
            )
            for i, ev in enumerate(sorted_ev):
                candidate_steps.append({
                    "step_number": i + 1,
                    "instruction": ev.get("content", ""),
                    "evidence_ids": [ev.get("evidence_id")] if ev.get("evidence_id") else [],
                })
        else:
            # FULL path: Synthesize via LLM
            llm_client = self._get_llm()
            self.llm_name = getattr(llm_client, "model_name", "unknown")
            system_prompt = self._load_system_prompt()

            user_prompt = (
                f"Machine Type: {machine_type}\n"
                f"Worker Query / Symptoms: {raw_query}\n"
                f"Diagnosed Fault Hypothesis: {selected_hyp.get('fault', 'Unknown')}\n"
                f"Subsystem: {selected_hyp.get('subsystem', 'General')}\n\n"
                "Retrieved Grounded Evidence:\n"
            )
            for ev in evidence_list:
                user_prompt += (
                    f"- [{ev.get('evidence_id')}] (Episode: {ev.get('episode_id')}, "
                    f"Sim: {ev.get('similarity_score')}): {ev.get('content')}\n"
                )

            if failed_attempts_to_avoid:
                user_prompt += "\nDocumented Past Failed Attempts to Avoid:\n"
                for fa in failed_attempts_to_avoid:
                    user_prompt += f"- DO NOT DO: {fa}\n"

            user_prompt += (
                "\nSynthesize actionable, sequential steps for the technician. "
                "Every step must strictly cite the valid evidence_id(s) from the retrieved evidence above."
            )

            try:
                plan_output: OrchestrationPlanOutput = llm_client.generate_structured(
                    prompt=user_prompt,
                    response_model=OrchestrationPlanOutput,
                    system_prompt=system_prompt,
                )
                for s in plan_output.steps:
                    candidate_steps.append({
                        "step_number": s.step_number,
                        "instruction": s.instruction,
                        "evidence_ids": s.evidence_ids,
                    })
            except Exception as e:
                logger.warning("LLM step synthesis failed: %s; falling back to grounded evidence", e)
                is_llm_fallback = True
                # Grounded fallback: use direct evidence steps
                for i, ev in enumerate(evidence_list):
                    candidate_steps.append({
                        "step_number": i + 1,
                        "instruction": ev.get("content", ""),
                        "evidence_ids": [ev.get("evidence_id")] if ev.get("evidence_id") else [],
                    })

        # 6. Citation Integrity Validation
        valid_evidence_ids: Set[str] = {
            e.get("evidence_id") for e in evidence_list if e.get("evidence_id")
        }
        valid_steps, citation_coverage = validate_and_filter_citations(candidate_steps, valid_evidence_ids)

        self.trace_logger.log(
            agent=self.name,
            event="CITATION_VALIDATED",
            input_data={"total_candidate_steps": len(candidate_steps)},
            output_data={
                "valid_steps_count": len(valid_steps),
                "citation_coverage": citation_coverage,
                "is_fallback": is_llm_fallback,
            },
            decision="FALLBACK_USED" if is_llm_fallback else "CITATIONS_VERIFIED",
            llm="fallback" if is_llm_fallback else self.llm_name,
            trace_id=state.get("trace_id", "TR-UNKNOWN"),
            session_id=state.get("session_id", "SES-UNKNOWN"),
            stub=self.is_stub,
        )

        # If no valid cited steps remain, abstain
        if not valid_steps:
            abstain_reason = "No candidate repair steps with valid evidence citations remained after citation integrity check."
            return {
                "candidate_steps": [],
                "conflicts": conflicts,
                "failed_attempts_to_avoid": failed_attempts_to_avoid,
                "abstained": True,
                "abstention_reason": abstain_reason,
                "decision": "ABSTAIN",
            }

        return {
            "candidate_steps": valid_steps,
            "conflicts": conflicts,
            "failed_attempts_to_avoid": failed_attempts_to_avoid,
            "abstained": False,
            "abstention_reason": None,
            "decision": "PLAN_GENERATED",
            "metadata": {
                **state.get("metadata", {}),
                "routing": routing,
                "citation_coverage": citation_coverage,
                "llm_fallback": is_llm_fallback,
            },
        }

    def _process_revise(self, state: BlackboardState) -> Dict[str, Any]:
        """Phase 2: Revise candidate steps based on safety feedback (amendment/veto)."""
        current_steps = list(state.get("candidate_steps") or [])
        safety_verdict = state.get("safety_verdict") or {}
        violations = state.get("safety_violations") or []
        amendments = safety_verdict.get("suggested_amendments") or []

        # Read revision loop limits from config
        cfg: Dict[str, Any] = {}
        try:
            cfg = load_config()
        except Exception:
            pass
        max_loops = int(cfg.get("system", {}).get("safety_max_revision_loops", 2))
        loop_count = int(state.get("safety_loop_count", 0))

        # Apply amendments if present
        revised_steps: List[Dict[str, Any]] = []
        if amendments:
            # Prepend safety requirement as Step 1
            # Preserve valid citations from step 1 or note rule requirement
            first_amendment = amendments[0]
            existing_eids = current_steps[0].get("evidence_ids", []) if current_steps else []
            revised_steps.append({
                "step_number": 1,
                "instruction": f"MANDATORY SAFETY AMENDMENT: {first_amendment}",
                "evidence_ids": existing_eids,
            })
            for s in current_steps:
                s_copy = dict(s)
                s_copy["step_number"] = len(revised_steps) + 1
                revised_steps.append(s_copy)
        else:
            revised_steps = current_steps

        escalated = (loop_count >= max_loops)

        self.trace_logger.log(
            agent=self.name,
            event="PLAN_REVISED",
            input_data={"safety_loop_count": loop_count, "violations": violations},
            output_data={"revised_step_count": len(revised_steps), "escalated": escalated},
            decision="ESCALATED" if escalated else "REVISED",
            trace_id=state.get("trace_id", "TR-UNKNOWN"),
            session_id=state.get("session_id", "SES-UNKNOWN"),
            stub=self.is_stub,
        )

        return {
            "candidate_steps": revised_steps,
            "escalated_to_supervisor": escalated,
            "escalated": escalated,
            "decision": "PLAN_REVISED",
        }

    def _process_finalize(self, state: BlackboardState) -> Dict[str, Any]:
        """Phase 3: Assemble the FinalAnswer contract for technician delivery."""
        raw_query = state.get("raw_query", "")
        machine_type = state.get("machine_type", "unspecified")
        selected_hyp = state.get("selected_hypothesis") or {}
        abstained = bool(state.get("abstained", False))
        escalated = bool(state.get("escalated_to_supervisor", False) or state.get("escalated", False))
        abstention_reason = state.get("abstention_reason")
        trace_id = state.get("trace_id", "TR-UNKNOWN")

        safety_warnings = list(state.get("safety_violations") or [])
        failed_attempts = list(state.get("failed_attempts_to_avoid") or [])

        if abstained:
            final_ans = FinalAnswer(
                machine_type=machine_type,
                symptoms=[raw_query],
                diagnosed_cause=selected_hyp.get("fault", "Diagnosis incomplete / abstained"),
                steps=[],
                safety_warnings=safety_warnings,
                failed_attempts_to_avoid=failed_attempts,
                abstained=True,
                escalated=escalated,
                abstention_reason=abstention_reason or "Abstained during orchestration.",
                trace_id=trace_id,
            )
        else:
            candidate_steps = state.get("candidate_steps") or []
            action_steps = [
                ActionStep(
                    step_number=int(s.get("step_number", i + 1)),
                    instruction=str(s.get("instruction") or s.get("description", "")),
                    evidence_ids=list(s.get("evidence_ids") or []),
                )
                for i, s in enumerate(candidate_steps)
            ]
            final_ans = FinalAnswer(
                machine_type=machine_type,
                symptoms=[raw_query],
                diagnosed_cause=selected_hyp.get("fault", "Grounded repair procedure"),
                steps=action_steps,
                safety_warnings=safety_warnings,
                failed_attempts_to_avoid=failed_attempts,
                abstained=False,
                escalated=escalated,
                abstention_reason=None,
                trace_id=trace_id,
            )

        self.trace_logger.log(
            agent=self.name,
            event="FINAL_ANSWER_EMITTED",
            input_data={"query": raw_query, "machine": machine_type},
            output_data={
                "abstained": final_ans.abstained,
                "escalated": final_ans.escalated,
                "steps_count": len(final_ans.steps),
                "warnings_count": len(final_ans.safety_warnings),
            },
            decision="FINALIZED",
            trace_id=trace_id,
            session_id=state.get("session_id", "SES-UNKNOWN"),
            stub=self.is_stub,
        )

        return {
            "final_answer": final_ans.model_dump(),
            "decision": "FINALIZED",
        }
