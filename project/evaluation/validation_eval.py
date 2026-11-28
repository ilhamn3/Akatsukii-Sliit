"""K4 evaluation: how well Agent 3 flags problems in real Agent 2 drafts.

Inputs are the 50 committed v5 eval-split drafts (Groq gpt-oss-120b, 27 Nov), so no
model is called. Three questions, each with ground truth that does not come from A3:

1. Low confidence: does A3's `low_confidence` priority (and its blocking issues) pick out
   the drafts that really contain extraction errors? Truth = K7 per-episode scores
   against the narrative-coverage gold (fp/fn per field).
2. Contradictions: one known inconsistency is injected into a copy of each draft at a
   time; detection rate on the injected copies, false-alarm rate on the clean drafts.
3. Duplicates: exact re-submission through a real ReviewQueue, and semantic duplicates
   against a KnowledgeStore of the 400 gold episodes (MiniLM), with and without each
   draft's own episode; matches are scored by fault_id from the generator labels.

All data is synthetic. Thresholds are A3's defaults (0.75 confidence, 0.90 cosine); none
were tuned here.

    python -m project.evaluation.validation_eval --output-dir logs/validation_eval
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
V5 = ROOT / "project/evaluation/results/2026-11-27_eval_groq-gpt-oss-120b_v5"
GOLD_STORE = ROOT / "project/data/expanded/n400"
REQUIRED = ("machine_type", "symptoms", "steps", "root_cause")
SCHEMA = "validation_eval/v1"


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_inputs(results: Path = V5) -> tuple[list[dict], dict[str, dict]]:
    """(rows with draft + episode_id, per-episode K7 scores by episode_id)."""
    rows = [r for r in _jsonl(next(results.glob("predictions_*.jsonl"))) if r.get("draft")]
    scores = {r["episode_id"]: r for r in _jsonl(next(results.glob("*_per_episode.jsonl")))}
    return rows, scores


def ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def confusion(pairs: list[tuple[bool, bool]]) -> dict:
    """pairs of (flagged, truly_problem) -> counts, precision, recall."""
    tp = sum(f and t for f, t in pairs)
    fp = sum(f and not t for f, t in pairs)
    fn = sum(t and not f for f, t in pairs)
    tn = sum(not f and not t for f, t in pairs)
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": ratio(tp, tp + fp), "recall": ratio(tp, tp + fn)}


# 1. Low confidence vs real extraction errors -------------------------------------------------

def error_fields(score: dict) -> list[str]:
    """Fields with any unmatched prediction (fp) or missed gold item (fn)."""
    return sorted(f for f, c in (score.get("primary") or {}).items() if c.get("fp") or c.get("fn"))


def low_confidence_eval(rows, scores) -> dict:
    from project.agents.a3_validation import validate_draft

    per_draft, pairs_any, pairs_req = [], [], []
    for row in rows:
        report = validate_draft(row["draft"], trace_id="TR-K4-EVAL-LOW")
        errors = error_fields(scores[row["episode_id"]])
        required_errors = [f for f in errors if f in REQUIRED]
        low = report.priority == "low_confidence"
        blocking = sorted({f"{i.code}@{i.field_path or '-'}" for i in report.issues if i.severity == "blocking"})
        per_draft.append({"episode_id": row["episode_id"], "low_confidence": low,
                          "confidence": report.confidence.score, "blocking": blocking,
                          "warnings": sorted({i.code for i in report.issues if i.severity == "warning"}),
                          "error_fields": errors, "required_error_fields": required_errors})
        pairs_any.append((low, bool(errors)))
        pairs_req.append((low, bool(required_errors)))
    # Per blocking missing_required_fact: is the fact really absent from the draft but in gold?
    missing = []
    for d in per_draft:
        for item in d["blocking"]:
            code, path = item.split("@")
            if code == "missing_required_fact" and path in REQUIRED:
                fn = scores[d["episode_id"]]["primary"][path]["fn"]
                missing.append({"episode_id": d["episode_id"], "field": path,
                                "gold_has_it": bool(fn)})
    return {
        "drafts": len(per_draft),
        "low_confidence": sum(d["low_confidence"] for d in per_draft),
        "drafts_with_any_error": sum(bool(d["error_fields"]) for d in per_draft),
        "drafts_with_required_error": sum(bool(d["required_error_fields"]) for d in per_draft),
        "vs_any_error": confusion(pairs_any),
        "vs_required_field_error": confusion(pairs_req),
        "missing_required_fact_flags": {
            "total": len(missing),
            "real_miss_gold_states_it": sum(m["gold_has_it"] for m in missing),
            "not_in_narrative": sum(not m["gold_has_it"] for m in missing),
            "items": missing,
        },
        "per_draft": per_draft,
    }


# 2. Injected contradictions -----------------------------------------------------------------

def _span_for(draft: dict, path: str) -> dict | None:
    return next((s for s in draft["evidence"] if s["field_path"] == path), None)


def inject_failed_attempt_as_step(draft: dict) -> dict | None:
    """The failed attempt's action is also listed as a completed step."""
    ex = draft["extraction"]
    if not ex["failed_attempts"]:
        return None
    out = copy.deepcopy(draft)
    action = out["extraction"]["failed_attempts"][0]["action"]
    index = len(out["extraction"]["steps"])
    out["extraction"]["steps"].append(copy.deepcopy(action))
    span = _span_for(out, "failed_attempts.0.action")
    if span:
        out["evidence"].append({**span, "field_path": f"steps.{index}"})
    return out


