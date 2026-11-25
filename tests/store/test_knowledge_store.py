"""Real SQLite/FAISS/NetworkX, injected tiny embeddings: no model/API downloads."""

import json
from pathlib import Path
import sqlite3
from unittest.mock import Mock
import numpy as np
import pytest
from pydantic import ValidationError

from project.store.knowledge_store import KnowledgeStore
from project.store.models import VerifiedEpisode


class TinyEmbedder:
    model_id = "test-only-tiny-v1"
    dimension = 3

    def encode(self, texts):
        return np.array([[float('belt' in t.lower()), float('heat' in t.lower()), 0.1]
                         for t in texts], dtype=np.float32)


@pytest.fixture
def episodes():
    path = Path(__file__).parents[1] / 'fixtures/store/verified_episodes.json'
    return [VerifiedEpisode.model_validate(e) for e in json.loads(path.read_text())]


@pytest.fixture
def store(tmp_path):
    return KnowledgeStore(tmp_path / 'repairs.sqlite3', embedder=TinyEmbedder())


def test_verified_write_restart_and_provenance(store, episodes):
    for episode in episodes:
        store.put_verified(episode, trace_id='TR-WRITE')
    reopened = KnowledgeStore(store.db.path, embedder=TinyEmbedder())
    assert reopened.get(episodes[0].episode_id, trace_id='TR-READ') == episodes[0]
    matches = reopened.by_machine(' CONVEYOR_LINE ', trace_id='TR-MACHINE')
    assert matches == [episodes[0]]
    assert reopened.by_machine('conveyor', trace_id='TR-NO-IMPLICIT-ALIAS') == []
    hits = reopened.search_symptoms('belt slipping', trace_id='TR-SEARCH', k=1)
    assert hits[0].episode_id == episodes[0].episode_id
    assert hits[0].metadata['record']['failed_attempts'][0]['outcome'] == 'Belt still slipped'
    assert hits[0].metadata['outcome'] == episodes[0].outcome
    assert hits[0].metadata['provenance'] == episodes[0].provenance.model_dump(mode='json')
    assert hits[0].similarity_score > 0.99
    assert reopened.audit_events()[0]['trace_id'] == 'TR-WRITE'


def test_machine_filter_applied_before_top_k_and_empty_avoids_embedding(store, episodes):
    store.embedder.encode = Mock(wraps=store.embedder.encode)
    assert store.search_symptoms('belt', trace_id='TR-EMPTY') == []
    store.embedder.encode.assert_not_called()
    for episode in episodes:
        store.put_verified(episode, trace_id='TR-WRITE')
    hits = store.search_symptoms('belt', machine_type='air_compressor', k=1, trace_id='TR-FILTER')
    assert [h.episode_id for h in hits] == [episodes[1].episode_id]
    assert store.search_symptoms('belt', machine_type='air_compressor', min_score=0.9,
                                 trace_id='TR-THRESHOLD') == []


def test_graph_paths_and_episode_scoping(store, episodes):
    for episode in episodes:
        store.put_verified(episode, trace_id='TR-WRITE')
    hits = store.related('cause', episodes[0].record.root_cause, trace_id='TR-GRAPH')
    assert [h.episode_id for h in hits] == [episodes[0].episode_id]
    graph = store.graph_snapshot(trace_id='TR-GRAPH')
    relations = {data['relation'] for _, _, data in graph.edges(data=True)}
    assert {'exhibited', 'caused_by', 'addressed_by', 'has_episode_warning'} <= relations
    # Shared entity nodes (machine, asset, catalogue hazard) link episodes; all
    # other nodes are scoped to one episode, and every edge names its episode.
    from project.store.graph import SHARED
    for source, target, data in graph.edges(data=True):
        if source[0] not in SHARED:
            assert source[1] == data['episode_id']
        if target[0] not in SHARED:
            assert target[1] == data['episode_id']
    assert ('asset', 'syn-cv-01') in graph
    graph.clear()
    assert store.graph_snapshot(trace_id='TR-NEW-SNAPSHOT').number_of_nodes() > 0


def test_replacement_removes_old_vector_and_graph_facts(store, episodes):
    first = episodes[0]
    store.put_verified(first, trace_id='TR-ONE')
    replacement = first.model_copy(deep=True)
    replacement.record.machine_type = 'air_compressor'
    replacement.record.symptoms = ['overheating']
    replacement.record.root_cause = 'Corrected cause from review'
    store.put_verified(replacement, trace_id='TR-TWO')
    assert not store.by_machine('conveyor_line', trace_id='TR-MACHINE')
    assert not store.related('cause', first.record.root_cause, trace_id='TR-OLD-CAUSE')
    assert store.search_symptoms('heat', trace_id='TR-NEW-VECTOR')[0].similarity_score > 0.99
    assert len(store.related('cause', replacement.record.root_cause, trace_id='TR-NEW-CAUSE')) == 1


