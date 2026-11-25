"""Interface contracts and specifications for cross-work-package handoffs.

This module publishes the official interface contracts expected from teammate
modules. Other team members implement against these type contracts.
"""

from typing import List, Protocol
from project.core.schemas import Evidence


class RetrievalEngine(Protocol):
    """Interface protocol for the Hybrid Knowledge Store retrieval module (WP4).

    Owner: Kavibarath S. (Due: 26 Nov)
    """

    def retrieve(self, query: str, machine_type: str, k: int = 3) -> List[Evidence]:
        """Retrieve top-k grounded historical evidence chunks.

        Args:
            query: Worker query string or symptom description.
            machine_type: Target machine classification (e.g., 'CNC Lathe').
            k: Maximum number of grounded evidence items to retrieve (default: 3).

        Returns:
            List of Evidence objects ordered by similarity/relevance score,
            each containing evidence_id, episode_id, source_type, content,
            and similarity_score in [0.0, 1.0].
        """
        ...


def retrieve_contract_doc() -> str:
    """Return the formal documentation note for Kavibarath S."""
    return (
        "INTERFACE CONTRACT FOR KAVIBARATH S. (WP4 - Knowledge Store & Retrieval):\n"
        "Function Signature:\n"
        "    retrieve(query: str, machine_type: str, k: int = 3) -> list[Evidence]\n\n"
        "Requirements:\n"
        "1. Returns pydantic Evidence objects (from project.core.schemas).\n"
        "2. Must combine dense vector similarity (ChromaDB/FAISS) with graph hops (NetworkX).\n"
        "3. Every returned Evidence must have a valid evidence_id and similarity_score in [0.0, 1.0].\n"
        "4. Evidence Metadata Contract:\n"
        "   Each Evidence object should populate metadata dict with the following keys:\n"
        "   - 'machine' (str): Machine type/model associated with this evidence chunk.\n"
        "   - 'outcome' (str): Result of the fix, e.g. 'worked', 'failed', 'partial'.\n"
        "   - 'verified' (bool): True if verified by senior technician or QA.\n"
        "   - 'recorded_at' (str / ISO 8601): Timestamp when the episode/step was logged.\n"
        "   - 'root_cause' (str, optional): Identified root cause if known.\n"
        "   - 'kind' (str, optional): 'step', 'warning', 'diagnosis', 'symptom'.\n"
        "   - 'step_order' (int, optional): 1-based order of step if kind=='step'.\n"
        "   - 'hazards' (list[str], optional): Hazard IDs (e.g. ['H01', 'H04']) associated with this step.\n"
        "   NOTE: Orchestrator is designed defensively; missing keys default safely and will never crash.\n"
        "5. A temporary stub is provided in project/core/stubs/retrieval_stub.py for offline testing."
    )
