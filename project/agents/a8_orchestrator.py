"""Agent 8: Orchestrator Agent (Repair Step Synthesis, LLM Revision & Delivery).

Synthesizes safe, grounded repair action steps by reconciling diagnostic hypotheses
with retrieved historical evidence chunks and catalogue hazard controls.
Enforces adaptive routing (FAST vs FULL), cross-episode conflict ranking,
strict citation validation, LLM-guided safety revision loops, safety-wins
finalization, and human escalation ticket logging.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Set
import uuid

from pydantic import BaseModel, Field

from project.agents.base import BaseAgent
from project.core.llm import LLMClient, get_llm
from project.core.orchestration_helpers import (
    DEFAULT_CONFLICT_WEIGHTS,
    calculate_conflict_ranking,
    check_energy_loto_compliance,
    evaluate_adaptive_routing,
    extract_valid_evidence_ids,
    normalize_evidence,
    validate_and_filter_citations,
)
from project.core.registry import load_config
from project.core.safety_rules.rule_engine import RuleEngine
from project.core.schemas import ActionStep, FinalAnswer
from project.core.state import BlackboardState
from project.core.trace import TraceLogger

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parent.parent / "core" / "prompts" / "orchestrator_v1.md"
ESCALATIONS_LOG_PATH = Path("logs/escalations.jsonl")


class SynthesizedStep(BaseModel):
    """Structured step schema produced during LLM orchestration."""

    step_number: int = Field(ge=1, description="1-indexed sequence number of the step")
    instruction: str = Field(..., min_length=3, description="Imperative instruction for the technician")
    evidence_ids: List[str] = Field(
        default_factory=list,
        description="Grounded evidence IDs supporting this instruction, or [] for [SAFETY CONTROL ...] steps",
    )


class OrchestrationPlanOutput(BaseModel):
    """Structured schema expected from the LLM synthesis / revision call."""

    diagnosed_cause: str = Field(..., min_length=3, description="Confirmed root cause or fault diagnosis")
    steps: List[SynthesizedStep] = Field(..., min_length=1, description="Synthesized action steps")
    safety_warnings: List[str] = Field(
        default_factory=list, description="Critical warnings or safety prerequisites"
    )


def _steps_from_evidence(evidence_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build ordered grounded steps from evidence metadata.steps (or content fallback for stubs)."""
    steps: List[Dict[str, Any]] = []
    has_structured_steps = any(bool((ev.get("metadata") or {}).get("steps")) for ev in evidence_list)

    if has_structured_steps:
        # Use the top-ranked episode's steps when multiple episodes exist to avoid duplicating identical procedures
        top_ev = evidence_list[0]
        ep_eid = top_ev.get("evidence_id")
        meta_steps = list((top_ev.get("metadata") or {}).get("steps") or [])
        meta_steps.sort(key=lambda s: int(s.get("step_number", 999)) if isinstance(s, dict) else 999)
        for s in meta_steps:
            if not isinstance(s, dict):
                continue
            desc = str(s.get("description") or "").strip()
            if not desc:
                continue
            eid = s.get("evidence_id") or ep_eid
            steps.append({
                "step_number": len(steps) + 1,
                "instruction": desc,
                "evidence_ids": [eid] if eid else [],
            })
        return steps

    sorted_ev = sorted(
        evidence_list,
        key=lambda x: int((x.get("metadata") or {}).get("step_order", 99)),
    )
    for i, ev in enumerate(sorted_ev):
        eid = ev.get("evidence_id")
        steps.append({
            "step_number": i + 1,
            "instruction": ev.get("content", ""),
            "evidence_ids": [eid] if eid else [],
        })
    return steps


def _collect_required_controls(evidence_list: List[Dict[str, Any]]) -> tuple[List[str], List[str]]:
    """Gather unique catalogue hazard IDs and required control strings from evidence and RuleEngine."""
    hazard_ids: List[str] = []
    controls: List[str] = []
    try:
        engine = RuleEngine()
        hazard_map = engine.hazard_map
    except Exception:
        hazard_map = {}

    for ev in evidence_list:
        norm = normalize_evidence(ev)
        for hid in norm["hazards"]:
            if hid and hid not in hazard_ids:
                hazard_ids.append(hid)
                rule = hazard_map.get(hid)
                if rule and rule.required_control:
                    ctrl_str = f"[{hid}] {rule.required_control}"
                    if ctrl_str not in controls:
                        controls.append(ctrl_str)
        for sw in norm["safety_warnings"]:
            sw_str = str(sw).strip()
            if sw_str and sw_str not in controls:
                controls.append(sw_str)
    return hazard_ids, controls


