"""Build anonymised results and the blind-grading sheet (WP8, subtasks 8.3 and 8.4).

Step 1, after the sessions (and once system answers exist):

  python project/human_study/build_grading.py \
      --system multi_agent=runs/multi_agent.jsonl --system baseline=runs/baseline.jsonl

  reads   results/participants.csv          filled register (codes only, no names)
          results/P01_packet_A.csv ...      each participant's filled packet
          results/P01_extraction.csv ...    each participant's filled extraction sheet
          --system NAME=FILE.jsonl          optional system answers, see SYSTEM ANSWERS below
  writes  results/diagnosis_results.csv     all human answers with tier (protocol section 7)
          results/extraction_results.csv    all human extraction sheets
          grading/grading_sheet.csv         every answer, shuffled, random answer IDs, no source,
                                            gold answer alongside, blank score columns
          private/answer_key.csv            answer ID -> source. Git-ignored. Give it only to
                                            someone who does not grade.

Between sessions, `--check` validates the sheets saved so far and writes nothing.

Step 2, after grading (scores typed into the sheet; see grading/README.md for the rubric):

  python project/human_study/build_grading.py --unblind grading/grading_sheet.csv

  writes  results/graded_answers.csv        scores joined to source, for the benchmark harness

SYSTEM ANSWERS: one JSON object per line,
  {"query_id": "TQ-01", "run": 1, "latency_seconds": 4.2, "final_answer": {...FinalAnswer...}}
"run" and "latency_seconds" are optional. "final_answer" follows project/core/schemas.py
FinalAnswer (diagnosed_cause, steps[].instruction, steps[].evidence_ids, safety_warnings,
refusal, abstained, abstention_reason, escalated). When there are no steps, the abstention
reason (for example a clarifying question) is shown to graders in the steps column.

Answer IDs are random (not seeded) unless --seed is given, so graders can't rebuild the key
from the code. The sheet is not overwritten without --force, because new IDs would break a
sheet that is already being graded.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import pathlib
import random
import re
import secrets
import sys
from collections import Counter

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE.parent / "data"
MATERIALS = HERE / "materials"
RESULTS = HERE / "results"
GRADING = HERE / "grading"
PRIVATE = HERE / "private"
GOLD = DATA / "test_set" / "test_gold.jsonl"
QUERIES = DATA / "test_set" / "test_queries.jsonl"

PARTICIPANT_COLUMNS = ["participant_code", "packet", "study_area", "hands_on_experience", "session_date"]
ANSWER_FIELDS = ["cause_1", "cause_2", "cause_3", "steps", "safety_warnings", "refused_unsafe_request",
                 "episode_ids_cited"]
DIAGNOSIS_COLUMNS = ["participant_code", "packet", "query_id", "tier", "time_seconds", "timed_out",
                     *ANSWER_FIELDS, "notes"]
EXTRACTION_COLUMNS = ["participant_code", "episode_id", "time_seconds", "machine_type", "symptoms", "root_cause",
                      "steps", "tools", "parts", "failed_attempts", "safety_warnings"]
SCORE_COLUMNS = ["cause_score", "steps_covered", "safety_covered", "unsafe_advice", "behaviour_ok", "grader",
                 "grader_notes"]
SHEET_COLUMNS = ["answer_id", "query_id", "query", "expected_behaviour", "already_tried", "gold_root_cause",
                 "gold_key_steps", "n_key_steps", "gold_required_safety", "n_required_safety",
                 "gold_must_not_recommend", *[f"answer_{f}" for f in ANSWER_FIELDS], *SCORE_COLUMNS]
KEY_COLUMNS = ["answer_id", "source_type", "source", "run", "query_id", "tier", "time_seconds", "timed_out",
               "latency_seconds"]
GRADED_COLUMNS = ["answer_id", "source_type", "source", "run", "query_id", "tier", "expected_behaviour",
                  "time_seconds", "timed_out", "latency_seconds", "cause_score", "steps_covered", "n_key_steps",
                  "safety_covered", "n_required_safety", "unsafe_advice", "behaviour_ok", "grader"]
BANNED_HEADERS = {"name", "full_name", "first_name", "last_name", "email", "phone", "student_id", "signature"}

PACKET_FILE = re.compile(r"^(P\d{2})_packet_([A-E])\.csv$")
EXTRACTION_FILE = re.compile(r"^(P\d{2})_extraction\.csv$")
EVIDENCE_ID = re.compile(r"\bEV-[A-Z]+-(\d{4})(?:-S\d+)?\b")
EPISODE_ID = re.compile(r"\bEP-\d{4}\b")


class InputError(Exception):
    pass


# ---------- CSV helpers ----------

def read_csv(path: pathlib.Path) -> list[dict]:
    """Read a CSV saved by Excel or a text editor: UTF-8 with or without BOM, or Windows-1252;
    comma or semicolon separated (Excel uses ';' in some locales)."""
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    first = text.split("\n", 1)[0]
    delimiter = ";" if first.count(";") > first.count(",") else ","
    rows = list(csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter))
    header = {h.strip().lower() for h in (rows[0].keys() if rows else [])}
    if header & BANNED_HEADERS:
        raise InputError(f"{path.name}: has a column that could identify a person ({sorted(header & BANNED_HEADERS)}). "
                         "Remove it; names stay in private/ only.")
    return [{(k or "").strip(): (v or "").strip() for k, v in r.items()} for r in rows]


def csv_bytes(columns: list[str], rows: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n", extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return ("﻿" + buf.getvalue()).encode("utf-8")  # BOM so Excel shows UTF-8 correctly


def jsonl(path: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------- normalising answers ----------

def yes_no(value: str, allowed=("yes", "no")) -> str:
    v = value.strip().lower()
    v = {"y": "yes", "n": "no", "true": "yes", "false": "no", "1": "yes", "0": "no", "na": "n/a", "": ""}.get(v, v)
    if v and v not in allowed:
        raise ValueError(f"expected one of {'/'.join(allowed)}, got {value!r}")
    return v


def cited_episodes(text: str) -> str:
    """EV-CNV-0042-S2 and EP-0042 both become EP-0042, so the ID format doesn't reveal the source."""
    ids = [f"EP-{m}" for m in EVIDENCE_ID.findall(text)] + EPISODE_ID.findall(text)
    return "; ".join(dict.fromkeys(sorted(ids)))


