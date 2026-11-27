"""Headless Streamlit UI tests (WP6) using clearly labelled test doubles for project.core.api."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

UI_DIR = Path(__file__).resolve().parents[2] / "project" / "ui"
APP_PATH = UI_DIR / "app.py"
REVIEW_PATH = UI_DIR / "pages" / "1_Review_Queue.py"
TRACE_PATH = UI_DIR / "pages" / "2_Trace_Viewer.py"


def test_chat_renders_grounded_answer_and_no_mock_banner():
    fake_result = {
        "final_answer": {
            "machine_type": "air_compressor",
            "symptoms": ["belt squeals"],
            "diagnosed_cause": "worn and loose drive belt",
            "steps": [
                {"step_number": 1, "instruction": "[SAFETY CONTROL H06] Bring rotation to a full stop.", "evidence_ids": []},
                {"step_number": 2, "instruction": "Inspect and replace the drive belt.", "evidence_ids": ["EV-EP-0010-S1"]},
            ],
            "safety_warnings": ["H06: Bring rotation to a full stop."],
            "failed_attempts_to_avoid": ["sprayed belt dressing on the running belt"],
            "abstained": False,
            "escalated": False,
            "refusal": None,
            "escalation_ticket_id": None,
        },
        "evidence": [
            {
                "evidence_id": "EV-EP-0010",
                "episode_id": "EP-0010",
                "snippet": "Replaced worn drive belt on compressor.",
                "similarity_score": 0.91,
                "verified_by": "senior-tech-1",
                "outcome": "worked",
            }
        ],
        "trace_id": "TR-TEST-001",
        "trace_events": [],
        "refusal": None,
        "escalation_ticket_id": None,
        "clarification_needed": False,
        "clarification_question": None,
        "flags": {"fake_llm": False, "stub": False, "llm_check": "performed"},
        "error": None,
    }

    with patch("project.core.api._get_cached_graph", return_value=object()), \
         patch("project.core.api.run_query", return_value=fake_result):
        app = AppTest.from_file(str(APP_PATH)).run()
        assert not app.error  # No MOCK banner on real mode
        app.chat_input[0].set_value("Compressor belt squeals").run()
        assert not app.exception
        md_text = "\n".join(m.value for m in app.markdown)
        assert "worn and loose drive belt" in md_text
        assert "EV-EP-0010-S1" in md_text
        warn_text = "\n".join(w.value for w in app.warning)
        assert "sprayed belt dressing" in warn_text
        assert "FAKE LLM" not in warn_text


def test_chat_renders_refusal_escalation_error_and_flag_banners():
    # 1. Refusal + Abstained/Escalated + Fake/Stub/LLM-unavailable flags
    refusal_result = {
        "final_answer": {
            "diagnosed_cause": "stored compressed air",
            "steps": [],
            "safety_warnings": ["Vent receiver to zero pressure."],
            "failed_attempts_to_avoid": [],
            "abstained": True,
            "escalated": True,
            "abstention_reason": "Unsafe shortcut requested.",
            "refusal": "Request to skip venting refused.",
            "escalation_ticket_id": "ESC-9999",
        },
        "evidence": [],
        "trace_id": "TR-REF-001",
        "trace_events": [],
        "refusal": "Request to skip venting refused.",
        "escalation_ticket_id": "ESC-9999",
        "clarification_needed": False,
        "clarification_question": None,
        "flags": {"fake_llm": True, "stub": True, "llm_check": "skipped (429)"},
        "error": None,
    }
    with patch("project.core.api._get_cached_graph", return_value=object()), \
         patch("project.core.api.run_query", return_value=refusal_result):
        app = AppTest.from_file(str(APP_PATH)).run()
        app.chat_input[0].set_value("Can I open without venting?").run()
        assert not app.exception
        err_text = "\n".join(e.value for e in app.error)
        warn_text = "\n".join(w.value for w in app.warning)
        assert "Safety Refusal" in err_text
        assert "ESC-9999" in warn_text
        assert "Abstained / Escalated" in warn_text
        assert "FAKE LLM ACTIVE" in warn_text
        assert "STUB BACKEND ACTIVE" in warn_text
        assert "LLM safety oversight unavailable" in warn_text

    # 2. API Error Panel
    err_result = {
        "final_answer": None,
        "evidence": [],
        "trace_id": "TR-ERR-001",
        "trace_events": [],
        "flags": {"fake_llm": False, "stub": False, "llm_check": "unavailable"},
        "error": {"type": "RuntimeError", "message": "KnowledgeStore offline"},
    }
    with patch("project.core.api._get_cached_graph", return_value=object()), \
         patch("project.core.api.run_query", return_value=err_result):
        app = AppTest.from_file(str(APP_PATH)).run()
        app.chat_input[0].set_value("Lathe alarm").run()
        err_text = "\n".join(e.value for e in app.error)
        assert "Pipeline Error" in err_text
        assert "KnowledgeStore offline" in err_text


def test_review_queue_renders_and_approves_via_api():
    pending = [
        {
            "item_id": "REV-001",
            "status": "pending",
            "priority": "low_confidence",
            "confidence": 0.62,
            "episode_id": "EP-0999",
            "draft": {
                "machine_type": {"value": "cnc_lathe", "sources": [{"segment_id": "S1", "quote": "lathe spindle hot"}]},
                "root_cause": {"value": "clogged chiller filter", "sources": [{"segment_id": "S2", "quote": "filter was blocked"}]},
                "outcome": {"value": "worked", "sources": [{"segment_id": "S3", "quote": "ran cool"}]},
                "symptoms": [{"value": "spindle hot", "sources": [{"segment_id": "S1", "quote": "spindle hot"}]}],
                "steps": [{"value": "Clean chiller filter", "sources": [{"segment_id": "S2", "quote": "cleaned filter"}]}],
                "safety_warnings": [],
            },
            "report": {
                "issues": [
                    {"code": "LOW_CONF", "severity": "warning", "field_path": "steps", "message": "Check step order"}
                ]
            },
        }
    ]
    with patch("project.core.api.list_pending_reviews", return_value=pending), \
         patch("project.core.api.approve_review_item", return_value={"episode_id": "EP-0999"}) as mock_app:
        app = AppTest.from_file(str(REVIEW_PATH)).run()
        assert not app.exception
        assert any("REV-001" in s.value for s in app.subheader)
        app.button[0].click().run()
        mock_app.assert_called_once()
        assert any("EP-0999" in s.value for s in app.success)


def test_trace_viewer_renders_ordered_handoffs(tmp_path):
    trace_file = tmp_path / "audit_trace.jsonl"
    events = [
        {"ts": "2026-11-27T10:00:00Z", "trace_id": "TR-UI-1", "agent": "DiagnosticAgent", "event": "AGENT_COMPLETED", "decision": "HYPOTHESES", "latency_ms": 120.5, "stub": False},
        {"ts": "2026-11-27T10:00:01Z", "trace_id": "TR-UI-1", "agent": "SafetyAgent", "event": "AGENT_COMPLETED", "decision": "SAFETY_APPROVE", "latency_ms": 85.0, "stub": False},
    ]
    trace_file.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")

    with patch("project.core.trace.DEFAULT_LOG_PATH", trace_file):
        app = AppTest.from_file(str(TRACE_PATH)).run()
        assert not app.exception
        assert len(app.dataframe) == 1
        assert len(app.expander) == 2
