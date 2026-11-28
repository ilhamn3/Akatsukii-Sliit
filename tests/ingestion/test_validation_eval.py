"""K4 evaluation of Agent 3 flags: injections, scoring and the committed report."""

import json
from pathlib import Path

import pytest

from project.agents.a3_validation import validate_draft
from project.evaluation import validation_eval as ve
from tests.validation_helpers import HashEmbedder, make_draft

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def inputs():
    return ve.load_inputs()


def test_confusion_counts_precision_and_recall():
    result = ve.confusion([(True, True), (True, False), (False, True), (False, False), (False, True)])
    assert result == {"tp": 1, "fp": 1, "fn": 2, "tn": 1, "precision": 0.5, "recall": 0.3333}


@pytest.mark.parametrize("name", list(ve.INJECTIONS))
def test_each_injection_changes_the_draft_and_keeps_it_schema_valid(name, tmp_path):
    draft = make_draft(tmp_path, complete=True)
    inject, code = ve.INJECTIONS[name]
    changed = inject(draft)
    if changed is None:
        pytest.skip("injection not applicable to the fixture")
    assert changed != draft and draft == make_draft(tmp_path, complete=True)  # Input not mutated.
    validate_draft(changed, trace_id="TR-T")  # Still a valid DraftRepairRecord.


def test_low_confidence_and_contradiction_results_reproduce_the_committed_report(inputs):
    """Deterministic parts (no embedder) recompute exactly from the committed v5 drafts."""
    [path] = (ROOT / "project/evaluation/results").glob("*_validation_eval/validation_eval.json")
    committed = json.loads(path.read_text(encoding="utf-8"))
    rows, scores = inputs
    low = ve.low_confidence_eval(rows, scores)
    assert {k: low[k] for k in ("drafts", "low_confidence", "vs_any_error", "vs_required_field_error")} == \
        {k: committed["low_confidence"][k] for k in ("drafts", "low_confidence", "vs_any_error",
                                                     "vs_required_field_error")}
    assert ve.contradiction_eval(rows) == committed["contradictions"]
    assert committed["duplicates"]["exact"]["flagged_on_resubmission"] == 50
    assert committed["synthetic_data_only"] is True


def test_exact_duplicates_are_flagged_only_on_resubmission(inputs, tmp_path):
    rows, _ = inputs
    few = rows[:3]
    result = ve.duplicate_eval(few, workdir=tmp_path, embedder=HashEmbedder())
    assert result["exact"] == {"drafts": 3, "flagged_on_first_submission": 0, "flagged_on_resubmission": 3}
    assert set(result["semantic"]) == {"without_own", "with_own"}


def test_step_order_uses_segment_index_when_text_captures_have_no_timing(inputs):
    from project.agents.a3_validation import _segment_index

    rows, _ = inputs
    by_id = {r["episode_id"]: r["draft"] for r in rows}
    order = lambda d: any(i.code == "steps_out_of_source_order"  # noqa: E731
                          for i in validate_draft(d, trace_id="TR-T").issues)
    assert _segment_index("CAP-abc-S0012") == 12 and _segment_index("EV-1") == -1
    assert all(s["start_seconds"] == 0 for s in by_id["EP-0014"]["evidence"])  # Text: no timing at all.
    assert not order(by_id["EP-0014"]) and order(ve.inject_reversed_steps(by_id["EP-0014"]))
    # A shift log narrates all steps in one segment: order inside it is not checkable, so no flag.
    assert not order(ve.inject_reversed_steps(by_id["EP-0016"]))
    # Real model output: "grab ... from stores" listed before the restart it followed.
    assert order(by_id["EP-0278"])
