"""K3 synthetic verified-record smoke demo using real local MiniLM embeddings."""

import argparse
import json
from pathlib import Path
from project.store.embeddings import MiniLMEmbedder, storage_root
from project.store.knowledge_store import KnowledgeStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, help='Dedicated demo DB; defaults to E: local-data/store-demo')
    parser.add_argument('--local-files-only', action='store_true', help='Require cached model; never download')
    args = parser.parse_args()
    store = KnowledgeStore(args.db or storage_root() / 'store-demo' / 'repairs.sqlite3',
                           embedder=MiniLMEmbedder(local_files_only=args.local_files_only))
    fixture = Path(__file__).resolve().parents[1] / 'tests/fixtures/store/verified_episodes.json'
    for payload in json.loads(fixture.read_text(encoding='utf-8')):
        store.put_verified(payload, trace_id='TR-K3-SYNTHETIC-DEMO')
    hits = store.search_symptoms('The conveyor belt slips when carrying a load',
                                 trace_id='TR-K3-SYNTHETIC-SEARCH', k=2)
    summary = {
        'synthetic_data_only': True,
        'database': str(store.db.path),
        'embedding_model': store.embedder.model_id,
        'machine_matches': len(store.by_machine('conveyor_line', trace_id='TR-K3-MACHINE')),
        'vector_matches': [{'episode_id': h.episode_id, 'cosine': h.similarity_score} for h in hits],
        'graph_matches': [h.episode_id for h in store.related('hazard', 'Isolate electrical power before repair',
                                                            trace_id='TR-K3-GRAPH')],
        'graph_nodes': store.graph_snapshot(trace_id='TR-K3-GRAPH').number_of_nodes(),
        'audit_events': len(store.audit_events()),
    }
    print(json.dumps(summary, indent=2))
    if not hits or hits[0].episode_id != 'SYN-CONVEYOR-01':
        raise RuntimeError('Synthetic symptom smoke check failed')


if __name__ == '__main__':
    main()
