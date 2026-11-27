"""K7: field-level extraction precision / recall / F1 against ground truth.

Scores Agent 2 drafts (or human extraction forms) on the synthetic episodes in
project/data/episodes/. Every number is computed by this code; none is typed
by hand. The output JSON (schema `extraction_metrics/v1`) and its per-episode
JSONL are what the benchmark harness (Kajanan, WP7) reads.

Gold = what each narrative actually says (`narrative_coverage`), not the full
record: a technician who never mentions the tools should not cost the
extractor recall. Per field:
  machine_type      catalogue ID; predictions are resolved with the K5 resolver
  symptoms          mentioned symptoms (in spoken debriefs the first one is
                    narrated as the fault's lay description), plus the alarm code
                    if it was said
  root_cause        always narrated
  steps             only the steps the narrative mentions
  tools             only if the narrative lists them (detected in the text, since
                    the generator does not record it in narrative_coverage)
  parts             only if parts_mentioned
  failed_attempts   every failed attempt (all are narrated); scored on the action
  safety_warnings   the spoken hazard remark ("watch out for the ..."), if any

Matching is deterministic. Text is normalized (lower case, punctuation, number
words -> digits, light stemming, stop words), then items are paired one-to-one,
greedily by token Dice similarity, at a threshold (default 0.5, also reported
at 1.0 = exact normalized match). A failed extraction counts every gold item as
a miss. No LLM judges anything here.

    python -m project.evaluation.extraction_metrics run --split eval   # live A2, shared LLM
    python -m project.evaluation.extraction_metrics score --predictions <file.jsonl>
    python -m project.evaluation.extraction_metrics score-human --csv extraction_results.csv
"""

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
EPISODES = ROOT / "project" / "data" / "episodes"
CATALOGUE = ROOT / "project" / "data" / "fault_catalogue"
SCHEMA_VERSION = "extraction_metrics/v1"
FIELDS = ("machine_type", "symptoms", "root_cause", "steps", "tools", "parts",
          "failed_attempts", "safety_warnings")
LIST_FIELDS = {"symptoms", "steps", "tools", "parts", "failed_attempts", "safety_warnings"}
DEFAULT_THRESHOLD = 0.5
EVAL_SEED, EVAL_PER_MACHINE = 2026, 10
NUMBER_WORDS = {"oh": "0", "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
                "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9"}
STOPWORDS = {"the", "a", "an", "and", "or", "of", "to", "on", "in", "at", "for", "with", "from",
             "is", "was", "were", "it", "its", "that", "this", "i", "we", "so", "then", "by",
             "be", "been", "had", "has", "have", "up", "out", "all", "any", "um", "uh", "like"}


# ---------------------------------------------------------------- normalization

def _stem(token: str) -> str:
    """Light suffix stripping so 'replaced'/'replace'/'replacing' all match."""
    for suffix in ("ing", "ed", "es", "s"):
        if len(token) > len(suffix) + 2 and token.endswith(suffix):
            token = token[: -len(suffix)]
            break
    return token[:-1] if len(token) > 3 and token.endswith("e") else token


def tokens(text: str | None) -> frozenset[str]:
    """Content tokens; runs of number words become digits ('four one oh' -> '410')."""
    if not text:
        return frozenset()
    out, digits = [], ""
    for raw in re.findall(r"[a-z0-9]+", text.casefold()):
        if raw in NUMBER_WORDS or raw.isdigit():
            digits += NUMBER_WORDS.get(raw, raw)
            continue
        if digits:
            out.append(digits)
            digits = ""
        if raw not in STOPWORDS:
            out.append(_stem(raw))
    if digits:
        out.append(digits)
    return frozenset(out)


def dice(a: frozenset, b: frozenset) -> float:
    return 2 * len(a & b) / (len(a) + len(b)) if a and b else 0.0


