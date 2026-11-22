"""Synthetic adapter tests. No test here claims real speech recognition accuracy."""

import copy
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from project.agents import a1_capture
from project.agents.a1_capture import CaptureAgent, normalize_transcript, sample_video_frames
from project.core.capture_schemas import CaptureRequest, CaptureResult, TranscriptSegment
from project.core.state import create_initial_state
from project.core.trace import TraceLogger, read_trace


@pytest.fixture
def response():
    fixture = Path(__file__).parents[1] / "fixtures" / "capture" / "whisper_response.json"
    return json.loads(fixture.read_text(encoding="utf-8"))


@pytest.fixture
def recording(tmp_path):
    source = tmp_path / "sample recording.wav"
    # Deliberately not real audio: tests inject a mock transcriber.
    source.write_bytes(b"synthetic capture adapter input")
    return source


def make_agent(tmp_path, response):
    model = Mock()
    model.transcribe.return_value = copy.deepcopy(response)
    return CaptureAgent(trace_logger=TraceLogger(str(tmp_path / "trace.jsonl")), model=model)


def test_timestamped_output_and_handoff_are_json_serializable(tmp_path, recording, response):
    agent = make_agent(tmp_path, response)
    state = create_initial_state("")
    state["capture_request"] = {"source_path": str(recording)}
    result = agent.run(state)
    capture = CaptureResult.model_validate(result["capture_result"])
    assert [(s.start_seconds, s.end_seconds) for s in capture.segments] == [(0, 2.5), (3, 7)]
    assert "did not help" in capture.text
    assert not capture.review_required
    assert capture.frames == []
    assert capture.segments[0].evidence_id != capture.segments[1].evidence_id
    assert result["messages"][-1]["payload"]["updates"]["capture_result"]["capture_id"] == capture.capture_id
    assert not result["errors"]
    json.dumps(result)
    trace = read_trace(str(tmp_path / "trace.jsonl"))
    assert [t["event"] for t in trace] == ["AGENT_START", "AGENT_COMPLETED"]
    assert all(t["trace_id"] == state["trace_id"] for t in trace)


def test_identical_source_has_stable_evidence_ids(tmp_path, recording, response):
    agent = make_agent(tmp_path, response)
    state = {"capture_request": {"source_path": str(recording)}}
    first = agent.process(state)["capture_result"]
    second = agent.process(state)["capture_result"]
    assert first == second
    assert "capture_result" not in state


@pytest.mark.parametrize("metric,value,flag", [
    ("avg_logprob", -1.2, "low_log_probability"),
    ("no_speech_prob", 0.9, "possible_non_speech"),
    ("compression_ratio", 3.0, "repetitive_text"),
])
def test_unclear_speech_is_retained_and_flagged(tmp_path, recording, response, metric, value, flag):
    response["segments"][0][metric] = value
    result = make_agent(tmp_path, response).process({"capture_request": {"source_path": str(recording)}})
    capture = result["capture_result"]
    assert capture["review_required"]
    assert flag in capture["segments"][0]["quality_flags"]
    assert capture["segments"][0]["text"] == "The conveyor stopped."


def test_missing_metrics_are_not_treated_as_high_confidence(response):
    del response["segments"][0]["avg_logprob"]
    _, segments, _ = normalize_transcript(response, "CAP-TEST")
    assert "missing_quality_metrics" in segments[0].quality_flags


def test_silence_and_text_without_timestamps_are_explicitly_flagged():
    text, segments, flags = normalize_transcript({"text": "", "segments": []}, "CAP-TEST")
    assert (text, segments) == ("", [])
    assert "no_speech_detected" in flags
    text, segments, flags = normalize_transcript({"text": "Unaligned speech"}, "CAP-TEST")
    assert text == "Unaligned speech"
    assert "missing_timestamps" in flags


def test_empty_segments_are_visible_as_a_quality_issue(response):
    response["segments"][0]["text"] = " "
    _, segments, flags = normalize_transcript(response, "CAP-TEST")
    assert len(segments) == 1
    assert "empty_segment" in flags


