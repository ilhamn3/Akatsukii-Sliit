from pathlib import Path

from streamlit.testing.v1 import AppTest


APP_PATH = Path(__file__).resolve().parents[2] / "project" / "ui" / "app.py"


def test_mock_chat_labels_are_explicit():
    app = AppTest.from_file(str(APP_PATH)).run()

    assert "MOCK DATA - NOT A REAL DIAGNOSIS - NOT SAFETY CHECKED" in app.error[0].value

    app.chat_input[0].set_value("hydraulic pressure drop").run()

    assert not app.exception
    rendered = "\n".join(element.value for element in app.markdown)
    assert "MOCK safety: NOT CHECKED" in rendered
    assert "MOCK confidence" in rendered
    assert "Mock citations" in rendered
    assert "approved" not in rendered.lower()
