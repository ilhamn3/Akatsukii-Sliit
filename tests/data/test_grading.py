"""Tests for the blind-grading builder (8.4), on made-up answers."""
import csv
import io
import json

import pytest

from project.human_study import build_grading as bg


def write_csv(path, columns, rows, delimiter=",", encoding="utf-8-sig"):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, delimiter=delimiter, lineterminator="\r\n", extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    path.write_bytes(buf.getvalue().encode(encoding))


def read(path):
    return bg.read_csv(path)


@pytest.fixture
def study(tmp_path):
    """Two fake participants (P01 packet A, P02 packet B), one extraction sheet, one system."""
    res = tmp_path / "results"
    res.mkdir()
    write_csv(res / "participants.csv", bg.PARTICIPANT_COLUMNS, [
        {"participant_code": "P01", "packet": "A", "study_area": "mechanical", "hands_on_experience": "yes",
         "session_date": "2026-11-27"},
        {"participant_code": "P02", "packet": "B", "study_area": "computing", "hands_on_experience": "no",
         "session_date": "2026-11-27"},
    ])
    for code, packet in (("P01", "A"), ("P02", "B")):
        rows = bg.read_csv(bg.MATERIALS / f"packet_{packet}.csv")
        for i, r in enumerate(rows):
            r.update(participant_code=code, time_seconds=str(60 + i), timed_out="no",
                     cause_1=f"{code} cause for {r['query_id']}", steps="lock out\ncheck it",
                     safety_warnings="isolate first", refused_unsafe_request="n/a",
                     episode_ids_cited="EP-0042, EV-CNV-0011-S2")
        write_csv(res / f"{code}_packet_{packet}.csv", list(rows[0].keys()), rows)
    ext = bg.read_csv(bg.MATERIALS / "extraction_sheet.csv")
    for r in ext:
        r.update(participant_code="P01", time_seconds="300", machine_type="conveyor_line", root_cause="x")
    write_csv(res / "P01_extraction.csv", list(ext[0].keys()), ext)

    gold, _ = bg.load_gold()
    sys_file = tmp_path / "ma.jsonl"
    lines = [json.dumps({"query_id": q, "run": 1, "latency_seconds": 3.5, "final_answer": {
        "machine_type": "conveyor_line", "symptoms": ["s"], "diagnosed_cause": f"system cause for {q}",
        "steps": [{"step_number": 1, "instruction": "lock out the line", "evidence_ids": ["EV-CNV-0007-S1"]}],
        "safety_warnings": ["isolate"], "abstained": False, "escalated": False, "trace_id": "TR-1"}})
        for q in sorted(gold)[:10]]
    sys_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    args = ["--results", str(res), "--grading", str(tmp_path / "grading"),
            "--key", str(tmp_path / "private" / "answer_key.csv"), "--seed", "1"]
    return {"tmp": tmp_path, "res": res, "sys": sys_file, "args": args,
            "sheet": tmp_path / "grading" / "grading_sheet.csv", "key": tmp_path / "private" / "answer_key.csv"}


def build(study, *extra):
    return bg.main(study["args"] + ["--system", f"multi_agent={study['sys']}", *extra])


def test_sheet_is_blind_and_complete(study):
    assert build(study) == 0
    sheet, key = read(study["sheet"]), read(study["key"])
    assert len(sheet) == len(key) == 24 + 10
    assert list(sheet[0].keys()) == bg.SHEET_COLUMNS
    banned = {"source", "source_type", "participant_code", "time_seconds", "latency_seconds", "notes", "tier"}
    assert not banned & set(sheet[0].keys())
    assert len({r["answer_id"] for r in sheet}) == len(sheet)
    # grouped by query so graders score one question at a time
    order = [r["query_id"] for r in sheet]
    assert sum(a != b for a, b in zip(order, order[1:])) == len(set(order)) - 1


def test_key_links_back_to_the_right_source(study):
    build(study)
    key = {k["answer_id"]: k for k in read(study["key"])}
    for r in read(study["sheet"]):
        k = key[r["answer_id"]]
        assert k["query_id"] == r["query_id"]
        if k["source_type"] == "human":
            assert r["answer_cause_1"] == f"{k['source']} cause for {r['query_id']}"
            assert k["time_seconds"]
        else:
            assert (k["source"], k["latency_seconds"]) == ("multi_agent", "3.5")
            assert r["answer_cause_1"] == f"system cause for {r['query_id']}"


def test_citations_use_one_format(study):
    build(study)
    for r in read(study["sheet"]):
        assert "EV-" not in r["answer_episode_ids_cited"]
    human = read(study["res"] / "diagnosis_results.csv")[0]
    assert human["episode_ids_cited"] == "EP-0011; EP-0042"
    assert human["tier"] in {"easy", "medium", "hard", "adversarial"}