def inject_unquoted_quantity(draft: dict) -> dict | None:
    """A step value gains a pressure the cited quote never states."""
    if not draft["extraction"]["steps"]:
        return None
    out = copy.deepcopy(draft)
    out["extraction"]["steps"][0]["value"] += " to 87 psi"
    return out


def inject_ungrounded_tool(draft: dict) -> dict | None:
    """A tool with no evidence span (the source never mentions it)."""
    out = copy.deepcopy(draft)
    out["extraction"]["tools"].append({"value": "torque wrench",
                                       "sources": [{"segment_id": "S0", "quote": "x"}]})
    return out  # No EvidenceSpan is added, so A3 sees an unsupported fact.


def inject_reversed_steps(draft: dict) -> dict | None:
    """Steps listed in reverse narration order (values and spans move together)."""
    steps = draft["extraction"]["steps"]
    if len(steps) < 2:
        return None
    out = copy.deepcopy(draft)
    n = len(steps)
    out["extraction"]["steps"] = list(reversed(out["extraction"]["steps"]))
    for span in out["evidence"]:
        path = span["field_path"].split(".")
        if path[0] == "steps" and len(path) == 2:
            span["field_path"] = f"steps.{n - 1 - int(path[1])}"
    return out


INJECTIONS = {
    "failed_attempt_listed_as_step": (inject_failed_attempt_as_step, "failed_attempt_conflicts_with_step"),
    "quantity_not_in_quote": (inject_unquoted_quantity, "quantity_not_in_quote"),
    "ungrounded_fact": (inject_ungrounded_tool, "ungrounded_fact"),
    "steps_out_of_order": (inject_reversed_steps, "steps_out_of_source_order"),
}


