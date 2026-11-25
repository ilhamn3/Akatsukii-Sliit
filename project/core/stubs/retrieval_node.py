"""Retrieval node agent wrapping the retrieval engine.

Works with either the retrieval stub or the real retrieval backend (WP4).
Populates `state["evidence"]` (list of Evidence dicts) and `state["retrieval_confidence"]`.
Flagged `stub: true` in config.yaml when using the stub backend.
"""

from typing import Any, Dict, List, Optional
from project.agents.base import BaseAgent
from project.core.state import BlackboardState
from project.core.trace import TraceLogger
from project.core.registry import load_config


class RetrievalNode(BaseAgent):
    """Retrieval Agent node for Pipeline B."""

    def __init__(
        self,
        name: str = "RetrievalNode",
        role: str = "Historical repair evidence retrieval",
        pipeline: str = "B",
        backend: Optional[str] = None,
        k: Optional[int] = None,
        trace_logger: Optional[TraceLogger] = None,
        is_stub: bool = True,
    ):
        super().__init__(
            name=name,
            role=role,
            pipeline=pipeline,
            trace_logger=trace_logger,
            is_stub=is_stub,
        )
        self.backend = backend
        self.k = k

    def _resolve_config(self) -> tuple[str, int]:
        cfg: Dict[str, Any] = {}
        try:
            cfg = load_config()
        except Exception:
            pass
        backend = self.backend or cfg.get("system", {}).get("retrieval_backend", "stub")
        k = self.k or cfg.get("retrieval", {}).get("k", 3)
        return backend, k

    def process(self, state: BlackboardState) -> Dict[str, Any]:
        backend, k = self._resolve_config()
        query = state.get("raw_query", "")
        machine_type = state.get("machine_type", "unspecified")

        evidence_items = []
        if backend == "real":
            try:
                import importlib
                mod = importlib.import_module("project.agents.a6_retrieval")
                retrieve_fn = getattr(mod, "retrieve")
                evidence_items = retrieve_fn(query=query, machine_type=machine_type, k=k)
            except Exception:
                from project.core.stubs.retrieval_stub import retrieve as stub_retrieve
                evidence_items = stub_retrieve(query=query, machine_type=machine_type, k=k)
        else:
            from project.core.stubs.retrieval_stub import retrieve as stub_retrieve
            evidence_items = stub_retrieve(query=query, machine_type=machine_type, k=k)

        # Convert to dicts for blackboard state
        evidence_dicts: List[Dict[str, Any]] = [
            ev.model_dump() if hasattr(ev, "model_dump") else dict(ev)
            for ev in evidence_items
        ]

        conf = 0.0
        if evidence_dicts:
            conf = max(float(e.get("similarity_score", 0.0)) for e in evidence_dicts)

        self.trace_logger.log(
            agent=self.name,
            event="RETRIEVAL_COMPLETED",
            input_data={"query": query, "machine": machine_type, "k": k, "backend": backend},
            output_data={"evidence_count": len(evidence_dicts), "top_similarity": conf},
            decision="RETRIEVED",
            confidence=conf,
            trace_id=state.get("trace_id", "TR-UNKNOWN"),
            session_id=state.get("session_id", "SES-UNKNOWN"),
            stub=self.is_stub,
        )

        return {
            "evidence": evidence_dicts,
            "retrieved_episodes": evidence_dicts,
            "retrieval_confidence": conf,
        }
