"""A6: hybrid vector + graph retrieval over verified repair episodes (K6).

Implements the shared RetrievalEngine contract (project/core/contracts.py):

    retrieve(query, machine_type, k=3) -> list[Evidence]

1. machine_type (free text, e.g. "CNC Lathe (Turning Center)") is resolved with the
   K5 resolver. A resolved machine filters candidates *before* ranking. An
   unresolved one searches every machine and marks results cross_machine=True.
2. Vector signal: cosine similarity between the query and each episode's observed
   symptoms (MiniLM, FAISS exact inner product), from one store snapshot.
3. Graph signal: traversal from the machine node through
   fault -> cause -> fix and the episode's failed attempts. It scores query-term
   coverage of the reached labels, weighting symptom/fault matches highest; an
   error code (e.g. ALM-410) found on the path counts as full support. An
   optional asset hop (machine_id) adds support for repairs on the same asset.
4. Fusion: score = 1 - (1 - cosine) * (1 - w_graph * graph) * (1 - w_asset * asset).
   Graph and asset evidence can only ADD support. A record with no graph match
   keeps exactly its cosine, so the Orchestrator's abstention threshold still
   reads on the similarity scale.

A6 ranks by relevance only. Outcome/verification/recency conflict ranking and
abstention belong to the Orchestrator (A8), which receives outcomes, failed
attempts, verification and step-level evidence IDs in each Evidence's metadata.
No LLM is used and no API key is needed.
"""

from collections import defaultdict
import re
import uuid

import networkx as nx

from project.agents.a4_graph import default_resolver
from project.agents.base import BaseAgent
from project.core.registry import load_config
from project.core.schemas import Evidence
from project.store.graph import KnowledgeGraph
from project.store.models import VerifiedEpisode, label_key
from project.store.vector_index import VectorIndex, normalized

# Weight of a matched query term by the graph node kind it was found on.
NODE_WEIGHTS = {"fault": 1.0, "cause": 0.75, "failed_attempt": 0.5, "fix": 0.5}
STOPWORDS = {
    "the", "and", "for", "with", "after", "before", "from", "into", "onto", "that", "this", "there",
    "when", "then", "than", "but", "not", "are", "was", "were", "has", "have", "had", "its", "our",
    "very", "about", "under", "over", "below", "above", "within", "during", "keeps", "keep", "machine",
    "noticeably", "excessive", "high", "low", "some", "any", "what", "why", "how", "does", "did",
}
_CODE = re.compile(r"\b([a-z]{1,5})-?(\d{2,4})\b", re.IGNORECASE)


