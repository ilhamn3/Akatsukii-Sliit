"""K7 extraction metrics: real synthetic episodes and gold, real Agent 2 with a
scripted test client (no API calls). Numbers come from the scorer under test."""

import csv
import json
from unittest.mock import Mock
import pytest

from project.core.capture_schemas import CaptureResult
from project.core.extraction_schemas import ExtractedRepair
from project.core.trace import TraceLogger
from project.evaluation import extraction_metrics as km


@pytest.fixture(scope="module")
def data():
    return km.load_dataset()


@pytest.fixture(autouse=True)
def isolated_trace(monkeypatch, tmp_path):
    monkeypatch.setattr(TraceLogger, "_instance", TraceLogger(str(tmp_path / "trace.jsonl")))


def test_normalization_handles_spoken_codes_fillers_and_plurals():
    assert km.tokens("the four one oh alarm") == km.tokens("alarm 410")
    assert km.dice(km.tokens("the four one oh alarm"), km.tokens("alarm ALM-410")) >= 0.5
    assert km.tokens("Replaced the belts") == km.tokens("replace belt") == km.tokens("replacing belt")
    assert km.tokens("um, the sprocket") == {"sprocket"}


def test_matching_is_one_to_one_and_thresholded():
    counts = km.match_counts(["stretched drive chain", "stretched drive chain", "noise"],
                             ["stretched drive chain", "clicking noise at the drive"], 0.5)
    assert (counts["tp"], counts["fp"], counts["fn"]) == (2, 1, 0)  # The duplicate is a false positive.
    strict = km.match_counts(["noise at drive"], ["clicking noise at the drive"], 1.0)
    assert (strict["tp"], strict["fp"], strict["fn"]) == (0, 1, 1)
    assert km.match_counts([], [], 0.5) == {"tp": 0, "fp": 0, "fn": 0, "pairs": []}


def test_gold_reflects_what_the_narrative_says(data):
    spoken = km.gold_for("EP-0001", data)  # Audio debrief
    assert spoken["machine_type"] == "conveyor_line"
    assert spoken["symptoms"][0] == data["faults"]["C06"]["lay_description"]
    assert spoken["parts"] == ["drive chain"] and spoken["tools"] == []  # Tools never narrated.
    log = km.gold_for("EP-0002", data)  # Shift log with step 2 omitted
    assert log["symptoms"] == ["tool marks repeating at regular spacing"]
    assert len(log["steps"]) == 4 and log["parts"] == []
    assert log["tools"] == ["air gun with guard", "brush", "feeler gauge"]
    remark = km.gold_for("EP-0010", data)
    assert remark["safety_warnings"] == ["Electrical energy"]
    assert remark["failed_attempts"] == ["sprayed belt dressing on the running belt", "replaced the motor bearings"]
    assert km.gold_for("EP-0023", data)["tools"]  # Fillers inside the tools line ("infrared basically, thermometer").


def test_oracle_scores_one_and_empty_or_failed_scores_zero(data):
    ids = km.select_episodes(data, "eval")
    oracle, _ = km.score_predictions([{"episode_id": e, "prediction": km.gold_for(e, data)} for e in ids],
                                     data, system="oracle")
    assert oracle["primary"]["micro"]["f1"] == oracle["exact_match"]["micro"]["f1"] == 1.0
    empty = {"machine_type": None, "root_cause": None, **{f: [] for f in km.LIST_FIELDS}}
    blank, _ = km.score_predictions([{"episode_id": e, "prediction": empty} for e in ids], data, system="empty")
    assert blank["primary"]["micro"]["recall"] == 0.0 and blank["primary"]["micro"]["precision"] is None
    failed, rows = km.score_predictions([{"episode_id": ids[0], "prediction": None, "error": "timeout"}],
                                        data, system="failed")
    assert failed["failed_episodes"] == [ids[0]] and failed["primary"]["micro"]["tp"] == 0
    assert failed["primary"]["micro"]["fn"] == sum(r["primary"][f]["fn"] for r in rows for f in km.FIELDS) > 0


def test_machine_type_is_scored_through_the_catalogue_resolver(data):
    gold = km.gold_for("EP-0001", data)
    good = km.score_episode({**gold, "machine_type": "conveyor two"}, gold, 0.5)["machine_type"]
    bad = km.score_episode({**gold, "machine_type": "cooling pump"}, gold, 0.5)["machine_type"]
    assert (good["tp"], good["fp"], good["fn"]) == (1, 0, 0)
    assert (bad["tp"], bad["fp"], bad["fn"]) == (0, 1, 1)