def match_counts(predicted: list[str], gold: list[str], threshold: float) -> dict:
    """One-to-one greedy pairing by Dice similarity. Returns tp/fp/fn + pairs."""
    pt, gt = [tokens(p) for p in predicted], [tokens(g) for g in gold]
    scored = sorted(((dice(p, g), i, j) for i, p in enumerate(pt) for j, g in enumerate(gt)),
                    key=lambda x: (-x[0], x[1], x[2]))
    used_p, used_g, pairs = set(), set(), []
    for score, i, j in scored:
        if score + 1e-12 < threshold or score == 0:
            break
        if i not in used_p and j not in used_g:
            used_p.add(i)
            used_g.add(j)
            pairs.append({"predicted": predicted[i], "gold": gold[j], "dice": round(score, 4)})
    tp = len(pairs)
    return {"tp": tp, "fp": len(predicted) - tp, "fn": len(gold) - tp, "pairs": pairs}


# ------------------------------------------------------------------- gold data

def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_dataset(root: Path = EPISODES) -> dict:
    narratives = {n["episode_id"]: n for n in _jsonl(root / "narratives.jsonl")}
    truth = {t["episode_id"]: t for t in _jsonl(root / "ground_truth.jsonl")}
    faults = {f["fault_id"]: f for f in json.loads((CATALOGUE / "faults.json").read_text(encoding="utf-8"))}
    hazards = {h["hazard_id"]: h for h in json.loads((CATALOGUE / "hazards.json").read_text(encoding="utf-8"))}
    samples = json.loads((root / "samples.json").read_text(encoding="utf-8"))
    dev = sorted(s["narrative"]["episode_id"] for s in samples)
    return {"narratives": narratives, "truth": truth, "faults": faults, "hazards": hazards, "dev": dev}


def _listed_in(items: list[str], narrative: str) -> bool:
    """Tools are narrated as one list or not at all; fillers may split words."""
    wanted = frozenset().union(*(tokens(i) for i in items)) if items else frozenset()
    return bool(wanted) and len(wanted & tokens(narrative)) / len(wanted) >= 0.8


def gold_for(episode_id: str, data: dict) -> dict:
    narrative, truth = data["narratives"][episode_id], data["truth"][episode_id]
    record, cov = truth["record"], truth["narrative_coverage"]
    symptoms = list(cov["symptoms_mentioned"])
    if narrative["source"] == "audio_debrief" and symptoms:
        symptoms[0] = data["faults"][truth["fault_id"]]["lay_description"]
    if cov["error_code_mentioned"]:
        symptoms.append(f"alarm {cov['error_code_mentioned']}")
    return {
        "machine_type": record["machine_type"],
        "symptoms": symptoms,
        "root_cause": record["root_cause"],
        "steps": [s["description"] for s in record["steps"] if s["step_number"] in cov["steps_mentioned"]],
        "tools": list(record["tools"]) if _listed_in(record["tools"], narrative["narrative"]) else [],
        "parts": list(record["parts"]) if cov["parts_mentioned"] else [],
        "failed_attempts": [a["action"] for a in record["failed_attempts"]],
        "safety_warnings": ([data["hazards"][cov["hazard_remark"]]["name"]] if cov["hazard_remark"] else []),
    }


def select_episodes(data: dict, split: str) -> list[str]:
    """dev = samples.json (12, for prompt work); eval = 10 per machine, seeded,
    disjoint from dev. Prompts must not be tuned on the eval split."""
    if split == "dev":
        return list(data["dev"])
    dev, rng, chosen = set(data["dev"]), random.Random(EVAL_SEED), []
    by_machine = {}
    for eid in sorted(data["narratives"]):
        if eid not in dev:
            by_machine.setdefault(data["narratives"][eid]["machine_type"], []).append(eid)
    if split == "all":
        return sorted(eid for ids in by_machine.values() for eid in ids)
    if split != "eval":
        raise ValueError("split must be dev, eval or all")
    for machine in sorted(by_machine):
        chosen += rng.sample(by_machine[machine], EVAL_PER_MACHINE)
    return sorted(chosen)


