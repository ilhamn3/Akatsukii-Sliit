"""Factory Memory — Streamlit App Entrypoint (WP6).

Uses `st.navigation` so the first sidebar page is labelled "Chat" instead of "app".
Run with: `streamlit run project/ui/app.py`
"""

from __future__ import annotations

from pathlib import Path
import sys

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

st.set_page_config(page_title="Factory Memory", page_icon="🏭", layout="wide")

UI_DIR = Path(__file__).resolve().parent

pg = st.navigation(
    [
        st.Page(str(UI_DIR / "chat_page.py"), title="Chat", icon="💬", default=True),
        st.Page(str(UI_DIR / "pages" / "1_Review_Queue.py"), title="Review Queue", icon="📝"),
        st.Page(str(UI_DIR / "pages" / "2_Trace_Viewer.py"), title="Trace Viewer", icon="🔍"),
    ]
)
pg.run()
