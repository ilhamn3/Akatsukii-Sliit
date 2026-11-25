"""A6 hybrid retrieval: catalogue-derived synthetic episodes written through A4,
real SQLite/FAISS/NetworkX, test-only embeddings (no model download)."""

import copy
import inspect
import json
from unittest.mock import Mock
import pytest

from project.agents.a4_graph import FAULTS, HAZARDS, GraphAgent
from project.agents.a6_retrieval import HybridRetriever, RetrievalAgent, codes, terms
from project.core.contracts import RetrievalEngine
from project.core.registry import build_graph, load_config
from project.core.schemas import Evidence
from project.core.state import create_initial_state
from project.core.trace import read_trace
from project.store.knowledge_store import KnowledgeStore
from scripts.retrieval_dev_eval import catalogue_episodes, evaluate
from tests.validation_helpers import HashEmbedder

FAULT_DATA = json.loads(FAULTS.read_text(encoding="utf-8"))
HAZARD_DATA = json.loads(HAZARDS.read_text(encoding="utf-8"))


def loaded(tmp_path, embedder, episodes=None):
    store = KnowledgeStore(tmp_path / "repairs.sqlite3", embedder=embedder)
    writer = GraphAgent(store)
    for episode in episodes or catalogue_episodes(FAULT_DATA, HAZARD_DATA):
        writer.put_verified(episode, trace_id="TR-LOAD")
    return store


@pytest.fixture
def store(tmp_path):
    return loaded(tmp_path, HashEmbedder())


def test_contract_shape_and_ordering(store):
    params = inspect.signature(HybridRetriever.retrieve).parameters
    contract = inspect.signature(RetrievalEngine.retrieve).parameters
    assert list(contract) == list(params)[: len(contract)] and params["k"].default == 3
    hits = HybridRetriever(store).retrieve("spindle housing too hot, thermal alarm", "CNC Lathe (Turning Center)")
    assert len(hits) == 3 and all(isinstance(h, Evidence) for h in hits)
    assert all(0 <= h.similarity_score <= 1 and h.evidence_id.startswith("EV-") for h in hits)
    assert [h.similarity_score for h in hits] == sorted((h.similarity_score for h in hits), reverse=True)
    assert hits[0].episode_id == "SYN-CAT-L01"
    assert {h.metadata["record"]["machine_type"] for h in hits} == {"cnc_lathe"}
    assert hits[0].metadata["retrieval"]["machine_filter"] == "cnc_lathe"


def test_failed_attempts_outcomes_and_citations_are_returned(store):
    [hit] = HybridRetriever(store).retrieve("spindle housing too hot to touch", "cnc_lathe", k=1)
    attempt = hit.metadata["failed_attempts"][0]
    assert attempt["action"] == "replaced the spindle bearings" and attempt["why_failed"]
    assert hit.metadata["outcome"].startswith("Fault cleared")
    assert "Failed attempts (do not repeat): replaced the spindle bearings" in hit.content
    assert [s["evidence_id"] for s in hit.metadata["steps"]][:2] == ["SYN-L01-S1", "SYN-L01-S2"]
    assert hit.metadata["provenance"]["source_sha256"] and hit.metadata["verified_by"]
    assert "H06" in hit.metadata["catalogue_hazards"]


def test_machine_filter_precedes_ranking_and_unknown_machine_searches_all(store):
    retriever = HybridRetriever(store)
    for label in ("Conveyor Line (Automated Material Handling)", "conveyor belt"):
        hits = retriever.retrieve("spindle housing too hot", label, k=5)
        assert {h.metadata["record"]["machine_type"] for h in hits} == {"conveyor_line"}
    wide = retriever.retrieve("spindle housing too hot", "unspecified", k=1)
    assert wide[0].episode_id == "SYN-CAT-L01" and wide[0].metadata["retrieval"]["cross_machine"]


def test_graph_traversal_finds_error_codes(store):
    # Graph-only ablation: no vector signal at all, the alarm code is found on the fault path.
    [hit] = HybridRetriever(store, use_vector=False).retrieve("operator panel shows ALM-410", "cnc_lathe", k=1)
    assert hit.episode_id == "SYN-CAT-L01" and hit.source_type == "graph"
    assert hit.metadata["retrieval"]["matched_codes"] == ["alm410"]
    assert hit.metadata["retrieval"]["graph_score"] == 1.0 and hit.similarity_score == 0.5
    assert any(n.startswith("fault:SYN-CAT-L01") for n in hit.metadata["retrieval"]["matched_nodes"])
    hybrid = HybridRetriever(store).retrieve("operator panel shows ALM-410", "cnc_lathe", k=2)
    assert hybrid[0].episode_id == "SYN-CAT-L01" and hybrid[0].similarity_score > hybrid[1].similarity_score


def test_graph_evidence_only_adds_support(store):
    hybrid = HybridRetriever(store).retrieve("drive belt slipping under load", "conveyor_line", k=8)
    vector = {h.episode_id: h.similarity_score
              for h in HybridRetriever(store, graph_weight=0, asset_weight=0).retrieve(
                  "drive belt slipping under load", "conveyor_line", k=8)}
    for hit in hybrid:
        assert hit.similarity_score >= vector.get(hit.episode_id, 0.0) - 1e-9
        assert hit.metadata["retrieval"]["vector_cosine"] == pytest.approx(vector.get(hit.episode_id, 0.0), abs=1e-6)


