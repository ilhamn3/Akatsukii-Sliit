"""Shared K4 test helpers: real A2 drafts from the synthetic fixture, tiny embedder."""

import json
from pathlib import Path
import numpy as np

from project.agents.a2_extraction import ExtractionAgent
from project.core.extraction_schemas import ExtractedRepair
from project.core.llm import FakeLLM
from project.core.state import create_initial_state
from project.core.trace import TraceLogger

FIXTURES = Path(__file__).parent / "fixtures" / "extraction"
SHA_A, SHA_B = "a" * 64, "b" * 64


class KeywordEmbedder:
    """Test-only deterministic embedder; never used outside tests."""

    model_id = "test-only-keyword-v1"
    dimension = 3

    def encode(self, texts):
        return np.array([[float("stopped" in t.lower()), float("heat" in t.lower()), 0.1]
                         for t in texts], dtype=np.float32)


class HashEmbedder:
    """Test-only bag-of-words embedder (crc32 buckets): cosine ~ word overlap."""

    model_id = "test-only-hash-bow-v1"
    dimension = 256

    def encode(self, texts):
        import re
        import zlib
        rows = []
        for text in texts:
            row = np.zeros(self.dimension, dtype=np.float32)
            row[0] = 0.05  # Never all-zero.
            for word in re.findall(r"[a-z0-9]+", text.lower()):
                row[1 + zlib.crc32(word.encode()) % (self.dimension - 1)] += 1.0
            rows.append(row)
        return np.array(rows)


def model_output(complete=False):
    data = json.loads((FIXTURES / "model_output.json").read_text())
    if complete:
        cause = [{"segment_id": "S1", "quote": "The connector was loose."}]
        data["root_cause"] = {"value": "loose connector", "sources": cause}
        data["failed_attempts"][0]["why_failed"] = {
            "value": "the connector, not the sensor, was at fault", "sources": cause}
    return data


def make_draft(tmp_path, *, complete=False, sha=None, quality_flags=None) -> dict:
    """Run the real Agent 2 over the synthetic capture with a canned model output."""
    state = create_initial_state("")
    state["capture_result"] = json.loads((FIXTURES / "capture.json").read_text())
    if sha:
        state["capture_result"]["source_sha256"] = sha
    if quality_flags:
        state["capture_result"]["segments"][0]["quality_flags"] = quality_flags
    client = FakeLLM(canned_response=ExtractedRepair.model_validate(model_output(complete)))
    result = ExtractionAgent(client, TraceLogger(str(tmp_path / "a2.jsonl"))).run(state)
    assert result["draft_record"] is not None, result.get("errors")
    return result["draft_record"]
