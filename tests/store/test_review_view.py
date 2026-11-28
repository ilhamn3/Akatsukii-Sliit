"""Review page logic (K4): what the reviewer sees and how corrections map to A3."""

from project.agents.a3_validation import CORRECTABLE, apply_corrections, draft_fields, resolved
from project.core.extraction_schemas import DraftRepairRecord, ExtractedRepair
from project.store import review_view as rv
from tests.validation_helpers import make_draft


def test_every_extracted_field_is_shown_including_empty_ones(tmp_path):
    assert [name for name, _, _ in rv.FACT_FIELDS] == list(ExtractedRepair.model_fields)
    draft = make_draft(tmp_path)
    sections = {s["field"]: s for s in rv.fact_sections(draft["extraction"])}
    assert sections["tools"]["items"] == [] and sections["root_cause"]["items"] == []
    [attempt] = sections["failed_attempts"]["items"]
    assert attempt["action"] == {"value": "Replacing the sensor", "quotes": ["Replacing the sensor"]}
    assert attempt["why_failed"] is None  # Unknown stays visibly unknown.


def test_blocking_issues_map_to_corrections_that_a3_accepts_and_resolves(tmp_path):
    draft = make_draft(tmp_path)
    ext = draft["extraction"]
    issues = [
        {"code": "missing_required_fact", "severity": "blocking", "field_path": "root_cause", "message": "m"},
        {"code": "missing_required_fact", "severity": "blocking",
         "field_path": "failed_attempts.0.why_failed", "message": "m"},
        {"code": "ungrounded_fact", "severity": "blocking", "field_path": "steps.0", "message": "m"},
        {"code": "quantity_not_in_quote", "severity": "blocking", "field_path": "steps.0", "message": "m"},
        {"code": "possible_duplicate", "severity": "warning", "field_path": "steps", "message": "m"},
        {"code": "duplicate_source", "severity": "blocking", "field_path": None, "message": "m"},
    ]
    targets = rv.correction_targets(issues, ext)
    assert [(t["key"], t["kind"], len(t["issues"])) for t in targets] == [
        ("root_cause", "text", 1), ("failed_attempts.0.why_failed", "text", 1), ("steps", "lines", 2)]
    assert targets[2]["current"] == "secured the connector"  # Prefilled, so a list edit keeps the rest.
    assert [i["code"] for i in rv.uncorrectable(issues)] == ["duplicate_source"]

    inputs = {"root_cause": " loose connector ", "failed_attempts.0.why_failed": "",
              "steps": "secured the connector\n\n re-tested the line "}
    corrections = rv.build_corrections(targets, inputs)
    assert corrections == {"root_cause": "loose connector",
                           "steps": ["secured the connector", "re-tested the line"]}  # Blank left out.
    fields, supplied = apply_corrections(draft_fields(DraftRepairRecord.model_validate(draft)),
                                         corrections)
    assert fields["root_cause"] == "loose connector" and supplied == ["root_cause", "steps"]
    assert all(key.split(".")[0] in CORRECTABLE for key in corrections)
    blocking = [i for i in issues if i["severity"] == "blocking" and i["field_path"]]
    from project.core.validation_schemas import ValidationIssue
    still = [i["field_path"] for i in blocking if not resolved(ValidationIssue(**i), supplied)]
    assert still == ["failed_attempts.0.why_failed"]  # The blank input does not resolve its issue.


def test_review_flags_and_confidence_label_state_what_the_reviewer_must_know():
    draft = {"review_reasons": ["awaiting_validation", "offline_fixture_output",
                                "extraction_retried_after_invalid_output"]}
    assert [level for level, _ in rv.review_flags(draft)] == ["error", "warning"]
    item = {"confidence": 0.7375, "report": {"confidence": {"method": "rule_based_v1_uncalibrated"}}}
    label = rv.confidence_label(item)
    assert label.startswith("74%") and "uncalibrated" in label and "not a probability" in label
