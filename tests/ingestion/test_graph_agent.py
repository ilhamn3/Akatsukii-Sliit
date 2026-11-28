"""A4 entity resolution and verified graph writes: real vocabulary/catalogue data,
real SQLite/FAISS/NetworkX, test-only embeddings."""

import json
import networkx as nx
import pytest

from project.agents.a3_validation import ReviewBlocked, validate_draft
from project.agents.a4_graph import ALIASES, FAULTS, HAZARDS, EntityResolver, GraphAgent, default_resolver
from project.core.trace import TraceLogger, read_trace
from project.store.knowledge_store import KnowledgeStore
from project.store.review_queue import ReviewQueue
from tests.validation_helpers import SHA_B, KeywordEmbedder, make_draft
import yaml


@pytest.fixture
def trace(monkeypatch, tmp_path):
    logger = TraceLogger(str(tmp_path / "trace.jsonl"))
    monkeypatch.setattr(TraceLogger, "_instance", logger)
    return logger


@pytest.fixture
def store(tmp_path):
    return KnowledgeStore(tmp_path / "repairs.sqlite3", embedder=KeywordEmbedder())


@pytest.fixture
def queue(tmp_path, store, trace):
    return ReviewQueue(tmp_path / "queue.sqlite3", store=store)


def enqueue(queue, store, draft):
    return queue.enqueue(draft, validate_draft(draft, trace_id="TR-Q", queue=queue, store=store), trace_id="TR-Q")


@pytest.mark.parametrize("raw,expected", [
    ("conveyor", "conveyor_line"), ("Conveyor Belt", "conveyor_line"), ("the belt conveyor", "conveyor_line"),
    ("hyd. press 3", "hydraulic_press"), ("Hydraulic Press", "hydraulic_press"), ("press", "hydraulic_press"),
    ("CNC lathe", "cnc_lathe"), ("turning center", "cnc_lathe"),
    ("injection molding machine", "injection_moulding"), ("injection press", "injection_moulding"),
    ("screw compressor", "air_compressor"), ("air_compressor", "air_compressor"),
])
def test_machine_aliases_resolve_to_one_catalogue_id(raw, expected):
    link = default_resolver().resolve_machine(raw)
    assert (link.status, link.entity_id, link.raw) == ("resolved", expected, raw)


@pytest.mark.parametrize("raw,status,candidates", [
    ("drill press", "unresolved", ["hydraulic_press"]),          # Not the catalogue's press.
    ("refrigeration compressor", "unresolved", ["air_compressor"]),
    ("cooling pump", "unresolved", []), ("belt", "unresolved", []),
    ("conveyor and compressor", "ambiguous", ["air_compressor", "conveyor_line"]),
])
def test_unknown_or_ambiguous_labels_are_not_forced(raw, status, candidates):
    link = default_resolver().resolve_machine(raw)
    assert (link.status, link.entity_id, link.candidates) == (status, None, candidates)


@pytest.mark.parametrize("raw", ["hyd. press 3", "Press-03", "PRESS 03", "press no. 3", "hydraulic press #3", "3"])
def test_asset_aliases_resolve_to_one_entity(raw):
    link = default_resolver().resolve_asset(raw, "hydraulic_press")
    assert (link.status, link.entity_id) == ("resolved", "HYD-03")  # The data's HYD-NN convention.


def test_asset_conflicts_unknown_formats_and_registry():
    resolver = default_resolver()
    conflict = resolver.resolve_asset("Press-03", "conveyor_line")
    # "press" also names injection moulding machines ("injection press").
    assert conflict.status == "conflict"
    assert conflict.candidates == ["conveyor_line", "hydraulic_press", "injection_moulding"]
    assert resolver.resolve_asset("Line 2", "conveyor_line").entity_id == "CNV-02"
    assert resolver.resolve_asset("SN-88231-X", "hydraulic_press").status == "unresolved"
    vocabulary = yaml.safe_load(ALIASES.read_text(encoding="utf-8"))
    registered = EntityResolver(vocabulary, json.loads(FAULTS.read_text()), json.loads(HAZARDS.read_text()),
                                assets=["HYD-01"])
    assert registered.resolve_asset("press 3", "hydraulic_press").method == "asset_registry"
    assert registered.resolve_asset("press 1", "hydraulic_press").entity_id == "HYD-01"


