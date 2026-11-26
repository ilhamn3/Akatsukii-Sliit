import json
from pathlib import Path

import streamlit as st

st.set_page_config(page_title="Trace Viewer", page_icon="🔍")
st.title("🔍 Trace Viewer")
st.error("🚫 MOCK DATA - NOT A REAL AGENT TRACE")
st.caption("Every step the agents took for one question, in order.")

TRACE_FILE = Path(__file__).parent.parent / "mock_trace.jsonl"

steps = []
if TRACE_FILE.exists():
    for line in TRACE_FILE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            steps.append(json.loads(line))
else:
    st.warning("Trace file not found.")

st.write(f"**Total steps: {len(steps)}**")

for s in sorted(steps, key=lambda x: x.get("step", 0)):
    with st.expander(f"Step {s.get('step')}: {s.get('agent')}", expanded=True):
        st.write(f"**Action:** {s.get('action')}")
        st.write(f"**Output:** {s.get('output')}")
        st.write(f"**[MOCK] Confidence:** {s.get('confidence', 0):.0%}")