def test_asset_hop_prefers_repairs_on_the_same_asset(tmp_path):
    base = [e for e in catalogue_episodes(FAULT_DATA, HAZARD_DATA) if e.episode_id == "SYN-CAT-C02"][0]
    twins = []
    for suffix, asset in (("A", "Line 1"), ("B", "Line 2")):
        data = base.model_dump(mode="json")
        data["episode_id"], data["record"]["evidence_id"] = f"SYN-TWIN-{suffix}", f"EV-SYN-TWIN-{suffix}"
        data["record"]["machine_id"] = asset
        twins.append(data)
    from project.store.models import VerifiedEpisode
    store = loaded(tmp_path, HashEmbedder(), [VerifiedEpisode.model_validate(t) for t in twins])
    hits = HybridRetriever(store).retrieve("belt slipping at the drive", "conveyor_line", k=2,
                                           machine_id="conveyor line 2")
    assert hits[0].episode_id == "SYN-TWIN-B" and hits[0].metadata["retrieval"]["asset_match"]
    assert hits[0].metadata["record"]["machine_id"] == "CONV-02"
    assert hits[0].similarity_score > hits[1].similarity_score


def test_empty_store_and_invalid_input(tmp_path):
    embedder = HashEmbedder()
    embedder.encode = Mock(wraps=embedder.encode)
    empty = HybridRetriever(KnowledgeStore(tmp_path / "empty.sqlite3", embedder=embedder))
    assert empty.retrieve("anything", "cnc_lathe") == []
    embedder.encode.assert_not_called()
    for query, k in ((" ", 3), ("belt", 0), (None, 3)):
        with pytest.raises(ValueError):
            empty.retrieve(query, "cnc_lathe", k=k)
    with pytest.raises(ValueError):
        HybridRetriever(empty.store, graph_weight=1.5)


def test_terms_and_codes():
    assert codes("ALM-304 and SV-024 and ERR-99; not 105C or M8") == {"alm304", "sv024", "err99"}
    assert terms("The belts were slipping") == {"belt", "slipp"}


def test_agent_fills_blackboard_and_traces(store, isolated_trace):
    state = create_initial_state("Drive belt slipping under load", machine_type="Conveyor Line", trace_id="TR-B-1")
    result = RetrievalAgent(engine=HybridRetriever(store)).run(state)
    assert len(result["retrieved_episodes"]) == 3
    Evidence.model_validate(result["retrieved_episodes"][0])
    assert result["retrieval_confidence"] == result["retrieved_episodes"][0]["similarity_score"]
    assert result["failed_attempts_retrieved"] and "failed because" in result["failed_attempts_retrieved"][0]
    assert result["graph_subgraph_nodes"]
    event = read_trace(str(isolated_trace.log_path))[-1]
    assert (event["agent"], event["event"], event["decision"]) == ("RetrievalAgent", "AGENT_COMPLETED", "EVIDENCE_RETRIEVED")


def test_agent_failure_is_traced_and_leaves_empty_evidence(isolated_trace):
    engine = Mock()
    engine.retrieve.side_effect = RuntimeError("store unavailable (simulated)")
    state = create_initial_state("belt slipping", machine_type="conveyor", trace_id="TR-B-2")
    state["retrieved_episodes"] = [{"stale": True}]
    result = RetrievalAgent(engine=engine).run(state)
    assert result["retrieved_episodes"] == [] and result["retrieval_confidence"] == 0.0
    assert result["audit_trace"][-1]["event"] == "AGENT_ERROR"


def test_pipeline_b_with_retrieval_node(store, monkeypatch):
    """Ilham's Pipeline B with A6 inserted by config only (proposed I5 wiring)."""
    monkeypatch.setattr("project.agents.a6_retrieval.HybridRetriever", lambda: HybridRetriever(store))
    cfg = copy.deepcopy(load_config())
    pipe = cfg["pipelines"]["B"]
    pipe["agents"].append({"name": "RetrievalAgent", "module": "project.agents.a6_retrieval",
                           "class": "RetrievalAgent", "enabled": True, "stub": False})
    for edge in pipe["edges"]:
        if edge["from"] == "DiagnosticAgent":
            edge["on_false"] = "RetrievalAgent"
    pipe["edges"].append({"from": "RetrievalAgent", "to": "DummyOrchestratorAgent"})
    state = create_initial_state("Drive belt slipping under load", machine_type="Conveyor Line", trace_id="TR-B-3")
    final = build_graph("B", config_override=cfg).invoke(state)
    senders = [m["sender"] for m in final["messages"]]
    assert senders.index("RetrievalAgent") < senders.index("SafetyAgent")
    assert final["retrieved_episodes"][0]["metadata"]["record"]["machine_type"] == "conveyor_line"


def test_dev_check_reports_code_computed_metrics(store):
    report = evaluate(store, FAULT_DATA[:10])
    assert report["queries"] == 10
    for scope in ("within_machine", "all_machines"):
        for mode in ("vector_only", "graph_only", "hybrid"):
            metrics = report["results"][f"{scope}/{mode}"]
            assert 0 <= metrics["hit@1"] <= metrics["hit@3"] <= 1 and 0 <= metrics["mrr"] <= 1
