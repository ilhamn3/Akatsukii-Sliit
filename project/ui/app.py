import streamlit as st
from mock_data import mock_answer, get_repair

st.set_page_config(page_title="Factory Memory", page_icon="🏭")
st.title("🏭 Factory Memory")
st.error("🚫 MOCK DATA - NOT A REAL DIAGNOSIS - NOT SAFETY CHECKED")
st.caption("Ask about a machine problem. Advisory only.")

if "messages" not in st.session_state:
    st.session_state.messages = []


def show_answer(result: dict):
    st.write(result["answer"])
    st.write(
        f"🚫 **MOCK safety status (not checked):** {result['safety_verdict']} · "
        f"**MOCK confidence (not validated):** {result['confidence']:.0%}"
    )

    st.markdown("**Mock citations (not real sources; click to view mock repair data):**")
    for repair_id in result["citations"]:
        repair = get_repair(repair_id)
        with st.expander(f"📄 [MOCK] {repair_id}: {repair.get('machine', 'Unknown')}"):
            st.write(f"**Symptom:** {repair.get('symptom')}")
            st.write(f"**Root cause:** {repair.get('root_cause')}")
            st.write(f"**Fix:** {repair.get('fix')}")
            st.write(f"**Failed attempts:** {repair.get('failed_attempts')}")
            st.warning(f"MOCK safety note (not safety checked): {repair.get('safety_warning')}")


for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        if m["role"] == "user":
            st.write(m["text"])
        else:
            show_answer(m["result"])

query = st.chat_input("Describe the problem...")
if query:
    st.session_state.messages.append({"role": "user", "text": query})
    st.session_state.messages.append({"role": "assistant", "result": mock_answer(query)})
    st.rerun()