def test_splits_are_deterministic_stratified_and_disjoint(data):
    eval_ids = km.select_episodes(data, "eval")
    assert eval_ids == km.select_episodes(km.load_dataset(), "eval") and len(eval_ids) == 50
    machines = [data["narratives"][e]["machine_type"] for e in eval_ids]
    assert {m: machines.count(m) for m in set(machines)} == {m: 10 for m in set(machines)}
    assert not set(eval_ids) & set(km.select_episodes(data, "dev"))
    with pytest.raises(ValueError):
        km.select_episodes(data, "test")


def test_narrative_capture_is_a_valid_text_capture(data):
    capture = CaptureResult.model_validate(km.narrative_capture(data["narratives"]["EP-0002"]))
    assert capture.media_type == "text" and capture.segments[0].text.startswith("CNC-04:")
    assert " ".join(s.text for s in capture.segments) == capture.text
    assert CaptureResult.model_validate(km.narrative_capture(data["narratives"]["EP-0001"])).media_type == "audio"


def scripted_client(data, episode_id="EP-0002"):
    """Test-only client: returns facts that quote the real EP-0002 segments."""
    segs = km.narrative_capture(data["narratives"][episode_id])["segments"]
    fact = lambda value, i, quote=None: {"value": value, "sources": [  # noqa: E731
        {"segment_id": segs[i]["evidence_id"], "quote": quote or value}]}
    output = ExtractedRepair.model_validate({
        "machine_type": None, "machine_id": fact("CNC-04", 0),
        "symptoms": [fact("tool marks repeating at regular spacing", 0)],
        "steps": [fact("stop the spindle and LOTO", 2), fact("re-run a test cut", 2)],
        "tools": [fact("brush", 3), fact("feeler gauge", 3)], "parts": [],
        "root_cause": fact("turret not fully clamped because chips are packed under the clamp face", 1),
        "failed_attempts": [], "safety_warnings": [], "outcome": fact("returned to production", 4)})
    client = Mock()
    client.model_name = "test-scripted-client"
    client.generate_structured.return_value = output
    return client


def test_real_agent2_run_resumes_and_scores(tmp_path, data):
    client = scripted_client(data)
    path = tmp_path / "predictions.jsonl"
    rows = km.run_extraction(["EP-0002"], data, path, client=client)
    assert rows[0]["error"] is None and rows[0]["draft"]["status"] == "draft"
    assert rows[0]["prediction"]["root_cause"].startswith("turret not fully clamped")
    km.run_extraction(["EP-0002"], data, path, client=client)  # Resume: no second model call.
    assert client.generate_structured.call_count == 1
    report, per_episode = km.score_predictions(rows, data, system="multi_agent_a2")
    fields = report["primary"]["fields"]
    assert fields["root_cause"]["tp"] == 1 and fields["symptoms"]["f1"] == 1.0
    assert (fields["steps"]["tp"], fields["steps"]["fn"]) == (2, 2)  # 2 of the 4 narrated steps.
    assert (fields["tools"]["tp"], fields["tools"]["fn"]) == (2, 1)
    assert fields["machine_type"]["fn"] == 1  # Draft left machine_type null.
    assert report["schema_version"] == "extraction_metrics/v1" and per_episode[0]["episode_id"] == "EP-0002"


def test_provider_failure_is_recorded_as_a_scored_miss(tmp_path, data):
    client = Mock()
    client.model_name = "test-failing-client"
    client.generate_structured.side_effect = TimeoutError("simulated")
    [row] = km.run_extraction(["EP-0002"], data, tmp_path / "p.jsonl", client=client)
    assert row["prediction"] is None and "TimeoutError" in row["error"]
    report, _ = km.score_predictions([row], data, system="multi_agent_a2")
    assert report["failed_episodes"] == ["EP-0002"] and report["primary"]["micro"]["f1"] == 0.0
    retried = km.run_extraction(["EP-0002"], data, tmp_path / "p.jsonl", client=scripted_client(data),
                                retry_errors=True)
    assert retried[0]["error"] is None  # Last row per episode wins after a retry.


