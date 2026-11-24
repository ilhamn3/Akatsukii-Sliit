"""K4 review-queue backend (HITL Checkpoint 1) for the technician review page.

Every Agent 3 draft lands here as `pending`. `approve()` is the ONLY path that
creates a VerifiedEpisode: it requires a reviewer identity, re-derives the record
from the stored draft (never trusting a stored report, status label or confidence),
refuses unresolved blocking issues and publishes to the knowledge store before the
item is marked approved. Every decision is recorded with actor, time and trace ID.

The backend records identity but does NOT authenticate it. The calling UI/service
must authenticate the reviewer and pass a trusted identity string.
"""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
from typing import Literal
import uuid

from pydantic import Field

from project.agents.a3_validation import ReviewBlocked, _issue, episode_ids, verify
from project.agents.a4_graph import GraphAgent
from project.core.extraction_schemas import DraftRepairRecord, StrictModel
from project.core.validation_schemas import ValidationReport
from project.store.embeddings import storage_root
from project.store.models import VerifiedEpisode

Status = Literal["pending", "approved", "rejected"]
STATUSES = ("pending", "approved", "rejected")


class ReviewItem(StrictModel):
    item_id: str
    status: Status
    priority: Literal["low_confidence", "standard"]
    confidence: float = Field(ge=0, le=1)
    episode_id: str
    source_sha256: str
    capture_id: str
    draft: DraftRepairRecord
    report: ValidationReport
    created_at: datetime
    updated_at: datetime
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_note: str | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value, name, *, minimum=1, maximum=200) -> str:
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise ValueError(f"{name} must be a string of {minimum}-{maximum} characters")
    if any(ord(ch) < 32 for ch in value):
        raise ValueError(f"{name} must not contain control characters")
    return value.strip()