def _extract_top_root_cause(
    evidence_list: List[Dict[str, Any]], conflicts: List[Dict[str, Any]]
) -> Optional[str]:
    """Return record.root_cause of the top-ranked retrieved episode if present."""
    top_ep_id = conflicts[0]["episode_id"] if conflicts else None
    ordered = sorted(
        evidence_list,
        key=lambda e: 0 if e.get("episode_id") == top_ep_id else 1,
    )
    for ev in ordered:
        meta = ev.get("metadata") or {}
        rec = meta.get("record") or {}
        rc = rec.get("root_cause") or meta.get("root_cause")
        if rc and str(rc).strip():
            return str(rc).strip()
    return None


def _diagnoses_disagree(diag_fault: str, ev_root_cause: str) -> bool:
    """Return True if DiagnosticAgent's fault hypothesis and evidence root_cause have low overlap."""
    if not diag_fault or not ev_root_cause:
        return False
    a_tokens = {t for t in diag_fault.lower().split() if len(t) > 3}
    b_tokens = {t for t in ev_root_cause.lower().split() if len(t) > 3}
    if not a_tokens or not b_tokens:
        return diag_fault.strip().lower() != ev_root_cause.strip().lower()
    overlap = len(a_tokens & b_tokens) / min(len(a_tokens), len(b_tokens))
    return overlap < 0.35


