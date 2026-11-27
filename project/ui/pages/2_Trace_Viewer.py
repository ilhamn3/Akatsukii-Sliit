"""Trace Viewer page (WP6, reading real audit traces via project.core.trace)."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Dict, List

import streamlit as st

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from project.core.trace import DEFAULT_LOG_PATH, read_trace  # noqa: E402

st.set_page_config(page_title="Trace Viewer", page_icon="🔍", layout="wide")
st.title("🔍 Audit Trace Viewer")
st.caption("Inspect ordered agent handoffs from `logs/audit_trace.jsonl`.")

all_events: List[Dict[str, Any]] = read_trace(str(DEFAULT_LOG_PATH))
session_events: List[Dict[str, Any]] = st.session_state.get("last_trace_events") or []
if session_events:
    seen_keys = {(e.get("trace_id"), e.get("ts"), e.get("agent"), e.get("event")) for e in all_events}
    for e in session_events:
        k = (e.get("trace_id"), e.get("ts"), e.get("agent"), e.get("event"))
        if k not in seen_keys:
            all_events.append(e)

# Recent trace IDs (most recent first)
recent_trace_ids: List[str] = []
for ev in reversed(all_events):
    tid = ev.get("trace_id")
    if tid and tid not in recent_trace_ids:
        recent_trace_ids.append(tid)

last_tid = st.session_state.get("last_trace_id")
if last_tid and last_tid not in recent_trace_ids:
    recent_trace_ids.insert(0, last_tid)

if not recent_trace_ids:
    st.info("No audit trace events found yet. Run a query on the Chat page first.")
else:
    default_idx = recent_trace_ids.index(last_tid) if last_tid in recent_trace_ids else 0
    selected_tid = st.selectbox("Select Trace ID (defaults to last query)", recent_trace_ids, index=default_idx)

    events = [e for e in all_events if e.get("trace_id") == selected_tid]
    st.write(f"**Trace `{selected_tid}` — {len(events)} handoff events**")

    if events:
        table_rows = [
            {
                "#": idx,
                "agent": e.get("agent", ""),
                "event": e.get("event", ""),
                "decision": e.get("decision") or "-",
                "latency_ms": e.get("latency_ms"),
                "stub": bool(e.get("stub", False)),
                "error": e.get("error") or ((e.get("output_data") or {}).get("error") if isinstance(e.get("output_data"), dict) else None) or "-",
                "ts": e.get("ts", ""),
            }
            for idx, e in enumerate(events, start=1)
        ]
        st.dataframe(table_rows, use_container_width=True)

        st.markdown("**Handoff Details (in order):**")
        for idx, e in enumerate(events, start=1):
            label = f"{idx}. {e.get('agent', 'Unknown')} — {e.get('event', '')} ({e.get('decision') or '-'})"
            with st.expander(label):
                st.write(f"**Timestamp:** `{e.get('ts', '')}` · **LLM:** `{e.get('llm')}` · **Tokens:** `{e.get('tokens')}` · **Stub:** `{bool(e.get('stub', False))}`")
                if e.get("error"):
                    st.error(f"Error: {e.get('error')}")
                if e.get("input_data") is not None:
                    st.markdown("**Input:**")
                    st.json(e.get("input_data"))
                if e.get("output_data") is not None:
                    st.markdown("**Output:**")
                    st.json(e.get("output_data"))
