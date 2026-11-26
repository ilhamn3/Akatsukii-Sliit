"""SQLite is the durable authority for verified episodes, vectors and audit events."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from project.store.models import VerifiedEpisode


class SQLiteStore:
    def __init__(self, path: Path, *, model_id: str, dimension: int):
        if not model_id.strip() or dimension < 1:
            raise ValueError("An embedding model identity and positive dimension are required")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS store_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS episodes (
                    episode_id TEXT PRIMARY KEY,
                    evidence_id TEXT NOT NULL UNIQUE,
                    machine_key TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    vector TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_machine ON episodes(machine_key);
                CREATE TABLE IF NOT EXISTS audit_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL, trace_id TEXT NOT NULL,
                    operation TEXT NOT NULL, details TEXT NOT NULL
                );
            ''')
            db.execute("BEGIN IMMEDIATE")
            for key, value in {"schema_version": "1", "embedding_model": model_id,
                               "embedding_dimension": str(dimension)}.items():
                db.execute("INSERT OR IGNORE INTO store_meta VALUES (?, ?)", (key, value))
                actual = db.execute("SELECT value FROM store_meta WHERE key=?", (key,)).fetchone()[0]
                if actual != value:
                    raise ValueError(f"Store {key} mismatch; explicit migration/reindex required")
            # Monotonic data revision: bumped in every committing write, so any
            # process can tell whether its cached projections are still current.
            db.execute("INSERT OR IGNORE INTO store_meta VALUES ('revision', '0')")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            db.execute("PRAGMA foreign_keys=ON")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def revision(db) -> int:
        return int(db.execute("SELECT value FROM store_meta WHERE key='revision'").fetchone()[0])

    @staticmethod
    def bump_revision(db) -> None:
        db.execute("UPDATE store_meta SET value = CAST(value AS INTEGER) + 1 WHERE key='revision'")

    @staticmethod
    def snapshot(db):
        rows = db.execute("SELECT payload, vector FROM episodes ORDER BY episode_id").fetchall()
        episodes = [VerifiedEpisode.model_validate_json(row[0]) for row in rows]
        vectors = {e.episode_id: json.loads(row[1]) for e, row in zip(episodes, rows)}
        return episodes, vectors

    @staticmethod
    def audit(db, trace_id, operation, details):
        db.execute("INSERT INTO audit_events(ts, trace_id, operation, details) VALUES (?, ?, ?, ?)",
                   (datetime.now(timezone.utc).isoformat(), trace_id, operation, json.dumps(details)))
