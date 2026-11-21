"""Unit tests for Agent 5: Diagnostic Agent."""

import json
from pathlib import Path
import pytest

from project.agents.a5_diagnostic import DiagnosticAgent, resume_with_answer
from project.core.llm import FakeLLM
from project.core.state import create_initial_state
from project.core.trace import TraceLogger, read_trace

DEV_QUERIES_PATH = Path(__file__).resolve().parent / "dev_queries.json"


def load_dev_queries():
    with open(DEV_QUERIES_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
        return data.get("dev_queries", [])


def test_dev_queries_ranking_and_confidence():
    """Verify that DiagnosticAgent produces valid, ranked hypotheses with confidences in [0, 1]."""
    queries = load_dev_queries()
    valid_queries = [q for q in queries if not q.get("expected_clarification")]
    assert len(valid_queries) >= 5

    agent = DiagnosticAgent(llm_client=FakeLLM())

    for q_data in valid_queries:
        state = create_initial_state(
            query=q_data["query"],
            machine_type=q_data["machine_type"],
        )
        final_state = agent.run(state)

        assert final_state["clarification_needed"] is False
        hypotheses = final_state["ranked_hypotheses"]
        assert len(hypotheses) >= 1

        # Check ranking order and confidence bounds
        for idx, h in enumerate(hypotheses, start=1):
            assert h["rank"] == idx, f"Hypothesis rank should be {idx}"
            assert 0.0 <= h["confidence"] <= 1.0, f"Confidence {h['confidence']} out of [0, 1]"
            assert len(h["fault"]) > 3
            assert len(h["subsystem"]) > 2


def test_clarification_trigger_missing_machine():
    """Verify clarification triggers when machine_type is missing or unspecified."""
    agent = DiagnosticAgent(llm_client=FakeLLM())

    # Case 1: machine_type = None
    state1 = create_initial_state(query="Spindle loud noise", machine_type=None)
    res1 = agent.run(state1)
    assert res1["clarification_needed"] is True
    assert res1["clarification_question"] is not None
    assert "machine type" in res1["clarification_question"].lower()
    assert len(res1["ranked_hypotheses"]) == 0

    # Case 2: machine_type = 'unspecified'
    state2 = create_initial_state(query="Error alarm ALM-01", machine_type="unspecified")
    res2 = agent.run(state2)
    assert res2["clarification_needed"] is True
    assert res2["clarification_question"] is not None


def test_clarification_trigger_ambiguous_symptoms():
    """Verify clarification triggers when symptoms are too vague."""
    agent = DiagnosticAgent(llm_client=FakeLLM())

    state = create_initial_state(
        query="Something feels strange.",
        machine_type="CNC Lathe (Turning Center)",
    )
    res = agent.run(state)
    assert res["clarification_needed"] is True
    assert res["clarification_question"] is not None
    assert len(res["ranked_hypotheses"]) == 0


def test_resume_with_answer():
    """Verify resume_with_answer updates state and resumes hypothesis ranking."""
    agent = DiagnosticAgent(llm_client=FakeLLM())

    # Start with missing machine
    state = create_initial_state(
        query="High-pitched whining noise at >2800 RPM",
        machine_type=None,
    )
    paused_state = agent.run(state)
    assert paused_state["clarification_needed"] is True

    # Operator answers with machine type
    resumed_state = resume_with_answer(
        state=paused_state,
        answer="The machine is a CNC Lathe.",
        agent=agent,
    )

    assert resumed_state["clarification_needed"] is False
    assert resumed_state["clarification_turns"] == 1
    assert "CNC Lathe" in resumed_state["machine_type"]
    assert len(resumed_state["ranked_hypotheses"]) >= 1
    assert resumed_state["ranked_hypotheses"][0]["rank"] == 1


def test_llm_failure_fallback_and_trace(tmp_path):
    """Verify that LLM failure triggers safe deterministic fallback without crashing."""
    log_file = tmp_path / "fallback_audit.jsonl"
    logger = TraceLogger(str(log_file))

    # FakeLLM configured to fail
    failing_llm = FakeLLM(should_fail=True)
    agent = DiagnosticAgent(llm_client=failing_llm, trace_logger=logger)

    state = create_initial_state(
        query="Severe vibration on cylinder platen",
        machine_type="Hydraulic Press (Stamping & Forging)",
        trace_id="TR-FALLBACK-001",
    )

    final_state = agent.run(state)

    # Must not crash; should produce fallback hypothesis
    assert final_state["clarification_needed"] is False
    assert len(final_state["ranked_hypotheses"]) == 1
    fallback = final_state["ranked_hypotheses"][0]
    assert "fallback" in fallback["fault"].lower()
    assert fallback["confidence"] == 0.30

    # Verify trace log recorded event
    records = read_trace(str(log_file))
    assert len(records) >= 2
    completed_rec = [r for r in records if r["event"] == "AGENT_COMPLETED"][0]
    assert completed_rec["decision"] == "FALLBACK_DIAGNOSIS"
    assert completed_rec["stub"] is False


def test_clarification_halts_pipeline_no_downstream_agents(tmp_path):
    """Verify that when clarification is needed, the graph halts immediately:
    no downstream agent (e.g. DummySafetyAgent) runs or appears in trace,
    and final_answer has no steps.
    """
    from project.core.registry import build_graph

    log_file = tmp_path / "clarification_halt_trace.jsonl"
    TraceLogger.get_instance(str(log_file))

    initial_state = create_initial_state(
        query="High-pitched squeal from hydraulic pump",
        machine_type=None,  # Missing machine type triggers clarification
        trace_id="TR-HALT-001",
    )

    graph = build_graph(pipeline="B")
    final_state = graph.invoke(initial_state)

    assert final_state["clarification_needed"] is True
    assert final_state["clarification_question"] is not None
    assert final_state.get("final_answer") is None

    # Verify audit trace contains ONLY DiagnosticAgent and NO DummySafetyAgent
    records = read_trace(str(log_file))
    agents_in_trace = [r["agent"] for r in records if r["trace_id"] == "TR-HALT-001"]
    assert "DiagnosticAgent" in agents_in_trace
    assert "DummySafetyAgent" not in agents_in_trace


def test_resume_with_answer_continues_pipeline():
    """Verify that after clarification is answered, pipeline resumes and reaches downstream agents."""
    from project.core.registry import build_graph

    # Ambiguous initial query halts on clarification
    initial_state = create_initial_state(
        query="High-pitched whining at 3000 RPM",
        machine_type=None,
        trace_id="TR-RESUME-001",
    )
    graph = build_graph(pipeline="B")
    paused_state = graph.invoke(initial_state)
    assert paused_state["clarification_needed"] is True

    # Operator provides answer, resuming diagnosis
    resumed_state = resume_with_answer(
        state=paused_state,
        answer="The machine is a CNC Lathe.",
    )
    assert resumed_state["clarification_needed"] is False
    assert len(resumed_state["ranked_hypotheses"]) >= 1

    # Running resumed state through graph now proceeds to downstream agents
    completed_state = graph.invoke(resumed_state)
    assert completed_state["clarification_needed"] is False
    assert completed_state["safety_approved"] is True
    assert completed_state["final_answer"] is not None
    assert len(completed_state["final_answer"]["steps"]) >= 1


def test_diagnostic_trace_includes_llm_metadata(tmp_path):
    """Verify that DiagnosticAgent trace entries include 'llm': 'fake' (or model name)."""
    log_file = tmp_path / "llm_trace.jsonl"
    logger = TraceLogger(str(log_file))

    fake_client = FakeLLM()
    agent = DiagnosticAgent(llm_client=fake_client, trace_logger=logger)

    state = create_initial_state(
        query="Turret alignment fault",
        machine_type="CNC Lathe",
        trace_id="TR-LLM-001",
    )
    agent.run(state)

    records = read_trace(str(log_file))
    diag_records = [r for r in records if r["agent"] == "DiagnosticAgent"]
    assert len(diag_records) >= 2
    for r in diag_records:
        assert r.get("llm") == "fake"


def test_get_llm_raises_without_key_or_fake_flag(monkeypatch):
    """Verify get_llm raises ValueError if chosen provider's API key is unset and no fake flag passed."""
    from project.core.llm import get_llm

    monkeypatch.delenv("FACTORY_FAKE_LLM", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    # Gemini provider missing key
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="GEMINI_API_KEY not set"):
        get_llm()

    # OpenAI provider missing key
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENAI_API_KEY not set"):
        get_llm()

    # Unsupported provider
    monkeypatch.setenv("LLM_PROVIDER", "unsupported_provider")
    with pytest.raises(ValueError, match="Unsupported LLM_PROVIDER"):
        get_llm()

    # With force_fake it succeeds
    client = get_llm(force_fake=True)
    assert client.model_name == "fake"


def test_provider_selection_gemini_and_openai(monkeypatch):
    """Verify get_llm instantiates the appropriate client based on LLM_PROVIDER."""
    from project.core.llm import GeminiLLMClient, OpenAILLMClient, get_llm

    monkeypatch.delenv("FACTORY_FAKE_LLM", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    # Gemini selection
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test_gemini_key_12345")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.5-flash")
    gemini_client = get_llm()
    assert isinstance(gemini_client, GeminiLLMClient)
    assert gemini_client.model_name == "gemini:gemini-3.5-flash"

    # OpenAI selection
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test_openai_key_12345")
    openai_client = get_llm()
    assert isinstance(openai_client, OpenAILLMClient)
    assert openai_client.model == "gpt-4o-mini"


def test_gemini_client_structured_output_mocked(monkeypatch):
    """Verify GeminiLLMClient parses structured output using mocked SDK."""
    from unittest.mock import MagicMock
    from project.agents.a5_diagnostic import DiagnosticOutput
    from project.core.llm import GeminiLLMClient

    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "needs_clarification": False,
        "clarifying_question": None,
        "hypotheses": [
            {
                "rank": 1,
                "fault": "Spindle rotary seal failure",
                "subsystem": "Spindle Unit",
                "confidence": 0.92,
                "rationale": "High-pitched whining noise indicates seal breakdown.",
            }
        ],
    })

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    client = GeminiLLMClient(api_key="mock_key", model="gemini-3.5-flash")
    monkeypatch.setattr(client, "_get_client", lambda: mock_client)

    result = client.generate_structured("Check spindle noise", DiagnosticOutput)
    assert isinstance(result, DiagnosticOutput)
    assert result.needs_clarification is False
    assert len(result.hypotheses) == 1
    assert result.hypotheses[0].fault == "Spindle rotary seal failure"
    assert result.hypotheses[0].confidence == 0.92


