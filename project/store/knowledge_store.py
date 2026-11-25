"""K3 facade. K4 supplies verified envelopes; K5 resolves entities; K6 ranks retrieval."""

from dataclasses import dataclass, field
import json
from pathlib import Path
import threading

from project.core.schemas import Evidence
from project.store.embeddings import MiniLMEmbedder, storage_root
from project.store.graph import KnowledgeGraph
from project.store.models import VerifiedEpisode, label_key, symptom_text
from project.store.sqlite_db import SQLiteStore
from project.store.vector_index import VectorIndex, normalized


@dataclass
class Projection:
    """Parsed episodes plus lazily built FAISS/graph projections for ONE store revision.

    Shared read-only across KnowledgeStore instances in this process; callers must
    not mutate it. Public KnowledgeStore reads return independent copies.
    """

    revision: int
    episodes: list[VerifiedEpisode]
    vectors: dict[str, list[float]]
    dimension: int
    _index: VectorIndex | None = None
    _graph: KnowledgeGraph | None = None
    _memo: dict = field(default_factory=dict, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def memo(self, key, build):
        """Cache a derived, read-only artifact (e.g. K6 term indexes) for this revision."""
        with self._lock:
            if key not in self._memo:
                self._memo[key] = build()
            return self._memo[key]

    def index(self) -> VectorIndex:
        with self._lock:
            if self._index is None:
                self._index = VectorIndex(self.vectors, self.dimension)
            return self._index

    def graph(self) -> KnowledgeGraph:
        with self._lock:
            if self._graph is None:
                self._graph = KnowledgeGraph(self.episodes)
            return self._graph


# (resolved database path, embedding model) -> latest Projection. SQLite's revision
# counter is authoritative: a write from any process invalidates every cache entry.
_CACHE: dict[tuple[str, str], Projection] = {}
_CACHE_LOCK = threading.Lock()


class KnowledgeStore:
    """Small-dataset store with transactional writes and revision-cached projections.

    SQLite is the only durable authority. Each read checks the store revision in
    the same read transaction as the data, and rebuilds parsed episodes and the
    FAISS/graph projections only when the revision changed. Public reads return
    independent copies. All writes/reads are trace-correlated in SQLite
    audit_events. No LLM, draft conversion or catalogue inference occurs here.
    """

    def __init__(self, db_path: Path | None = None, *, embedder=None):
        self.embedder = embedder if embedder is not None else MiniLMEmbedder()
        self.db = SQLiteStore(db_path or storage_root() / "store" / "repairs.sqlite3",
                              model_id=self.embedder.model_id, dimension=self.embedder.dimension)
        self._cache_key = (str(self.db.path.resolve()), self.embedder.model_id)

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
                stored_vector = vector[0].tolist()
                db.execute('''INSERT INTO episodes VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(episode_id) DO UPDATE SET
                    machine_key=excluded.machine_key, payload=excluded.payload, vector=excluded.vector''',
                    (episode.episode_id, episode.record.evidence_id, label_key(episode.record.machine_type),
                     serialized, json.dumps(stored_vector)))
                # Projections are built per episode, so validating the new episode's
                # own projection is sufficient; failure rolls back the row and vector.
                VectorIndex({episode.episode_id: stored_vector}, self.embedder.dimension)
                KnowledgeGraph([episode])
                previous = self.db.revision(db)
                self.db.bump_revision(db)
                self.db.audit(db, trace_id, "STORE_UPSERT", {"episode_id": episode.episode_id,
                              "evidence_id": episode.record.evidence_id, "embedding_model": self.embedder.model_id})
            self._advance_cache(previous, episode, stored_vector)
            return episode.episode_id
        except Exception as exc:
            with self.db.connect() as db:
                self.db.audit(db, trace_id, "STORE_ERROR", {"error_type": type(exc).__name__})
            raise

    def _advance_cache(self, previous, episode, vector):
        """After a committed write, update an up-to-date cache entry incrementally."""
        with _CACHE_LOCK:
            cached = _CACHE.get(self._cache_key)
            if cached is None or cached.revision != previous:
                return  # Stale or absent: the next read reloads from SQLite.
            episodes = {e.episode_id: e for e in cached.episodes}
            episodes[episode.episode_id] = episode
            vectors = dict(cached.vectors)
            vectors[episode.episode_id] = vector
            _CACHE[self._cache_key] = Projection(previous + 1, [episodes[k] for k in sorted(episodes)],
                                                 vectors, self.embedder.dimension)

    def projection(self, *, trace_id: str, operation: str = "STORE_SNAPSHOT") -> Projection:
        """Consistent committed read as a shared, read-only Projection (used by K6).

        The revision and rows are read in one transaction; the read lock is released
        before the audit event is appended, avoiding read-to-write lock upgrades.
        """
        self._trace(trace_id)
        with self.db.connect() as db:
            db.execute("BEGIN")
            revision = self.db.revision(db)
            with _CACHE_LOCK:
                cached = _CACHE.get(self._cache_key)
            if cached is None or cached.revision != revision:
                episodes, vectors = self.db.snapshot(db)
                cached = Projection(revision, episodes, vectors, self.embedder.dimension)
                with _CACHE_LOCK:
                    current = _CACHE.get(self._cache_key)
                    if current is None or current.revision <= revision:
                        _CACHE[self._cache_key] = cached
            db.commit()
            self.db.audit(db, trace_id, operation, {"episode_count": len(cached.episodes)})
        return cached

    def snapshot(self, *, trace_id: str, operation: str = "STORE_SNAPSHOT"):
        """One consistent committed read: (episodes, {episode_id: unit vector}).

        The lists are new containers; the episode objects are shared read-only.
        """
        projection = self.projection(trace_id=trace_id, operation=operation)
        return list(projection.episodes), dict(projection.vectors)

    def get(self, episode_id: str, *, trace_id: str) -> VerifiedEpisode | None:
        """Return the verified record and complete provenance, or None."""
        projection = self.projection(trace_id=trace_id, operation="STORE_GET")
        found = next((e for e in projection.episodes if e.episode_id == episode_id), None)
        return found.model_copy(deep=True) if found is not None else None

    def by_machine(self, machine_type: str, *, trace_id: str) -> list[VerifiedEpisode]:
        """Match case/whitespace-normalized stored categories; no alias resolution."""
        projection = self.projection(trace_id=trace_id, operation="STORE_MACHINE_QUERY")
        return [e.model_copy(deep=True) for e in projection.episodes
                if label_key(e.record.machine_type) == label_key(machine_type)]

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
        projection = self.projection(trace_id=trace_id, operation="STORE_VECTOR_QUERY")
        selected = {e.episode_id: e for e in projection.episodes if machine_type is None or
                    label_key(e.record.machine_type) == label_key(machine_type)}
        if not selected:
            return []
        query_vector = normalized(self.embedder.encode([query]), self.embedder.dimension)
        if len(query_vector) != 1:
            raise ValueError("Expected one query embedding")
        ranked = [(key, score) for key, score in projection.index().search(
            query_vector[0], k=len(projection.episodes)) if key in selected]
        return [self._evidence(selected[key], "vector", score)
                for key, score in ranked[:k] if score >= min_score]

    def related(self, kind: str, label: str, *, trace_id: str,
                machine_type: str | None = None, limit: int = 10) -> list[Evidence]:
        """Find episodes by exact normalized machine/fault/cause/fix/hazard label."""
        if limit < 1 or not label.strip():
            raise ValueError("Provide nonempty label and positive limit")
        projection = self.projection(trace_id=trace_id, operation="STORE_GRAPH_QUERY")
        matched = projection.graph().find(kind, label)
        selected = [e for e in projection.episodes if e.episode_id in matched and (machine_type is None or
                    label_key(e.record.machine_type) == label_key(machine_type))]
        return [self._evidence(e, "graph") for e in selected[:limit]]

    def graph_snapshot(self, *, trace_id: str):
        """Return an independent NetworkX MultiDiGraph with episode-tagged edges."""
        projection = self.projection(trace_id=trace_id, operation="STORE_GRAPH_SNAPSHOT")
        return projection.graph().graph.copy()

    def audit_events(self):
        """Return durable trace-correlated store events in commit order."""
        with self.db.connect() as db:
            rows = db.execute("SELECT sequence, ts, trace_id, operation, details FROM audit_events ORDER BY sequence").fetchall()
        return [dict(sequence=r[0], ts=r[1], trace_id=r[2], operation=r[3], details=json.loads(r[4])) for r in rows]