def _stem(token: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def terms(text: str) -> set[str]:
    """Content terms: lower-case, stop words removed, crude suffix stemming."""
    return {_stem(t) for t in re.findall(r"[a-z0-9]+", text.casefold())
            if t not in STOPWORDS and (len(t) >= 3 or t.isdigit())}


def codes(text: str) -> set[str]:
    """Alarm/error codes such as ALM-410, SV-024 or ERR-99, normalized to 'alm410'."""
    return {f"{a.casefold()}{b}" for a, b in _CODE.findall(text)}


def summarize(episode: VerifiedEpisode) -> str:
    """Readable evidence text for the Orchestrator/LLM; full data is in metadata."""
    r = episode.record
    lines = [f"Verified repair {episode.episode_id} on {r.machine_type}"
             + (f" (asset {r.machine_id})" if r.machine_id else "") + "."]
    lines.append("Symptoms: " + "; ".join(r.symptoms) + ".")
    lines.append(f"Root cause: {r.root_cause}.")
    lines.append("Fix steps: " + " ".join(
        f"{s.step_number}. {s.description}" + (f" [{s.evidence_id}]" if s.evidence_id else "") for s in r.steps))
    if r.failed_attempts:
        lines.append("Failed attempts (do not repeat): " + " ".join(
            f"{a.action} -> {a.outcome}; failed because {a.why_failed}." for a in r.failed_attempts))
    if r.safety_warnings:
        lines.append("Safety warnings: " + "; ".join(r.safety_warnings) + ".")
    if episode.outcome:
        lines.append(f"Outcome: {episode.outcome}.")
    return "\n".join(lines)


class HybridRetriever:
    """RetrievalEngine implementation backed by the K3 KnowledgeStore."""

    def __init__(self, store=None, *, resolver=None, graph_weight=None, asset_weight=None, use_vector=True):
        cfg = load_config().get("retrieval", {})
        self._store = store
        self.use_vector = use_vector  # False only for graph-only ablation in dev checks.
        self.resolver = resolver or default_resolver()
        self.graph_weight = float(graph_weight if graph_weight is not None else cfg.get("graph_weight", 0.5))
        self.asset_weight = float(asset_weight if asset_weight is not None else cfg.get("asset_weight", 0.1))
        if not (0 <= self.graph_weight <= 1 and 0 <= self.asset_weight <= 1):
            raise ValueError("Retrieval weights must be in [0, 1]")

    @property
    def store(self):
        if self._store is None:  # Lazy: needs the E: storage activation.
            from project.store.knowledge_store import KnowledgeStore
            self._store = KnowledgeStore()
        return self._store

    def _graph_features(self, graph, episodes, query_terms, query_codes, machine):
        """Per-episode graph score and matched nodes, by traversal from machine nodes."""
        starts = ([("machine", label_key(machine))] if machine else
                  [n for n, d in graph.nodes(data=True) if d["kind"] == "machine"])
        reached = defaultdict(list)
        for start in starts:
            if start in graph:
                for node in nx.descendants(graph, start):
                    kind = graph.nodes[node]["kind"]
                    if kind in NODE_WEIGHTS and node[1] in episodes:
                        reached[node[1]].append(node)
        features = {}
        for episode_id, nodes in reached.items():
            best, matched, text = {}, [], []
            for node in nodes:
                label = graph.nodes[node]["label"]
                text.append(label)
                hit = terms(label) & query_terms
                if hit:
                    matched.append(node)
                    for term in hit:
                        best[term] = max(best.get(term, 0.0), NODE_WEIGHTS[graph.nodes[node]["kind"]])
            code_hits = sorted(query_codes & codes(" ".join(text)))
            coverage = sum(best.values()) / len(query_terms) if query_terms else 0.0
            features[episode_id] = {"score": 1.0 if code_hits else min(1.0, coverage),
                                    "matched_terms": sorted(best), "matched_codes": code_hits,
                                    "matched_nodes": [":".join(n) for n in sorted(set(matched))]}
        return features

    def retrieve(self, query: str, machine_type: str | None, k: int = 3, *,
                 machine_id: str | None = None, trace_id: str | None = None) -> list[Evidence]:
        """Top-k verified episodes as Evidence, ordered by fused relevance in [0, 1]."""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("A nonempty query is required")
        if not isinstance(k, int) or k < 1:
            raise ValueError("k must be a positive integer")
        trace_id = trace_id or f"TR-RETRIEVE-{uuid.uuid4().hex[:8].upper()}"
        resolution = self.resolver.resolve_machine(machine_type) if machine_type and machine_type.strip() else None
        machine = resolution.entity_id if resolution is not None else None
        episodes, vectors = self.store.snapshot(trace_id=trace_id, operation="RETRIEVAL_QUERY")
        selected = {e.episode_id: e for e in episodes
                    if machine is None or label_key(e.record.machine_type) == label_key(machine)}
        if not selected:
            return []
        cosine = {}
        if self.use_vector:
            embedder = self.store.embedder
            index = VectorIndex({key: vectors[key] for key in selected}, embedder.dimension)
            query_vector = normalized(embedder.encode([query]), embedder.dimension)
            cosine = {key: max(0.0, score) for key, score in index.search(query_vector[0], k=len(selected))}
        graph = KnowledgeGraph(list(selected.values())).graph
        query_terms, query_codes = terms(query), codes(query)
        features = self._graph_features(graph, selected, query_terms, query_codes, machine)
        asset_hits = set()
        if machine and machine_id:
            asset = self.resolver.resolve_asset(machine_id, machine)
            asset_key = ("asset", label_key(asset.entity_id or machine_id))
            if asset_key in graph:
                asset_hits = {edge["episode_id"] for _, _, edge in graph.out_edges(asset_key, data=True)}
        ranked = []
        for episode_id, episode in selected.items():
            cos = cosine.get(episode_id, 0.0)
            feature = features.get(episode_id, {"score": 0.0, "matched_terms": [], "matched_codes": [],
                                                "matched_nodes": []})
            on_asset = episode_id in asset_hits
            if cos <= 0 and feature["score"] <= 0 and not on_asset:
                continue
            fused = 1 - (1 - cos) * (1 - self.graph_weight * feature["score"]) * (
                1 - (self.asset_weight if on_asset else 0.0))
            ranked.append((round(min(1.0, max(0.0, fused)), 6), episode_id, cos, feature, on_asset))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [self._evidence(selected[eid], fused, cos, feature, on_asset, machine, resolution)
                for fused, eid, cos, feature, on_asset in ranked[:k]]

    def _evidence(self, episode, fused, cos, feature, on_asset, machine, resolution):
        r = episode.record
        graph_part = self.graph_weight * feature["score"]
        hazards = sorted({l.entity_id for l in episode.provenance.entity_links
                          if l.kind == "hazard" and l.status == "resolved"})
        return Evidence(
            evidence_id=r.evidence_id, episode_id=episode.episode_id,
            source_type="vector" if cos >= graph_part else "graph",
            content=summarize(episode), similarity_score=fused,
            metadata={
                "record": r.model_dump(mode="json"),
                "outcome": episode.outcome,
                "failed_attempts": [a.model_dump(mode="json") for a in r.failed_attempts],
                "steps": [s.model_dump(mode="json") for s in r.steps],  # step-level evidence IDs
                "safety_warnings": r.safety_warnings,
                "catalogue_hazards": hazards,
                "provenance": episode.provenance.model_dump(mode="json"),
                "verified_by": episode.verified_by,
                "verified_at": episode.verified_at.isoformat(),
                "retrieval": {
                    "fused_score": fused, "vector_cosine": round(cos, 6), "graph_score": feature["score"],
                    "asset_match": on_asset, "matched_terms": feature["matched_terms"],
                    "matched_codes": feature["matched_codes"], "matched_nodes": feature["matched_nodes"],
                    "weights": {"graph": self.graph_weight, "asset": self.asset_weight},
                    "machine_filter": machine, "cross_machine": machine is None,
                    "machine_resolution": None if resolution is None else resolution.model_dump(mode="json"),
                },
            },
        )


class RetrievalAgent(BaseAgent):
    """Pipeline B node: raw_query + machine_type -> retrieved evidence on the blackboard."""

    def __init__(self, engine=None, trace_logger=None, k=None):
        super().__init__("RetrievalAgent", "Hybrid vector + graph retrieval of verified repairs", "B", trace_logger)
        self.engine = engine
        self.k = int(k if k is not None else load_config().get("retrieval", {}).get("k", 3))

    def run(self, state):
        state.update(retrieved_episodes=[], graph_subgraph_nodes=[], failed_attempts_retrieved=[],
                     retrieval_confidence=0.0)
        return super().run(state)

    def process(self, state):
        engine = self.engine = self.engine or HybridRetriever()
        machine = state.get("machine_type")
        evidence = engine.retrieve(state.get("raw_query") or "",
                                   None if machine in (None, "", "unspecified") else machine, k=self.k,
                                   machine_id=state.get("machine_id"), trace_id=state.get("trace_id"))
        failed = [f"{a['action']} -> {a['outcome']}; failed because {a['why_failed']} "
                  f"[{e.episode_id} / {e.evidence_id}]"
                  for e in evidence for a in e.metadata["failed_attempts"]]
        nodes = list(dict.fromkeys(n for e in evidence for n in e.metadata["retrieval"]["matched_nodes"]))
        return {"retrieved_episodes": [e.model_dump(mode="json") for e in evidence],
                "graph_subgraph_nodes": nodes, "failed_attempts_retrieved": failed,
                "retrieval_confidence": max((e.similarity_score for e in evidence), default=0.0),
                "decision": "EVIDENCE_RETRIEVED" if evidence else "NO_EVIDENCE_FOUND"}