# ------------------------------------------------------------------ predictions

def prediction_from_draft(draft: dict) -> dict:
    """Agent 2 DraftRepairRecord -> the scored field values."""
    ex = draft["extraction"]
    value = lambda fact: fact["value"] if fact else None  # noqa: E731
    return {
        "machine_type": value(ex["machine_type"]), "root_cause": value(ex["root_cause"]),
        **{f: [x["value"] for x in ex[f]] for f in ("symptoms", "steps", "tools", "parts", "safety_warnings")},
        "failed_attempts": [a["action"]["value"] for a in ex["failed_attempts"]],
    }


def _split_cell(cell: str | None) -> list[str]:
    return [p.strip(" -*•") for p in re.split(r"[\n;]+", cell or "") if p.strip(" -*•")]


def prediction_from_form(row: dict) -> dict:
    """Human extraction form row (human_study protocol columns) -> scored values."""
    return {"machine_type": (row.get("machine_type") or "").strip() or None,
            "root_cause": (row.get("root_cause") or "").strip() or None,
            **{f: _split_cell(row.get(f)) for f in LIST_FIELDS}}


def narrative_capture(narrative: dict) -> dict:
    """Narrative text -> CaptureResult dict for Agent 2 (sentence segments, no timing)."""
    text = narrative["narrative"]
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    capture_id = f"CAP-{digest[:24]}"
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    return {"capture_id": capture_id, "source_path": f"narratives.jsonl#{narrative['episode_id']}",
            "source_sha256": digest, "media_type": "audio" if narrative["source"] == "audio_debrief" else "text",
            "language": "en", "text": text, "frames": [], "quality_flags": [], "review_required": False,
            "segments": [{"evidence_id": f"{capture_id}-S{i:04d}", "start_seconds": 0.0, "end_seconds": 0.0,
                          "text": s} for i, s in enumerate(sentences)]}


# ---------------------------------------------------------------------- scoring

def score_episode(prediction: dict | None, gold: dict, threshold: float, resolver=None) -> dict:
    """Per-field tp/fp/fn for one episode. prediction=None means extraction failed."""
    if resolver is None:
        from project.agents.a4_graph import default_resolver
        resolver = default_resolver()
    fields = {}
    for field in FIELDS:
        g = gold[field]
        if prediction is None:
            n = len(g) if field in LIST_FIELDS else int(g is not None)
            fields[field] = {"tp": 0, "fp": 0, "fn": n, "pairs": []}
        elif field == "machine_type":
            raw = prediction["machine_type"]
            canon = resolver.canonical_machine(raw) if raw else None
            hit = canon is not None and canon == g
            fields[field] = {"tp": int(hit), "fp": int(raw is not None and not hit), "fn": int(not hit),
                             "pairs": [{"predicted": raw, "resolved": canon, "gold": g}]}
        elif field in LIST_FIELDS:
            fields[field] = match_counts(list(prediction[field] or []), g, threshold)
        else:
            p = prediction[field]
            fields[field] = match_counts([p] if p else [], [g] if g else [], threshold)
    return fields


def _prf(tp, fp, fn):
    # Undefined ratios are None (e.g. precision with no predictions), never an
    # invented 1.0. F1 = 2TP/(2TP+FP+FN): 0 when gold exists but nothing matched,
    # None only when there was nothing to find and nothing predicted.
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f = 2 * tp / (2 * tp + fp + fn) if tp + fp + fn else None
    return {"precision": None if p is None else round(p, 4), "recall": None if r is None else round(r, 4),
            "f1": None if f is None else round(f, 4)}


