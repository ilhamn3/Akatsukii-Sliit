"""Helper utilities for Orchestrator Agent (PR I5).

Contains evidence normalisation for K6 HybridRetriever & stub evidence,
energy threshold parsing, LOTO / isolation compliance checks,
conflict ranking with per-term breakdown, adaptive routing evaluation,
and strict citation validation.
"""

from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional, Set, Tuple

DEFAULT_CONFLICT_WEIGHTS = {
    "outcome_worked": 0.40,
    "outcome_failed": -0.50,
    "verified_by_senior": 0.25,
    "recency_decay": 0.15,
    "vector_similarity": 0.20,
}

VOLTAGE_PATTERN = re.compile(r"\b(\d+(?:\.\d+)?)\s*(v|volts|vac|vdc|kv)\b", re.IGNORECASE)
PRESSURE_PATTERN = re.compile(r"\b(\d+(?:\.\d+)?)\s*(bar|psi|mpa)\b", re.IGNORECASE)
ISOLATION_PATTERN = re.compile(
    r"\b(loto|lockout|lock[- ]out|tag[- ]out|isolate|isolation|disconnect switch|bleed|depressuri[sz]e|zero[- ]energy|safety block)\b",
    re.IGNORECASE,
)

# Documented keyword rules for classifying free-text outcome descriptions.
FAILED_OUTCOME_KEYWORDS = (
    "failed",
    "unsuccessful",
    "not resolved",
    "recurred",
    "ineffective",
    "persisted",
)
WORKED_OUTCOME_KEYWORDS = (
    "worked",
    "success",
    "completed",
    "resolved",
    "restored",
    "returned to production",
    "fault cleared",
)


def classify_outcome(outcome_text: Optional[str]) -> str:
    """Classify free-text outcome into 'worked', 'failed', or 'unknown'."""
    if not outcome_text:
        return "unknown"
    text = str(outcome_text).strip().lower()
    if any(kw in text for kw in FAILED_OUTCOME_KEYWORDS):
        return "failed"
    if any(kw in text for kw in WORKED_OUTCOME_KEYWORDS):
        return "worked"
    return "unknown"


