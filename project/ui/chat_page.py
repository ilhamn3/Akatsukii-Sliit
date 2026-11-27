"""Factory Memory — Chat Troubleshooting Page (WP6, connected to Pipeline B via project.core.api)."""

from __future__ import annotations

from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    _env_file = ROOT / ".env"
    if _env_file.exists():
        load_dotenv(dotenv_path=_env_file)
except ImportError:
    pass

from project.core import api as core_api  # noqa: E402

try:
    st.set_page_config(page_title="Factory Memory — Chat", page_icon="🏭", layout="wide")
except Exception:
    pass


@st.cache_resource(show_spinner=False)
def _warm_store_and_graph() -> bool:
    """Ensure Pipeline B graph/store/embedder are cached once per Streamlit process."""
    try:
        core_api._get_cached_graph("B")
        return True
    except Exception:
        return False


_warm_store_and_graph()

st.title("🏭 Factory Memory — Shop-Floor Assistant")
st.caption("Grounded troubleshooting backed by Pipeline B (`project.core.api.run_query`).")

MACHINE_OPTIONS = [
    "Auto-detect",
    "cnc_lathe",
    "hydraulic_press",
    "injection_moulding",
    "conveyor_line",
    "air_compressor",
]

with st.sidebar:
    selected_machine = st.selectbox("Machine Type (optional)", MACHINE_OPTIONS, index=0)
    st.caption("Storage defaults to repo-local `.factory_memory` when `FACTORY_MEMORY_STORAGE` is unset.")

if "messages" not in st.session_state:
    st.session_state.messages = []
if "last_trace_id" not in st.session_state:
    st.session_state.last_trace_id = None
if "last_trace_events" not in st.session_state:
    st.session_state.last_trace_events = []


def _render_flag_banners(flags: Dict[str, Any]) -> None:
    if flags.get("fake_llm"):
        st.warning("FAKE LLM ACTIVE: Simulated output only — not a real model response.")
    if flags.get("stub"):
        st.warning("STUB BACKEND ACTIVE: One or more pipeline nodes are running in stub mode.")
    llm_check = str(flags.get("llm_check") or "")
    if llm_check and (llm_check.startswith("skipped") or llm_check == "unavailable"):
        st.warning(f"LLM safety oversight unavailable (`{llm_check}`): deterministic rules only.")


def show_answer(result: Dict[str, Any], elapsed_s: Optional[float] = None) -> None:
    """Render a `project.core.api.run_query` result dict."""
    flags = result.get("flags") or {}
    _render_flag_banners(flags)

    if elapsed_s is not None:
        st.caption(f"Trace ID: `{result.get('trace_id')}` · Elapsed: `{elapsed_s:.1f} s`")
    elif result.get("trace_id"):
        st.caption(f"Trace ID: `{result.get('trace_id')}`")

    # 1. API / Pipeline Error Panel (never show a fake answer)
    err = result.get("error")
    if err:
        st.error(f"Pipeline Error ({err.get('type', 'Error')}): {err.get('message', 'Unknown error')}")
        return

    # 2. Clarification Request
    if result.get("clarification_needed"):
        st.info(f"**Clarification needed:** {result.get('clarification_question')}")
        return

    final_ans = result.get("final_answer") or {}
    refusal = result.get("refusal") or final_ans.get("refusal")
    abstained = bool(final_ans.get("abstained"))
    escalated = bool(final_ans.get("escalated"))
    ticket_id = result.get("escalation_ticket_id") or final_ans.get("escalation_ticket_id")

    # 3. Safety Refusal
    if refusal:
        st.error(f"**Safety Refusal:** {refusal}")

    # 4. Abstained / Escalated status
    if abstained or escalated:
        status_label = "Abstained / Escalated" if (abstained and escalated) else ("Abstained" if abstained else "Escalated")
        reason = final_ans.get("abstention_reason") or "Safety or evidence verification requires supervisor review."
        ticket_part = f" · Ticket ID: `{ticket_id}`" if ticket_id else ""
        st.warning(f"**{status_label}:** {reason}{ticket_part}")

    # 5. Diagnosed Cause & Repair Steps with Citations
    if final_ans.get("diagnosed_cause"):
        st.markdown(f"**Diagnosed Cause:** {final_ans['diagnosed_cause']}")

    steps: List[Dict[str, Any]] = final_ans.get("steps") or []
    if steps:
        st.markdown("**Recommended Steps:**")
        for s in steps:
            num = s.get("step_number", "")
            inst = s.get("instruction", "")
            cits = s.get("evidence_ids") or []
            cit_str = " " + " ".join(f"`{c}`" for c in cits) if cits else ""
            st.markdown(f"{num}. {inst}{cit_str}")

    # 6. Evidence Panel (source episode details)
    evidence: List[Dict[str, Any]] = result.get("evidence") or []
    if evidence:
        st.markdown("**Cited Source Episodes (Evidence Panel):**")
        for ev in evidence:
            eid = ev.get("evidence_id") or "EV"
            ep_id = ev.get("episode_id") or "unknown"
            sim = ev.get("similarity_score", 0.0)
            with st.expander(f"📄 {eid} · Episode `{ep_id}` (similarity: {sim:.3f})"):
                st.write(f"**Provenance (`verified_by`):** {ev.get('verified_by', 'unknown')}")
                st.write(f"**Outcome:** {ev.get('outcome', 'unknown')}")
                st.write(f"**Snippet:** {ev.get('snippet', '')}")

    # 7. Failed Attempts to Avoid
    failed = final_ans.get("failed_attempts_to_avoid") or []
    if failed:
        st.markdown("**Failed Attempts to Avoid:**")
        for item in failed:
            st.warning(f"Avoid: {item}")

    # 8. Safety Notes / Warnings
    warnings = final_ans.get("safety_warnings") or []
    if warnings:
        st.markdown("**Safety Notes:**")
        for w in warnings:
            st.info(str(w))


for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        if m["role"] == "user":
            st.write(m["text"])
        else:
            show_answer(m["result"], elapsed_s=m.get("elapsed_s"))

query = st.chat_input("Describe the machine problem...")
if query:
    st.session_state.messages.append({"role": "user", "text": query})
    machine_arg = None if selected_machine == "Auto-detect" else selected_machine
    with st.spinner("Running Pipeline B (Diagnostic -> Retrieval -> Orchestrator -> Safety; ~15–30 s)..."):
        t0 = time.perf_counter()
        result = core_api.run_query(query=query, machine_type=machine_arg)
        elapsed_s = time.perf_counter() - t0
    st.session_state.last_trace_id = result.get("trace_id")
    st.session_state.last_trace_events = result.get("trace_events") or []
    st.session_state.messages.append({"role": "assistant", "result": result, "elapsed_s": elapsed_s})
    st.rerun()
