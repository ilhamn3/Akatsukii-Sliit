"""Review-queue backend: human decisions, audit history and verified publishing."""

from unittest.mock import Mock
import pytest

from project.agents.a3_validation import ReviewBlocked, validate_draft
from project.core.validation_schemas import ValidationReport
from project.store.knowledge_store import KnowledgeStore
from project.store.review_queue import ReviewQueue, main
from tests.validation_helpers import SHA_B, KeywordEmbedder, make_draft


@pytest.fixture
def store(tmp_path):
    return KnowledgeStore(tmp_path / "repairs.sqlite3", embedder=KeywordEmbedder())


@pytest.fixture
def queue(tmp_path, store):
    return ReviewQueue(tmp_path / "queue.sqlite3", store=store)


def add(queue, store, tmp_path, **kwargs):
    draft = make_draft(tmp_path, **kwargs)
    return queue.enqueue(draft, validate_draft(draft, trace_id="TR-Q", queue=queue, store=store), trace_id="TR-Q")


def test_approval_publishes_verified_episode_with_identity_and_provenance(tmp_path, queue, store):
    item = add(queue, store, tmp_path, complete=True)
    episode = queue.approve(item.item_id, reviewer="senior.tech@plant", trace_id="TR-APPROVE", note="Checked")
    assert episode.status == "verified" and episode.verified_by == "senior.tech@plant"
    assert episode.verified_at.utcoffset() is not None
    assert episode.outcome == "conveyor ran again"
    assert episode.provenance.evidence_spans == item.draft.evidence  # Original draft field paths kept.
    assert episode.provenance.prompt_version == "extraction_v3"
    assert episode.provenance.reviewer_supplied_fields == []
    assert store.get(episode.episode_id, trace_id="TR-READ") == episode
    decided = queue.get(item.item_id)
    assert decided.status == "approved" and decided.decided_by == "senior.tech@plant" and decided.decided_at
    events = queue.events(item.item_id)
    assert [e["action"] for e in events] == ["ENQUEUED", "APPROVED"]
    assert events[1]["actor"] == "senior.tech@plant" and events[1]["trace_id"] == "TR-APPROVE"
    assert "offline_fixture_output" in events[1]["details"]["acknowledged_warnings"]
    assert any(e["operation"] == "STORE_UPSERT" and e["trace_id"] == "TR-APPROVE" for e in store.audit_events())


def test_missing_facts_block_approval_until_reviewer_supplies_them(tmp_path, queue, store):
    item = add(queue, store, tmp_path)
    with pytest.raises(ReviewBlocked) as blocked:
        queue.approve(item.item_id, reviewer="reviewer-1", trace_id="TR-1")
    assert {i.field_path for i in blocked.value.issues} == {"root_cause", "failed_attempts.0.why_failed"}
    assert queue.get(item.item_id).status == "pending" and store.get(item.episode_id, trace_id="TR") is None
    assert queue.events(item.item_id)[-1]["action"] == "APPROVE_FAILED"
    episode = queue.approve(item.item_id, reviewer="reviewer-1", trace_id="TR-2", corrections={
        "root_cause": "Loose connector confirmed by reviewer",
        "failed_attempts.0.why_failed": "Sensor was not the fault"})
    assert episode.record.root_cause == "Loose connector confirmed by reviewer"
    assert episode.provenance.reviewer_supplied_fields == ["failed_attempts.0.why_failed", "root_cause"]
    assert all(s.field_path != "root_cause" for s in episode.provenance.evidence_spans)
    assert queue.events(item.item_id)[-1]["details"]["corrections"]["root_cause"].startswith("Loose")


def test_tampered_report_or_confidence_cannot_bypass_review(tmp_path, queue, store):
    draft = make_draft(tmp_path)
    honest = validate_draft(draft, trace_id="TR", store=store)
    forged = ValidationReport.model_validate({**honest.model_dump(mode="json"), "issues": [],
                                              "priority": "standard",
                                              "confidence": {**honest.confidence.model_dump(), "score": 1.0}})
    item = queue.enqueue(draft, forged, trace_id="TR-FORGED")
    assert item.status == "pending"
    with pytest.raises(ReviewBlocked):
        queue.approve(item.item_id, reviewer="reviewer-1", trace_id="TR-X")
    assert store.get(item.episode_id, trace_id="TR") is None