def _filter_failed_attempts(
    steps: List[Dict[str, Any]], evidence_list: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Drop candidate steps that substantially repeat a documented failed_attempts action."""
    failed_actions: List[str] = []
    for ev in evidence_list:
        for fa in normalize_evidence(ev)["failed_attempts"]:
            if isinstance(fa, dict) and fa.get("action"):
                failed_actions.append(str(fa["action"]).strip().lower())
    if not failed_actions:
        return steps

    kept: List[Dict[str, Any]] = []
    for s in steps:
        instr_low = str(s.get("instruction") or "").lower()
        # Do not drop safety control steps or negative warnings ("do not ...")
        if instr_low.startswith("[safety") or "do not" in instr_low or "never " in instr_low:
            kept.append(s)
            continue
        repeats_failed = any(len(act) >= 8 and act in instr_low for act in failed_actions)
        if not repeats_failed:
            kept.append(s)

    for idx, s in enumerate(kept, 1):
        s["step_number"] = idx
    return kept


def _is_safety_control_step(instruction: str) -> bool:
    low = instruction.strip().lower()
    return low.startswith("[safety control") or low.startswith("[safety amendment")


_FAULTS_CATALOGUE_CACHE: Optional[List[Dict[str, Any]]] = None


def _get_faults_catalogue() -> List[Dict[str, Any]]:
    global _FAULTS_CATALOGUE_CACHE
    if _FAULTS_CATALOGUE_CACHE is None:
        faults_path = Path(__file__).resolve().parent.parent / "data" / "fault_catalogue" / "faults.json"
        if faults_path.exists():
            import json as _json
            _FAULTS_CATALOGUE_CACHE = _json.loads(faults_path.read_text(encoding="utf-8"))
        else:
            _FAULTS_CATALOGUE_CACHE = []
    return _FAULTS_CATALOGUE_CACHE


def _collect_required_hazard_ids(
    state: BlackboardState,
    diagnosed_cause: Optional[str],
    rule_ids: List[str],
) -> List[str]:
    """Collect deduped hazard IDs from state, retrieved/cited episodes, faults.json, and rule_ids."""
    ordered: List[str] = []
    seen: set[str] = set()

    def _add(hid: Optional[str]) -> None:
        if not hid:
            return
        h = str(hid).strip().upper()
        if h.startswith("SR-"):
            h = h[3:]
        if h.startswith("H") and h not in seen:
            seen.add(h)
            ordered.append(h)

    for h in (state.get("hazard_ids") or []):
        _add(h)

    for ev in (state.get("evidence") or []):
        ev_meta = ev.get("metadata") or {}
        for h in (ev_meta.get("catalogue_hazards") or []):
            _add(h)
        rec = ev_meta.get("record") or {}
        for h in (rec.get("hazards") or []):
            _add(h)

    selected_hyp = state.get("selected_hypothesis") or {}
    hyp_fid = str(selected_hyp.get("fault_id") or "").strip().upper()
    diag_low = str(diagnosed_cause or "").strip().lower()
    for f in _get_faults_catalogue():
        fid = str(f.get("fault_id") or "").strip().upper()
        rc_low = str(f.get("root_cause") or "").strip().lower()
        name_low = str(f.get("name") or "").strip().lower()
        if (hyp_fid and fid == hyp_fid) or (
            diag_low and ((rc_low and (rc_low in diag_low or diag_low in rc_low)) or (name_low and name_low in diag_low))
        ):
            for h in (f.get("hazards") or []):
                _add(h)

    for rid in rule_ids:
        if str(rid).upper().startswith("SR-H"):
            _add(rid)

    return ordered


def _inject_finalize_catalogue_controls(
    action_steps: List[ActionStep],
    safety_warnings: List[str],
    hazard_ids: List[str],
) -> tuple[List[ActionStep], List[str]]:
    """Deterministically ensure all required catalogue controls appear before repair steps and in warnings."""
    if not hazard_ids:
        return action_steps, safety_warnings
    try:
        engine = RuleEngine()
    except Exception:
        return action_steps, safety_warnings

    canonical_controls: set[str] = set()
    for hid, r in engine.hazard_map.items():
        if r.required_control:
            canonical_controls.add(f"[safety control {hid.lower()}] {r.required_control.strip().lower()}")
            canonical_controls.add(f"[safety control {r.id.lower()}] {r.required_control.strip().lower()}")
            canonical_controls.add(f"[safety amendment {r.id.lower()}] {r.required_control.strip().lower()}")
    for r in engine.rules:
        if r.required_control:
            canonical_controls.add(f"[safety control {r.id.lower()}] {r.required_control.strip().lower()}")
            canonical_controls.add(f"[safety amendment {r.id.lower()}] {r.required_control.strip().lower()}")

    existing_ctrl_steps: List[ActionStep] = []
    repair_steps: List[ActionStep] = []
    seen_ctrl_tags: set[str] = set()
    seen_ctrl_texts: set[str] = set()

    for s in action_steps:
        inst = s.instruction.strip()
        if _is_safety_control_step(inst):
            if inst.lower() in canonical_controls:
                existing_ctrl_steps.append(ActionStep(step_number=1, instruction=inst, evidence_ids=[]))
                seen_ctrl_texts.add(inst.lower())
                for hid in hazard_ids:
                    if f" {hid}]" in inst or f"{hid}:" in inst:
                        seen_ctrl_tags.add(hid)
            else:
                # Relabel LLM-authored steps that carry the control prefix without matching catalogue text
                import re as _re
                relabeled = _re.sub(
                    r"^\[SAFETY\s+(?:CONTROL|AMENDMENT)[^\]]*\]\s*",
                    "",
                    inst,
                    flags=_re.IGNORECASE,
                ).strip()
                if relabeled:
                    repair_steps.append(
                        ActionStep(step_number=s.step_number, instruction=relabeled, evidence_ids=list(s.evidence_ids))
                    )
        else:
            repair_steps.append(s)

    new_ctrl_steps: List[ActionStep] = []
    updated_warnings = list(safety_warnings)
    warn_corpus = "\n".join(updated_warnings).lower()

    for hid in hazard_ids:
        rule = engine.hazard_map.get(hid)
        if not rule or not rule.required_control:
            continue
        ctrl_text = rule.required_control.strip()
        ctrl_inst = f"[SAFETY CONTROL {hid}] {ctrl_text}"
        warn_entry = f"{hid}: {ctrl_text}"

        if hid not in seen_ctrl_tags and ctrl_inst.lower() not in seen_ctrl_texts and ctrl_text.lower() not in seen_ctrl_texts:
            new_ctrl_steps.append(ActionStep(step_number=1, instruction=ctrl_inst, evidence_ids=[]))
            seen_ctrl_tags.add(hid)
            seen_ctrl_texts.add(ctrl_inst.lower())

        if hid.lower() not in warn_corpus and ctrl_text.lower() not in warn_corpus:
            updated_warnings.append(warn_entry)
            warn_corpus += f"\n{warn_entry.lower()}"

    ordered_steps = existing_ctrl_steps + new_ctrl_steps + repair_steps
    renumbered = [
        ActionStep(step_number=idx, instruction=s.instruction, evidence_ids=list(s.evidence_ids))
        for idx, s in enumerate(ordered_steps, start=1)
    ]
    return renumbered, updated_warnings


def _ensure_catalogue_controls(
    steps: List[Dict[str, Any]],
    hazard_ids: List[str],
    extra_rule_ids: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Deterministically prepend any missing required controls from RuleEngine before intrusive steps."""
    if not hazard_ids and not extra_rule_ids:
        return steps
    try:
        engine = RuleEngine()
    except Exception:
        return steps

    rule_by_id = {r.id: r for r in engine.rules}
    target_rules = []
    seen = set()
    for hid in hazard_ids:
        r = engine.hazard_map.get(hid)
        if r and r.id not in seen:
            target_rules.append(r)
            seen.add(r.id)
    for rid in (extra_rule_ids or []):
        r = rule_by_id.get(rid)
        if r and r.id not in seen:
            target_rules.append(r)
            seen.add(r.id)

    if not target_rules:
        return steps

    import re
    norm_text = " \n ".join(str(s.get("instruction") or "").lower() for s in steps)
    prepended: List[Dict[str, Any]] = []
    for r in target_rules:
        satisfied = any(re.search(pat, norm_text) for pat in (r.required_step_patterns or []))
        if not satisfied and r.required_control:
            tag = r.hazard_id or r.id
            prepended.append({
                "step_number": 0,
                "instruction": f"[SAFETY CONTROL {tag}] {r.required_control}",
                "evidence_ids": [],
            })

    combined = prepended + list(steps)
    for idx, s in enumerate(combined, 1):
        s["step_number"] = idx
    return combined


def _write_escalation_ticket(
    *,
    trace_id: str,
    query: str,
    machine: str,
    reasons: List[str],
    rule_ids: List[str],
) -> str:
    """Append an escalation ticket JSONL record to logs/escalations.jsonl and return ticket_id."""
    suffix = trace_id.split("-")[-1] if "-" in trace_id and len(trace_id) > 4 else uuid.uuid4().hex[:8].upper()
    ticket_id = f"ESC-{suffix}"
    ESCALATIONS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ticket_id": ticket_id,
        "trace_id": trace_id,
        "query": query,
        "machine": machine,
        "reasons": reasons,
        "rule_ids": rule_ids,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with open(ESCALATIONS_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return ticket_id


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
            "Every non-control step must cite valid evidence_ids."
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
        trace_id = state.get("trace_id", "TR-UNKNOWN")
        session_id = state.get("session_id", "SES-UNKNOWN")

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
                trace_id=trace_id,
                session_id=session_id,
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
                trace_id=trace_id,
                session_id=session_id,
                stub=self.is_stub,
            )
            return {
                "candidate_steps": [],
                "abstained": True,
                "abstention_reason": loto_reason,
                "decision": "ABSTAIN",
            }

        # 3. Calculate Cross-Episode Conflict Ranking & Extract Top Evidence Root Cause
        conflicts, failed_attempts_to_avoid = calculate_conflict_ranking(evidence_list, weights)
        evidence_root_cause = _extract_top_root_cause(evidence_list, conflicts)
        diag_fault = str(selected_hyp.get("fault") or "").strip()
        if evidence_root_cause and diag_fault and _diagnoses_disagree(diag_fault, evidence_root_cause):
            self.trace_logger.log(
                agent=self.name,
                event="DIAGNOSIS_DISAGREEMENT",
                input_data={"diagnostic_hypothesis": diag_fault},
                output_data={"evidence_root_cause": evidence_root_cause},
                decision="USE_EVIDENCE_ROOT_CAUSE",
                trace_id=trace_id,
                session_id=session_id,
                stub=self.is_stub,
            )

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
            trace_id=trace_id,
            session_id=session_id,
            stub=self.is_stub,
        )

        # 4. Collect Required Safety Controls & Hazards from Evidence + RuleEngine
        hazard_ids, required_controls = _collect_required_controls(evidence_list)

        # 5. Evaluate Adaptive Routing (FAST vs FULL)
        routing, routing_reason = evaluate_adaptive_routing(evidence_list, conflicts, similarity_threshold=0.85)
        self.trace_logger.log(
            agent=self.name,
            event="ROUTING_DECIDED",
            input_data={"top_similarity": retrieval_conf, "episodes_count": len(conflicts)},
            output_data={"routing": routing, "reason": routing_reason},
            decision=routing,
            trace_id=trace_id,
            session_id=session_id,
            stub=self.is_stub,
        )

        # 6. Synthesize Candidate Steps
        candidate_steps: List[Dict[str, Any]] = []
        is_llm_fallback = False

        if routing == "FAST":
            candidate_steps = _steps_from_evidence(evidence_list)
        else:
            llm_client = self._get_llm()
            self.llm_name = getattr(llm_client, "model_name", "unknown")
            system_prompt = self._load_system_prompt()

            user_prompt = (
                f"Machine Type: {machine_type}\n"
                f"Worker Query / Symptoms: {raw_query}\n"
                f"Primary Evidence Root Cause: {evidence_root_cause or diag_fault or 'Unknown'}\n"
                f"Diagnostic Hypothesis (alternative): {diag_fault or 'Unknown'}\n\n"
                "Retrieved Grounded Evidence:\n"
            )
            for ev in evidence_list:
                user_prompt += (
                    f"- [{ev.get('evidence_id')}] (Episode: {ev.get('episode_id')}, "
                    f"Sim: {ev.get('similarity_score')}): {ev.get('content')}\n"
                )
                meta_steps = (ev.get("metadata") or {}).get("steps") or []
                if meta_steps:
                    user_prompt += "  Documented Steps with Evidence IDs:\n"
                    for st in meta_steps:
                        if isinstance(st, dict):
                            st_eid = st.get("evidence_id") or ev.get("evidence_id")
                            user_prompt += f"    * Step {st.get('step_number')} [{st_eid}]: {st.get('description')}\n"

            if required_controls:
                user_prompt += "\nMandatory Safety Controls (MUST appear BEFORE any intrusive/disassembly step):\n"
                for ctrl in required_controls:
                    user_prompt += f"- {ctrl}\n"

            if failed_attempts_to_avoid:
                user_prompt += "\nDocumented Past Failed Attempts to Avoid (DO NOT recommend these):\n"
                for fa in failed_attempts_to_avoid:
                    user_prompt += f"- DO NOT DO: {fa}\n"

            user_prompt += (
                "\nSynthesize actionable, sequential steps for the technician. "
                "Place all isolation / lockout / zero-energy / depressurisation / safety-block steps FIRST "
                "before any physical disassembly. Every step must strictly cite valid evidence_id(s) above."
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
                candidate_steps = _steps_from_evidence(evidence_list)

        # 7. Filter Failed Attempts, Validate Citations, and Ensure Required Catalogue Controls First
        candidate_steps = _filter_failed_attempts(candidate_steps, evidence_list)
        valid_evidence_ids: Set[str] = extract_valid_evidence_ids(evidence_list)
        valid_steps, citation_coverage = validate_and_filter_citations(candidate_steps, valid_evidence_ids)
        if valid_steps:
            valid_steps = _ensure_catalogue_controls(valid_steps, hazard_ids)

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
            trace_id=trace_id,
            session_id=session_id,
            stub=self.is_stub,
        )

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
            "hazard_ids": hazard_ids,
            "abstained": False,
            "abstention_reason": None,
            "decision": "PLAN_GENERATED",
            "metadata": {
                **dict(state.get("metadata") or {}),
                "routing": routing,
                "citation_coverage": citation_coverage,
                "llm_fallback": is_llm_fallback,
                "evidence_root_cause": evidence_root_cause,
            },
        }

    def _process_revise(self, state: BlackboardState) -> Dict[str, Any]:
        """Phase 2: LLM-guided revision incorporating all safety violations, amendments, and rule IDs."""
        current_steps = list(state.get("candidate_steps") or [])
        safety_verdict = state.get("safety_verdict") or {}
        violations = list(state.get("safety_violations") or [])
        amendments = list(safety_verdict.get("suggested_amendments") or [])
        rule_ids = list(safety_verdict.get("rule_ids") or [])
        verdict = str(safety_verdict.get("verdict") or "").lower()
        evidence_list = list(state.get("evidence") or [])
        trace_id = state.get("trace_id", "TR-UNKNOWN")
        session_id = state.get("session_id", "SES-UNKNOWN")

        cfg: Dict[str, Any] = {}
        try:
            cfg = load_config()
        except Exception:
            pass
        max_loops = int(cfg.get("system", {}).get("safety_max_revision_loops", 2))
        loop_count = int(state.get("safety_loop_count", 0))

        # Hard veto with no suggested amendments and no violations cannot be fixed by looping: escalate immediately.
        if verdict == "veto" and not amendments and not violations:
            self.trace_logger.log(
                agent=self.name,
                event="PLAN_ESCALATED",
                input_data={"safety_loop_count": loop_count, "verdict": verdict, "violations": violations},
                output_data={"escalated": True, "reason": "veto with no suggested amendments"},
                decision="ESCALATED",
                trace_id=trace_id,
                session_id=session_id,
                stub=self.is_stub,
            )
            return {
                "candidate_steps": current_steps,
                "escalated_to_supervisor": True,
                "escalated": True,
                "decision": "ESCALATED",
            }

        valid_evidence_ids = extract_valid_evidence_ids(evidence_list)
        revised_steps: List[Dict[str, Any]] = []
        llm_revise_used = False

        # Try LLM-guided revision using all safety violations, amendments, and rule IDs
        try:
            llm_client = self._get_llm()
            self.llm_name = getattr(llm_client, "model_name", "unknown")
            system_prompt = self._load_system_prompt()

            steps_text = "\n".join(
                f"Step {s.get('step_number')}: {s.get('instruction')} [Citations: {', '.join(s.get('evidence_ids') or [])}]"
                for s in current_steps
            )
            prompt = (
                f"Machine Type: {state.get('machine_type', 'unspecified')}\n"
                f"Query: {state.get('raw_query', '')}\n\n"
                f"Current Candidate Steps:\n{steps_text}\n\n"
                f"Safety Verdict: {verdict}\n"
                f"Triggered Rule IDs: {', '.join(rule_ids) if rule_ids else 'None'}\n"
                f"Safety Violations:\n" + "\n".join(f"- {v}" for v in violations) + "\n\n"
                f"Suggested Amendments:\n" + "\n".join(f"- {a}" for a in amendments) + "\n\n"
                f"Valid Grounded Evidence IDs: {', '.join(sorted(valid_evidence_ids))}\n\n"
                "Produce a FULL revised ordered plan that resolves ALL safety violations and amendments.\n"
                "Rules:\n"
                "1. Any step added from a safety rule/control MUST be labelled '[SAFETY CONTROL <rule/hazard id>] <instruction>' with evidence_ids=[].\n"
                "2. All safety isolation / LOTO / depressurisation / zero-speed / safety-block steps MUST appear BEFORE intrusive work.\n"
                "3. Every non-control step MUST cite valid evidence_ids from the list above; do not invent or borrow IDs."
            )

            rev_output: OrchestrationPlanOutput = llm_client.generate_structured(
                prompt=prompt,
                response_model=OrchestrationPlanOutput,
                system_prompt=system_prompt,
            )
            llm_revise_used = True

            for s in rev_output.steps:
                instr = s.instruction.strip()
                if _is_safety_control_step(instr):
                    revised_steps.append({
                        "step_number": len(revised_steps) + 1,
                        "instruction": instr,
                        "evidence_ids": [],
                    })
                else:
                    filtered_eids = [eid for eid in s.evidence_ids if eid in valid_evidence_ids]
                    if filtered_eids:
                        revised_steps.append({
                            "step_number": len(revised_steps) + 1,
                            "instruction": instr,
                            "evidence_ids": filtered_eids,
                        })
        except Exception as e:
            logger.warning("LLM revise failed: %s; using deterministic safety-control prepend fallback", e)

        # Deterministic fallback if LLM failed or yielded no steps
        if not revised_steps:
            rule_tag = rule_ids[0] if rule_ids else "SAFETY"
            for am in amendments:
                revised_steps.append({
                    "step_number": len(revised_steps) + 1,
                    "instruction": f"[SAFETY CONTROL {rule_tag}] {am}",
                    "evidence_ids": [],
                })
            for s in current_steps:
                s_copy = dict(s)
                instr = str(s_copy.get("instruction") or "")
                if _is_safety_control_step(instr):
                    s_copy["evidence_ids"] = []
                    s_copy["step_number"] = len(revised_steps) + 1
                    revised_steps.append(s_copy)
                else:
                    filtered_eids = [eid for eid in (s_copy.get("evidence_ids") or []) if eid in valid_evidence_ids]
                    if filtered_eids:
                        s_copy["evidence_ids"] = filtered_eids
                        s_copy["step_number"] = len(revised_steps) + 1
                        revised_steps.append(s_copy)

        # Ensure any deterministic rule controls triggered in SafetyAgent are present at the front
        if revised_steps:
            revised_steps = _ensure_catalogue_controls(
                revised_steps,
                list(state.get("hazard_ids") or []),
                extra_rule_ids=rule_ids,
            )

        escalated = (loop_count >= max_loops)

        self.trace_logger.log(
            agent=self.name,
            event="PLAN_REVISED",
            input_data={"safety_loop_count": loop_count, "violations": violations, "rule_ids": rule_ids},
            output_data={
                "revised_step_count": len(revised_steps),
                "escalated": escalated,
                "llm_revise_used": llm_revise_used,
            },
            decision="ESCALATED" if escalated else "REVISED",
            llm=self.llm_name if llm_revise_used else "fallback",
            trace_id=trace_id,
            session_id=session_id,
            stub=self.is_stub,
        )

        return {
            "candidate_steps": revised_steps,
            "escalated_to_supervisor": escalated,
            "escalated": escalated,
            "decision": "ESCALATED" if escalated else "PLAN_REVISED",
        }

    def _process_finalize(self, state: BlackboardState) -> Dict[str, Any]:
        """Phase 3: Assemble the FinalAnswer contract. Safety outcome always wins; write escalation tickets."""
        raw_query = state.get("raw_query", "")
        machine_type = state.get("machine_type", "unspecified")
        selected_hyp = state.get("selected_hypothesis") or {}
        ranked_hyps = list(state.get("ranked_hypotheses") or [])
        plan_abstained = bool(state.get("abstained", False))
        safety_approved = bool(state.get("safety_approved", False))
        safety_verdict = state.get("safety_verdict") or {}
        trace_id = state.get("trace_id", "TR-UNKNOWN")
        session_id = state.get("session_id", "SES-UNKNOWN")
        meta = dict(state.get("metadata") or {})

        evidence_root_cause = meta.get("evidence_root_cause")
        diagnosed_cause = (
            evidence_root_cause
            if (evidence_root_cause and not plan_abstained)
            else selected_hyp.get("fault", "Diagnosis incomplete / abstained" if plan_abstained else "Grounded repair procedure")
        )

        safety_warnings = list(state.get("safety_violations") or [])
        failed_attempts = list(state.get("failed_attempts_to_avoid") or [])
        refusal = meta.get("refusal") or (state.get("final_answer") or {}).get("refusal")
        if refusal and refusal not in safety_warnings:
            safety_warnings.insert(0, refusal)

        llm_check_status = str(meta.get("llm_check") or "")
        if llm_check_status.startswith("skipped"):
            unavail_msg = "LLM safety oversight unavailable: deterministic rules only"
            if unavail_msg not in safety_warnings:
                safety_warnings.append(unavail_msg)
        if meta.get("llm_intent_unavailable"):
            intent_msg = "LLM intent check unavailable"
            if intent_msg not in safety_warnings:
                safety_warnings.append(intent_msg)
        for note in meta.get("informational_notes") or []:
            info_msg = f"[INFO NOTE] {note}"
            if info_msg not in safety_warnings:
                safety_warnings.append(info_msg)

        cfg: Dict[str, Any] = {}
        try:
            cfg = load_config()
        except Exception:
            pass
        llm_only_concerns_block = bool(cfg.get("system", {}).get("llm_only_concerns_block", False))
        rule_ids = list(safety_verdict.get("rule_ids") or [])
        has_det_violations = bool(meta.get("has_det_violations", False)) or any(
            str(rid).startswith("SR-") and rid != "SR-X05" for rid in rule_ids
        )

        escalated = bool(state.get("escalated_to_supervisor", False) or state.get("escalated", False))
        if not plan_abstained and not safety_approved:
            escalated = True

        ticket_id: Optional[str] = None
        if escalated:
            ticket_id = _write_escalation_ticket(
                trace_id=trace_id,
                query=raw_query,
                machine=machine_type,
                reasons=safety_warnings or [str(safety_verdict.get("reason") or "Safety escalation")],
                rule_ids=rule_ids,
            )

        if plan_abstained:
            final_ans = FinalAnswer(
                machine_type=machine_type,
                symptoms=[raw_query],
                diagnosed_cause=diagnosed_cause,
                steps=[],
                safety_warnings=safety_warnings,
                failed_attempts_to_avoid=failed_attempts,
                abstained=True,
                escalated=escalated,
                abstention_reason=state.get("abstention_reason") or "Abstained during orchestration.",
                refusal=refusal,
                alternative_hypotheses=ranked_hyps,
                escalation_ticket_id=ticket_id,
                trace_id=trace_id,
            )
        elif not safety_approved and (has_det_violations or llm_only_concerns_block or not state.get("candidate_steps")):
            safety_reason = (
                safety_verdict.get("reason")
                or state.get("abstention_reason")
                or "Safety verification did not approve the candidate procedure."
            )
            final_ans = FinalAnswer(
                machine_type=machine_type,
                symptoms=[raw_query],
                diagnosed_cause=diagnosed_cause,
                steps=[],
                safety_warnings=safety_warnings,
                failed_attempts_to_avoid=failed_attempts,
                abstained=True,
                escalated=True,
                abstention_reason=safety_reason,
                refusal=refusal,
                alternative_hypotheses=ranked_hyps,
                escalation_ticket_id=ticket_id,
                trace_id=trace_id,
            )
        else:
            # Either safety_approved=True, OR only LLM-only advisory concerns remained (llm_only_concerns_block=False)
            if not safety_approved:
                adv_note = "LLM advisory concerns unresolved after max revision loops; supervisor review recommended."
                if adv_note not in safety_warnings:
                    safety_warnings.append(adv_note)
            candidate_steps = state.get("candidate_steps") or []
            action_steps = [
                ActionStep(
                    step_number=int(s.get("step_number", i + 1)),
                    instruction=str(s.get("instruction") or s.get("description", "")),
                    evidence_ids=list(s.get("evidence_ids") or []),
                )
                for i, s in enumerate(candidate_steps)
            ]
            required_hazards = _collect_required_hazard_ids(state, diagnosed_cause, rule_ids)
            action_steps, safety_warnings = _inject_finalize_catalogue_controls(
                action_steps, safety_warnings, required_hazards
            )
            final_ans = FinalAnswer(
                machine_type=machine_type,
                symptoms=[raw_query],
                diagnosed_cause=diagnosed_cause,
                steps=action_steps,
                safety_warnings=safety_warnings,
                failed_attempts_to_avoid=failed_attempts,
                abstained=False,
                escalated=escalated,
                abstention_reason=None,
                refusal=refusal,
                alternative_hypotheses=ranked_hyps,
                escalation_ticket_id=ticket_id,
                trace_id=trace_id,
            )

        self.trace_logger.log(
            agent=self.name,
            event="FINAL_ANSWER_EMITTED",
            input_data={"query": raw_query, "machine": machine_type},
            output_data={
                "abstained": final_ans.abstained,
                "escalated": final_ans.escalated,
                "escalation_ticket_id": ticket_id,
                "refusal": refusal,
                "steps_count": len(final_ans.steps),
                "warnings_count": len(final_ans.safety_warnings),
            },
            decision="FINALIZED",
            trace_id=trace_id,
            session_id=session_id,
            stub=self.is_stub,
        )

        return {
            "final_answer": final_ans.model_dump(),
            "abstained": final_ans.abstained,
            "escalated": final_ans.escalated,
            "abstention_reason": final_ans.abstention_reason,
            "decision": "FINALIZED",
        }
