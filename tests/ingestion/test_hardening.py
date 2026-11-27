"""K8 hardening: bounded retries, timeouts that don't crash Pipeline A, fallbacks
that keep work reviewable, durable failure traces and concurrent store writes.
Real agents, graph, SQLite/FAISS/NetworkX; test clients/embedders only."""

import json
from pathlib import Path
import threading
from unittest.mock import Mock
import pytest

from project.agents.a2_extraction import ExtractionAgent
from project.agents.a3_validation import ValidationAgent, validate_draft
from project.agents.a6_retrieval import HybridRetriever
from project.core.extraction_schemas import ExtractedRepair
from project.core.llm import FakeLLM
from project.core.registry import build_graph
from project.core.state import create_initial_state
from project.core.trace import TraceLogger, read_trace
from project.store.knowledge_store import KnowledgeStore
from project.store.review_queue import ReviewQueue
from scripts.retrieval_dev_eval import catalogue_episodes
from tests.validation_helpers import FIXTURES, HashEmbedder, KeywordEmbedder, make_draft, model_output

FAULTS = json.loads((Path(__file__).parents[2] / "project/data/fault_catalogue/faults.json").read_text())
HAZARDS = json.loads((Path(__file__).parents[2] / "project/data/fault_catalogue/hazards.json").read_text())


@pytest.fixture
def trace(monkeypatch, tmp_path):
    logger = TraceLogger(str(tmp_path / "trace.jsonl"))
    monkeypatch.setattr(TraceLogger, "_instance", logger)
    return logger


def capture_state():
    state = create_initial_state("", trace_id="TR-K8")
    state["capture_result"] = json.loads((FIXTURES / "capture.json").read_text())
    return state


def client_returning(*outputs):
    client = Mock()
    client.model_name = "test-client"
    client.generate_structured.side_effect = list(outputs)
    return client


# ------------------------------------------------------------------ A2 retries

def test_invalid_output_gets_one_corrective_retry(trace):
    bad = model_output(complete=True)
    bad["symptoms"][0]["sources"][0]["quote"] = "a quote that is not in the segment"
    client = client_returning(ExtractedRepair.model_validate(bad),
                              ExtractedRepair.model_validate(model_output(complete=True)))
    result = ExtractionAgent(client, trace).run(capture_state())
    assert result["draft_record"] is not None and client.generate_structured.call_count == 2
    assert "extraction_retried_after_invalid_output" in result["draft_record"]["review_reasons"]
    retry_prompt = json.loads(client.generate_structured.call_args_list[1].kwargs["prompt"])
    assert "does not match segment" in retry_prompt["previous_output_rejected"]
    assert retry_prompt["segments"] == json.loads(client.generate_structured.call_args_list[0].kwargs["prompt"])["segments"]
    assert read_trace(str(trace.log_path))[-1]["decision"] == "EXTRACTION_DRAFT_CREATED_AFTER_2_ATTEMPTS"


def test_retries_are_bounded_and_configurable(trace, monkeypatch):
    malformed = {"unexpected": "data"}
    client = client_returning(malformed, malformed, malformed)
    result = ExtractionAgent(client, trace).run(capture_state())
    assert result["draft_record"] is None and client.generate_structured.call_count == 2
    assert "after 2 attempt(s)" in result["errors"][-1]
    monkeypatch.setenv("EXTRACTION_MAX_ATTEMPTS", "1")
    single = client_returning(malformed)
    ExtractionAgent(single, trace).run(capture_state())
    assert single.generate_structured.call_count == 1
    monkeypatch.setenv("EXTRACTION_MAX_ATTEMPTS", "9")
    assert "between 1 and 5" in ExtractionAgent(client_returning(), trace).run(capture_state())["errors"][-1]


def test_timeouts_are_not_multiplied_by_a2(trace):
    client = client_returning(TimeoutError("simulated provider timeout"))
    result = ExtractionAgent(client, trace).run(capture_state())
    assert client.generate_structured.call_count == 1  # The shared client owns transport retries.
    assert result["draft_record"] is None and "TimeoutError" in result["errors"][-1]


# ------------------------------------------------- Pipeline A survives failures

def run_pipeline_a(monkeypatch, tmp_path, *, capture=None, client=None):
    from project.agents.a1_capture import CaptureAgent
    monkeypatch.setenv("FACTORY_MEMORY_STORAGE", str(tmp_path))
    monkeypatch.setattr(CaptureAgent, "process", capture or (lambda self, s: {
        "capture_result": json.loads((FIXTURES / "capture.json").read_text())}))
    monkeypatch.setattr("project.agents.a2_extraction.get_llm_client", lambda: client)
    return build_graph("A").invoke(create_initial_state("", trace_id="TR-K8-PIPE"))