def aggregate(per_episode: list[dict], key: str) -> dict:
    totals = {f: {"tp": 0, "fp": 0, "fn": 0} for f in FIELDS}
    for row in per_episode:
        for f in FIELDS:
            for c in ("tp", "fp", "fn"):
                totals[f][c] += row[key][f][c]
    fields = {f: {**t, **_prf(t["tp"], t["fp"], t["fn"])} for f, t in totals.items()}
    micro = {c: sum(t[c] for t in totals.values()) for c in ("tp", "fp", "fn")}
    f1s = [v["f1"] for v in fields.values() if v["f1"] is not None]
    return {"fields": fields, "micro": {**micro, **_prf(**micro)},
            "macro_f1": round(sum(f1s) / len(f1s), 4) if f1s else None}


def _git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True, timeout=10, check=True).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def score_predictions(predictions: list[dict], data: dict, *, threshold=DEFAULT_THRESHOLD,
                      system: str, meta: dict | None = None) -> tuple[dict, list[dict]]:
    """predictions: [{episode_id, prediction (dict|None), error?}] -> (report, per-episode rows).

    The last row per episode wins, as in run_extraction, so a predictions log that
    still holds superseded failed attempts is scored once per episode.
    """
    from project.agents.a4_graph import default_resolver
    resolver = default_resolver()
    latest = {}
    for item in predictions:
        latest[item["episode_id"]] = item  # Keeps first-seen episode order, last row's content.
    predictions = list(latest.values())
    rows = []
    for item in predictions:
        gold = gold_for(item["episode_id"], data)
        rows.append({"episode_id": item["episode_id"], "source": data["narratives"][item["episode_id"]]["source"],
                     "machine_type": gold["machine_type"], "error": item.get("error"),
                     "primary": score_episode(item.get("prediction"), gold, threshold, resolver),
                     "exact": score_episode(item.get("prediction"), gold, 1.0, resolver)})
    by_source = {s: aggregate([r for r in rows if r["source"] == s], "primary")["micro"]
                 for s in sorted({r["source"] for r in rows})}
    report = {
        "schema_version": SCHEMA_VERSION, "system": system, "synthetic_data_only": True,
        "generated_at": datetime.now(timezone.utc).isoformat(), "code_commit": _git_commit(),
        **(meta or {}),
        "n_episodes": len(rows), "episodes": [r["episode_id"] for r in rows],
        "failed_episodes": [r["episode_id"] for r in rows if r["error"] or r["primary"] is None],
        # Infrastructure failures (quota, timeout, network, auth) are scored as misses
        # but listed separately; a report with any is not a valid prompt/model comparison.
        "provider_failed_episodes": [r["episode_id"] for r in rows if failure_kind(r["error"]) == "provider"],
        "matching": {"method": "greedy one-to-one token Dice", "threshold": threshold,
                     "machine_type": "K5 resolver to catalogue ID"},
        "fields_scored": list(FIELDS),
        "primary": aggregate(rows, "primary"),
        "exact_match": aggregate(rows, "exact"),
        "micro_by_source": by_source,
    }
    return report, rows


def compare_reports(baseline: dict, candidate: dict) -> dict:
    """Field-level F1 deltas. Refuses to compare different episode sets or scorers,
    because the difference would then not be attributable to the prompt/model."""
    if baseline["episodes"] != candidate["episodes"]:
        raise ValueError("Reports cover different episodes; compare runs on the same split")
    if baseline["matching"] != candidate["matching"] or baseline["schema_version"] != candidate["schema_version"]:
        raise ValueError("Reports used different scoring settings")
    incomplete = [name for name, report in (("baseline", baseline), ("candidate", candidate))
                  if report.get("provider_failed_episodes")]
    if incomplete:
        raise ValueError(f"Provider failures in {', '.join(incomplete)} (quota/timeout/network); "
                         "re-run with --retry-errors before comparing")

    def f1(report, key):
        block = report["primary"]["micro"] if key == "micro" else report["primary"]["fields"][key]
        return block["f1"]

    rows = {key: {"baseline": f1(baseline, key), "candidate": f1(candidate, key),
                  "delta": None if f1(baseline, key) is None or f1(candidate, key) is None
                  else round(f1(candidate, key) - f1(baseline, key), 4)}
            for key in ("micro", *FIELDS)}
    return {"baseline": {"prompt_versions": baseline.get("prompt_versions"), "models": baseline.get("models")},
            "candidate": {"prompt_versions": candidate.get("prompt_versions"), "models": candidate.get("models")},
            "split": candidate.get("split"), "n_episodes": candidate["n_episodes"],
            "failed_episodes": {"baseline": len(baseline["failed_episodes"]),
                                "candidate": len(candidate["failed_episodes"])},
            "f1": rows}


