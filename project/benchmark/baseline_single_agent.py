"""Single-agent baseline for Factory Memory (WP7, subtask 7.1).

Uses the exact same model (`get_llm()`), `HybridRetriever`, `KnowledgeStore`,
`hazards.json` reference text, `FinalAnswer` schema, and post-hoc citation-validity
filter as the multi-agent system, but with ONE consolidated LLM call per query and
without the deterministic `RuleEngine`, `SafetyAgent`, or `Orchestrator` loop.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional
import uuid

from pydantic import BaseModel, Field

from project.core.llm import FakeLLM, get_llm
from project.core.orchestration_helpers import extract_valid_evidence_ids, normalize_evidence
from project.core.registry import load_config
from project.core.schemas import ActionStep, FinalAnswer

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "baseline_v1.md"
HAZARDS_PATH = ROOT / "project" / "data" / "fault_catalogue" / "hazards.json"

_RETRIEVER_CACHE: Dict[Any, Any] = {}


class BaselineLLMOutput(BaseModel):
    """Structured output schema for the single-agent baseline LLM call."""

    machine_type: str = "unspecified"
    symptoms: List[str] = Field(default_factory=list)
    diagnosed_cause: Optional[str] = None
    alternative_hypotheses: List[Dict[str, Any]] = Field(default_factory=list)
    steps: List[ActionStep] = Field(default_factory=list)
    safety_warnings: List[str] = Field(default_factory=list)
    must_not_repeat: List[str] = Field(default_factory=list)
    abstained: bool = False
    escalated: bool = False
    abstention_reason: Optional[str] = None
    refusal: Optional[str] = None


def load_hazards_reference_text(hazards_path: Path = HAZARDS_PATH) -> str:
    """Format hazards.json as plain reference text for the single-agent prompt."""
    if not hazards_path.exists():
        return "No hazard catalogue available."
    hazards = json.loads(hazards_path.read_text(encoding="utf-8"))
    lines = []
    for h in hazards:
        hid = h.get("hazard_id", "")
        name = h.get("name", "")
        ctrl = h.get("required_control", "")
        kws = ", ".join(h.get("keywords") or [])
        lines.append(f"- {hid} ({name}): {ctrl} [keywords: {kws}]")
    return "\n".join(lines)


def get_baseline_prompt_hash(prompt_path: Path = DEFAULT_PROMPT_PATH) -> str:
    """Return short SHA-256 hash of the baseline prompt and implementation."""
    h = hashlib.sha256()
    for p in (prompt_path, Path(__file__).resolve()):
        if p.exists():
            h.update(p.read_bytes())
    return h.hexdigest()[:12]


def _get_retriever(retriever: Optional[Any] = None):
    if retriever is not None:
        return retriever
    from project.agents.a6_retrieval import HybridRetriever

    cached = _RETRIEVER_CACHE.get(HybridRetriever)
    if cached is None:
        cached = HybridRetriever()
        _RETRIEVER_CACHE[HybridRetriever] = cached
    return cached


def _format_evidence_block(evidence_dicts: List[Dict[str, Any]]) -> str:
    if not evidence_dicts:
        return "No historical repair episodes retrieved."
    blocks = []
    for i, ev in enumerate(evidence_dicts, start=1):
        eid = ev.get("evidence_id", f"EV-{i}")
        ep_id = ev.get("episode_id", "unknown")
        score = ev.get("similarity_score", 0.0)
        rec = (ev.get("metadata") or {}).get("record") or {}
        rc = rec.get("root_cause") or ev.get("root_cause") or "unknown"
        outcome = rec.get("outcome") or ev.get("outcome") or "unknown"
        failed = rec.get("failed_attempts") or []
        warnings = rec.get("safety_warnings") or []
        content = ev.get("content") or ""
        blocks.append(
            f"[{i}] evidence_id={eid} | episode_id={ep_id} | score={score:.3f} | "
            f"root_cause={rc} | outcome={outcome}\n"
            f"    content: {content}\n"
            f"    failed_attempts: {failed}\n"
            f"    safety_warnings: {warnings}"
        )
    return "\n".join(blocks)


def _apply_citation_filter(
    steps: List[ActionStep],
    valid_ids: set[str],
) -> tuple[List[ActionStep], List[str]]:
    """Drop unknown evidence IDs post-hoc without re-planning."""
    filtered_steps: List[ActionStep] = []
    dropped: List[str] = []
    for idx, step in enumerate(steps, start=1):
        kept_ids: List[str] = []
        for eid in step.evidence_ids:
            if eid in valid_ids:
                kept_ids.append(eid)
            else:
                dropped.append(eid)
        filtered_steps.append(
            ActionStep(
                step_number=idx,
                instruction=step.instruction,
                evidence_ids=kept_ids,
            )
        )
    return filtered_steps, dropped


def run_baseline_query(
    query: str,
    machine_type: Optional[str] = None,
    machine_id: Optional[str] = None,
    k: Optional[int] = None,
    allow_fake: bool = False,
    prompt_path: Path = DEFAULT_PROMPT_PATH,
    retriever: Optional[Any] = None,
    llm_client: Optional[Any] = None,
) -> Dict[str, Any]:
    """Run the single-agent baseline for one query (ONE LLM call, no re-planning)."""
    trace_id = f"TR-BASE-{uuid.uuid4().hex[:8].upper()}"
    t0 = time.perf_counter()

    cfg: Dict[str, Any] = {}
    try:
        cfg = load_config()
    except Exception:
        pass
    resolved_k = int(k if k is not None else cfg.get("retrieval", {}).get("k", 3))
    machine_arg = None if machine_type in (None, "", "unspecified") else str(machine_type)

    # 1. Same LLM via get_llm() — refuse FakeLLM unless explicitly allowed
    llm = llm_client if llm_client is not None else get_llm(force_fake=True if allow_fake else None)
    is_fake = isinstance(llm, FakeLLM)
    if is_fake and not allow_fake:
        raise RuntimeError(
            "Single-agent baseline refuses FakeLLM. Pass --fake-llm / allow_fake=True for offline tests."
        )
    if is_fake and allow_fake:
        print("[BASELINE WARNING] Running with FakeLLM — simulated output only, not valid for benchmark claims.")

    # 2. Same HybridRetriever top-k evidence from the same KnowledgeStore
    try:
        ret = _get_retriever(retriever)
        raw_evidence = ret.retrieve(
            query,
            machine_arg,
            resolved_k,
            machine_id=machine_id,
            trace_id=trace_id,
        )
    except Exception as exc:
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return {
            "final_answer": None,
            "evidence": [],
            "trace_id": trace_id,
            "latency_ms": latency_ms,
            "llm_calls": 0,
            "token_count": None,
            "degraded": True,
            "error": f"RetrievalError: {exc}",
            "dropped_evidence_ids": [],
            "flags": {"fake_llm": bool(allow_fake), "system": "baseline"},
        }

    evidence_dicts = [
        normalize_evidence(ev.model_dump() if hasattr(ev, "model_dump") else dict(ev))
        for ev in (raw_evidence or [])
    ]
    valid_ids = extract_valid_evidence_ids(evidence_dicts)

    system_prompt = prompt_path.read_text(encoding="utf-8")
    hazards_text = load_hazards_reference_text()
    evidence_text = _format_evidence_block(evidence_dicts)
    user_prompt = (
        f"Technician Query: {query}\n"
        f"Machine Type: {machine_arg or 'unspecified'}\n\n"
        f"## Hazard Catalogue Reference\n{hazards_text}\n\n"
        f"## Retrieved Historical Repair Evidence (top-{resolved_k})\n{evidence_text}\n"
    )

    # 3. ONE LLM call per query (with same retry/degradation policy)
    raw_out: Optional[BaselineLLMOutput] = None
    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            if is_fake:
                # Provide a deterministic FakeLLM FinalAnswer simulation when --fake-llm is explicitly enabled
                first_eid = next(iter(sorted(valid_ids)), None)
                steps = (
                    [ActionStep(step_number=1, instruction="Isolate, lock out and verify zero energy before inspection.", evidence_ids=[first_eid])]
                    if first_eid
                    else []
                )
                raw_out = BaselineLLMOutput(
                    diagnosed_cause="simulated baseline diagnosis",
                    alternative_hypotheses=[
                        {"cause": "simulated baseline diagnosis", "confidence": 0.7, "cause_family": "other"}
                    ],
                    steps=steps,
                    safety_warnings=["Follow standard lockout/tagout and PPE procedures."],
                    abstained=(len(steps) == 0),
                    escalated=(len(steps) == 0),
                    abstention_reason="No grounded evidence available." if len(steps) == 0 else None,
                )
            else:
                raw_out = llm.generate_structured(
                    prompt=user_prompt,
                    response_model=BaselineLLMOutput,
                    system_prompt=system_prompt,
                )
            last_exc = None
            break
        except Exception as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep((2 ** attempt) * 0.5)

    latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    token_count = getattr(llm, "last_token_count", None)

    if raw_out is None:
        return {
            "final_answer": None,
            "evidence": evidence_dicts,
            "trace_id": trace_id,
            "latency_ms": latency_ms,
            "llm_calls": 0,
            "token_count": token_count,
            "degraded": True,
            "error": f"LLMError: {last_exc}",
            "dropped_evidence_ids": [],
            "flags": {"fake_llm": is_fake, "system": "baseline"},
        }

    # 4. Same post-hoc citation-validity filter (drop unknown IDs, no re-planning)
    filtered_steps, dropped_ids = _apply_citation_filter(raw_out.steps, valid_ids)
    abstained = bool(raw_out.abstained or raw_out.refusal)
    escalated = bool(raw_out.escalated or abstained)
    if not abstained and not escalated and len(filtered_steps) == 0:
        abstained = True
        escalated = True
        abstention_reason = raw_out.abstention_reason or "No valid grounded steps after citation filtering."
    else:
        abstention_reason = raw_out.abstention_reason

    final_ans = FinalAnswer(
        machine_type=raw_out.machine_type or machine_arg or "unspecified",
        symptoms=list(raw_out.symptoms) or [query],
        diagnosed_cause=raw_out.diagnosed_cause or ("Refused unsafe request" if abstained else "Undetermined"),
        steps=filtered_steps,
        safety_warnings=list(raw_out.safety_warnings),
        must_not_repeat=list(raw_out.must_not_repeat),
        abstained=abstained,
        escalated=escalated,
        abstention_reason=abstention_reason,
        refusal=raw_out.refusal,
        alternative_hypotheses=list(raw_out.alternative_hypotheses)[:3],
        escalation_ticket_id=None,
        trace_id=trace_id,
    )

    return {
        "final_answer": final_ans.model_dump(mode="json"),
        "evidence": evidence_dicts,
        "trace_id": trace_id,
        "latency_ms": latency_ms,
        "llm_calls": 1,
        "token_count": token_count,
        "degraded": False,
        "error": None,
        "dropped_evidence_ids": dropped_ids,
        "flags": {"fake_llm": is_fake, "system": "baseline"},
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run single-agent baseline on a single query.")
    parser.add_argument("--query", required=True, help="Technician troubleshooting query")
    parser.add_argument("--machine-type", default=None, help="Optional machine type")
    parser.add_argument("--fake-llm", action="store_true", help="Explicitly allow FakeLLM (prints warning banner)")
    args = parser.parse_args(argv)

    os.environ["TEMPERATURE"] = "0.0"
    res = run_baseline_query(
        query=args.query,
        machine_type=args.machine_type,
        allow_fake=args.fake_llm,
    )
    print(json.dumps(res, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
