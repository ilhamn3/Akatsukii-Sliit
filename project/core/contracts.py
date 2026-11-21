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
        "4. A temporary stub is provided in project/core/stubs/retrieval_stub.py for offline testing."
    )