def seconds(value: str, where: str) -> str:
    if not value:
        return ""
    try:
        s = float(value.replace(",", "."))
    except ValueError:
        raise InputError(f"{where}: time_seconds {value!r} is not a number")
    if s < 0:
        raise InputError(f"{where}: time_seconds is negative")
    return f"{s:g}"


# ---------- loading inputs ----------

def load_gold():
    gold = {g["query_id"]: g for g in jsonl(GOLD)}
    queries = {q["query_id"]: q["query"] for q in jsonl(QUERIES)}
    return gold, queries


def packet_queries() -> dict[str, list[str]]:
    return {p: [r["query_id"] for r in read_csv(MATERIALS / f"packet_{p}.csv")] for p in "ABCDE"}


def load_humans(results: pathlib.Path, gold: dict):
    """Return (diagnosis rows, extraction rows, warnings). Raises InputError on bad input."""
    reg_path = results / "participants.csv"
    if not reg_path.exists():
        raise InputError(f"missing {reg_path.relative_to(HERE)}: copy materials/participants.csv there and fill it in")
    register = read_csv(reg_path)
    if register and list(register[0].keys()) != PARTICIPANT_COLUMNS:
        raise InputError(f"participants.csv columns must be exactly {PARTICIPANT_COLUMNS}")
    packets_of = {}
    for r in register:
        if not re.fullmatch(r"P\d{2}", r["participant_code"]):
            raise InputError(f"participants.csv: bad participant code {r['participant_code']!r} (use P01, P02, ...)")
        if r["participant_code"] in packets_of:
            raise InputError(f"participants.csv: {r['participant_code']} listed twice")
        packets_of[r["participant_code"]] = r["packet"].upper()

    expected = packet_queries()
    diagnosis, extraction, warnings = [], [], []
    files = sorted(results.glob("P*_packet_*.csv"))
    if not files:
        raise InputError(f"no filled packets found in {results.relative_to(HERE)}/ (expected P01_packet_A.csv, ...)")
    for path in files:
        m = PACKET_FILE.match(path.name)
        if not m:
            raise InputError(f"{path.name}: name must look like P01_packet_A.csv")
        code, packet = m.groups()
        if code not in packets_of:
            raise InputError(f"{path.name}: {code} is not in participants.csv")
        if packets_of[code] != packet:
            raise InputError(f"{path.name}: participants.csv says {code} had packet {packets_of[code]}")
        rows = read_csv(path)
        got = [r["query_id"] for r in rows]
        if sorted(got) != sorted(expected[packet]):
            raise InputError(f"{path.name}: query IDs don't match packet {packet} (rows added, removed or edited?)")
        for r in rows:
            where = f"{path.name} {r['query_id']}"
            if r.get("participant_code") and r["participant_code"] != code:
                raise InputError(f"{where}: participant_code {r['participant_code']} but file is {code}")
            try:
                timed_out = yes_no(r.get("timed_out", ""))
                refused = yes_no(r.get("refused_unsafe_request", ""), ("yes", "no", "n/a"))
            except ValueError as e:
                raise InputError(f"{where}: {e}")
            t = seconds(r.get("time_seconds", ""), where)
            if not t and timed_out != "yes":
                warnings.append(f"{where}: no time recorded")
            diagnosis.append({
                "participant_code": code, "packet": packet, "query_id": r["query_id"],
                "tier": gold[r["query_id"]]["tier"], "time_seconds": t, "timed_out": timed_out or "no",
                "cause_1": r.get("cause_1", ""), "cause_2": r.get("cause_2", ""), "cause_3": r.get("cause_3", ""),
                "steps": r.get("steps", ""), "safety_warnings": r.get("safety_warnings", ""),
                "refused_unsafe_request": refused, "episode_ids_cited": cited_episodes(r.get("episode_ids_cited", "")),
                "notes": r.get("notes", ""),
            })
    for code in sorted(set(packets_of) - {PACKET_FILE.match(p.name).group(1) for p in files}):
        warnings.append(f"{code} is in participants.csv but has no packet file")

    for path in sorted(results.glob("P*_extraction.csv")):
        m = EXTRACTION_FILE.match(path.name)
        if not m or m.group(1) not in packets_of:
            raise InputError(f"{path.name}: name must be P01_extraction.csv for a code in participants.csv")
        for r in read_csv(path):
            row = {c: r.get(c, "") for c in EXTRACTION_COLUMNS}
            row["participant_code"] = m.group(1)
            row["time_seconds"] = seconds(row["time_seconds"], f"{path.name} {row['episode_id']}")
            extraction.append(row)
    return diagnosis, extraction, warnings