def contradiction_eval(rows) -> dict:
    from project.agents.a3_validation import validate_draft

    clean_codes = []
    for row in rows:
        report = validate_draft(row["draft"], trace_id="TR-K4-EVAL-CLEAN")
        clean_codes.append({i.code for i in report.issues})
    out = {}
    for name, (inject, code) in INJECTIONS.items():
        applicable = detected = 0
        for row in rows:
            changed = inject(row["draft"])
            if changed is None:
                continue
            applicable += 1
            report = validate_draft(changed, trace_id="TR-K4-EVAL-INJ")
            detected += any(i.code == code for i in report.issues)
        out[name] = {"expected_code": code, "applicable_drafts": applicable, "detected": detected,
                     "detection_rate": ratio(detected, applicable),
                     "false_alarms_on_clean": sum(code in c for c in clean_codes),
                     "clean_drafts": len(clean_codes)}
    out["natural_warnings_on_clean"] = {
        code: sum(code in c for c in clean_codes)
        for code in sorted({c for codes in clean_codes for c in codes})}
    return out


# 3. Duplicates --------------------------------------------------------------------------------

def _gold_envelopes() -> list[dict]:
    return _jsonl(GOLD_STORE / "episodes.jsonl")


def _fault_ids() -> dict[str, str]:
    return {r["episode_id"]: r["fault_id"] for r in _jsonl(GOLD_STORE / "labels.jsonl")}


def duplicate_eval(rows, *, workdir: Path, embedder=None) -> dict:
    from project.agents.a3_validation import validate_draft
    from project.store.knowledge_store import KnowledgeStore
    from project.store.models import VerifiedEpisode
    from project.store.review_queue import ReviewQueue

    faults = _fault_ids()
    eval_ids = {r["episode_id"] for r in rows}
    envelopes = [VerifiedEpisode.model_validate(e) for e in _gold_envelopes()]

    # 3a. Exact re-submission of the same recording through a real queue.
    qstore = KnowledgeStore(workdir / "exact.sqlite3", embedder=embedder) if embedder else \
        KnowledgeStore(workdir / "exact.sqlite3")
    queue = ReviewQueue(workdir / "queue.sqlite3", store=qstore)
    first_flags = second_flags = 0
    for row in rows:
        report = validate_draft(row["draft"], trace_id="TR-K4-EVAL-DUP1", queue=queue, store=qstore)
        first_flags += any(i.code == "duplicate_source" for i in report.issues)
        queue.enqueue(row["draft"], report, trace_id="TR-K4-EVAL-DUP1")
    for row in rows:
        report = validate_draft(row["draft"], trace_id="TR-K4-EVAL-DUP2", queue=queue, store=qstore)
        second_flags += any(i.code == "duplicate_source" for i in report.issues)
    exact = {"drafts": len(rows), "flagged_on_first_submission": first_flags,
             "flagged_on_resubmission": second_flags}

    # 3b. Semantic: verified store of gold episodes, with or without each draft's own episode.
    def store_with(name, keep):
        store = KnowledgeStore(workdir / f"{name}.sqlite3", embedder=embedder) if embedder else \
            KnowledgeStore(workdir / f"{name}.sqlite3")
        for env in envelopes:
            if keep(env.episode_id):
                store.put_verified(env, trace_id=f"TR-K4-EVAL-{name.upper()}")
        return store

    conditions = {}
    for name, keep in (("without_own", lambda e: e not in eval_ids), ("with_own", lambda e: True)):
        store = store_with(name, keep)
        per, flagged, own_hits, kinds = [], 0, 0, {"same_episode": 0, "same_fault": 0, "other_fault": 0}
        for row in rows:
            report = validate_draft(row["draft"], trace_id="TR-K4-EVAL-SEM", store=store)
            matches = next((i.related_ids for i in report.issues if i.code == "possible_duplicate"), [])
            flagged += bool(matches)
            own_hits += row["episode_id"] in matches
            for m in matches:
                kind = ("same_episode" if m == row["episode_id"] else
                        "same_fault" if faults.get(m) == faults.get(row["episode_id"]) else "other_fault")
                kinds[kind] += 1
            per.append({"episode_id": row["episode_id"], "fault_id": faults.get(row["episode_id"]),
                        "matches": matches})
        conditions[name] = {"store_episodes": sum(keep(e.episode_id) for e in envelopes),
                            "drafts_flagged": flagged, "own_episode_found": own_hits,
                            "matched_ids_by_kind": kinds, "per_draft": per}
    return {"exact": exact, "semantic": conditions, "threshold": 0.90, "k": 3,
            "cosine_diagnostic": cosine_diagnostic(rows, envelopes, faults, store.embedder)}