def test_rejected_key_stops_the_run_without_metrics(monkeypatch, tmp_path, data):
    client = Mock()
    client.model_name = "test-rejected-key"
    client.generate_structured.side_effect = RuntimeError("Error code: 401 - Invalid API Key")
    monkeypatch.setattr("project.core.llm.get_llm_client", lambda: client)
    ids = km.select_episodes(data, "dev")[:3]
    assert km.main(["run", "--episodes", *ids, "--output-dir", str(tmp_path)]) == 2
    assert client.generate_structured.call_count == 1  # Stopped after the first rejection.
    assert not list(tmp_path.glob("extraction_metrics_*.json"))
    rows = [json.loads(l) for l in (tmp_path / "predictions_custom_extraction_v3.jsonl").read_text().splitlines()]
    assert len(rows) == 1 and "401" in rows[0]["error"]


def test_run_with_every_episode_failing_writes_no_metrics(monkeypatch, tmp_path, data):
    client = Mock()
    client.model_name = "test-down"
    client.generate_structured.side_effect = TimeoutError("simulated outage")
    monkeypatch.setattr("project.core.llm.get_llm_client", lambda: client)
    assert km.main(["run", "--episodes", "EP-0002", "--output-dir", str(tmp_path)]) == 1
    assert not list(tmp_path.glob("extraction_metrics_*.json"))


def test_prompt_version_is_selectable_recorded_and_kept_apart(monkeypatch, tmp_path, data):
    from project.agents import a2_extraction as a2
    assert a2.prompt_path().stem == "extraction_v3"  # Default unchanged until dev results justify v4.
    monkeypatch.setenv("EXTRACTION_PROMPT", "extraction_v4")
    client = scripted_client(data)
    [row] = km.run_extraction(["EP-0002"], data, tmp_path / "p.jsonl", client=client)
    assert row["draft"]["prompt_version"] == "extraction_v4"
    assert "stated diagnosis" in client.generate_structured.call_args.kwargs["system_prompt"]
    for bad in ("extraction_v99", "../secrets", "diagnostic_v1"):
        monkeypatch.setenv("EXTRACTION_PROMPT", bad)
        with pytest.raises(ValueError):
            a2.prompt_path()


def test_v4_keeps_the_unstated_cause_safeguard_and_leaks_no_catalogue_causes(data):
    import re
    causes = [f["root_cause"] for f in data["faults"].values()]
    for version in ("extraction_v4", "extraction_v5"):
        text = (km.ROOT / f"project/core/prompts/{version}.md").read_text(encoding="utf-8")
        assert "requires root_cause=null" in text and "The connector was loose" in text
        assert "suspected, hedged" in text
        section = text.split("root_cause is the fault")[1].split("parts means")[0]
        for example in re.findall(r'"([^"]{6,80})"', section):  # Must not hint at real catalogue answers.
            assert max(km.dice(km.tokens(example), km.tokens(c)) for c in causes) < 0.5, (version, example)


def test_compare_reports_field_deltas_on_the_same_split_only(data):
    ids = km.select_episodes(data, "dev")
    gold = [{"episode_id": e, "prediction": km.gold_for(e, data)} for e in ids]
    worse = [{"episode_id": e, "prediction": {**km.gold_for(e, data), "root_cause": None}} for e in ids]
    a, _ = km.score_predictions(worse, data, system="v3", meta={"prompt_versions": ["extraction_v3"]})
    b, _ = km.score_predictions(gold, data, system="v4", meta={"prompt_versions": ["extraction_v4"]})
    diff = km.compare_reports(a, b)
    assert diff["f1"]["root_cause"]["baseline"] is not None and diff["f1"]["root_cause"]["delta"] > 0
    assert diff["f1"]["steps"]["delta"] == 0
    other, _ = km.score_predictions(gold[:3], data, system="v4")
    with pytest.raises(ValueError, match="different episodes"):
        km.compare_reports(a, other)


@pytest.mark.parametrize("error,kind", [
    ("[ExtractionAgent] RuntimeError: OpenAI API call failed after 3 retries: Error code: 429 - Rate limit "
     "reached for model on tokens per day (TPD)", "provider"),
    ("[ExtractionAgent] TimeoutError: simulated", "provider"),
    ("[ExtractionAgent] RuntimeError: Error code: 401 - Invalid API Key", "provider"),
    ("[ExtractionAgent] ValueError: Extraction output invalid after 2 attempt(s): Raw: loose connection timeout",
     "output"),
    ("[ExtractionAgent] ValueError: Source quote does not match segment for tools.1", "output"),
    (None, None),
])
def test_failures_are_classified_as_provider_or_output(error, kind):
    assert km.failure_kind(error) == kind