class ReviewQueue:
    """SQLite-backed queue. Decisions are serialized with BEGIN IMMEDIATE."""

    def __init__(self, db_path: Path | None = None, *, store=None, graph_agent=None):
        self.path = Path(db_path or storage_root() / "review" / "review_queue.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # store: KnowledgeStore (get/put_verified). Approved episodes are published
        # through Agent 4 (K5), which resolves catalogue entities before writing.
        self.store = store
        self.publisher = graph_agent if graph_agent is not None else (
            GraphAgent(store) if store is not None else None)
        with self._connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS review_items (
                    item_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected')),
                    priority TEXT NOT NULL, confidence REAL NOT NULL, episode_id TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL, capture_id TEXT NOT NULL,
                    draft TEXT NOT NULL, report TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    decided_by TEXT, decided_at TEXT, decision_note TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_review_status ON review_items(status, priority, created_at);
                CREATE INDEX IF NOT EXISTS idx_review_source ON review_items(source_sha256);
                CREATE TABLE IF NOT EXISTS review_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL, item_id TEXT NOT NULL, trace_id TEXT NOT NULL,
                    actor TEXT NOT NULL, action TEXT NOT NULL, details TEXT NOT NULL
                );
            ''')

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _event(db, item_id, trace_id, actor, action, details):
        db.execute("INSERT INTO review_events(ts, item_id, trace_id, actor, action, details) VALUES (?, ?, ?, ?, ?, ?)",
                   (_now().isoformat(), item_id, trace_id, actor, action, json.dumps(details, default=str)))

    @staticmethod
    def _row(row) -> ReviewItem:
        keys = ("item_id", "status", "priority", "confidence", "episode_id", "source_sha256", "capture_id",
                "draft", "report", "created_at", "updated_at", "decided_by", "decided_at", "decision_note")
        data = dict(zip(keys, row))
        data["draft"], data["report"] = json.loads(data["draft"]), json.loads(data["report"])
        return ReviewItem.model_validate(data)

    def _select(self, db, where="", params=()):
        order = "CASE priority WHEN 'low_confidence' THEN 0 ELSE 1 END, created_at, item_id"
        return [self._row(r) for r in db.execute(f"SELECT * FROM review_items {where} ORDER BY {order}", params)]

    # ----- A3 side -------------------------------------------------------------------

    def enqueue(self, draft, report, *, trace_id: str, actor: str = "ValidationAgent") -> ReviewItem:
        """Add a validated draft as a pending item. Status is always 'pending'."""
        trace_id = _text(trace_id, "trace_id")
        draft = DraftRepairRecord.model_validate(draft.model_dump(mode="json") if hasattr(draft, "model_dump") else draft)
        report = ValidationReport.model_validate(report.model_dump(mode="json") if hasattr(report, "model_dump") else report)
        if report.source_sha256 != draft.source_sha256 or report.episode_id != episode_ids(draft)[0]:
            raise ValueError("Validation report does not belong to this draft")
        item_id, now = f"RQ-{uuid.uuid4().hex[:12].upper()}", _now().isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO review_items VALUES (?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)",
                       (item_id, report.priority, report.confidence.score, report.episode_id, draft.source_sha256,
                        draft.capture_id, draft.model_dump_json(), report.model_dump_json(), now, now))
            self._event(db, item_id, trace_id, actor, "ENQUEUED", {
                "priority": report.priority, "confidence": report.confidence.score,
                "issues": [f"{i.severity}:{i.code}" for i in report.issues]})
        return self.get(item_id)

    def find_identical(self, draft) -> ReviewItem | None:
        """A pending/approved item holding exactly this draft (idempotent re-runs)."""
        serialized = DraftRepairRecord.model_validate(draft.model_dump(mode="json")).model_dump_json()
        return next((i for i in self.active_by_source(draft.source_sha256)
                     if i.draft.model_dump_json() == serialized), None)

    def active_by_source(self, source_sha256: str) -> list[ReviewItem]:
        with self._connect() as db:
            return self._select(db, "WHERE source_sha256=? AND status IN ('pending', 'approved')", (source_sha256,))

    # ----- Review page API -----------------------------------------------------------

    def get(self, item_id: str) -> ReviewItem | None:
        with self._connect() as db:
            items = self._select(db, "WHERE item_id=?", (item_id,))
        return items[0] if items else None

    def list_items(self, *, status: str | None = "pending", limit: int = 100) -> list[ReviewItem]:
        """Low-confidence items first, then oldest first. status=None lists all."""
        if status is not None and status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES} or None")
        if limit < 1:
            raise ValueError("limit must be positive")
        with self._connect() as db:
            items = self._select(db, "WHERE status=?", (status,)) if status else self._select(db)
        return items[:limit]

    def approve(self, item_id: str, *, reviewer: str, trace_id: str,
                corrections: dict | None = None, note: str | None = None) -> VerifiedEpisode:
        """Verify a pending item and publish it to the knowledge store.

        corrections: reviewer-supplied values keyed by RepairRecord field name
        (plus 'outcome') or 'failed_attempts.<i>.<action|outcome|why_failed>'.
        A blocking issue is resolved only by correcting its field. Raises
        ReviewBlocked (item stays pending) when issues remain unresolved.
        """
        reviewer, trace_id = _text(reviewer, "reviewer"), _text(trace_id, "trace_id")
        note = _text(note, "note", maximum=2000) if note is not None else None
        if self.store is None or self.publisher is None:
            raise RuntimeError("approve() needs a knowledge store to publish the verified episode")
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")  # Serialize decisions; store write happens inside.
                found = self._select(db, "WHERE item_id=?", (item_id,))
                if not found:
                    raise KeyError(f"Unknown review item {item_id}")
                item = found[0]
                if item.status != "pending":
                    raise ValueError(f"Review item {item_id} is already {item.status}")
                approved = [r[0] for r in db.execute(
                    "SELECT item_id FROM review_items WHERE source_sha256=? AND status='approved'",
                    (item.source_sha256,))]
                if approved or self.store.get(item.episode_id, trace_id=trace_id) is not None:
                    raise ReviewBlocked([_issue("duplicate_source", "blocking", None,
                                                "This recording is already verified; reject this item",
                                                approved or [item.episode_id])])
                decided = _now()
                episode, supplied = verify(item.draft, reviewer=reviewer, verified_at=decided,
                                           corrections=corrections)
                # Agent 4 canonicalizes entities, then writes SQLite/vector/graph (K3).
                episode = self.store.get(self.publisher.put_verified(episode, trace_id=trace_id),
                                         trace_id=trace_id)
                db.execute('''UPDATE review_items SET status='approved', decided_by=?, decided_at=?,
                              decision_note=?, updated_at=? WHERE item_id=? AND status='pending' ''',
                           (reviewer, decided.isoformat(), note, decided.isoformat(), item_id))
                self._event(db, item_id, trace_id, reviewer, "APPROVED", {
                    "episode_id": episode.episode_id, "evidence_id": episode.record.evidence_id,
                    "reviewer_supplied_fields": supplied, "corrections": corrections or {},
                    "entity_links": [f"{l.kind}:{l.field_path}={l.entity_id or l.status}"
                                     for l in episode.provenance.entity_links],
                    "acknowledged_warnings": sorted({i.code for i in item.report.issues if i.severity == "warning"}),
                    "confidence": episode.record.confidence, "note": note})
            return episode
        except Exception as exc:
            with self._connect() as db:
                self._event(db, item_id, trace_id, reviewer, "APPROVE_FAILED", {
                    "error_type": type(exc).__name__,
                    "unresolved": [f"{i.code}@{i.field_path}" for i in getattr(exc, "issues", [])]})
            raise

    def reject(self, item_id: str, *, reviewer: str, reason: str, trace_id: str) -> ReviewItem:
        """Reject a pending item; it never reaches the verified store."""
        reviewer, trace_id = _text(reviewer, "reviewer"), _text(trace_id, "trace_id")
        reason = _text(reason, "reason", minimum=3, maximum=2000)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            found = self._select(db, "WHERE item_id=?", (item_id,))
            if not found:
                raise KeyError(f"Unknown review item {item_id}")
            if found[0].status != "pending":
                raise ValueError(f"Review item {item_id} is already {found[0].status}")
            now = _now().isoformat()
            db.execute('''UPDATE review_items SET status='rejected', decided_by=?, decided_at=?,
                          decision_note=?, updated_at=? WHERE item_id=?''', (reviewer, now, reason, now, item_id))
            self._event(db, item_id, trace_id, reviewer, "REJECTED", {"reason": reason})
        return self.get(item_id)

    def events(self, item_id: str | None = None) -> list[dict]:
        """Durable decision/audit history in commit order."""
        with self._connect() as db:
            rows = db.execute("SELECT sequence, ts, item_id, trace_id, actor, action, details FROM review_events "
                              + ("WHERE item_id=? " if item_id else "") + "ORDER BY sequence",
                              (item_id,) if item_id else ()).fetchall()
        return [dict(sequence=r[0], ts=r[1], item_id=r[2], trace_id=r[3], actor=r[4], action=r[5],
                     details=json.loads(r[6])) for r in rows]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Inspect and decide review-queue items")
    parser.add_argument("--queue-db")
    parser.add_argument("--store-db")
    parser.add_argument("--local-files-only", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("list")
    listing.add_argument("--status", default="pending", choices=[*STATUSES, "all"])
    sub.add_parser("show").add_argument("item_id")
    approve = sub.add_parser("approve")
    approve.add_argument("item_id")
    approve.add_argument("--reviewer", required=True)
    approve.add_argument("--corrections", help="JSON file of reviewer-supplied values")
    approve.add_argument("--note")
    reject = sub.add_parser("reject")
    reject.add_argument("item_id")
    reject.add_argument("--reviewer", required=True)
    reject.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    trace_id = f"TR-REVIEW-{uuid.uuid4().hex[:8].upper()}"
    try:
        store = None
        if args.command == "approve":
            from project.store.embeddings import MiniLMEmbedder
            from project.store.knowledge_store import KnowledgeStore
            store = KnowledgeStore(Path(args.store_db) if args.store_db else None,
                                   embedder=MiniLMEmbedder(local_files_only=args.local_files_only))
        queue = ReviewQueue(Path(args.queue_db) if args.queue_db else None, store=store)
        if args.command == "list":
            for item in queue.list_items(status=None if args.status == "all" else args.status):
                print(f"{item.item_id}  {item.status:8}  {item.priority:14}  C={item.confidence:.2f}  "
                      f"{len(item.report.blocking)} blocking  {item.capture_id}")
        elif args.command == "show":
            item = queue.get(args.item_id)
            if item is None:
                raise KeyError(args.item_id)
            print(json.dumps({"item": item.model_dump(mode="json"), "events": queue.events(item.item_id)}, indent=2))
        elif args.command == "approve":
            # utf-8-sig: Windows PowerShell 5.1 writes a BOM with -Encoding utf8.
            corrections = (json.loads(Path(args.corrections).read_text(encoding="utf-8-sig"))
                           if args.corrections else None)
            episode = queue.approve(args.item_id, reviewer=args.reviewer, trace_id=trace_id,
                                    corrections=corrections, note=args.note)
            print(f"Approved {args.item_id} -> verified {episode.episode_id} ({trace_id})")
        else:
            queue.reject(args.item_id, reviewer=args.reviewer, reason=args.reason, trace_id=trace_id)
            print(f"Rejected {args.item_id} ({trace_id})")
        return 0
    except (KeyError, ValueError, OSError, RuntimeError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