@pytest.mark.parametrize("start,end", [(2, 1), (-1, 2), (float("nan"), 2), (0, float("inf"))])
def test_invalid_segment_offsets_are_rejected(start, end):
    with pytest.raises(ValidationError):
        TranscriptSegment(evidence_id="EV-1", start_seconds=start, end_seconds=end, text="speech")


def test_nonchronological_segments_are_rejected(response):
    response["segments"].reverse()
    with pytest.raises(ValueError, match="chronological"):
        normalize_transcript(response, "CAP-TEST")


@pytest.mark.parametrize("settings", [
    {"frame_interval_seconds": 0}, {"max_frames": 0}, {"max_frames": 101},
    {"media_type": "image"}, {"frame_interval_seconds": float("nan")},
])
def test_invalid_capture_settings_are_rejected(settings):
    with pytest.raises(ValidationError):
        CaptureRequest(source_path="test.wav", **settings)


def test_missing_source_logs_error_and_clears_previous_output(tmp_path, response):
    agent = make_agent(tmp_path, response)
    state = create_initial_state("")
    state.update(capture_request={"source_path": str(tmp_path / "missing.wav")}, capture_result={"stale": True})
    result = agent.run(state)
    assert result["capture_result"] is None
    assert "FileNotFoundError" in result["errors"][-1]
    assert result["audit_trace"][-1]["event"] == "AGENT_ERROR"
    agent._model.transcribe.assert_not_called()


def test_empty_source_is_rejected(tmp_path, recording, response):
    recording.write_bytes(b"")
    with pytest.raises(ValueError, match="empty"):
        make_agent(tmp_path, response).process({"capture_request": {"source_path": str(recording)}})


def test_transcriber_failure_does_not_emit_success(tmp_path, recording, response):
    agent = make_agent(tmp_path, response)
    agent._model.transcribe.side_effect = RuntimeError("decoder failed")
    state = create_initial_state("")
    state["capture_request"] = {"source_path": str(recording)}
    result = agent.run(state)
    assert result["capture_result"] is None
    assert result["audit_trace"][-1]["event"] == "AGENT_ERROR"


def test_model_is_loaded_once_and_uses_local_transcription(monkeypatch, tmp_path, recording, response):
    model = Mock()
    model.transcribe.return_value = response
    loader = Mock(return_value=model)
    monkeypatch.setitem(sys.modules, "whisper", SimpleNamespace(load_model=loader))
    monkeypatch.setattr(a1_capture, "_binary", lambda name: name)
    monkeypatch.setenv("WHISPER_MODEL", "tiny.en")
    monkeypatch.setenv("WHISPER_DEVICE", "cpu")
    agent = CaptureAgent(trace_logger=TraceLogger(str(tmp_path / "trace.jsonl")))
    for _ in range(2):
        agent.process({"capture_request": {"source_path": str(recording)}})
    loader.assert_called_once_with("tiny.en", device="cpu", download_root="whisper_cache")
    assert model.transcribe.call_args.kwargs["task"] == "transcribe"
    assert model.transcribe.call_args.kwargs["fp16"] is False


def test_missing_ffmpeg_has_actionable_error(monkeypatch):
    monkeypatch.setattr(a1_capture.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="Install FFmpeg"):
        a1_capture._binary("ffmpeg")


@pytest.mark.parametrize("failure", [
    subprocess.TimeoutExpired("ffmpeg", 30),
    RuntimeError("Media processing failed"),
])
def test_video_failure_is_traced(tmp_path, recording, response, failure):
    agent = make_agent(tmp_path, response)
    agent._frame_sampler = Mock(side_effect=failure)
    state = create_initial_state("")
    state["capture_request"] = {"source_path": str(recording), "media_type": "video"}
    result = agent.run(state)
    assert result["capture_result"] is None
    assert result["audit_trace"][-1]["event"] == "AGENT_ERROR"


