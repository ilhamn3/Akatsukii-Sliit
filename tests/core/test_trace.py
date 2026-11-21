"""Unit tests for Factory Memory TraceLogger and audit trail."""

import concurrent.futures
import json
import tempfile
from pathlib import Path
import pytest
from project.core.trace import TraceLogger, read_trace


def test_trace_logger_creates_file_and_logs(tmp_path):
    """Verify TraceLogger writes valid JSONL lines with expected schema."""
    log_file = tmp_path / "test_audit.jsonl"
    logger = TraceLogger(str(log_file))

    record = logger.log(
        agent="DiagnosticAgent",
        event="HYPOTHESIS_FORMULATED",
        input_data={"query": "Whining noise"},
        output_data={"confidence": 0.89},
        decision="RANKED",
        latency_ms=45.2,
        trace_id="TR-TEST-001",
        session_id="SES-TEST-001",
    )

    assert log_file.exists()
    assert record["agent"] == "DiagnosticAgent"
    assert record["event"] == "HYPOTHESIS_FORMULATED"
    assert record["latency_ms"] == 45.2

    # Check read_trace helper
    lines = read_trace(str(log_file))
    assert len(lines) == 1
    assert lines[0]["trace_id"] == "TR-TEST-001"
    assert lines[0]["decision"] == "RANKED"


def test_trace_logger_thread_safety(tmp_path):
    """Verify TraceLogger handles concurrent log calls without corruption."""
    log_file = tmp_path / "concurrent_audit.jsonl"
    logger = TraceLogger(str(log_file))

    def write_log(idx: int):
        logger.log(
            agent=f"Agent-{idx}",
            event="HANDOFF",
            input_data={"idx": idx},
            latency_ms=10.0 + idx,
            trace_id=f"TR-CONCURRENT-{idx}",
        )

    # 20 concurrent threads writing to the same log
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(write_log, i) for i in range(20)]
        concurrent.futures.wait(futures)

    records = read_trace(str(log_file))
    assert len(records) == 20
    agent_names = {r["agent"] for r in records}
    assert len(agent_names) == 20
