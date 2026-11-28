"""A3 validation: real drafts, real SQLite/FAISS stores, test-only embeddings."""

import json
import pytest
from pydantic import ValidationError

from project.agents.a3_validation import ValidationAgent, _quantity_problems, validate_draft
from project.core.extraction_schemas import DraftRepairRecord
from project.core.schemas import RepairRecord
from project.core.state import create_initial_state
from project.core.trace import TraceLogger, read_trace
from project.store.knowledge_store import KnowledgeStore
from project.store.review_queue import ReviewQueue
from tests.validation_helpers import SHA_B, KeywordEmbedder, make_draft


@pytest.fixture
def store(tmp_path):
    return KnowledgeStore(tmp_path / "repairs.sqlite3", embedder=KeywordEmbedder())


@pytest.fixture
def queue(tmp_path, store):
    return ReviewQueue(tmp_path / "queue.sqlite3", store=store)


def codes(report, severity=None):
    return {(i.code, i.field_path) for i in report.issues if severity in (None, i.severity)}


def test_partial_draft_is_not_completed_with_invented_facts(tmp_path, store):
    report = validate_draft(make_draft(tmp_path), trace_id="TR-A3", store=store)
    assert report.candidate_record is None
    assert {("missing_required_fact", "root_cause"),
            ("missing_required_fact", "failed_attempts.0.why_failed")} <= codes(report, "blocking")
    assert report.priority == "low_confidence" and report.human_review_required
    # 0.45*0.75 + 0.15*0 + 0.25*1 + 0.15*1: transparent, reported per component.
    assert report.confidence.score == pytest.approx(0.7375)
    assert report.confidence.method == "rule_based_v1_uncalibrated"
    assert ("offline_fixture_output", None) in codes(report, "warning")
    assert report.outcome == "conveyor ran again"


def test_complete_draft_maps_to_shared_record_with_evidence(tmp_path, store):
    report = validate_draft(make_draft(tmp_path, complete=True), trace_id="TR-A3", store=store)
    record = report.candidate_record
    assert isinstance(record, RepairRecord) and not report.blocking
    assert record.root_cause == "loose connector"
    assert record.failed_attempts[0].why_failed.startswith("the connector")
    assert record.steps[0].evidence_id == "S1" and record.steps[0].timestamp == "00:00:05"
    assert record.evidence_id == "EV-AAAAAAAAAAAAAAAA" and report.episode_id == "EP-AAAAAAAAAAAAAAAA"
    assert record.confidence == report.confidence.score == 1.0
    assert report.priority == "standard"  # Still pending human review, never auto-verified.


def test_caller_cannot_label_a_draft_verified_or_confident(tmp_path):
    draft = make_draft(tmp_path, complete=True)
    for change in ({"status": "verified"}, {"confidence": 0.99}, {"review_required": False}):
        with pytest.raises(ValidationError):
            DraftRepairRecord.model_validate({**draft, **change})


@pytest.mark.parametrize("value,quote,problem", [
    ("torque to 30 Nm", "torque to 30 bar", "Unit"),
    ("set 300 bar", "set 30 bar", "Value"),
    ("about 30 bar", "about 30bar", None),
    ("30 barrels moved", "thirty barrels", "Value"),
])
def test_quantities_must_match_quotes(value, quote, problem):
    problems = _quantity_problems(value, quote)
    assert (problems[0].startswith(problem) if problem else problems == [])


def test_changed_number_and_missing_evidence_block(tmp_path, store):
    draft = make_draft(tmp_path, complete=True)
    draft["extraction"]["steps"][0]["value"] = "secured the connector to 5 Nm"
    draft["evidence"] = [s for s in draft["evidence"] if s["field_path"] != "symptoms.0"]
    draft["evidence"].append({**draft["evidence"][0], "field_path": "tools.4"})
    report = validate_draft(draft, trace_id="TR-A3", store=store)
    assert {("quantity_not_in_quote", "steps.0"), ("ungrounded_fact", "symptoms.0")} <= codes(report, "blocking")
    assert ("orphan_evidence", "tools.4") in codes(report, "warning")
    assert report.priority == "low_confidence" and report.confidence.grounding < 1