def test_frame_sampling_offsets_cap_and_fresh_directory(monkeypatch, tmp_path, recording):
    calls = []
    monkeypatch.setattr(a1_capture, "_binary", lambda name: name)

    def command(args):
        calls.append(args)
        if args[0] == "ffprobe":
            return json.dumps({"streams": [{"codec_type": "video"}], "format": {"duration": "21"}})
        Path(args[-1]).write_bytes(b"mock jpg")
        return ""

    monkeypatch.setattr(a1_capture, "_media_command", command)
    request = CaptureRequest(source_path=str(recording), media_type="video", max_frames=2,
                             frame_output_dir=str(tmp_path / "frames"))
    first = sample_video_frames(recording, request, "CAP-TEST")
    second = sample_video_frames(recording, request, "CAP-TEST")
    assert [f.timestamp_seconds for f in first] == [0, 10]
    assert first[0].path != second[0].path
    assert all(str(recording) in call for call in calls)


def test_video_without_video_stream_is_rejected(monkeypatch, tmp_path, recording):
    monkeypatch.setattr(a1_capture, "_binary", lambda name: name)
    monkeypatch.setattr(a1_capture, "_media_command", lambda args: '{"streams": []}')
    with pytest.raises(ValueError, match="no video stream"):
        sample_video_frames(recording, CaptureRequest(source_path=str(recording)), "CAP-TEST")


def test_media_command_timeout_and_nonzero_exit(monkeypatch):
    run = Mock(return_value=SimpleNamespace(returncode=1, stderr="bad media", stdout=""))
    monkeypatch.setattr(a1_capture.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="bad media"):
        a1_capture._media_command(["ffmpeg", "-version"])
    assert run.call_args.kwargs["timeout"] == 30
    assert "shell" not in run.call_args.kwargs
    run.side_effect = subprocess.TimeoutExpired("ffmpeg", 30)
    with pytest.raises(subprocess.TimeoutExpired):
        a1_capture._media_command(["ffmpeg", "-version"])


def test_video_result_includes_sampled_frame_evidence(tmp_path, recording, response):
    agent = make_agent(tmp_path, response)
    agent._frame_sampler = Mock(return_value=[{
        "evidence_id": "EV-FRAME-1", "timestamp_seconds": 0,
        "path": str(tmp_path / "frame.jpg"),
    }])
    result = agent.process({"capture_request": {"source_path": str(recording), "media_type": "video"}})
    capture = CaptureResult.model_validate(result["capture_result"])
    assert capture.frames[0].evidence_id == "EV-FRAME-1"
    assert capture.segments


def test_graph_preserves_capture_request_and_result(monkeypatch, tmp_path, recording, response):
    from project.core.registry import build_graph
    monkeypatch.setattr(CaptureAgent, "_transcribe", lambda *args: response)
    monkeypatch.setattr(TraceLogger, "_instance", TraceLogger(str(tmp_path / "graph.jsonl")))
    state = create_initial_state("")
    state["capture_request"] = {"source_path": str(recording)}
    result = build_graph("A").invoke(state)
    assert result["capture_result"]["segments"][0]["text"] == "The conveyor stopped."
    assert result["messages"][-1]["sender"] == "CaptureAgent"


def test_cli_writes_capture_json(monkeypatch, tmp_path, recording, response):
    monkeypatch.setattr(CaptureAgent, "_transcribe", lambda *args: response)
    monkeypatch.setattr(TraceLogger, "_instance", TraceLogger(str(tmp_path / "cli.jsonl")))
    output = tmp_path / "capture.json"
    assert a1_capture.main(["--source", str(recording), "--output", str(output)]) == 0
    assert CaptureResult.model_validate_json(output.read_text()).segments


def test_cli_failure_returns_nonzero(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(TraceLogger, "_instance", TraceLogger(str(tmp_path / "cli.jsonl")))
    output = tmp_path / "capture.json"
    assert a1_capture.main(["--source", str(tmp_path / "missing.wav"), "--output", str(output)]) == 1
    assert "Recording not found" in capsys.readouterr().err
    assert not output.exists()