def test_invalid_corrections_and_identities_are_refused(tmp_path, queue, store):
    item = add(queue, store, tmp_path)
    with pytest.raises(ValueError, match="Unsupported"):
        queue.approve(item.item_id, reviewer="r1", trace_id="T", corrections={"status": "verified"})
    with pytest.raises(ReviewBlocked) as short:
        queue.approve(item.item_id, reviewer="r1", trace_id="T", corrections={
            "root_cause": "x", "failed_attempts.0.why_failed": "Sensor was fine"})
    assert short.value.issues[0].code == "schema_violation"
    for reviewer in ("", "   ", "bad\nname", None):
        with pytest.raises(ValueError):
            queue.approve(item.item_id, reviewer=reviewer, trace_id="T")
    assert queue.get(item.item_id).status == "pending"


def test_reject_is_final_and_needs_reason(tmp_path, queue, store):
    item = add(queue, store, tmp_path)
    with pytest.raises(ValueError):
        queue.reject(item.item_id, reviewer="r1", reason=" ", trace_id="T")
    rejected = queue.reject(item.item_id, reviewer="r1", reason="Duplicate narration", trace_id="TR-REJ")
    assert rejected.status == "rejected" and rejected.decision_note == "Duplicate narration"
    with pytest.raises(ValueError, match="already rejected"):
        queue.approve(item.item_id, reviewer="r2", trace_id="T", corrections={"root_cause": "anything"})
    assert store.get(item.episode_id, trace_id="T") is None
    with pytest.raises(KeyError):
        queue.reject("RQ-MISSING", reviewer="r1", reason="nope", trace_id="T")


def test_second_approval_and_duplicate_source_are_refused(tmp_path, queue, store):
    first = add(queue, store, tmp_path, complete=True)
    duplicate = add(queue, store, tmp_path, complete=True)  # Same recording enqueued twice.
    assert any(i.code == "duplicate_source" for i in duplicate.report.blocking)
    queue.approve(first.item_id, reviewer="r1", trace_id="T1")
    with pytest.raises(ValueError, match="already approved"):
        queue.approve(first.item_id, reviewer="r2", trace_id="T2")
    with pytest.raises(ReviewBlocked) as blocked:
        queue.approve(duplicate.item_id, reviewer="r1", trace_id="T3")
    assert blocked.value.issues[0].code == "duplicate_source"
    assert store.get(first.episode_id, trace_id="T").verified_by == "r1"


def test_store_failure_leaves_item_pending(tmp_path, store):
    failing = Mock(wraps=store)
    failing.put_verified.side_effect = OSError("disk full (simulated)")
    queue = ReviewQueue(tmp_path / "queue.sqlite3", store=failing)
    item = add(queue, store, tmp_path, complete=True)
    with pytest.raises(OSError):
        queue.approve(item.item_id, reviewer="r1", trace_id="T")
    assert queue.get(item.item_id).status == "pending"
    assert queue.events(item.item_id)[-1]["details"]["error_type"] == "OSError"


def test_listing_order_filters_and_restart(tmp_path, queue, store):
    standard = add(queue, store, tmp_path, complete=True)
    low = add(queue, store, tmp_path, sha=SHA_B)
    assert [i.item_id for i in queue.list_items()] == [low.item_id, standard.item_id]
    queue.reject(standard.item_id, reviewer="r1", reason="Not needed", trace_id="T")
    reopened = ReviewQueue(queue.path, store=store)
    assert [i.item_id for i in reopened.list_items()] == [low.item_id]
    assert [i.item_id for i in reopened.list_items(status="rejected")] == [standard.item_id]
    assert len(reopened.list_items(status=None)) == 2
    with pytest.raises(ValueError):
        reopened.list_items(status="verified")


def test_approve_requires_a_store(tmp_path, store):
    queue = ReviewQueue(tmp_path / "q.sqlite3")
    draft = make_draft(tmp_path, complete=True)
    item = queue.enqueue(draft, validate_draft(draft, trace_id="T", store=store), trace_id="T")
    with pytest.raises(RuntimeError):
        queue.approve(item.item_id, reviewer="r1", trace_id="T")


def test_cli_list_show_reject(tmp_path, queue, store, capsys):
    item = add(queue, store, tmp_path)
    db = ["--queue-db", str(queue.path)]
    assert main([*db, "list"]) == 0 and item.item_id in capsys.readouterr().out
    assert main([*db, "show", item.item_id]) == 0 and '"ENQUEUED"' in capsys.readouterr().out
    assert main([*db, "reject", item.item_id, "--reviewer", "r1", "--reason", "CLI test"]) == 0
    assert queue.get(item.item_id).status == "rejected"
    assert main([*db, "show", "RQ-NONE"]) == 1