def cosine_diagnostic(rows, envelopes, faults, embedder) -> dict:
    """Symptom cosine (A3's dedup signal) from each draft to its own gold episode, the closest
    other episode of the same fault, and the closest other-fault episode on the same machine.
    Diagnostic only: it shows whether any threshold could separate them; none is fitted here."""
    import numpy as np
    from project.agents.a4_graph import default_resolver
    from project.store.vector_index import normalized

    resolver = default_resolver()
    by_id = {e.episode_id: e for e in envelopes}
    gold_ids = list(by_id)
    gold_vecs = normalized(embedder.encode(["\n".join(by_id[i].record.symptoms) for i in gold_ids]),
                           embedder.dimension)
    own, same_fault, other_fault = [], [], []
    for row in rows:
        ex = row["draft"]["extraction"]
        if not ex["symptoms"] or not ex["machine_type"]:
            continue
        machine = resolver.canonical_machine(ex["machine_type"]["value"])
        vec = normalized(embedder.encode(["\n".join(f["value"] for f in ex["symptoms"])]), embedder.dimension)[0]
        sims = gold_vecs @ vec
        eid, fault = row["episode_id"], faults.get(row["episode_id"])
        own.append(float(sims[gold_ids.index(eid)]))
        same = [s for i, s in zip(gold_ids, sims) if i != eid and faults.get(i) == fault]
        other = [s for i, s in zip(gold_ids, sims)
                 if faults.get(i) != fault and by_id[i].record.machine_type == machine]
        if same:
            same_fault.append(float(max(same)))
        if other:
            other_fault.append(float(max(other)))

    def stats(values):
        if not values:
            return None
        arr = np.array(values)
        return {"n": len(values), "min": round(float(arr.min()), 3), "median": round(float(np.median(arr)), 3),
                "max": round(float(arr.max()), 3), "at_or_above_0.90": int((arr >= 0.90).sum())}

    return {"own_episode": stats(own), "closest_same_fault_other_episode": stats(same_fault),
            "closest_other_fault_same_machine": stats(other_fault)}


def run(results: Path = V5, *, embedder=None, log=print) -> dict:
    rows, scores = load_inputs(results)
    log(f"{len(rows)} drafts from {results.name}")
    low = low_confidence_eval(rows, scores)
    log(f"low confidence: {low['low_confidence']} flagged; vs any error {low['vs_any_error']}")
    contra = contradiction_eval(rows)
    log("contradictions: " + ", ".join(f"{k} {v['detected']}/{v['applicable_drafts']}"
                                      for k, v in contra.items() if "detected" in v))
    with tempfile.TemporaryDirectory() as tmp:  # Throwaway stores; TEMP is on E: after activation.
        dup = duplicate_eval(rows, workdir=Path(tmp), embedder=embedder)
    log(f"duplicates: exact {dup['exact']}; semantic "
        + ", ".join(f"{k}: flagged {v['drafts_flagged']}, own {v['own_episode_found']}"
                    for k, v in dup["semantic"].items()))
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                                text=True, check=True).stdout.strip()
    except Exception:
        commit = None
    return {"schema_version": SCHEMA, "synthetic_data_only": True,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "code_commit": commit,
            "drafts_source": str(results.relative_to(ROOT)).replace("\\", "/"),
            "embedder": "default KnowledgeStore embedder" if embedder is None else type(embedder).__name__,
            "low_confidence": low, "contradictions": contra, "duplicates": dup}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "logs" / "validation_eval")
    args = parser.parse_args(argv)
    report = run()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    out = args.output_dir / "validation_eval.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
