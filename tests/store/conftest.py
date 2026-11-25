import pytest
from project.core.trace import TraceLogger


@pytest.fixture(autouse=True)
def isolated_trace(monkeypatch, tmp_path):
    """Approvals run Agent 4, which traces; keep test traces out of logs/."""
    logger = TraceLogger(str(tmp_path / "trace.jsonl"))
    monkeypatch.setattr(TraceLogger, "_instance", logger)
    return logger
