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
        "4. Evidence Metadata Contract (K6 HybridRetriever output):\n"
        "   Each Evidence object populates metadata dict with the following keys:\n"
        "   - 'record' (dict): Serialized RepairRecord (machine_type, machine_id, symptoms, steps, tools, parts, root_cause, ...).\n"
        "   - 'outcome' (str | None): Free-text outcome of the episode (e.g. 'Fault cleared...').\n"
        "   - 'failed_attempts' (list[dict]): Documented wrong fixes (action, outcome, why_failed).\n"
        "   - 'steps' (list[dict]): RepairStep dicts with step_number, description, timestamp, and step-level evidence_id.\n"
        "   - 'safety_warnings' (list[str]): Mandatory safety warnings from the verified record.\n"
        "   - 'catalogue_hazards' (list[str]): Resolved hazard IDs (e.g. ['H01', 'H06']).\n"
        "   - 'provenance' (dict): Source capture_id, source_path, sha256, evidence_spans, entity_links.\n"
        "   - 'verified_by' (str): Reviewer or loader identity.\n"
        "   - 'verified_at' (str): ISO 8601 UTC timestamp when the episode was verified.\n"
        "   - 'retrieval' (dict): Retrieval diagnostics (fused_score, vector_cosine, graph_score, asset_match, matched_terms, matched_codes, matched_nodes, weights, machine_filter, cross_machine, machine_resolution).\n"
        "   NOTE: Orchestrator normalizes both real K6 metadata and stub metadata defensively; missing keys never crash.\n"
        "5. A temporary stub is provided in project/core/stubs/retrieval_stub.py for offline testing."
    )
