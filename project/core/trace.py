"""Thread-safe JSONL audit trace logger for Factory Memory.

Every inter-agent handoff, decision, error, and state mutation is logged as an
immutable line in 'logs/audit_trace.jsonl'.

JSONL Log Line Schema:
---------------------
{
  "ts": "2026-11-21T16:50:00.000000+00:00",  # ISO-8601 UTC timestamp
  "trace_id": "TR-ABC12345",                  # Unique workflow execution identifier
  "session_id": "SES-XYZ98765",              # User/worker session identifier
  "agent": "DiagnosticAgent",                 # Name of the emitting agent
  "event": "HANDOFF_COMPLETED",               # Event type (e.g., STEP_START, VETO, DECISION)
  "input_digest": {...},                      # Concise digest or summary of inputs
  "output_digest": {...},                     # Concise digest or summary of outputs
  "decision": "APPROVED",                     # Optional decision tag (e.g., APPROVE, VETO, ABSTAIN)
  "latency_ms": 124.5,                        # Execution latency in milliseconds
  "error": null                               # Error string if an exception occurred, else null
}
"""

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_LOG_PATH = Path("logs/audit_trace.jsonl")


def _make_digest(data: Any, max_chars: int = 400) -> Any:
    """Create a safe, serializable summary digest of an input or output payload."""
    if data is None:
        return None
    if isinstance(data, (int, float, bool, str)):
        s = str(data)
        return s if len(s) <= max_chars else s[:max_chars] + "...[truncated]"
    if isinstance(data, list):
        return [_make_digest(item, max_chars // 2) for item in data[:5]]
    if isinstance(data, dict):
        digest = {}
        for k, v in list(data.items())[:8]:
            digest[str(k)] = _make_digest(v, max_chars // 2)
        return digest
    try:
        if hasattr(data, "model_dump"):
            return _make_digest(data.model_dump(), max_chars)
        if hasattr(data, "__dict__"):
            return _make_digest(data.__dict__, max_chars)
    except Exception:
        pass
    return str(data)[:max_chars]


class TraceLogger:
    """Thread-safe logger appending structured JSONL records to the audit trail."""

    _instance: Optional["TraceLogger"] = None
    _lock = threading.Lock()

    def __init__(self, log_path: Optional[str] = None):
        self.log_path = Path(log_path) if log_path else DEFAULT_LOG_PATH
        self._file_lock = threading.Lock()
        self._ensure_log_dir()

    @classmethod
    def get_instance(cls, log_path: Optional[str] = None) -> "TraceLogger":
        """Singleton accessor for standard trace logging."""
        with cls._lock:
            if cls._instance is None or (log_path and Path(log_path) != cls._instance.log_path):
                cls._instance = cls(log_path)
            return cls._instance

    def _ensure_log_dir(self) -> None:
        """Create parent log directory and .gitkeep if missing."""
        parent = self.log_path.parent
        if not parent.exists():
            parent.mkdir(parents=True, exist_ok=True)
        gitkeep = parent / ".gitkeep"
        if not gitkeep.exists():
            try:
                gitkeep.touch()
            except Exception:
                pass

    def log(
        self,
        agent: str,
        event: str,
        input_data: Any = None,
        output_data: Any = None,
        decision: Optional[str] = None,
        latency_ms: float = 0.0,
        error: Optional[str] = None,
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
        stub: bool = False,
        llm: Optional[str] = None,
        tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Append a single structured JSONL line to the audit trail."""
        record: Dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "trace_id": trace_id or "TR-UNKNOWN",
            "session_id": session_id or "SES-UNKNOWN",
            "agent": agent,
            "stub": bool(stub),
            "event": event,
            "input_digest": _make_digest(input_data),
            "output_digest": _make_digest(output_data),
            "decision": decision,
            "latency_ms": round(float(latency_ms), 2),
            "error": error,
        }
        if llm is not None:
            record["llm"] = llm
        if tokens is not None:
            try:
                record["tokens"] = int(tokens)
            except (TypeError, ValueError):
                pass

        line = json.dumps(record, default=str)
        with self._file_lock:
            self._ensure_log_dir()
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")

        return record


def read_trace(path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Read and parse the audit trace JSONL file for UI or benchmark inspection."""
    target = Path(path) if path else DEFAULT_LOG_PATH
    if not target.exists():
        return []
    records = []
    with open(target, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records
