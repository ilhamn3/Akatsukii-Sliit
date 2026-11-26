import streamlit as st
from mock_data import get_review_queue

st.set_page_config(page_title="Review Queue", page_icon="📝")
st.title("📝 Technician Review Queue")
st.error("🚫 MOCK DATA - NOT REAL RECORDS - DECISIONS ARE NOT SAVED")
st.caption("Records the AI was not sure about. Approve or reject each one.")

if "queue" not in st.session_state:
    st.session_state.queue = get_review_queue()
if "decisions" not in st.session_state:
    st.session_state.decisions = {}

waiting = [r for r in st.session_state.queue if r["id"] not in st.session_state.decisions]
st.write(f"**Waiting for review: {len(waiting)}**")

if not waiting:
    st.success("All records reviewed.")

for r in waiting:
    with st.container(border=True):
        st.subheader(f"{r['id']}: {r['machine']}")
        st.write(f"**Root cause:** {r['root_cause']}")
        st.write(f"**Fix:** {r['fix']}")
        st.warning(f"[MOCK] Safety: {r['safety_warning']}")
        st.write(f"[MOCK] AI confidence: **{r['confidence']:.0%}** (low)")

        col1, col2 = st.columns(2)
        if col1.button("✅ Approve", key=f"approve_{r['id']}"):
            st.session_state.decisions[r["id"]] = "approved"
            st.rerun()
        if col2.button("❌ Reject", key=f"reject_{r['id']}"):
            st.session_state.decisions[r["id"]] = "rejected"
            st.rerun()

if st.session_state.decisions:
    st.divider()
    st.markdown("**Decisions so far:**")
    for rid, d in st.session_state.decisions.items():
        st.write(f"- {rid}: {d}")