def test_hazard_links_are_specific_or_stay_ambiguous():
    resolver = default_resolver()
    [electrical] = resolver.link_hazards("Isolate electrical power before repair", "conveyor_line", 0)
    assert (electrical.entity_id, electrical.field_path) == ("H02", "safety_warnings.0")
    assert {l.entity_id for l in resolver.link_hazards("The ram could drop", "hydraulic_press", 1)} == {"H03", "H12"}
    [generic] = resolver.link_hazards("warning about stored energy", "conveyor_line", 0)
    assert generic.status == "ambiguous" and generic.entity_id is None and generic.candidates == ["H13"]
    assert resolver.link_hazards("Wear gloves", "cnc_lathe", 0)[0].status == "unresolved"


def test_vocabulary_is_validated_against_the_catalogue():
    vocabulary = yaml.safe_load(ALIASES.read_text(encoding="utf-8"))
    faults, hazards = json.loads(FAULTS.read_text()), json.loads(HAZARDS.read_text())
    for broken in ({**vocabulary, "machines": {**vocabulary["machines"], "lathe_x": vocabulary["machines"]["cnc_lathe"]}},
                   {**vocabulary, "machines": {k: v for k, v in vocabulary["machines"].items() if k != "cnc_lathe"}},
                   {**vocabulary, "hazard_phrases": {"H99": ["made up"]}},
                   {**vocabulary, "ignore_words": [False]}):
        with pytest.raises((ValueError, TypeError)):
            EntityResolver(broken, faults, hazards)


def test_approval_writes_canonical_linked_episode(tmp_path, queue, store, trace):
    item = enqueue(queue, store, make_draft(tmp_path, complete=True))
    episode = queue.approve(item.item_id, reviewer="r1", trace_id="TR-APPROVE", corrections={
        "safety_warnings": ["Isolate electrical power before repair"]})
    assert episode.record.machine_type == "conveyor_line"
    machine = next(l for l in episode.provenance.entity_links if l.kind == "machine")
    assert (machine.raw, machine.method) == ("conveyor", "alias")
    assert store.by_machine("conveyor_line", trace_id="T") == [episode]
    assert [h.episode_id for h in store.related("catalogue_hazard", "H02", trace_id="T")] == [episode.episode_id]
    graph = store.graph_snapshot(trace_id="T")
    eid = episode.episode_id
    path = [("machine", "conveyor_line"), ("fault", eid, "0"), ("cause", eid, "0"), ("fix", eid, "0"),
            ("hazard", eid, "0"), ("catalogue_hazard", "h02")]
    assert all(graph.has_edge(a, b) for a, b in zip(path, path[1:]))
    assert nx.has_path(graph, path[0], path[-1])
    events = [e for e in read_trace(str(tmp_path / "trace.jsonl")) if e["agent"] == "GraphAgent"]
    assert events[-1]["event"] == "AGENT_COMPLETED" and events[-1]["trace_id"] == "TR-APPROVE"
    assert events[-1]["decision"] == "GRAPH_WRITE_COMPLETED"
    assert "machine:machine_type=conveyor_line" in queue.events(item.item_id)[-1]["details"]["entity_links"]


def test_asset_number_in_label_becomes_canonical_asset(tmp_path, queue, store):
    draft = make_draft(tmp_path, complete=True)
    draft["extraction"]["machine_type"]["value"] = "hyd. press 3"
    for span in draft["evidence"]:  # Keep the label grounded so A3's number check passes.
        if span["field_path"] == "machine_type":
            span["quote"] = "hyd. press 3"
    episode = queue.approve(enqueue(queue, store, draft).item_id, reviewer="r1", trace_id="T")
    assert (episode.record.machine_type, episode.record.machine_id) == ("hydraulic_press", "HYD-03")
    assert [h.episode_id for h in store.related("asset", "hyd-03", trace_id="T")] == [episode.episode_id]
    links = {l.kind: l for l in episode.provenance.entity_links}
    assert links["machine"].raw == links["asset"].raw == "hyd. press 3"