def test_idempotent_write_and_second_instance_sees_updates(store, episodes):
    peer = KnowledgeStore(store.db.path, embedder=TinyEmbedder())
    store.put_verified(episodes[0], trace_id='TR-ONE')
    store.embedder.encode = Mock(side_effect=AssertionError('must not re-embed'))
    store.put_verified(episodes[0], trace_id='TR-RETRY')
    assert store.audit_events()[-1]['operation'] == 'STORE_NOOP'
    assert peer.get(episodes[0].episode_id, trace_id='TR-PEER') == episodes[0]


@pytest.mark.parametrize('projection', ['VectorIndex', 'KnowledgeGraph'])
def test_projection_failure_rolls_back_sql_and_vector(monkeypatch, store, episodes, projection):
    store.put_verified(episodes[0], trace_id='TR-BASE')
    monkeypatch.setattr('project.store.knowledge_store.' + projection,
                        Mock(side_effect=RuntimeError('simulated projection failure')))
    with pytest.raises(RuntimeError):
        store.put_verified(episodes[1], trace_id='TR-FAIL')
    assert store.get(episodes[1].episode_id, trace_id='TR-VERIFY') is None
    assert store.get(episodes[0].episode_id, trace_id='TR-VERIFY') == episodes[0]
    assert any(e['operation'] == 'STORE_ERROR' and e['trace_id'] == 'TR-FAIL' for e in store.audit_events())
    with store.db.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM episodes').fetchone()[0] == 1


@pytest.mark.parametrize('vector', [[[0, 0, 0]], [[float('nan'), 1, 0]], [[1, 0]], [[float('inf'), 1, 0]]])
def test_bad_embeddings_do_not_write(store, episodes, vector):
    store.embedder.encode = Mock(return_value=vector)
    with pytest.raises(ValueError):
        store.put_verified(episodes[0], trace_id='TR-BAD-VECTOR')
    assert store.get(episodes[0].episode_id, trace_id='TR-VERIFY') is None


def test_reject_draft_missing_verifier_and_invalid_provenance(store, episodes):
    for mutate in [lambda p: p.update(status='draft'), lambda p: p.pop('verified_by'),
                   lambda p: p['provenance']['evidence_spans'][0].update(end_seconds=-1),
                   lambda p: p.update(verified_at='2026-11-24T00:00:00')]:
        payload = episodes[0].model_dump(mode='json')
        mutate(payload)
        with pytest.raises(ValidationError):
            store.put_verified(payload, trace_id='TR-INVALID')
    assert not store.by_machine('conveyor_line', trace_id='TR-VERIFY')


def test_conflicting_evidence_ids_are_rejected(store, episodes):
    store.put_verified(episodes[0], trace_id='TR-ONE')
    episodes[1].record.evidence_id = episodes[0].record.evidence_id
    with pytest.raises(sqlite3.IntegrityError):
        store.put_verified(episodes[1], trace_id='TR-DUPLICATE')
    changed = episodes[0].model_copy(deep=True)
    changed.record.evidence_id = 'NEW-EVIDENCE'
    with pytest.raises(ValueError):
        store.put_verified(changed, trace_id='TR-CHANGE-ID')


def test_embedding_identity_mismatch_requires_reindex(store):
    other = TinyEmbedder()
    other.model_id = 'incompatible-model'
    with pytest.raises(ValueError, match='embedding_model mismatch'):
        KnowledgeStore(store.db.path, embedder=other)


def test_parameterized_storage_and_query_copy(store, episodes):
    episodes[0].record.machine_type = "conveyor'; DROP TABLE episodes; --"
    store.put_verified(episodes[0], trace_id='TR-WRITE')
    assert store.by_machine(episodes[0].record.machine_type, trace_id='TR-READ') == [episodes[0]]
    result = store.get(episodes[0].episode_id, trace_id='TR-READ')
    result.record.parts.clear()
    assert store.get(episodes[0].episode_id, trace_id='TR-READ').record.parts
    with pytest.raises(ValueError, match='trace_id'):
        store.put_verified(episodes[1], trace_id='')
