"""K5 entities_v2: the data's asset convention, bare prefixes, number words, machine type
from an asset ID, and the evaluation against the generator labels."""

import json
from pathlib import Path

import pytest

from project.agents.a3_validation import draft_fields, static_issues, validate_draft
from project.agents.a4_graph import default_resolver, derived_machine_type, entity_issues
from project.core.extraction_schemas import DraftRepairRecord
from project.evaluation import entity_eval as ee
from project.evaluation import extraction_metrics as km
from tests.validation_helpers import make_draft

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("raw,machine", [("INJ-04", "injection_moulding"), ("CMP-02", "air_compressor"),
                                         ("HYD-03", "hydraulic_press"), ("CNC-04", "cnc_lathe"),
                                         ("CNV-01", "conveyor_line"), ("compressor one", "air_compressor")])
def test_asset_id_alone_names_one_machine(raw, machine):
    assert default_resolver().machine_from_asset(raw).entity_id == machine


@pytest.mark.parametrize("raw", ["line 2", "three", "machine 3", "SN-88231-X", "CNC"])
def test_loose_words_or_missing_numbers_do_not_supply_a_machine(raw):
    assert default_resolver().machine_from_asset(raw).status != "resolved"


def test_bare_prefix_and_spoken_numbers_resolve_but_k7_scoring_ignores_the_prefix_rule():
    resolver = default_resolver()
    assert resolver.resolve_machine("CNC").entity_id == "cnc_lathe"
    assert resolver.resolve_machine("CNC", allow_prefix=False).status == "unresolved"
    assert resolver.resolve_asset("three", "conveyor_line").entity_id == "CNV-03"
    assert resolver.resolve_asset("Press-03", "hydraulic_press").entity_id == "HYD-03"  # Old form still reads.
    assert resolver.version == "entities_v2"
    gold = {f: ([] if f in km.LIST_FIELDS else None) for f in km.FIELDS} | {"machine_type": "cnc_lathe"}
    scored = km.score_episode({**gold, "machine_type": "CNC"}, gold, km.DEFAULT_THRESHOLD, resolver)
    assert scored["machine_type"]["tp"] == 0  # An asset prefix is not a named category.
    assert km.score_episode({**gold, "machine_type": "lathe"}, gold, km.DEFAULT_THRESHOLD, resolver)[
        "machine_type"]["tp"] == 1


def test_a3_uses_the_asset_id_when_no_machine_type_is_stated(tmp_path):
    draft = make_draft(tmp_path, complete=True)
    sources = draft["extraction"]["machine_type"]["sources"]
    draft["extraction"]["machine_type"] = None
    draft["extraction"]["machine_id"] = {"value": "CNV-02", "sources": sources}
    for span in draft["evidence"]:
        if span["field_path"] == "machine_type":
            span["field_path"] = "machine_id"
    record = DraftRepairRecord.model_validate(draft)
    codes = {(i.code, i.severity) for i in static_issues(record)}
    assert ("machine_type_from_asset_id", "warning") in codes
    assert not any(code == "missing_required_fact" and sev == "blocking" and "machine" in code for code, sev in codes)
    assert draft_fields(record)["machine_type"] == "conveyor_line"
    assert derived_machine_type(default_resolver(), None, "CNV-02") == "conveyor_line"
    report = validate_draft(draft, trace_id="TR-T")
    assert not [i for i in report.blocking if i.field_path == "machine_type"]
    # Without an asset ID, a missing machine type still blocks.
    draft["extraction"]["machine_id"] = None
    assert any(i.code == "missing_required_fact" and i.field_path == "machine_type"
               for i in validate_draft(draft, trace_id="TR-T").blocking)
    assert entity_issues(default_resolver(), None, None) == ([], None, None)


def test_derived_machine_type_is_kept_in_provenance_after_approval(tmp_path):
    from project.store.knowledge_store import KnowledgeStore
    from project.store.review_queue import ReviewQueue
    from tests.validation_helpers import KeywordEmbedder

    store = KnowledgeStore(tmp_path / "s.sqlite3", embedder=KeywordEmbedder())
    queue = ReviewQueue(tmp_path / "q.sqlite3", store=store)
    draft = make_draft(tmp_path, complete=True)
    draft["extraction"]["machine_id"] = {"value": "CNV-02", "sources": [{"segment_id": "S0", "quote": "CNV-02"}]}
    draft["extraction"]["machine_type"] = None
    for span in draft["evidence"]:
        if span["field_path"] == "machine_type":  # The cited quote must contain the asset number.
            span.update(field_path="machine_id", quote="CNV-02")
    item = queue.enqueue(draft, validate_draft(draft, trace_id="T", queue=queue, store=store), trace_id="T")
    episode = queue.approve(item.item_id, reviewer="r1", trace_id="T")
    assert (episode.record.machine_type, episode.record.machine_id) == ("conveyor_line", "CNV-02")
    machine = next(l for l in episode.provenance.entity_links if l.kind == "machine")
    assert (machine.raw, machine.method) == ("CNV-02", "asset_prefix")


def test_committed_entity_report_matches_a_fresh_run():
    [path] = (ROOT / "project/evaluation/results").glob("*_entity_eval/entity_eval.json")
    committed = json.loads(path.read_text(encoding="utf-8"))
    gold = ee.gold_records()
    assert ee.gold_id_check(gold) == committed["gold_ids"]
    fresh = ee.draft_check(ee._last_rows(next(ee.EVAL.glob("predictions_*.jsonl"))), gold)
    assert {k: fresh[k] for k in ("v1", "v2")} == {k: committed["eval"][k] for k in ("v1", "v2")}
    assert committed["gold_ids"]["v2"]["asset_id_kept"] == 400 and committed["eval"]["v2"]["machine_wrong"] == 0
