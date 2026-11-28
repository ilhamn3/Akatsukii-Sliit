"""Offline tests for scripts/run_scalability.py using a tiny synthetic store (no HF download, no LLM)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

import numpy as np

from project.agents.a4_graph import GraphAgent
from project.store.knowledge_store import KnowledgeStore
from project.store.models import VerifiedEpisode
from scripts.run_scalability import run_scalability


class _TinyDeterministicEmbedder:
    """Offline hash embedder (no sentence-transformers download)."""

    dimension = 16
    model_id = "tiny-offline-test"
    model_name = "tiny-offline-test"

    def encode(self, texts: List[str]) -> np.ndarray:
        vecs = np.zeros((len(texts), self.dimension), dtype=np.float32)
        for i, txt in enumerate(texts):
            for tok in txt.lower().split():
                idx = sum(ord(c) for c in tok) % self.dimension
                vecs[i, idx] += 1.0
            norm = float(np.linalg.norm(vecs[i]))
            if norm > 0:
                vecs[i] /= norm
            else:
                vecs[i, 0] = 1.0
        return vecs


def _make_episode(ep_id: str, machine: str, symptom: str, root_cause: str) -> VerifiedEpisode:
    return VerifiedEpisode.model_validate({
        "episode_id": ep_id,
        "status": "verified",
        "verified_by": "test-harness",
        "verified_at": "2026-11-28T00:00:00+00:00",
        "outcome": f"Resolved {root_cause}.",
        "record": {
            "evidence_id": f"EV-{ep_id}",
            "machine_type": machine,
            "symptoms": [symptom],
            "root_cause": root_cause,
            "steps": [
                {
                    "step_number": 1,
                    "description": "Inspect and clean filter.",
                    "evidence_id": f"EV-{ep_id}-S1",
                    "source_quote": symptom,
                }
            ],
            "tools": ["wrench"],
            "parts": ["filter"],
            "safety_warnings": ["Isolate power before opening housing."],
            "failed_attempts": [],
            "confidence": 0.9,
            "source": "synthetic_test",
        },
        "provenance": {
            "capture_id": f"CAP-{ep_id}",
            "source_path": "tests/tiny.jsonl",
            "source_sha256": "0" * 64,
            "source_type": "synthetic_test",
            "evidence_spans": [
                {
                    "field_path": "symptoms.0",
                    "evidence_id": f"EV-{ep_id}-S1",
                    "quote": symptom,
                    "start_seconds": 0,
                    "end_seconds": 0,
                }
            ],
        },
    })


def test_scalability_tiny_store_offline(tmp_path: Path):
    dev_q_path = tmp_path / "dev_queries.jsonl"
    dev_g_path = tmp_path / "dev_gold.jsonl"
    dev_q_path.write_text(
        json.dumps({"query_id": "TQ-1", "tier": "easy", "query": "compressor belt squeals and guard shakes"}) + "\n"
        + json.dumps({"query_id": "TQ-2", "tier": "easy", "query": "press relief valve stuck open pressure low"}) + "\n",
        encoding="utf-8",
    )
    dev_g_path.write_text(
        json.dumps({
            "query_id": "TQ-1",
            "tier": "easy",
            "machine_type": "air_compressor",
            "fault_id": "A08",
            "root_cause": "worn and loose drive belt",
            "cause_family": "wear",
            "supporting_episode_ids": ["EP-T1"],
        }) + "\n"
        + json.dumps({
            "query_id": "TQ-2",
            "tier": "easy",
            "machine_type": "hydraulic_press",
            "fault_id": "P02",
            "root_cause": "relief valve stuck partly open from contamination",
            "cause_family": "clogging",
            "supporting_episode_ids": ["EP-T2"],
        }) + "\n",
        encoding="utf-8",
    )

    def _tiny_loader(size: str, db_path: Path, local_files_only: bool = False):
        store = KnowledgeStore(db_path, embedder=_TinyDeterministicEmbedder())
        writer = GraphAgent(store)
        eps = [
            _make_episode("EP-T1", "air_compressor", "compressor belt squeals and guard shakes", "worn and loose drive belt"),
            _make_episode("EP-T2", "hydraulic_press", "press relief valve stuck open pressure low", "relief valve stuck partly open from contamination"),
        ]
        if size == "n400":
            eps.append(_make_episode("EP-T3", "cnc_lathe", "coolant flow alarm at nozzle", "blocked coolant strainer"))
        for ep in eps:
            writer.put_verified(ep, trace_id=f"TR-TINY-{size}")
        return {"source_rows": len(eps), "processed": len(eps), "existing_before": 0, "total_in_store": len(eps)}

    out_json = tmp_path / "out" / "results.json"
    out_md = tmp_path / "out" / "SCALABILITY.md"
    report = run_scalability(
        sizes=("n100", "n400"),
        repeats=2,
        k=3,
        dev_queries_path=dev_q_path,
        dev_gold_path=dev_g_path,
        out_json=out_json,
        out_md=out_md,
        loader_fn=_tiny_loader,
        embedder_factory=_TinyDeterministicEmbedder,
    )

    assert out_json.exists() and out_md.exists()
    assert len(report["sizes"]) == 2
    s100, s400 = report["sizes"]
    assert s100["episodes"] == 2
    assert s400["episodes"] == 3
    assert s100["total_queries_timed"] == 4
    assert s100["latency_ms"]["p50"] <= s100["latency_ms"]["p95"] <= s100["latency_ms"]["max"]
    assert s100["top3_supporting_ep_hit_rate"] == 1.0
    assert s100["top3_root_cause_hit_rate"] == 1.0
    assert s100["top3_cause_family_hit_rate"] == 1.0
    assert s100["disk_bytes"] > 0
    md_text = out_md.read_text(encoding="utf-8")
    assert "## Honest Assessment" in md_text
    assert "`n100`" in md_text and "`n400`" in md_text