def test_simulated_api_timeout_does_not_crash_pipeline_a(monkeypatch, tmp_path, trace):
    client = client_returning(TimeoutError("simulated provider timeout"))
    result = run_pipeline_a(monkeypatch, tmp_path, client=client)  # Must not raise.
    assert [m["sender"] for m in result["messages"]] == ["CaptureAgent", "ExtractionAgent", "ValidationAgent"]
    assert len(result["errors"]) == 1 and "TimeoutError" in result["errors"][0]  # Root cause only.
    assert result["draft_record"] is None and result["review_item_id"] is None
    events = [(e["agent"], e["event"], e["decision"]) for e in read_trace(str(trace.log_path))
              if e["trace_id"] == "TR-K8-PIPE" and e["event"] != "AGENT_START"]
    assert ("ExtractionAgent", "AGENT_ERROR", "ERROR") in events
    assert ("ValidationAgent", "AGENT_COMPLETED", "SKIPPED_UPSTREAM_FAILURE") in events


def test_capture_failure_is_traced_once_and_downstream_skips(monkeypatch, tmp_path, trace):
    def broken(self, state):
        raise RuntimeError("FFmpeg is required (simulated missing binary)")
    client = Mock()
    result = run_pipeline_a(monkeypatch, tmp_path, capture=broken, client=client)
    client.generate_structured.assert_not_called()
    assert len(result["errors"]) == 1 and "FFmpeg" in result["errors"][0]
    decisions = {e["agent"]: e["decision"] for e in read_trace(str(trace.log_path))
                 if e["trace_id"] == "TR-K8-PIPE" and e["event"] != "AGENT_START"}
    assert decisions == {"CaptureAgent": "ERROR", "ExtractionAgent": "SKIPPED_UPSTREAM_FAILURE",
                         "ValidationAgent": "SKIPPED_UPSTREAM_FAILURE"}


def test_successful_pipeline_still_reaches_the_review_queue(monkeypatch, tmp_path, trace):
    client = FakeLLM(canned_response=ExtractedRepair.model_validate(model_output(complete=True)))
    result = run_pipeline_a(monkeypatch, tmp_path, client=client)
    assert result["errors"] == [] and result["review_item_id"]


# -------------------------------------------------------- A3 and A6 fallbacks

class BrokenStore:
    """A store whose reads fail as if SQLite were locked and the embedder offline."""

    embedder = KeywordEmbedder()

    def get(self, *args, **kwargs):
        import sqlite3
        raise sqlite3.OperationalError("database is locked (simulated)")

    def search_symptoms(self, *args, **kwargs):
        raise OSError("embedding model unavailable (simulated)")


def test_dedup_failures_degrade_to_reviewable_warnings(tmp_path, trace):
    queue = ReviewQueue(tmp_path / "q.sqlite3", store=KnowledgeStore(tmp_path / "s.sqlite3", embedder=KeywordEmbedder()))
    draft = make_draft(tmp_path, complete=True)
    report = validate_draft(draft, trace_id="TR-K8", queue=queue, store=BrokenStore())
    warnings = {i.code: i.message for i in report.issues if i.severity == "warning"}
    assert "OperationalError" in warnings["store_dedup_unavailable"]
    assert "OSError" in warnings["semantic_dedup_unavailable"]
    state = create_initial_state("", trace_id="TR-K8")
    state["draft_record"] = draft
    result = ValidationAgent(queue=queue, store=BrokenStore(), trace_logger=trace).run(state)
    assert result["review_item_id"] and queue.get(result["review_item_id"]).status == "pending"


def test_retrieval_falls_back_to_graph_when_embeddings_fail(tmp_path):
    store = KnowledgeStore(tmp_path / "r.sqlite3", embedder=HashEmbedder())
    for episode in catalogue_episodes(FAULTS, HAZARDS)[:8]:
        store.put_verified(episode, trace_id="TR-LOAD")
    store.embedder.encode = Mock(side_effect=OSError("model weights missing (simulated)"))
    hits = HybridRetriever(store).retrieve("operator panel shows ALM-410, spindle hot", "cnc_lathe", k=2)
    assert hits[0].episode_id == "SYN-CAT-L01"
    assert all(h.metadata["retrieval"]["degraded"] == ["vector_unavailable:OSError"] for h in hits)
    assert all(h.metadata["retrieval"]["vector_cosine"] == 0 and h.similarity_score <= 0.5 for h in hits)


# ----------------------------------------------------------- store contention

def test_concurrent_writers_all_commit(tmp_path):
    db = tmp_path / "concurrent.sqlite3"
    KnowledgeStore(db, embedder=HashEmbedder())
    episodes, errors = catalogue_episodes(FAULTS, HAZARDS)[:20], []

    def write(chunk):
        store = KnowledgeStore(db, embedder=HashEmbedder())  # Separate instance per writer.
        for episode in chunk:
            try:
                store.put_verified(episode, trace_id="TR-CONCURRENT")
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(repr(exc))

    threads = [threading.Thread(target=write, args=(episodes[i::4],)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert errors == [] and not any(t.is_alive() for t in threads)
    reader = KnowledgeStore(db, embedder=HashEmbedder())
    projection = reader.projection(trace_id="TR-READ")
    assert len(projection.episodes) == 20 and projection.revision == 20
    assert sum(e["operation"] == "STORE_UPSERT" for e in reader.audit_events()) == 20
