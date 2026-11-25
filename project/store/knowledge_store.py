"""K3 facade. K4 supplies verified envelopes; K5 resolves entities; K6 ranks retrieval."""

import json
from pathlib import Path
from project.core.schemas import Evidence
from project.store.embeddings import MiniLMEmbedder, storage_root
from project.store.graph import KnowledgeGraph
from project.store.models import VerifiedEpisode, label_key, symptom_text
from project.store.sqlite_db import SQLiteStore
from project.store.vector_index import VectorIndex, normalized


class KnowledgeStore:
    """Small-dataset store with transactional writes and fresh read projections.

    Public reads return independent copies. All writes/reads are trace-correlated in
    SQLite audit_events. No LLM, draft conversion or catalogue inference occurs here.
    """

    def __init__(self, db_path: Path | None = None, *, embedder=None):
        self.embedder = embedder if embedder is not None else MiniLMEmbedder()
        self.db = SQLiteStore(db_path or storage_root() / "store" / "repairs.sqlite3",
                              model_id=self.embedder.model_id, dimension=self.embedder.dimension)

    @staticmethod
    def _trace(trace_id):
        if not isinstance(trace_id, str) or not trace_id.strip():
            raise ValueError("A nonempty trace_id is required")

    def put_verified(self, episode: VerifiedEpisode | dict, *, trace_id: str) -> str:
        """Atomically insert/replace an episode and its embedding; validate graph/index.

        Repeating an identical envelope is idempotent. A replacement must keep its
        episode/evidence ID pair. Review authorization is the caller's responsibility.
        """
        self._trace(trace_id)
        try:
            # Round-trip also revalidates instances mutated after construction.
            payload = episode.model_dump(mode="json") if isinstance(episode, VerifiedEpisode) else episode
            episode = VerifiedEpisode.model_validate(payload)
            with self.db.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                existing = db.execute("SELECT evidence_id, payload FROM episodes WHERE episode_id=?",
                                      (episode.episode_id,)).fetchone()
                if existing and existing[0] != episode.record.evidence_id:
                    raise ValueError("Cannot replace an episode's primary evidence ID")
                serialized = episode.model_dump_json()
                if existing and existing[1] == serialized:
                    self.db.audit(db, trace_id, "STORE_NOOP", {"episode_id": episode.episode_id})
                    return episode.episode_id
                vectors = self.embedder.encode([symptom_text(episode)])
                vector = normalized(vectors, self.embedder.dimension)
                if len(vector) != 1:
                    raise ValueError("Expected one embedding per episode")
                db.execute('''INSERT INTO episodes VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(episode_id) DO UPDATE SET
                    machine_key=excluded.machine_key, payload=excluded.payload, vector=excluded.vector''',
                    (episode.episode_id, episode.record.evidence_id, label_key(episode.record.machine_type),
                     serialized, json.dumps(vector[0].tolist())))
                episodes, stored_vectors = self.db.snapshot(db)
                # Any projection failure rolls back both the SQL row and vector.
                VectorIndex(stored_vectors, self.embedder.dimension)
                KnowledgeGraph(episodes)
                self.db.audit(db, trace_id, "STORE_UPSERT", {"episode_id": episode.episode_id,
                              "evidence_id": episode.record.evidence_id, "embedding_model": self.embedder.model_id})
            return episode.episode_id
        except Exception as exc:
            with self.db.connect() as db:
                self.db.audit(db, trace_id, "STORE_ERROR", {"error_type": type(exc).__name__})
            raise

    def _snapshot(self, trace_id, operation):
        self._trace(trace_id)
        with self.db.connect() as db:
            # One SELECT supplies a consistent snapshot; release its read lock
            # before appending the audit event, avoiding read-to-write lock upgrades.
            episodes, vectors = self.db.snapshot(db)
            self.db.audit(db, trace_id, operation, {"episode_count": len(episodes)})
        return episodes, vectors

    def snapshot(self, *, trace_id: str, operation: str = "STORE_SNAPSHOT"):
        """One consistent committed read: (episodes, {episode_id: unit vector}).

        K6 hybrid retrieval scores vectors and graph paths from this single
        snapshot so both signals describe the same data.
        """
        return self._snapshot(trace_id, operation)

    def get(self, episode_id: str, *, trace_id: str) -> VerifiedEpisode | None:
        """Return the verified record and complete provenance, or None."""
        episodes, _ = self._snapshot(trace_id, "STORE_GET")
        return next((e for e in episodes if e.episode_id == episode_id), None)

    def by_machine(self, machine_type: str, *, trace_id: str) -> list[VerifiedEpisode]:
        """Match case/whitespace-normalized stored categories; no alias resolution."""
        episodes, _ = self._snapshot(trace_id, "STORE_MACHINE_QUERY")
        return [e for e in episodes if label_key(e.record.machine_type) == label_key(machine_type)]

    @staticmethod
    def _evidence(episode, source_type, score=0.0):
        return Evidence(
            evidence_id=episode.record.evidence_id, episode_id=episode.episode_id,
            source_type=source_type, content=episode.record.model_dump_json(),
            similarity_score=max(0.0, min(1.0, score)),
            metadata={"record": episode.record.model_dump(mode="json"), "outcome": episode.outcome,
                      "provenance": episode.provenance.model_dump(mode="json"),
                      "verified_by": episode.verified_by, "verified_at": episode.verified_at.isoformat(),
                      "raw_cosine": score if source_type == "vector" else None},
        )

    def search_symptoms(self, query: str, *, trace_id: str, k: int = 5,
                        machine_type: str | None = None, min_score: float = 0.0) -> list[Evidence]:
        """Cosine search of observed symptoms; scores are similarity, not confidence.

        Apply the machine filter before top-k selection. Empty stores avoid model
        download. K6 owns hybrid ranking and abstention thresholds.
        """
        if not query.strip() or k < 1 or not 0 <= min_score <= 1:
            raise ValueError("Provide nonempty query, positive k and min_score in [0, 1]")
        episodes, vectors = self._snapshot(trace_id, "STORE_VECTOR_QUERY")
        selected = {e.episode_id: e for e in episodes if machine_type is None or
                    label_key(e.record.machine_type) == label_key(machine_type)}
        if not selected:
            return []
        index = VectorIndex({key: vectors[key] for key in selected}, self.embedder.dimension)
        query_vector = normalized(self.embedder.encode([query]), self.embedder.dimension)
        if len(query_vector) != 1:
            raise ValueError("Expected one query embedding")
        return [self._evidence(selected[key], "vector", score)
                for key, score in index.search(query_vector[0], k=k) if score >= min_score]

    def related(self, kind: str, label: str, *, trace_id: str,
                machine_type: str | None = None, limit: int = 10) -> list[Evidence]:
        """Find episodes by exact normalized machine/fault/cause/fix/hazard label."""
        if limit < 1 or not label.strip():
            raise ValueError("Provide nonempty label and positive limit")
        episodes, _ = self._snapshot(trace_id, "STORE_GRAPH_QUERY")
        matched = KnowledgeGraph(episodes).find(kind, label)
        selected = [e for e in episodes if e.episode_id in matched and (machine_type is None or
                    label_key(e.record.machine_type) == label_key(machine_type))]
        return [self._evidence(e, "graph") for e in selected[:limit]]

    def graph_snapshot(self, *, trace_id: str):
        """Return an independent NetworkX MultiDiGraph with episode-tagged edges."""
        episodes, _ = self._snapshot(trace_id, "STORE_GRAPH_SNAPSHOT")
        return KnowledgeGraph(episodes).graph

    def audit_events(self):
        """Return durable trace-correlated store events in commit order."""
        with self.db.connect() as db:
            rows = db.execute("SELECT sequence, ts, trace_id, operation, details FROM audit_events ORDER BY sequence").fetchall()
        return [dict(sequence=r[0], ts=r[1], trace_id=r[2], operation=r[3], details=json.loads(r[4])) for r in rows]