def test_unresolved_machine_blocks_until_a_catalogue_label_is_given(tmp_path, queue, store):
    draft = make_draft(tmp_path, complete=True)
    draft["extraction"]["machine_type"]["value"] = "cooling pump"
    item = enqueue(queue, store, draft)
    assert any(i.code == "machine_type_unresolved" for i in item.report.blocking)
    assert item.priority == "low_confidence"
    for attempt in (None, {"machine_type": "cooling pump 2"}):
        with pytest.raises(ReviewBlocked) as blocked:
            queue.approve(item.item_id, reviewer="r1", trace_id="T", corrections=attempt)
        assert blocked.value.issues[0].code == "machine_type_unresolved"
    assert queue.get(item.item_id).status == "pending"
    episode = queue.approve(item.item_id, reviewer="r1", trace_id="T", corrections={"machine_type": "conveyor_line"})
    assert episode.record.machine_type == "conveyor_line"
    assert "machine_type" in episode.provenance.reviewer_supplied_fields


def test_asset_conflict_blocks_until_corrected(tmp_path, queue, store):
    draft = make_draft(tmp_path, complete=True)
    draft["extraction"]["machine_id"] = {"value": "Press-03", "sources": draft["extraction"]["machine_type"]["sources"]}
    draft["evidence"].append({**draft["evidence"][0], "field_path": "machine_id"})
    item = enqueue(queue, store, draft)
    assert ("asset_type_conflict", "machine_id") in {(i.code, i.field_path) for i in item.report.blocking}
    with pytest.raises(ReviewBlocked):
        queue.approve(item.item_id, reviewer="r1", trace_id="T")
    episode = queue.approve(item.item_id, reviewer="r1", trace_id="T", corrections={"machine_id": "Line 2"})
    assert episode.record.machine_id == "CNV-02"


def test_direct_writes_cannot_bypass_resolution(tmp_path, store, trace):
    agent = GraphAgent(store)
    queue = ReviewQueue(tmp_path / "q.sqlite3", store=store, graph_agent=agent)
    item = enqueue(queue, store, make_draft(tmp_path, complete=True))
    episode = queue.approve(item.item_id, reviewer="r1", trace_id="T")
    bad = episode.model_copy(update={"record": episode.record.model_copy(update={"machine_type": "cooling pump"})})
    with pytest.raises(ReviewBlocked):
        agent.put_verified(bad, trace_id="TR-BAD")
    assert store.get(episode.episode_id, trace_id="T").record.machine_type == "conveyor_line"
    assert read_trace(str(tmp_path / "trace.jsonl"))[-1]["event"] == "AGENT_ERROR"


def test_re_resolution_is_idempotent_and_keeps_original_text(tmp_path, queue, store):
    episode = queue.approve(enqueue(queue, store, make_draft(tmp_path, complete=True)).item_id,
                            reviewer="r1", trace_id="T")
    again, _ = GraphAgent(store).resolve_episode(episode)
    assert again == episode
    events = store.audit_events()
    GraphAgent(store).put_verified(episode, trace_id="TR-REPEAT")
    assert store.audit_events()[len(events):][0]["operation"] == "STORE_NOOP"


def test_dedup_compares_canonical_machines(tmp_path, queue, store):
    enqueue(queue, store, make_draft(tmp_path, complete=True))
    other = make_draft(tmp_path, complete=True, sha=SHA_B)
    other["extraction"]["machine_type"]["value"] = "conveyor belt"  # Different label, same entity.
    report = validate_draft(other, trace_id="T", queue=queue, store=store)
    assert any(i.code == "possible_duplicate" for i in report.issues)