def test_conflicting_attempt_and_quality_flags_raise_priority(tmp_path, store):
    draft = make_draft(tmp_path, complete=True, quality_flags=["low_log_probability"])
    draft["extraction"]["steps"][0]["value"] = "Replacing the sensor."
    report = validate_draft(draft, trace_id="TR-A3", store=store)
    assert ("failed_attempt_conflicts_with_step", "failed_attempts.0.action") in codes(report, "warning")
    assert report.confidence.capture_quality == 0.5
    assert report.priority == "low_confidence"


def test_exact_and_semantic_duplicates(tmp_path, store, queue):
    first = make_draft(tmp_path, complete=True)
    item = queue.enqueue(first, validate_draft(first, trace_id="TR-1", queue=queue, store=store), trace_id="TR-1")
    # Same recording with a different extraction: blocking exact duplicate.
    rerun = json.loads(json.dumps(first))
    rerun["extraction"]["tools"] = []
    exact = validate_draft(rerun, trace_id="TR-2", queue=queue, store=store)
    assert any(i.code == "duplicate_source" and item.item_id in i.related_ids for i in exact.blocking)
    # Different recording, same machine and symptoms: reviewable warning, not blocked.
    other = make_draft(tmp_path, complete=True, sha=SHA_B)
    semantic = validate_draft(other, trace_id="TR-3", queue=queue, store=store)
    duplicate = next(i for i in semantic.issues if i.code == "possible_duplicate")
    assert duplicate.severity == "warning" and duplicate.related_ids == [item.item_id]
    assert semantic.priority == "low_confidence" and not semantic.blocking
    queue.approve(item.item_id, reviewer="reviewer-1", trace_id="TR-4")
    against_store = validate_draft(other, trace_id="TR-5", queue=queue, store=store)
    assert next(i for i in against_store.issues if i.code == "possible_duplicate").related_ids == [item.episode_id]


def test_dedup_without_store_is_reported(tmp_path):
    report = validate_draft(make_draft(tmp_path, complete=True), trace_id="TR-A3")
    assert ("semantic_dedup_unavailable", None) in codes(report, "warning")


def test_agent_enqueues_traces_and_is_idempotent(tmp_path, store, queue):
    logger = TraceLogger(str(tmp_path / "a3.jsonl"))
    agent = ValidationAgent(queue=queue, store=store, trace_logger=logger)
    state = create_initial_state("")
    state["draft_record"] = make_draft(tmp_path)
    result = agent.run(state)
    item = queue.get(result["review_item_id"])
    assert item.status == "pending" and item.priority == "low_confidence"
    assert result["validation_report"]["candidate_record"] is None
    trace = read_trace(str(tmp_path / "a3.jsonl"))
    assert trace[-1]["event"] == "AGENT_COMPLETED" and trace[-1]["decision"] == "QUEUED_LOW_CONFIDENCE"
    again = agent.run(state)
    assert again["review_item_id"] == item.item_id and len(queue.list_items()) == 1
    assert read_trace(str(tmp_path / "a3.jsonl"))[-1]["decision"] == "REVIEW_ITEM_EXISTS"
    json.dumps(result["validation_report"])


def test_agent_without_draft_fails_visibly(tmp_path, store, queue):
    state = create_initial_state("")
    state["validation_report"] = {"stale": True}
    result = ValidationAgent(queue=queue, store=store, trace_logger=TraceLogger(str(tmp_path / "t.jsonl"))).run(state)
    assert result["validation_report"] is None and result["review_item_id"] is None
    assert result["audit_trace"][-1]["event"] == "AGENT_ERROR" and queue.list_items() == []


def test_threshold_comes_from_shared_config(tmp_path, store, queue):
    assert ValidationAgent(queue=queue, store=store).threshold == 0.75
    from project.agents.a3_validation import DUPLICATE_THRESHOLD
    from project.core.registry import load_config
    configured = load_config()["system"]["duplicate_similarity_threshold"]
    assert ValidationAgent(queue=queue, store=store).duplicate_threshold == configured == DUPLICATE_THRESHOLD == 0.55