def test_results_files(study):
    build(study)
    diag = read(study["res"] / "diagnosis_results.csv")
    assert list(diag[0].keys()) == bg.DIAGNOSIS_COLUMNS and len(diag) == 24
    ext = read(study["res"] / "extraction_results.csv")
    assert list(ext[0].keys()) == bg.EXTRACTION_COLUMNS and "narrative" not in ext[0]


def test_system_refusal_and_abstention_are_visible(study, tmp_path):
    gold, _ = bg.load_gold()
    q1, q2 = sorted(gold)[:2]
    base = {"machine_type": "conveyor_line", "symptoms": ["s"], "diagnosed_cause": "x", "steps": [],
            "safety_warnings": [], "trace_id": "TR-1"}
    path = tmp_path / "edge.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in [
        {"query_id": q1, "final_answer": {**base, "refusal": "Will not advise working on a live line.",
                                          "abstained": False, "escalated": False}},
        {"query_id": q2, "final_answer": {**base, "abstained": True,
                                          "abstention_reason": "CLARIFICATION NEEDED: which roller squeals?"}},
    ]) + "\n", encoding="utf-8")
    answers = {a["query_id"]: a for a in bg.load_system("edge", path, gold)}
    assert answers[q1]["refused_unsafe_request"] == "yes"
    assert answers[q2]["cause_1"] == "" and "which roller squeals?" in answers[q2]["steps"]


def test_refuses_to_overwrite_a_sheet(study):
    assert build(study) == 0
    assert build(study) == 1
    assert build(study, "--force") == 0


def test_rejects_identifying_column(study):
    reg = study["res"] / "participants.csv"
    rows = read(reg)
    write_csv(reg, ["participant_code", "name", "packet"], [{**r, "name": "someone"} for r in rows])
    assert build(study) == 1 and not study["sheet"].exists()


def test_rejects_edited_packet(study):
    path = study["res"] / "P02_packet_B.csv"
    rows = read(path)
    rows[0]["query_id"] = "TQ-99"
    write_csv(path, list(rows[0].keys()), rows)
    assert build(study) == 1


def test_reads_excel_semicolon_cp1252(study):
    path = study["res"] / "P01_packet_A.csv"
    rows = read(path)
    rows[0]["notes"] = "café break"
    write_csv(path, list(rows[0].keys()), rows, delimiter=";", encoding="cp1252")
    assert build(study) == 0
    assert any(d["notes"] == "café break" for d in read(study["res"] / "diagnosis_results.csv"))


def score(rows, blank=None):
    for i, r in enumerate(rows):
        r.update(cause_score=str(i % 3), steps_covered="1", safety_covered="1",
                 unsafe_advice="no", behaviour_ok="yes" if i % 2 else "no", grader="KK")
    if blank:
        rows[0][blank] = ""
    return rows


def test_unblind_roundtrip(study):
    build(study)
    rows = score(read(study["sheet"]))
    write_csv(study["sheet"], bg.SHEET_COLUMNS, rows)
    assert bg.main(study["args"] + ["--unblind", str(study["sheet"])]) == 0
    graded = read(study["res"] / "graded_answers.csv")
    assert list(graded[0].keys()) == bg.GRADED_COLUMNS and len(graded) == 34
    assert {g["source"] for g in graded} == {"P01", "P02", "multi_agent"}
    by_id = {r["answer_id"]: r for r in rows}
    assert all(g["cause_score"] == by_id[g["answer_id"]]["cause_score"] for g in graded)


@pytest.mark.parametrize("blank", ["cause_score", "unsafe_advice", "grader"])
def test_unblind_rejects_unfinished_sheet(study, blank):
    build(study)
    write_csv(study["sheet"], bg.SHEET_COLUMNS, score(read(study["sheet"]), blank=blank))
    assert bg.main(study["args"] + ["--unblind", str(study["sheet"])]) == 1


def test_unblind_rejects_steps_above_gold(study):
    build(study)
    rows = score(read(study["sheet"]))
    rows[0]["steps_covered"] = str(int(rows[0]["n_key_steps"]) + 1)
    write_csv(study["sheet"], bg.SHEET_COLUMNS, rows)
    assert bg.main(study["args"] + ["--unblind", str(study["sheet"])]) == 1


def test_unblind_rejects_mismatched_key(study):
    build(study)
    rows = score(read(study["sheet"]))[1:]
    write_csv(study["sheet"], bg.SHEET_COLUMNS, rows)
    assert bg.main(study["args"] + ["--unblind", str(study["sheet"])]) == 1
