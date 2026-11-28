"""Technician Review Queue page (WP6, connected to K4 ReviewQueue via project.core.api)."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Dict, List

import streamlit as st

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from project.core import api as core_api  # noqa: E402

try:
    st.set_page_config(page_title="Review Queue", page_icon="📝", layout="wide")
except Exception:
    pass
st.title("📝 Technician Review Queue")
st.caption("Pending extracted repair drafts from K4 ReviewQueue (`project.core.api`).")

reviewer_name = st.text_input("Reviewer name", value="senior-tech-1")

if "review_feedback" in st.session_state and st.session_state.review_feedback:
    fb_kind, fb_msg = st.session_state.review_feedback
    if fb_kind == "success":
        st.success(fb_msg)
    else:
        st.error(fb_msg)
    st.session_state.review_feedback = None

try:
    pending_items: List[Dict[str, Any]] = core_api.list_pending_reviews()
except Exception as exc:
    st.error(f"Failed to load pending reviews: {exc}")
    pending_items = []

st.write(f"**Pending items:** {len(pending_items)}")

if not pending_items:
    st.info("No pending review items in the queue.")


def _render_fact_with_quotes(label: str, fact: Any) -> None:
    if not fact:
        st.write(f"**{label}:** *(none)*")
        return
    if isinstance(fact, dict):
        val = fact.get("value", "")
        quotes = [s.get("quote", "") for s in (fact.get("sources") or []) if isinstance(s, dict)]
        q_str = " | ".join(f'"{q}"' for q in quotes if q)
        st.write(f"**{label}:** {val}" + (f" — *Quotes:* {q_str}" if q_str else ""))
    else:
        st.write(f"**{label}:** {fact}")


for item in pending_items:
    item_id = item.get("item_id", "")
    draft = item.get("draft") or {}
    ext = draft.get("extraction") if isinstance(draft.get("extraction"), dict) else draft
    report = item.get("report") or {}
    issues = report.get("issues") or []
    blocking_issues = [i for i in issues if i.get("severity") == "blocking"]
    warning_issues = [i for i in issues if i.get("severity") == "warning"]

    mt_val = (ext.get("machine_type") or {}).get("value") or "unspecified"
    ep_id = item.get("episode_id", "")
    conf = float(item.get("confidence", 0.0))

    with st.container(border=True):
        st.subheader(f"{item_id} · Episode `{ep_id}` ({mt_val})")
        st.write(f"**Priority:** `{item.get('priority', 'standard')}` · **Confidence:** `{conf:.0%}`")

        _render_fact_with_quotes("Root Cause", ext.get("root_cause"))
        _render_fact_with_quotes("Outcome", ext.get("outcome"))

        symptoms = ext.get("symptoms") or []
        if symptoms:
            st.markdown("**Symptoms & Source Quotes:**")
            for idx, sym in enumerate(symptoms, start=1):
                _render_fact_with_quotes(f"Symptom {idx}", sym)

        steps = ext.get("steps") or []
        if steps:
            st.markdown("**Steps & Source Quotes:**")
            for idx, step in enumerate(steps, start=1):
                _render_fact_with_quotes(f"Step {idx}", step)

        safety_warnings = ext.get("safety_warnings") or []
        if safety_warnings:
            st.markdown("**Safety Warnings:**")
            for idx, sw in enumerate(safety_warnings, start=1):
                _render_fact_with_quotes(f"Warning {idx}", sw)

        if blocking_issues:
            st.error("**Blocking Validation Issues:**")
            for iss in blocking_issues:
                st.write(f"- `{iss.get('code')}` ({iss.get('field_path') or '-'}): {iss.get('message')}")
        if warning_issues:
            st.warning("**Warning Validation Issues:**")
            for iss in warning_issues:
                st.write(f"- `{iss.get('code')}` ({iss.get('field_path') or '-'}): {iss.get('message')}")

        note_val = st.text_input("Approval note / Rejection reason", key=f"note_{item_id}", placeholder="Optional note for approve; required reason for reject")
        col1, col2 = st.columns(2)
        if col1.button("✅ Approve & Publish", key=f"approve_{item_id}"):
            try:
                ep = core_api.approve_review_item(
                    item_id,
                    reviewer=reviewer_name.strip() or "senior-tech-1",
                    note=note_val.strip() or None,
                )
                new_ep_id = ep.get("episode_id", ep_id)
                st.session_state.review_feedback = ("success", f"Approved {item_id}; published episode `{new_ep_id}` to KnowledgeStore.")
                st.rerun()
            except Exception as exc:
                st.error(f"Approval failed for {item_id}: {exc}")

        if col2.button("❌ Reject", key=f"reject_{item_id}"):
            try:
                reason = note_val.strip()
                if not reason:
                    st.error("Please enter a rejection reason before rejecting.")
                else:
                    core_api.reject_review_item(
                        item_id,
                        reviewer=reviewer_name.strip() or "senior-tech-1",
                        reason=reason,
                    )
                    st.session_state.review_feedback = ("success", f"Rejected {item_id} ({reason}).")
                    st.rerun()
            except Exception as exc:
                st.error(f"Rejection failed for {item_id}: {exc}")
