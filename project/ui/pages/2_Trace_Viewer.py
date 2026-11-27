import sys
from pathlib import Path

import streamlit as st

# Let this page find Ilham's code (project/core/trace.py)
PROJECT_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = PROJECT_DIR.parent
sys.path.insert(0, str(PROJECT_DIR))

from core.trace import read_trace  # noqa: E402

LOG_FILE = REPO_DIR / "logs" / "audit_trace.jsonl"

st.set_page_config(page_title="Trace Viewer", page_icon="🔍")
st.title("🔍 Trace Viewer")
st.caption("Every step the agents took for one question, in order.")

records = read_trace(str(LOG_FILE))

if not records:
    st.info(
        "No trace found yet. Ask a question with the real agents first, "
        f"then refresh this page. Looking for: {LOG_FILE}"
    )
    st.stop()

# Group the lines by trace_id (one trace = one question)
trace_ids = []
for r in records:
    tid = r.get("trace_id", "TR-UNKNOWN")
    if tid not in trace_ids:
        trace_ids.append(tid)

chosen = st.selectbox("Choose a trace", list(reversed(trace_ids)))
steps = [r for r in records if r.get("trace_id", "TR-UNKNOWN") == chosen]

st.write(f"**Total steps: {len(steps)}**")

if any(s.get("stub") for s in steps):
    st.error("🚫 Some steps come from STUB (fake) agents. They are not real results.")

for i, s in enumerate(steps, start=1):
    label = f"Step {i}: {s.get('agent')} · {s.get('event')}"
    if s.get("stub"):
        label += "  [STUB]"
    with st.expander(label, expanded=False):
        st.write(f"**Time:** {s.get('ts')}")
        if s.get("decision"):
            st.write(f"**Decision:** {s.get('decision')}")
        if s.get("confidence") is not None:
            st.write(f"**Confidence:** {s.get('confidence'):.0%}")
        st.write(f"**Latency:** {s.get('latency_ms')} ms")
        if s.get("llm"):
            st.write(f"**Model:** {s.get('llm')} · **Tokens:** {s.get('tokens')}")
        st.write("**Input:**")
        st.json(s.get("input_digest"))
        st.write("**Output:**")
        st.json(s.get("output_digest"))
        if s.get("error"):
            st.error(f"Error: {s.get('error')}")