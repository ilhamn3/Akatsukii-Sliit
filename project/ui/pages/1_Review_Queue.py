"""Technician Review Queue page (WP6, connected to K4 ReviewQueue via project.core.api).

Approval publishes every extracted fact to the verified store, so the page shows all
of them with their quotes, the draft's review reasons and missing fields, and the
blocking issues with the corrections that resolve them (project.store.review_view).
"""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Dict, List

import streamlit as st

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from project.core import api as core_api  # noqa: E402
from project.core.validation_schemas import ReviewBlocked  # noqa: E402
from project.store import review_view as rv  # noqa: E402

try:
    st.set_page_config(page_title="Review Queue", page_icon="📝", layout="wide")
except Exception:
    pass
st.title("📝 Technician Review Queue")
st.caption("Pending extracted repair drafts from K4 ReviewQueue (`project.core.api`). "
           "Approving publishes every fact below to the verified knowledge store.")

# No default: the name is written to the audit trail and to verified_by.
reviewer_name = st.text_input("Reviewer name (recorded in the audit trail)", key="reviewer_name",
                              placeholder="e.g. your name or staff ID").strip()
if not reviewer_name:
    st.info("Enter your reviewer name to approve or reject items.")

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


def _fact_line(label: str, view: Dict[str, Any] | None) -> str:
    if view is None:
        return f"**{label}:** *(unknown)*"
    quotes = " | ".join(f'"{q}"' for q in view["quotes"])
    return f"**{label}:** {view['value']}" + (f" — *Quotes:* {quotes}" if quotes else "")


def _render_sections(extraction: Dict[str, Any]) -> None:
    for section in rv.fact_sections(extraction):
        label, items = section["label"], section["items"]
        if not items:
            st.markdown(f"**{label}:** *(none extracted)*")
        elif section["kind"] == "scalar":
            st.markdown(_fact_line(label, items[0]))
        elif section["kind"] == "list":
            st.markdown(f"**{label}:**")
            for idx, view in enumerate(items, start=1):
                st.markdown("- " + _fact_line(f"{idx}", view))
        else:
            st.markdown(f"**{label}:**")
            for idx, attempt in enumerate(items, start=1):
                st.markdown(f"- **Attempt {idx}**  \n"
                            + "  \n".join(_fact_line(child.replace("_", " ").capitalize(), attempt[child])
                                          for child in rv.ATTEMPT_CHILDREN))


for item in pending_items:
    item_id = item.get("item_id", "")
    draft = item.get("draft") or {}
    ext = draft.get("extraction") or {}
    report = item.get("report") or {}
    issues = report.get("issues") or []
    blocking_issues = [i for i in issues if i.get("severity") == "blocking"]
    warning_issues = [i for i in issues if i.get("severity") == "warning"]
    targets = rv.correction_targets(issues, ext)
    unfixable = rv.uncorrectable(issues)

    mt_val = (ext.get("machine_type") or {}).get("value") or "unspecified"
    ep_id = item.get("episode_id", "")

    with st.container(border=True):
        st.subheader(f"{item_id} · Episode `{ep_id}` ({mt_val})")
        st.write(f"**Priority:** `{item.get('priority', 'standard')}` · **Confidence:** {rv.confidence_label(item)}")
        st.caption(f"Capture `{draft.get('capture_id', '-')}` · source `{draft.get('source_type', '-')}` · "
                   f"model `{draft.get('model', '-')}` · prompt `{draft.get('prompt_version', '-')}`")

        for level, message in rv.review_flags(draft):
            (st.error if level == "error" else st.warning)(message)
        reasons = draft.get("review_reasons") or []
        missing = draft.get("missing_fields") or []
        st.markdown(f"**Review reasons:** {', '.join(f'`{r}`' for r in reasons) or '*(none)*'}  \n"
                    f"**Missing fields:** {', '.join(f'`{m}`' for m in missing) or '*(none)*'}")

        _render_sections(ext)

        if blocking_issues:
            st.error("**Blocking Validation Issues:**")
            for iss in blocking_issues:
                st.write(f"- `{iss.get('code')}` ({iss.get('field_path') or '-'}): {iss.get('message')}")
        if warning_issues:
            st.warning("**Warning Validation Issues:**")
            for iss in warning_issues:
                st.write(f"- `{iss.get('code')}` ({iss.get('field_path') or '-'}): {iss.get('message')}")

        inputs: Dict[str, str] = {}
        if unfixable:
            st.error("This draft has blocking issues no correction can resolve "
                     f"({', '.join(i.get('code', '?') for i in unfixable)}). It can only be rejected.")
        elif targets:
            st.markdown("**Corrections required before approval.** Values you write here have no "
                        "source quote and are recorded as reviewer-supplied in the episode's provenance.")
            for target in targets:
                label = f"{target['key']} ({', '.join(i.get('code', '?') for i in target['issues'])})"
                widget_key = f"fix_{item_id}_{target['key']}"
                if target["kind"] == "lines":
                    inputs[target["key"]] = st.text_area(label + ", one per line", value=target["current"],
                                                         key=widget_key)
                else:
                    inputs[target["key"]] = st.text_input(label, value=target["current"], key=widget_key)
        corrections = rv.build_corrections(targets, inputs)
        corrections_complete = not unfixable and all(t["key"] in corrections for t in targets)

        note_val = st.text_input("Approval note / Rejection reason", key=f"note_{item_id}",
                                 placeholder="Optional note for approve; required reason for reject")
        col1, col2 = st.columns(2)
        if col1.button("✅ Approve & Publish", key=f"approve_{item_id}",
                       disabled=not reviewer_name or not corrections_complete):
            try:
                ep = core_api.approve_review_item(
                    item_id,
                    reviewer=reviewer_name,
                    corrections=corrections or None,
                    note=note_val.strip() or None,
                )
                new_ep_id = ep.get("episode_id", ep_id)
                st.session_state.review_feedback = ("success", f"Approved {item_id}; published episode `{new_ep_id}` to KnowledgeStore.")
                st.rerun()
            except ReviewBlocked as exc:
                st.error(f"Approval refused for {item_id}; still blocking: "
                         + ", ".join(f"{i.code}@{i.field_path or '-'}" for i in exc.issues))
            except Exception as exc:
                st.error(f"Approval failed for {item_id}: {exc}")

        if col2.button("❌ Reject", key=f"reject_{item_id}", disabled=not reviewer_name):
            try:
                reason = note_val.strip()
                if not reason:
                    st.error("Please enter a rejection reason before rejecting.")
                else:
                    core_api.reject_review_item(
                        item_id,
                        reviewer=reviewer_name,
                        reason=reason,
                    )
                    st.session_state.review_feedback = ("success", f"Rejected {item_id} ({reason}).")
                    st.rerun()
            except Exception as exc:
                st.error(f"Rejection failed for {item_id}: {exc}")
