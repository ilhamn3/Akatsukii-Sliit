import streamlit as st
from mock_data import mock_answer

st.set_page_config(page_title="Factory Memory", page_icon="🏭")
st.title("🏭 Factory Memory")
st.caption("Ask about a machine problem. Advisory only.")

if "messages" not in st.session_state:
    st.session_state.messages = []

# show old messages
for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        st.write(m["text"])

query = st.chat_input("Describe the problem...")
if query:
    st.session_state.messages.append({"role": "user", "text": query})
    result = mock_answer(query)

    text = result["answer"] + "\n\n**Sources:**\n"
    for c in result["citations"]:
        text += f"- `{c['id']}`: {c['summary']}\n"
    text += f"\nSafety: **{result['safety_verdict']}** · Confidence: {result['confidence']:.0%}"

    st.session_state.messages.append({"role": "assistant", "text": text})
    st.rerun()