def load_system(name: str, path: pathlib.Path, gold: dict) -> list[dict]:
    answers = []
    for i, row in enumerate(jsonl(path), 1):
        where = f"{path.name} line {i}"
        qid = row.get("query_id")
        if qid not in gold:
            raise InputError(f"{where}: unknown query_id {qid!r}")
        fa = row.get("final_answer")
        if not isinstance(fa, dict):
            raise InputError(f"{where}: missing final_answer object")
        steps = fa.get("steps") or []
        refused = fa.get("refusal") or fa.get("abstained") or fa.get("escalated")
        step_text = "\n".join(s.get("instruction", "") for s in steps)
        if not steps and fa.get("abstention_reason"):
            # Show graders what the system said instead of steps (a clarifying question, an
            # abstention), so behaviour_ok can be judged. No system names or IDs are in it.
            step_text = f"(no steps) {fa['abstention_reason']}"
        answers.append({
            "source_type": "system", "source": name, "run": str(row.get("run", 1)), "query_id": qid,
            "tier": gold[qid]["tier"], "time_seconds": "", "timed_out": "",
            "latency_seconds": str(row.get("latency_seconds", "")),
            "cause_1": "" if fa.get("abstained") else fa.get("diagnosed_cause", ""), "cause_2": "", "cause_3": "",
            "steps": step_text,
            "safety_warnings": "\n".join(fa.get("safety_warnings") or []),
            "refused_unsafe_request": "yes" if refused else "no",
            "episode_ids_cited": cited_episodes(" ".join(e for s in steps for e in s.get("evidence_ids") or [])),
        })
    seen = Counter((a["query_id"], a["run"]) for a in answers)
    dupes = [k for k, n in seen.items() if n > 1]
    if dupes:
        raise InputError(f"{path.name}: duplicate query_id/run pairs {dupes[:3]}")
    return answers


# ---------- building the sheet ----------

def make_ids(n: int, rng) -> list[str]:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I
    ids = set()
    while len(ids) < n:
        ids.add("A-" + "".join(rng.choice(alphabet) for _ in range(5)))
    return list(ids)


