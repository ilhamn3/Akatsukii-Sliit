"""STUB: In-memory retrieval engine stub for Factory Memory.

STUB: Replace with Kavibarath S.'s module (WP4 - project/store/retrieval.py).
Never imported by production code paths once the real module exists;
never used to report benchmark results.
"""

from typing import List
from project.core.schemas import Evidence

# In-memory sample evidence for development and offline testing
_STUB_EVIDENCE_DATABASE: List[Evidence] = [
    Evidence(
        evidence_id="EV-CNC-0014-S1",
        episode_id="EP-CNC-0014",
        source_type="sqlite",
        content="Execute complete electrical Lockout/Tagout (LOTO) at main breaker panel PB-02.",
        similarity_score=0.95,
        metadata={
            "machine": "CNC Lathe (Turning Center)",
            "outcome": "worked",
            "verified": True,
            "recorded_at": "2026-02-15T09:00:00Z",
            "root_cause": "Rotary union bearing seizure",
            "kind": "step",
            "step_order": 1,
            "hazards": ["H01"],
        },
    ),
    Evidence(
        evidence_id="EV-CNC-0014-S2",
        episode_id="EP-CNC-0014",
        source_type="vector",
        content="Bleed hydraulic clamp pressure accumulator down to 0 bar via manual bleed valve V-12.",
        similarity_score=0.91,
        metadata={
            "machine": "CNC Lathe (Turning Center)",
            "outcome": "worked",
            "verified": True,
            "recorded_at": "2026-02-15T09:05:00Z",
            "root_cause": "Rotary union bearing seizure",
            "kind": "step",
            "step_order": 2,
            "hazards": ["H04"],
        },
    ),
    Evidence(
        evidence_id="EV-CNC-0014-S3",
        episode_id="EP-CNC-0014",
        source_type="graph",
        content="Replace rotary union cartridge (Part #RU-8842-B) and torque retention collar to 45 Nm.",
        similarity_score=0.88,
        metadata={
            "machine": "CNC Lathe (Turning Center)",
            "outcome": "worked",
            "verified": True,
            "recorded_at": "2026-02-15T09:20:00Z",
            "root_cause": "Rotary union bearing seizure",
            "kind": "step",
            "step_order": 3,
            "hazards": [],
        },
    ),
    Evidence(
        evidence_id="EV-CNC-0015-F1",
        episode_id="EP-CNC-0015",
        source_type="vector",
        content="Tightened chuck hydraulic relief valve without bleeding accumulator pressure first; blown O-ring seal.",
        similarity_score=0.82,
        metadata={
            "machine": "CNC Lathe (Turning Center)",
            "outcome": "failed",
            "verified": False,
            "recorded_at": "2026-01-10T08:00:00Z",
            "root_cause": "Misdiagnosed pressure drop as valve loose",
            "kind": "failed_attempt",
            "hazards": ["H04"],
        },
    ),
    Evidence(
        evidence_id="EV-HYD-0027-S1",
        episode_id="EP-HYD-0027",
        source_type="sqlite",
        content="Perform LOTO on main hydraulic pump disconnect switch DS-01 and insert platen safety block.",
        similarity_score=0.93,
        metadata={
            "machine": "Hydraulic Press (Stamping & Forging)",
            "outcome": "worked",
            "verified": True,
            "recorded_at": "2026-03-01T14:00:00Z",
            "root_cause": "Proportional pilot spool contamination",
            "kind": "step",
            "step_order": 1,
            "hazards": ["H01", "H04", "H05"],
        },
    ),
    Evidence(
        evidence_id="EV-HYD-0027-S2",
        episode_id="EP-HYD-0027",
        source_type="vector",
        content="Ultrasonic clean varnished proportional pilot spool; replace 10-micron pilot filter.",
        similarity_score=0.89,
        metadata={
            "machine": "Hydraulic Press (Stamping & Forging)",
            "outcome": "worked",
            "verified": True,
            "recorded_at": "2026-03-01T14:30:00Z",
            "root_cause": "Proportional pilot spool contamination",
            "kind": "step",
            "step_order": 2,
            "hazards": [],
        },
    ),
]


def retrieve(query: str, machine_type: str = "Unknown", k: int = 3) -> List[Evidence]:
    """STUB retrieval implementation matching the RetrievalEngine contract.

    Args:
        query: Worker symptom query.
        machine_type: Target machine category.
        k: Maximum number of evidence items to return.

    Returns:
        Filtered top-k Evidence records.
    """
    matches = []
    q_lower = query.lower()
    m_lower = machine_type.lower()

    for ev in _STUB_EVIDENCE_DATABASE:
        ev_machine = str(ev.metadata.get("machine", "")).lower()
        # Heuristic scoring based on machine and keyword overlap
        score = 0.50
        if m_lower != "unknown" and (m_lower in ev_machine or ev_machine in m_lower):
            score += 0.30
        if any(word in ev.content.lower() for word in q_lower.split() if len(word) > 3):
            score += 0.15

        ev_copy = ev.model_copy()
        ev_copy.similarity_score = min(round(score, 2), 1.0)
        matches.append(ev_copy)

    # Sort descending by score and return top k
    matches.sort(key=lambda x: x.similarity_score, reverse=True)
    return matches[:k]