def test_rate_limit_stops_the_run_and_blocks_comparison(monkeypatch, tmp_path, data):
    client = Mock()
    client.model_name = "test-quota"
    client.last_token_count = None
    client.generate_structured.side_effect = RuntimeError(
        "OpenAI API call failed after 3 retries: Error code: 429 - Rate limit reached ... tokens per day (TPD)")
    monkeypatch.setattr("project.core.llm.get_llm_client", lambda: client)
    ids = km.select_episodes(data, "dev")[:3]
    assert km.main(["run", "--episodes", *ids, "--output-dir", str(tmp_path)]) == 3
    assert client.generate_structured.call_count == 1 and not list(tmp_path.glob("extraction_metrics_*.json"))
    [row] = [json.loads(l) for l in (tmp_path / "predictions_custom_extraction_v3.jsonl").read_text().splitlines()]
    assert row["failure_kind"] == "provider" and "tokens" in row
    gold = [{"episode_id": e, "prediction": km.gold_for(e, data)} for e in ids]
    partial = [{**g, "prediction": None, "error": row["error"]} if g["episode_id"] == ids[0] else g for g in gold]
    good, _ = km.score_predictions(gold, data, system="a")
    hit, _ = km.score_predictions(partial, data, system="b")
    assert hit["provider_failed_episodes"] == [ids[0]]
    with pytest.raises(ValueError, match="Provider failures"):
        km.compare_reports(good, hit)


def test_v5_separates_clean_values_from_exact_quotes():
    text = (km.ROOT / "project/core/prompts/extraction_v5.md").read_text(encoding="utf-8")
    assert "A value is clean" in text and "A quote is exact" in text
    assert "belongs only in failed_attempts, never also in steps" in text
    assert "requires root_cause=null" in text  # v3/v4 safeguard kept.


def test_live_run_refuses_the_offline_fake_llm(monkeypatch, tmp_path):
    monkeypatch.setenv("FACTORY_FAKE_LLM", "1")
    assert km.main(["run", "--episodes", "EP-0002", "--output-dir", str(tmp_path)]) == 1
    assert not list(tmp_path.glob("extraction_metrics_*.json"))


def test_cli_score_and_human_forms(tmp_path, data):
    ids = km.select_episodes(data, "dev")[:3]
    preds = tmp_path / "preds.jsonl"
    preds.write_text("".join(json.dumps({"episode_id": e, "model": "oracle",
                                         "prediction": km.gold_for(e, data)}) + "\n" for e in ids))
    out = tmp_path / "metrics.json"
    assert km.main(["score", "--predictions", str(preds), "--output", str(out)]) == 0
    report = json.loads(out.read_text())
    assert report["primary"]["micro"]["f1"] == 1.0 and report["episodes"] == ids
    assert len(out.with_name("metrics_per_episode.jsonl").read_text().splitlines()) == 3
    gold = km.gold_for(ids[0], data)
    form = tmp_path / "extraction_results.csv"
    with form.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["participant_code", "episode_id", "time_seconds",
                                                    *km.FIELDS])
        writer.writeheader()
        writer.writerow({"participant_code": "P01", "episode_id": ids[0], "time_seconds": "300",
                         "machine_type": gold["machine_type"].replace("_", " "),
                         "root_cause": gold["root_cause"], "symptoms": "\n".join(gold["symptoms"]),
                         "steps": "; ".join(gold["steps"]), "tools": "\n".join(gold["tools"]),
                         "parts": "\n".join(gold["parts"]), "failed_attempts": "\n".join(gold["failed_attempts"]),
                         "safety_warnings": "\n".join(gold["safety_warnings"])})
    human_out = tmp_path / "human.json"
    assert km.main(["score-human", "--csv", str(form), "--output", str(human_out)]) == 0
    human = json.loads(human_out.read_text())
    assert human["system"] == "human" and human["participants"] == ["P01"]
    assert human["primary"]["micro"]["f1"] == 1.0
    with pytest.raises(SystemExit):
        km.main(["--threshold", "0", "score", "--predictions", str(preds)])