def write_outputs(report: dict, rows: list[dict], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    per_episode = output.with_name(output.stem + "_per_episode.jsonl")
    per_episode.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


# ---------------------------------------------------------------------- running

class ProviderAuthError(RuntimeError):
    """The provider rejected the credentials; every further call would fail too."""


class ProviderRateLimitError(RuntimeError):
    """Quota or rate limit reached (e.g. tokens per day); further calls fail too."""


_AUTH_ERROR = re.compile(r"\b401\b|invalid api key|authenticationerror|unauthori[sz]ed|permission denied",
                         re.IGNORECASE)
_RATE_LIMIT = re.compile(r"\b429\b|rate.?limit|quota", re.IGNORECASE)
_PROVIDER_FAILURE = re.compile(r"\b429\b|rate.?limit|quota|timeout|timed out|connection|\b5\d\d\b|"
                               r"api call failed after|service unavailable", re.IGNORECASE)


def _tokens(client) -> int | None:
    """Last call's token count when the shared client reports one (an int)."""
    value = getattr(client, "last_token_count", None)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def failure_kind(error: str | None) -> str | None:
    """'provider' = the model was not reached or refused (quota, timeout, network,
    auth): an infrastructure failure, not an extraction mistake. 'output' = the
    model answered but its output was invalid or ungrounded."""
    if not error:
        return None
    # A2 (K8) wraps every invalid-output failure in this message; its text may quote
    # raw model output ("loose connection"), so it must not be pattern-matched.
    if "Extraction output invalid after" in error or "No timestamped speech" in error:
        return "output"
    return "provider" if (_PROVIDER_FAILURE.search(error) or _AUTH_ERROR.search(error)) else "output"


_RETRY_AFTER = re.compile(r"try again in\s+(?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", re.IGNORECASE)


def retry_after_seconds(error: str, default: float = 300.0) -> float:
    """Wait suggested by the provider ("Please try again in 20m0.96s"), plus a margin."""
    match = _RETRY_AFTER.search(error or "")
    if not match or not any(match.groups()):
        return default
    hours, minutes, seconds = (float(g) if g else 0.0 for g in match.groups())
    return hours * 3600 + minutes * 60 + seconds + 5.0


def run_extraction(episode_ids: list[str], data: dict, predictions_path: Path, client=None,
                   *, retry_errors: bool = False, max_wait_seconds: float = 0.0,
                   sleep=None, log=None) -> list[dict]:
    """Run Agent 2 on each narrative; append results so an interrupted run resumes.

    The last row per episode wins. With retry_errors, episodes whose last attempt
    failed (e.g. a provider timeout) are attempted again; otherwise a failure is
    kept and scored as a miss. With max_wait_seconds > 0, a rate-limited episode
    is retried after the wait the provider suggests, until the total waiting
    budget is spent; then the run stops as without waiting.
    """
    import time
    from project.agents.a2_extraction import ExtractionAgent
    from project.core.state import create_initial_state
    sleep = sleep or time.sleep
    log = log or (lambda message: print(message, file=sys.stderr, flush=True))
    done = {r["episode_id"]: r for r in _jsonl(predictions_path)} if predictions_path.exists() else {}
    predictions_path.parent.mkdir(parents=True, exist_ok=True)
    agent = ExtractionAgent(llm_client=client)
    waited = 0.0
    with predictions_path.open("a", encoding="utf-8") as out:
        for eid in episode_ids:
            if eid in done and not (retry_errors and done[eid].get("error")):
                continue
            while True:
                row = _extract_one(agent, eid, data, client, create_initial_state)
                out.write(json.dumps(row) + "\n")
                out.flush()
                done[eid] = row
                if not (row["error"] and _RATE_LIMIT.search(row["error"])) or _AUTH_ERROR.search(row["error"]):
                    break
                wait = retry_after_seconds(row["error"])
                if waited + wait > max_wait_seconds:
                    break
                log(f"Rate limited on {eid}; waiting {wait / 60:.1f} min as the provider suggests "
                    f"({(max_wait_seconds - waited - wait) / 60:.0f} min of waiting budget left)...")
                sleep(wait)
                waited += wait
            # Stop instead of burning retries on every episode; rows are kept and
            # re-run later with --retry-errors once the key/quota is fixed.
            if row["error"] and _AUTH_ERROR.search(row["error"]):
                raise ProviderAuthError(f"Provider rejected the credentials on {eid}: {row['error'][-120:]}")
            if row["error"] and _RATE_LIMIT.search(row["error"]):
                raise ProviderRateLimitError(f"Provider rate limit/quota reached on {eid}: {row['error'][-160:]}")
    return [done[eid] for eid in episode_ids]


def _extract_one(agent, eid, data, client, create_initial_state):
    state = create_initial_state("", trace_id=f"TR-K7-{eid}")
    state["capture_result"] = narrative_capture(data["narratives"][eid])
    result = agent.run(state)
    draft = result.get("draft_record")
    row = {"episode_id": eid, "model": draft["model"] if draft else agent.llm_name,
           "prompt_version": draft["prompt_version"] if draft else None,
           "prediction": prediction_from_draft(draft) if draft else None,
           "draft": draft, "error": None if draft else "; ".join(result.get("errors") or ["no draft"]),
           # For quota budgeting; a failed call leaves the client's previous count.
           "tokens": _tokens(client) if draft else None}
    row["failure_kind"] = failure_kind(row["error"])
    return row


def main(argv=None):
    parser = argparse.ArgumentParser(description="Field-level extraction P/R/F1 (K7)")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Run Agent 2 (shared get_llm_client) on narratives, then score")
    run.add_argument("--split", default="eval", choices=["dev", "eval", "all"])
    run.add_argument("--episodes", nargs="*", help="Explicit episode IDs instead of a split")
    run.add_argument("--output-dir", default=str(ROOT / "logs" / "extraction_eval"))
    run.add_argument("--retry-errors", action="store_true", help="Re-run episodes whose last attempt failed")
    run.add_argument("--wait-on-rate-limit", type=float, default=0, metavar="MINUTES",
                     help="On a rate limit, wait as the provider suggests and continue, up to this many "
                          "minutes of waiting in total (0 = stop immediately)")
    score = sub.add_parser("score", help="Score an existing predictions JSONL")
    score.add_argument("--predictions", required=True)
    score.add_argument("--output")
    compare = sub.add_parser("compare", help="Per-field F1 differences between two metrics files")
    compare.add_argument("baseline")
    compare.add_argument("candidate")
    human = sub.add_parser("score-human", help="Score human extraction forms (CSV)")
    human.add_argument("--csv", required=True)
    human.add_argument("--output", default=str(ROOT / "logs" / "extraction_eval" / "human_extraction_metrics.json"))
    args = parser.parse_args(argv)
    if not 0 < args.threshold <= 1:
        parser.error("--threshold must be in (0, 1]")
    try:
        if args.command == "compare":
            print(json.dumps(compare_reports(json.loads(Path(args.baseline).read_text(encoding="utf-8")),
                                             json.loads(Path(args.candidate).read_text(encoding="utf-8"))),
                             indent=2))
            return 0
        data = load_dataset()
        if args.command == "run":
            ids = args.episodes or select_episodes(data, args.split)
            unknown = sorted(set(ids) - set(data["narratives"]))
            if unknown:
                raise ValueError(f"Unknown episode IDs: {unknown}")
            from project.core.llm import FakeLLM, get_llm_client
            client = get_llm_client()  # Shared team provider factory; never a private client.
            if isinstance(client, FakeLLM):
                raise ValueError("FakeLLM is configured (FACTORY_FAKE_LLM or missing key); "
                                 "K7 numbers require the team's real model")
            from project.agents.a2_extraction import prompt_path
            prompt = prompt_path().stem  # EXTRACTION_PROMPT; part of every file name so
            out_dir = Path(args.output_dir)  # resuming never mixes prompt versions.
            label = f"{'custom' if args.episodes else args.split}_{prompt}"
            if not 0 <= args.wait_on_rate_limit <= 24 * 60:
                raise ValueError("--wait-on-rate-limit must be between 0 and 1440 minutes")
            preds = run_extraction(ids, data, out_dir / f"predictions_{label}.jsonl", client=client,
                                   retry_errors=args.retry_errors,
                                   max_wait_seconds=args.wait_on_rate_limit * 60)
            if preds and all(p.get("error") for p in preds):
                raise ValueError("Every episode failed; no metrics written. Fix the provider and re-run "
                                 "with --retry-errors (see the error column in the predictions file)")
            models = sorted({p["model"] for p in preds if p.get("model")})
            report, rows = score_predictions(preds, data, threshold=args.threshold, system="multi_agent_a2",
                                             meta={"split": label, "models": models,
                                                   "prompt_versions": sorted({p["prompt_version"] for p in preds
                                                                              if p.get("prompt_version")})})
            output = out_dir / f"extraction_metrics_{label}.json"
        elif args.command == "score":
            preds = _jsonl(Path(args.predictions))
            report, rows = score_predictions(preds, data, threshold=args.threshold, system="multi_agent_a2",
                                             meta={"predictions_file": args.predictions,
                                                   "models": sorted({p["model"] for p in preds if p.get("model")})})
            output = Path(args.output) if args.output else Path(args.predictions).with_name("extraction_metrics.json")
        else:
            with open(args.csv, encoding="utf-8-sig", newline="") as handle:
                forms = list(csv.DictReader(handle))
            preds = [{"episode_id": r["episode_id"], "participant_code": r.get("participant_code"),
                      "prediction": prediction_from_form(r)} for r in forms]
            report, rows = score_predictions(preds, data, threshold=args.threshold, system="human",
                                             meta={"forms_file": args.csv,
                                                   "participants": sorted({p["participant_code"] for p in preds
                                                                           if p["participant_code"]})})
            for row, pred in zip(rows, preds):
                row["participant_code"] = pred["participant_code"]
            output = Path(args.output)
        write_outputs(report, rows, output)
        micro = report["primary"]["micro"]
        print(json.dumps({"output": str(output), "n_episodes": report["n_episodes"],
                          "failed_episodes": len(report["failed_episodes"]), "micro": micro,
                          "macro_f1": report["primary"]["macro_f1"]}, indent=2))
        return 0
    except ProviderAuthError as exc:
        print(f"{exc}\nNo metrics written. Check or replace the API key, then re-run with --retry-errors.",
              file=sys.stderr)
        return 2
    except ProviderRateLimitError as exc:
        print(f"{exc}\nNo metrics written. Wait for the provider quota to reset (or use the team's "
              "agreed account), then re-run the same command with --retry-errors; finished episodes are kept.",
              file=sys.stderr)
        return 3
    except (OSError, ValueError, KeyError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