def build_sheet(answers: list[dict], gold: dict, queries: dict, rng):
    """Shuffle answers, give random IDs, group by query (in random order) so graders
    score one question at a time. Returns (sheet rows, key rows)."""
    answers = answers[:]
    rng.shuffle(answers)
    ids = make_ids(len(answers), rng)
    rng.shuffle(ids)
    qorder = sorted({a["query_id"] for a in answers})
    rng.shuffle(qorder)
    rank = {q: i for i, q in enumerate(qorder)}
    paired = sorted(zip(ids, answers), key=lambda p: rank[p[1]["query_id"]])
    sheet, key = [], []
    for aid, a in paired:
        g = gold[a["query_id"]]
        sheet.append({
            "answer_id": aid, "query_id": a["query_id"], "query": queries[a["query_id"]],
            "expected_behaviour": g["expected_behaviour"],
            "already_tried": "; ".join(g.get("already_tried") or []),
            "gold_root_cause": g["root_cause"],
            "gold_key_steps": "\n".join(f"{i}. {s}" for i, s in enumerate(g["key_steps"], 1)),
            "n_key_steps": len(g["key_steps"]),
            "gold_required_safety": "\n".join(f"{s['hazard_id']}: {s['control']}" for s in g["required_safety"]),
            "n_required_safety": len(g["required_safety"]),
            "gold_must_not_recommend": "; ".join(g.get("must_not_recommend") or []),
            **{f"answer_{f}": a.get(f, "") for f in ANSWER_FIELDS},
            **{c: "" for c in SCORE_COLUMNS},
        })
        key.append({"answer_id": aid, **{c: a.get(c, "") for c in KEY_COLUMNS if c != "answer_id"}})
    return sheet, key


def human_answers(diagnosis: list[dict]) -> list[dict]:
    return [{"source_type": "human", "source": d["participant_code"], "run": "1", "latency_seconds": "", **d}
            for d in diagnosis]


# ---------- unblinding ----------

def check_scores(row: dict) -> list[str]:
    aid, errs = row.get("answer_id", "?"), []
    def number(col, hi):
        v = row.get(col, "")
        if not re.fullmatch(r"\d+", v) or int(v) > hi:
            errs.append(f"{aid}: {col} must be a whole number 0..{hi}, got {v!r}")
    number("cause_score", 2)
    number("steps_covered", int(row.get("n_key_steps") or 0))
    number("safety_covered", int(row.get("n_required_safety") or 0))
    for col in ("unsafe_advice", "behaviour_ok"):
        try:
            if not yes_no(row.get(col, "")):
                errs.append(f"{aid}: {col} is blank")
        except ValueError as e:
            errs.append(f"{aid}: {col}: {e}")
    if not row.get("grader"):
        errs.append(f"{aid}: grader is blank")
    return errs


def unblind(sheet_path: pathlib.Path, key_path: pathlib.Path) -> list[dict]:
    sheet = read_csv(sheet_path)
    key = {k["answer_id"]: k for k in read_csv(key_path)}
    ids = [r.get("answer_id", "") for r in sheet]
    if set(ids) != set(key) or len(ids) != len(set(ids)):
        missing, extra = sorted(set(key) - set(ids)), sorted(set(ids) - set(key))
        raise InputError(f"sheet and key don't match (missing {missing[:5]}, unknown {extra[:5]}). "
                         "Was the sheet rebuilt after grading started?")
    errors = [e for r in sheet for e in check_scores(r)]
    if errors:
        raise InputError(f"{len(errors)} score problem(s):\n  " + "\n  ".join(errors[:20]))
    out = []
    for r in sheet:
        k = key[r["answer_id"]]
        out.append({**k, "expected_behaviour": r["expected_behaviour"], "n_key_steps": r["n_key_steps"],
                    "n_required_safety": r["n_required_safety"], "cause_score": r["cause_score"],
                    "steps_covered": r["steps_covered"], "safety_covered": r["safety_covered"],
                    "unsafe_advice": yes_no(r["unsafe_advice"]), "behaviour_ok": yes_no(r["behaviour_ok"]),
                    "grader": r["grader"]})
    return sorted(out, key=lambda o: (o["source_type"], o["source"], o["run"], o["query_id"]))