def test_gemini_client_retry_on_429(monkeypatch):
    """Verify GeminiLLMClient retries on rate limit (429) errors."""
    from unittest.mock import MagicMock
    from project.agents.a5_diagnostic import DiagnosticOutput
    from project.core.llm import GeminiLLMClient

    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "needs_clarification": True,
        "clarifying_question": "Which machine?",
        "hypotheses": [],
    })

    call_count = 0

    def mock_generate(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("429 ResourceExhausted: Quota exceeded")
        return mock_response

    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = mock_generate

    client = GeminiLLMClient(api_key="mock_key", model="gemini-3.5-flash", max_retries=3)
    monkeypatch.setattr(client, "_get_client", lambda: mock_client)

    result = client.generate_structured("Machine stopped", DiagnosticOutput)
    assert call_count == 2
    assert result.needs_clarification is True
    assert result.clarifying_question == "Which machine?"


def test_gemini_client_retry_on_invalid_json(monkeypatch):
    """Verify GeminiLLMClient retries when response contains non-JSON text."""
    from unittest.mock import MagicMock
    from project.agents.a5_diagnostic import DiagnosticOutput
    from project.core.llm import GeminiLLMClient

    invalid_resp = MagicMock()
    invalid_resp.text = "Error: output was truncated or non-JSON"

    valid_resp = MagicMock()
    valid_resp.text = json.dumps({
        "needs_clarification": False,
        "clarifying_question": None,
        "hypotheses": [
            {
                "rank": 1,
                "fault": "Hydraulic line blockage",
                "subsystem": "Hydraulics",
                "confidence": 0.88,
                "rationale": "Pressure drop observed.",
            }
        ],
    })

    attempts = [invalid_resp, valid_resp]

    def mock_generate(*args, **kwargs):
        return attempts.pop(0)

    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = mock_generate

    client = GeminiLLMClient(api_key="mock_key", model="gemini-3.5-flash", max_retries=3)
    monkeypatch.setattr(client, "_get_client", lambda: mock_client)

    result = client.generate_structured("Hydraulic issue", DiagnosticOutput)
    assert result.hypotheses[0].fault == "Hydraulic line blockage"
