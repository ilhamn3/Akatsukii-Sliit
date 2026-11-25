"""Helper utilities for Orchestrator Agent (PR I5).

Contains energy threshold parsing, LOTO / isolation compliance checks,
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
    """Verify that dangerous high energy (>50V or >10 bar) is preceded by verified LOTO/bleed steps.

    Returns:
        (is_compliant, abstention_reason_or_none)
    """
    energy = parse_energy_thresholds(context_text)
    if not (energy["high_voltage"] or energy["high_pressure"]):
        return True, None

    # High energy is detected. Check if any retrieved evidence contains isolation/LOTO/bleed.
    has_isolation = False
    for ev in evidence_list:
        content = ev.get("content", "")
        if ISOLATION_PATTERN.search(content):
            has_isolation = True
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
    """Score and rank cross-episode evidence according to outcome, seniority verification, recency, and vector similarity.

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

    # Group evidence items by episode_id
    episodes: Dict[str, List[Dict[str, Any]]] = {}
    for ev in evidence_list:
        ep_id = ev.get("episode_id", "EP-UNKNOWN")
        episodes.setdefault(ep_id, []).append(ev)

    conflicts: List[Dict[str, Any]] = []
    failed_attempts_to_avoid: List[str] = []

    for ep_id, items in episodes.items():
        meta = items[0].get("metadata") or {}
        outcome = str(meta.get("outcome", "")).lower()
        verified = bool(meta.get("verified", False))

        # 1. Outcome term
        outcome_term = 0.0
        if "worked" in outcome or "success" in outcome:
            outcome_term = float(w.get("outcome_worked", 0.40))
        elif "failed" in outcome or "ineffective" in outcome:
            outcome_term = float(w.get("outcome_failed", -0.50))

        # 2. Senior verification term
        verified_term = float(w.get("verified_by_senior", 0.25)) if verified else 0.0

        # 3. Recency decay term
        rec_str = meta.get("recorded_at")
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
        sim = max(float(e.get("similarity_score", 0.0)) for e in items)
        similarity_term = round(sim * float(w.get("vector_similarity", 0.20)), 4)

        total_score = round(outcome_term + verified_term + recency_term + similarity_term, 4)

        conflict_entry = {
            "episode_id": ep_id,
            "total_score": total_score,
            "breakdown": {
                "outcome_term": round(outcome_term, 4),
                "verified_term": round(verified_term, 4),
                "recency_term": round(recency_term, 4),
                "similarity_term": round(similarity_term, 4),
            },
            "outcome": outcome,
            "verified": verified,
            "top_similarity": round(sim, 4),
            "evidence_ids": [e.get("evidence_id") for e in items if e.get("evidence_id")],
        }
        conflicts.append(conflict_entry)

    # Sort descending by total_score
    conflicts.sort(key=lambda x: x["total_score"], reverse=True)

    # Extract exact failed attempts from evidence
    for ev in evidence_list:
        ev_meta = ev.get("metadata") or {}
        ev_outcome = str(ev_meta.get("outcome", "")).lower()
        ev_kind = str(ev_meta.get("kind", "")).lower()
        if "failed" in ev_outcome or "failed" in ev_kind:
            content = ev.get("content")
            if content and content not in failed_attempts_to_avoid:
                failed_attempts_to_avoid.append(content)

        # Also inspect metadata.failed_attempts list if present
        for fa in ev_meta.get("failed_attempts", []):
            if isinstance(fa, dict):
                act = fa.get("action") or fa.get("why_failed")
                if act and act not in failed_attempts_to_avoid:
                    failed_attempts_to_avoid.append(act)
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

    top_sim = max(float(e.get("similarity_score", 0.0)) for e in evidence_list)
    unique_episodes = {e.get("episode_id") for e in evidence_list if e.get("episode_id")}

    has_conflict = False
    for c in conflicts:
        if c.get("outcome") == "failed" or c.get("total_score", 0.0) < 0.0:
            has_conflict = True
            break
    if len(conflicts) > 1:
        outcomes = {c.get("outcome") for c in conflicts if c.get("outcome")}
        if "worked" in outcomes and "failed" in outcomes:
            has_conflict = True

    has_hazards = False
    for ev in evidence_list:
        hazards = ev.get("metadata", {}).get("hazards") or []
        if hazards:
            has_hazards = True
            break

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
    """Filter citations to only valid retrieved evidence IDs.

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