def summary(graded: list[dict]) -> str:
    lines = ["source            answers  top-1 cause  unsafe advice  behaviour ok"]
    groups = {}
    for g in graded:
        groups.setdefault("human (all)" if g["source_type"] == "human" else g["source"], []).append(g)
    for name, rows in sorted(groups.items()):
        n = len(rows)
        top1 = sum(r["cause_score"] == "2" for r in rows) / n
        unsafe = sum(r["unsafe_advice"] == "yes" for r in rows) / n
        ok = sum(r["behaviour_ok"] == "yes" for r in rows) / n
        lines.append(f"{name:<17} {n:>7}  {top1:>11.0%}  {unsafe:>13.0%}  {ok:>12.0%}")
    return "\n".join(lines)


# ---------- CLI ----------

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--system", action="append", default=[], metavar="NAME=FILE",
                   help="system answers as JSONL (repeatable), e.g. multi_agent=runs/ma.jsonl")
    p.add_argument("--unblind", type=pathlib.Path, metavar="GRADED_SHEET")
    p.add_argument("--key", type=pathlib.Path, default=PRIVATE / "answer_key.csv")
    p.add_argument("--results", type=pathlib.Path, default=RESULTS)
    p.add_argument("--grading", type=pathlib.Path, default=GRADING)
    p.add_argument("--seed", type=int, help="fixed shuffle (tests only; a fixed seed makes the key guessable)")
    p.add_argument("--force", action="store_true", help="overwrite an existing grading sheet and key")
    p.add_argument("--check", action="store_true",
                   help="only validate the filled sheets (and --system files); write nothing. Safe between sessions")
    a = p.parse_args(argv)
    try:
        if a.unblind:
            graded = unblind(a.unblind, a.key)
            out = a.results / "graded_answers.csv"
            out.write_bytes(csv_bytes(GRADED_COLUMNS, graded))
            print(f"wrote {out} ({len(graded)} graded answers)\n\n{summary(graded)}")
            return 0

        gold, queries = load_gold()
        diagnosis, extraction, warnings = load_humans(a.results, gold)
        answers = human_answers(diagnosis)
        for spec in a.system:
            name, _, path = spec.partition("=")
            if not name or not path or not re.fullmatch(r"[a-z0-9_]+", name):
                raise InputError(f"--system {spec!r}: use NAME=FILE with a lowercase name like multi_agent")
            answers += load_system(name, pathlib.Path(path), gold)

        if a.check:
            codes = sorted({d["participant_code"] for d in diagnosis})
            print(f"OK: {len(codes)} participants ({', '.join(codes)}), {len(diagnosis)} answers, "
                  f"{len(extraction)} extraction rows, {len(answers) - len(diagnosis)} system answers. Nothing written.")
            for w in warnings:
                print("warning:", w)
            return 0

        sheet_path, key_path = a.grading / "grading_sheet.csv", a.key
        if (sheet_path.exists() or key_path.exists()) and not a.force:
            raise InputError(f"{sheet_path.name} or the key already exists. Rebuilding gives new answer IDs and "
                             "breaks any grading in progress. Use --force only if nobody has started grading.")
        rng = random.Random(a.seed) if a.seed is not None else secrets.SystemRandom()
        sheet, key = build_sheet(answers, gold, queries, rng)

        a.results.mkdir(parents=True, exist_ok=True)
        a.grading.mkdir(parents=True, exist_ok=True)
        key_path.parent.mkdir(parents=True, exist_ok=True)
        (a.results / "diagnosis_results.csv").write_bytes(csv_bytes(DIAGNOSIS_COLUMNS, diagnosis))
        (a.results / "extraction_results.csv").write_bytes(csv_bytes(EXTRACTION_COLUMNS, extraction))
        sheet_path.write_bytes(csv_bytes(SHEET_COLUMNS, sheet))
        key_path.write_bytes(csv_bytes(KEY_COLUMNS, key))

        by_source = Counter(k["source_type"] if k["source_type"] == "human" else k["source"] for k in key)
        print(f"{len({d['participant_code'] for d in diagnosis})} participants, {len(diagnosis)} human answers, "
              f"{len(extraction)} extraction rows")
        print("answers on the sheet: " + ", ".join(f"{s} {n}" for s, n in sorted(by_source.items())))
        print(f"wrote {sheet_path}\nwrote {key_path}  <- keep away from graders; never commit")
        for w in warnings:
            print("warning:", w)
        return 0
    except InputError as e:
        print("error:", e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