def normalize_evidence(ev: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize an Evidence dict from either real K6 HybridRetriever or stub retrieval.

    Handles both K6 metadata (`verified_by`, `verified_at`, `catalogue_hazards`,
    `steps`, `failed_attempts`, `outcome`, `record`) and stub metadata (`verified`,
    `recorded_at`, `hazards`, `outcome`). Missing keys default safely.
    """
    meta = ev.get("metadata") or {}
    verified = bool(meta.get("verified_by")) if "verified_by" in meta else bool(meta.get("verified", False))
    recorded_at = meta.get("verified_at") or meta.get("recorded_at")
    outcome_raw = meta.get("outcome")
    outcome_class = classify_outcome(outcome_raw)
    hazards = list(meta.get("catalogue_hazards") or meta.get("hazards") or [])
    failed_attempts = list(meta.get("failed_attempts") or [])
    steps = list(meta.get("steps") or [])
    safety_warnings = list(meta.get("safety_warnings") or [])

    return {
        "evidence_id": ev.get("evidence_id"),
        "episode_id": ev.get("episode_id", "EP-UNKNOWN"),
        "similarity_score": float(ev.get("similarity_score", 0.0)),
        "content": ev.get("content", ""),
        "verified": verified,
        "recorded_at": recorded_at,
        "outcome_raw": outcome_raw,
        "outcome_class": outcome_class,
        "hazards": hazards,
        "failed_attempts": failed_attempts,
        "steps": steps,
        "safety_warnings": safety_warnings,
        "kind": meta.get("kind"),
        "step_order": meta.get("step_order"),
    }


def extract_valid_evidence_ids(evidence_list: List[Dict[str, Any]]) -> Set[str]:
    """Collect all valid citation IDs: episode-level evidence_id plus every non-null step evidence_id."""
    valid_ids: Set[str] = set()
    for ev in evidence_list:
        eid = ev.get("evidence_id")
        if eid:
            valid_ids.add(str(eid))
        meta = ev.get("metadata") or {}
        for step in meta.get("steps") or []:
            if isinstance(step, dict):
                step_eid = step.get("evidence_id")
                if step_eid:
                    valid_ids.add(str(step_eid))
    return valid_ids


def parse_energy_thresholds(text: str) -> Dict[str, Any]:
    """Parse text for high electrical voltage (>50V) or hydraulic/pneumatic pressure (>10 bar)."""
    high_voltage = False
    max_voltage = 0.0
    for match in VOLTAGE_PATTERN.finditer(text):
        val = float(match.group(1))
        unit = match.group(2).lower()
        if unit == "kv":
            val *= 1000.0
        if val > max_voltage:
            max_voltage = val
        if val > 50.0:
            high_voltage = True

    high_pressure = False
    max_pressure_bar = 0.0
    for match in PRESSURE_PATTERN.finditer(text):
        val = float(match.group(1))
        unit = match.group(2).lower()
        val_in_bar = val
        if unit == "psi":
            val_in_bar = val / 14.5038
        elif unit == "mpa":
            val_in_bar = val * 10.0
        if val_in_bar > max_pressure_bar:
            max_pressure_bar = val_in_bar
        if val_in_bar > 10.0:
            high_pressure = True

    return {
        "high_voltage": high_voltage,
        "max_voltage": max_voltage,
        "high_pressure": high_pressure,
        "max_pressure_bar": max_pressure_bar,
    }


def check_energy_loto_compliance(
    context_text: str, evidence_list: List[Dict[str, Any]]
) -> Tuple[bool, Optional[str]]:
    """Verify that dangerous high energy (>50V or >10 bar) is preceded by verified LOTO/bleed steps."""
    energy = parse_energy_thresholds(context_text)
    if not (energy["high_voltage"] or energy["high_pressure"]):
        return True, None

    has_isolation = False
    for ev in evidence_list:
        content = ev.get("content", "")
        if ISOLATION_PATTERN.search(content):
            has_isolation = True
            break
        norm = normalize_evidence(ev)
        for st in norm["steps"]:
            desc = st.get("description", "") if isinstance(st, dict) else str(st)
            if ISOLATION_PATTERN.search(desc):
                has_isolation = True
                break
        if has_isolation:
            break

    if not has_isolation:
        hazard_types = []
        if energy["high_voltage"]:
            hazard_types.append(f"high voltage ({energy['max_voltage']:.0f}V > 50V)")
        if energy["high_pressure"]:
            hazard_types.append(f"high pressure ({energy['max_pressure_bar']:.1f} bar > 10 bar)")
        joined_hazards = " and ".join(hazard_types)
        reason = (
            f"Dangerous energy detected [{joined_hazards}] without verified Lockout/Tagout (LOTO) "
            "or depressurisation steps in retrieved evidence; abstaining to prevent critical safety hazard."
        )
        return False, reason

    return True, None


def calculate_conflict_ranking(
    evidence_list: List[Dict[str, Any]],
    weights: Optional[Dict[str, float]] = None,
    ref_date_iso: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Score and rank cross-episode evidence according to outcome, verification, recency, and vector similarity.

    Also extracts documented failed actions into failed_attempts_to_avoid using exact evidence text.
    """
    w = dict(DEFAULT_CONFLICT_WEIGHTS)
    if weights:
        w.update(weights)

    ref_date = datetime(2026, 11, 25, tzinfo=timezone.utc)
    if ref_date_iso:
        try:
            ref_date = datetime.fromisoformat(ref_date_iso.replace("Z", "+00:00"))
        except Exception:
            pass

    episodes: Dict[str, List[Dict[str, Any]]] = {}
    for ev in evidence_list:
        norm = normalize_evidence(ev)
        ep_id = norm["episode_id"]
        episodes.setdefault(ep_id, []).append(norm)

    conflicts: List[Dict[str, Any]] = []
    failed_attempts_to_avoid: List[str] = []

    for ep_id, items in episodes.items():
        first = items[0]
        outcome_class = first["outcome_class"]
        verified = first["verified"]

        # 1. Outcome term
        outcome_term = 0.0
        if outcome_class == "worked":
            outcome_term = float(w.get("outcome_worked", 0.40))
        elif outcome_class == "failed":
            outcome_term = float(w.get("outcome_failed", -0.50))

        # 2. Senior verification term
        verified_term = float(w.get("verified_by_senior", 0.25)) if verified else 0.0

        # 3. Recency decay term
        rec_str = first["recorded_at"]
        recency_factor = 0.5
        if rec_str:
            try:
                rec_dt = datetime.fromisoformat(str(rec_str).replace("Z", "+00:00"))
                delta_days = max(0, (ref_date - rec_dt).days)
                recency_factor = max(0.0, min(1.0, 1.0 - (delta_days / 365.0)))
            except Exception:
                recency_factor = 0.5
        recency_term = round(recency_factor * float(w.get("recency_decay", 0.15)), 4)

        # 4. Vector similarity term
        sim = max(item["similarity_score"] for item in items)
        similarity_term = round(sim * float(w.get("vector_similarity", 0.20)), 4)

        total_score = round(outcome_term + verified_term + recency_term + similarity_term, 4)

        conflicts.append({
            "episode_id": ep_id,
            "total_score": total_score,
            "breakdown": {
                "outcome_term": round(outcome_term, 4),
                "verified_term": round(verified_term, 4),
                "recency_term": round(recency_term, 4),
                "similarity_term": round(similarity_term, 4),
            },
            "outcome": outcome_class,
            "verified": verified,
            "top_similarity": round(sim, 4),
            "evidence_ids": [item["evidence_id"] for item in items if item["evidence_id"]],
        })

    conflicts.sort(key=lambda x: x["total_score"], reverse=True)

    # Extract exact failed attempts from normalized evidence
    for ev in evidence_list:
        norm = normalize_evidence(ev)
        ev_kind = str(norm.get("kind") or "").lower()
        if norm["outcome_class"] == "failed" or "failed" in ev_kind:
            content = norm["content"]
            if content and content not in failed_attempts_to_avoid:
                failed_attempts_to_avoid.append(content)

        for fa in norm["failed_attempts"]:
            if isinstance(fa, dict):
                action = fa.get("action", "")
                outcome = fa.get("outcome", "")
                why = fa.get("why_failed", "")
                if action and why:
                    text = f"{action} -> {outcome}; failed because {why}" if outcome else f"{action} (failed: {why})"
                else:
                    text = action or why
                if text and text not in failed_attempts_to_avoid:
                    failed_attempts_to_avoid.append(text)
            elif isinstance(fa, str) and fa not in failed_attempts_to_avoid:
                failed_attempts_to_avoid.append(fa)

    return conflicts, failed_attempts_to_avoid


def evaluate_adaptive_routing(
    evidence_list: List[Dict[str, Any]],
    conflicts: List[Dict[str, Any]],
    similarity_threshold: float = 0.85,
) -> Tuple[str, str]:
    """Determine whether to use FAST path (direct evidence steps) or FULL path (LLM synthesis).

    FAST requires:
    - Top similarity >= 0.85
    - Single episode
    - No conflict (no failed outcomes or negative scores)
    - No hazard flags in metadata
    """
    if not evidence_list:
        return "FULL", "No evidence retrieved; proceeding with full synthesis path."

    norms = [normalize_evidence(ev) for ev in evidence_list]
    top_sim = max(n["similarity_score"] for n in norms)
    unique_episodes = {n["episode_id"] for n in norms if n["episode_id"]}

    has_conflict = False
    for c in conflicts:
        if c.get("outcome") == "failed" or c.get("total_score", 0.0) < 0.0:
            has_conflict = True
            break
    if len(conflicts) > 1:
        outcomes = {c.get("outcome") for c in conflicts if c.get("outcome") in ("worked", "failed")}
        if "worked" in outcomes and "failed" in outcomes:
            has_conflict = True

    has_hazards = any(bool(n["hazards"]) for n in norms)

    if top_sim >= similarity_threshold and len(unique_episodes) <= 1 and not has_conflict and not has_hazards:
        return (
            "FAST",
            f"High similarity ({top_sim:.2f} >= {similarity_threshold}) single episode without conflicts or hazards; using direct grounded evidence.",
        )

    reasons = []
    if top_sim < similarity_threshold:
        reasons.append(f"similarity ({top_sim:.2f} < {similarity_threshold})")
    if len(unique_episodes) > 1:
        reasons.append(f"multiple episodes ({len(unique_episodes)})")
    if has_conflict:
        reasons.append("cross-episode conflict detected")
    if has_hazards:
        reasons.append("hazard tags present in evidence")

    return "FULL", f"Full LLM synthesis required: {', '.join(reasons)}."


def validate_and_filter_citations(
    steps: List[Dict[str, Any]],
    valid_evidence_ids: Set[str],
) -> Tuple[List[Dict[str, Any]], float]:
    """Filter citations to only valid retrieved evidence IDs (episode or step level).

    Drops steps that have no valid citations, renumbers remaining steps,
    and returns (filtered_steps, citation_coverage).
    """
    if not steps:
        return [], 0.0

    valid_steps = []
    total_steps = len(steps)

    for step in steps:
        raw_eids = step.get("evidence_ids") or []
        filtered_eids = [eid for eid in raw_eids if eid in valid_evidence_ids]
        if filtered_eids:
            step_copy = dict(step)
            step_copy["evidence_ids"] = filtered_eids
            step_copy["step_number"] = len(valid_steps) + 1
            valid_steps.append(step_copy)

    coverage = round(len(valid_steps) / total_steps, 4) if total_steps > 0 else 0.0
    return valid_steps, coverage
