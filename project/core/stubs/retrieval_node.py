"""Retrieval node agent wrapping the retrieval engine.

Uses `HybridRetriever` from `project.agents.a6_retrieval` when backend == "real"
(the default), or `retrieval_stub` only when explicitly requested via config
(`system.retrieval_backend: "stub"`) or `FACTORY_STUB_RETRIEVAL=1`.
Never silently falls back to the stub if the real retriever fails.
"""

import os
from typing import Any, Dict, List, Optional

from project.agents.base import BaseAgent
from project.core.registry import load_config
from project.core.state import BlackboardState
from project.core.trace import TraceLogger


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
        is_stub: Optional[bool] = None,
        retriever: Optional[Any] = None,
    ):
        self.backend = backend
        self.k = k
        self._retriever = retriever
        resolved_backend, _ = self._resolve_config()
        effective_stub = bool(is_stub) if is_stub is not None else (resolved_backend == "stub")
        super().__init__(
            name=name,
            role=role,
            pipeline=pipeline,
            trace_logger=trace_logger,
            is_stub=effective_stub,
        )

    def _resolve_config(self) -> tuple[str, int]:
        cfg: Dict[str, Any] = {}
        try:
            cfg = load_config()
        except Exception:
            pass
        if self.backend is not None:
            backend = self.backend
        elif os.getenv("FACTORY_STUB_RETRIEVAL") == "1":
            backend = "stub"
        else:
            backend = str(cfg.get("system", {}).get("retrieval_backend", "real"))
        k = int(self.k if self.k is not None else cfg.get("retrieval", {}).get("k", 3))
        return backend, k

    def process(self, state: BlackboardState) -> Dict[str, Any]:
        backend, k = self._resolve_config()
        self.is_stub = (backend == "stub")

        query = state.get("raw_query", "")
        raw_machine = state.get("machine_type")
        machine_arg = None if raw_machine in (None, "", "unspecified") else str(raw_machine)
        trace_id = state.get("trace_id", "TR-UNKNOWN")
        session_id = state.get("session_id", "SES-UNKNOWN")

        if backend == "stub":
            from project.core.stubs.retrieval_stub import retrieve as stub_retrieve
            evidence_items = stub_retrieve(
                query=query,
                machine_type=machine_arg or "Unknown",
                k=k,
            )
        else:
            try:
                if self._retriever is not None:
                    retriever = self._retriever
                else:
                    from project.agents.a6_retrieval import HybridRetriever
                    retriever = HybridRetriever()
                evidence_items = retriever.retrieve(
                    query,
                    machine_arg,
                    k,
                    machine_id=state.get("machine_id"),
                    trace_id=trace_id,
                )
            except Exception as exc:
                err_msg = f"[{self.name}] {type(exc).__name__}: {exc}"
                errors = list(state.get("errors") or [])
                errors.append(err_msg)
                self.trace_logger.log(
                    agent=self.name,
                    event="AGENT_ERROR",
                    input_data={"query": query, "machine": machine_arg, "k": k, "backend": backend},
                    output_data={"evidence_count": 0},
                    decision="RETRIEVAL_ERROR",
                    error=f"{type(exc).__name__}: {exc}",
                    trace_id=trace_id,
                    session_id=session_id,
                    stub=self.is_stub,
                )
                return {
                    "evidence": [],
                    "retrieved_episodes": [],
                    "retrieval_confidence": 0.0,
                    "errors": errors,
                    "decision": "RETRIEVAL_ERROR",
                }

        evidence_dicts: List[Dict[str, Any]] = [
            ev.model_dump(mode="json") if hasattr(ev, "model_dump") else dict(ev)
            for ev in evidence_items
        ]

        conf = 0.0
        if evidence_dicts:
            conf = max(float(e.get("similarity_score", 0.0)) for e in evidence_dicts)

        self.trace_logger.log(
            agent=self.name,
            event="RETRIEVAL_COMPLETED",
            input_data={"query": query, "machine": machine_arg, "k": k, "backend": backend},
            output_data={"evidence_count": len(evidence_dicts), "top_similarity": conf},
            decision="RETRIEVED",
            confidence=conf,
            trace_id=trace_id,
            session_id=session_id,
            stub=self.is_stub,
        )

        return {
            "evidence": evidence_dicts,
            "retrieved_episodes": evidence_dicts,
            "retrieval_confidence": conf,
        }
