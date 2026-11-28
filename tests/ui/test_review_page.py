"""Review Queue page against a REAL ReviewQueue and KnowledgeStore (no mocked API).

Only `_get_queue_and_store` is redirected to temporary storage with the test-only
embedder; the page, project.core.api, ReviewQueue, A3 and A4 all run for real.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

# AppTest's 3 s default is too tight on a cold first run (pages import the store and graph).
APP_TIMEOUT_S = 30

from project.agents.a3_validation import validate_draft
from project.core.trace import TraceLogger
from project.store.knowledge_store import KnowledgeStore
from project.store.review_queue import ReviewQueue
from tests.validation_helpers import SHA_B, KeywordEmbedder, make_draft

REVIEW_PATH = Path(__file__).resolve().parents[2] / "project" / "ui" / "pages" / "1_Review_Queue.py"


@pytest.fixture(autouse=True)
def isolated_trace(monkeypatch, tmp_path):
    monkeypatch.setattr(TraceLogger, "_instance", TraceLogger(str(tmp_path / "trace.jsonl")))


@pytest.fixture
def real_queue(tmp_path):
    store = KnowledgeStore(tmp_path / "repairs.sqlite3", embedder=KeywordEmbedder())
    queue = ReviewQueue(tmp_path / "queue.sqlite3", store=store)
    with patch("project.core.api._get_queue_and_store", lambda **_: (queue, store)):
        yield queue, store


def enqueue(queue, store, tmp_path, **kwargs):
    draft = make_draft(tmp_path, **kwargs)
    return queue.enqueue(draft, validate_draft(draft, trace_id="TR-UI", queue=queue, store=store), trace_id="TR-UI")


def page_text(app) -> str:
    parts = [m.value for m in app.markdown] + [e.value for e in app.error] + [w.value for w in app.warning]
    return "\n".join(parts + [str(getattr(m, "value", "")) for m in app.main])


def test_reviewer_sees_every_published_fact_and_flags_before_approving(tmp_path, real_queue):
    queue, store = real_queue
    item = enqueue(queue, store, tmp_path, complete=True)
    app = AppTest.from_file(str(REVIEW_PATH), default_timeout=APP_TIMEOUT_S).run()
    assert not app.exception
    text = page_text(app)
    for shown in ("Replacing the sensor", "the connector, not the sensor, was at fault",  # Failed attempt.
                  "**Tools:** *(none extracted)*", "**Parts:** *(none extracted)*",
                  "**Machine ID:** *(none extracted)*", "loose connector", "stored energy"):
        assert shown in text, shown
    assert "Fixture output, not a model run" in "\n".join(e.value for e in app.error)
    assert "offline_fixture_output" in text and "uncalibrated" in text

    approve = app.button(key=f"approve_{item.item_id}")
    assert approve.disabled  # No reviewer name yet: nothing can be recorded under a placeholder.
    app.text_input(key="reviewer_name").input("senior.tech@plant").run()
    assert not app.button(key=f"approve_{item.item_id}").disabled
    app.button(key=f"approve_{item.item_id}").click().run()
    assert not app.exception
    episode = store.get(item.episode_id, trace_id="TR-READ")
    assert episode.verified_by == "senior.tech@plant"
    assert queue.get(item.item_id).status == "approved"


def test_blocked_draft_is_approvable_only_after_the_reviewer_corrects_its_fields(tmp_path, real_queue):
    queue, store = real_queue
    item = enqueue(queue, store, tmp_path, sha=SHA_B)  # root_cause and why_failed unknown: blocking.
    app = AppTest.from_file(str(REVIEW_PATH), default_timeout=APP_TIMEOUT_S).run()
    app.text_input(key="reviewer_name").input("senior.tech@plant").run()
    assert app.button(key=f"approve_{item.item_id}").disabled  # Corrections still blank.

    app.text_input(key=f"fix_{item.item_id}_root_cause").input("loose connector").run()
    assert app.button(key=f"approve_{item.item_id}").disabled  # One correction is not enough.
    app.text_input(key=f"fix_{item.item_id}_failed_attempts.0.why_failed").input("the sensor was fine").run()
    app.button(key=f"approve_{item.item_id}").click().run()
    assert not app.exception and not app.error
    episode = store.get(item.episode_id, trace_id="TR-READ")
    assert episode.record.root_cause == "loose connector"
    assert episode.provenance.reviewer_supplied_fields == ["failed_attempts.0.why_failed", "root_cause"]


def test_duplicate_source_can_only_be_rejected_with_a_named_reviewer(tmp_path, real_queue):
    queue, store = real_queue
    first = enqueue(queue, store, tmp_path, complete=True)
    dup = enqueue(queue, store, tmp_path, complete=True)  # Same recording: duplicate_source blocks.
    assert dup.item_id != first.item_id
    app = AppTest.from_file(str(REVIEW_PATH), default_timeout=APP_TIMEOUT_S).run()
    assert "It can only be rejected" in "\n".join(e.value for e in app.error)
    app.text_input(key="reviewer_name").input("senior.tech@plant").run()
    assert app.button(key=f"approve_{dup.item_id}").disabled
    app.text_input(key=f"note_{dup.item_id}").input("same recording as an earlier item").run()
    app.button(key=f"reject_{dup.item_id}").click().run()
    assert queue.get(dup.item_id).status == "rejected"
    assert queue.get(dup.item_id).decided_by == "senior.tech@plant"
