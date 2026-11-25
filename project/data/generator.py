"""Seeded generator for synthetic repair episodes (WP2, subtasks 2.2 and 2.3).

Reads the fault catalogue and writes about 400 repair episodes told by 12 simulated
technicians. Each episode has two parts, stored in separate files:

  episodes/narratives.jsonl    what the technician said or wrote (input to Agents 1-2)
  episodes/ground_truth.jsonl  what really happened, as a RepairRecord (never shown to agents)

Narratives are spoken-style: fillers, shop-floor jargon, self-corrections, and omitted
steps (sometimes the safety step). The ground truth records which parts were left out,
so extraction can be scored fairly.

ALL DATA IS SYNTHETIC. No real technicians, machines or incidents are described.

The generator uses only the standard library and one random.Random(seed), so the same
seed always produces byte-identical files.

Usage:
  python project/data/generator.py                  # seed 42, 400 episodes
  python project/data/generator.py --seed 7 --n 100 --out some/dir
  python project/data/generator.py --check          # regenerate in memory, compare with files on disk
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import random
import re
import sys
from datetime import date, timedelta

HERE = pathlib.Path(__file__).resolve().parent
CATALOGUE = HERE / "fault_catalogue"
DEFAULT_OUT = HERE / "episodes"
GENERATOR_VERSION = "1.0.0"
DEFAULT_SEED = 42
DEFAULT_N = 400
START_DATE = date(2025, 1, 6)
SPAN_DAYS = 680
WORDS_PER_SECOND = 2.5  # rough speaking rate used for audio timestamps

MACHINES = {
    # machine_type: (evidence prefix, spoken nickname, number of assets on the floor)
    "cnc_lathe": ("CNC", "lathe", 4),
    "hydraulic_press": ("HYD", "press", 3),
    "injection_moulding": ("INJ", "moulding machine", 4),
    "conveyor_line": ("CNV", "conveyor", 3),
    "air_compressor": ("CMP", "compressor", 2),
}

# 12 simulated technicians. Rates are probabilities used by the narrative builder.
# activity = relative share of episodes they report.
TECHNICIANS = [
    {"technician_id": "T01", "experience": "senior", "shift": "day",   "source": "audio_debrief", "activity": 1.4, "filler_rate": 0.04, "jargon_rate": 0.8, "omission_rate": 0.25, "safety_mention": 0.75, "wrong_fix_rate": 0.15, "verbosity": 0.3},
    {"technician_id": "T02", "experience": "senior", "shift": "night", "source": "shift_log",     "activity": 1.2, "filler_rate": 0.0,  "jargon_rate": 0.9, "omission_rate": 0.35, "safety_mention": 0.6,  "wrong_fix_rate": 0.1,  "verbosity": 0.1},
    {"technician_id": "T03", "experience": "senior", "shift": "day",   "source": "audio_debrief", "activity": 1.0, "filler_rate": 0.06, "jargon_rate": 0.6, "omission_rate": 0.15, "safety_mention": 0.9,  "wrong_fix_rate": 0.2,  "verbosity": 0.6},
    {"technician_id": "T04", "experience": "mid",    "shift": "swing", "source": "audio_debrief", "activity": 1.0, "filler_rate": 0.1,  "jargon_rate": 0.5, "omission_rate": 0.2,  "safety_mention": 0.8,  "wrong_fix_rate": 0.35, "verbosity": 0.5},
    {"technician_id": "T05", "experience": "mid",    "shift": "night", "source": "shift_log",     "activity": 0.9, "filler_rate": 0.0,  "jargon_rate": 0.6, "omission_rate": 0.25, "safety_mention": 0.7,  "wrong_fix_rate": 0.3,  "verbosity": 0.3},
    {"technician_id": "T06", "experience": "mid",    "shift": "day",   "source": "audio_debrief", "activity": 1.1, "filler_rate": 0.14, "jargon_rate": 0.4, "omission_rate": 0.1,  "safety_mention": 0.85, "wrong_fix_rate": 0.3,  "verbosity": 0.8},
    {"technician_id": "T07", "experience": "mid",    "shift": "swing", "source": "audio_debrief", "activity": 0.8, "filler_rate": 0.08, "jargon_rate": 0.7, "omission_rate": 0.3,  "safety_mention": 0.55, "wrong_fix_rate": 0.4,  "verbosity": 0.4},
    {"technician_id": "T08", "experience": "mid",    "shift": "day",   "source": "shift_log",     "activity": 0.7, "filler_rate": 0.0,  "jargon_rate": 0.3, "omission_rate": 0.15, "safety_mention": 0.85, "wrong_fix_rate": 0.25, "verbosity": 0.5},
    {"technician_id": "T09", "experience": "junior", "shift": "night", "source": "audio_debrief", "activity": 0.8, "filler_rate": 0.18, "jargon_rate": 0.2, "omission_rate": 0.2,  "safety_mention": 0.7,  "wrong_fix_rate": 0.55, "verbosity": 0.7},
    {"technician_id": "T10", "experience": "junior", "shift": "swing", "source": "audio_debrief", "activity": 0.7, "filler_rate": 0.2,  "jargon_rate": 0.15, "omission_rate": 0.35, "safety_mention": 0.45, "wrong_fix_rate": 0.6,  "verbosity": 0.6},
    {"technician_id": "T11", "experience": "junior", "shift": "day",   "source": "shift_log",     "activity": 0.6, "filler_rate": 0.0,  "jargon_rate": 0.1, "omission_rate": 0.3,  "safety_mention": 0.6,  "wrong_fix_rate": 0.5,  "verbosity": 0.4},
    {"technician_id": "T12", "experience": "junior", "shift": "night", "source": "audio_debrief", "activity": 0.6, "filler_rate": 0.16, "jargon_rate": 0.3, "omission_rate": 0.25, "safety_mention": 0.65, "wrong_fix_rate": 0.5,  "verbosity": 0.5},
]

FILLERS = ["uh", "um", "like", "you know", "basically", "sort of", "yeah", "I mean"]

# Shop-floor jargon, applied per match with the technician's jargon_rate.
JARGON = [
    (r"\block(ed)? out and depressurise\b", "LOTO and bleed down"),
    (r"\block(ed)? out\b", "LOTO"),
    (r"\bdepressurise\b", "bleed down"),
    (r"\bhydraulic\b", "hyd"),
    (r"\bcoolant\b", "suds"),
    (r"\btemperature\b", "temp"),
    (r"\breplace\b", "swap out"),
    (r"\breplaced\b", "swapped"),
    (r"\binspect\b", "have a look at"),
    (r"\bcompressor\b", "comp"),
    (r"\bminutes\b", "mins"),
    (r"\bconfirm\b", "make sure"),
]

DIGIT_WORDS = "oh one two three four five six seven eight nine".split()

OPENERS = ["okay so", "right so", "yeah so", "alright", "so", "okay, quick one"]
CALLED = ["operator called me over,", "got called to", "had a job on", "this was on"]
WRONG_SELF = ["first I {action}.", "so first thing I {action}.", "I thought it was simple so I {action}."]
WRONG_OTHER = ["previous shift had already {action},", "someone before me {action},"]
WRONG_RESULT = ["didn't help, {why}.", "no luck, {why}.", "waste of time, {why}."]
DIAGNOSIS = ["turned out, {root}.", "found it in the end, {root}.", "problem was, {root}.", "so the real cause, {root}."]
CONNECT = ["then I", "so I", "after that", "next you", "then", "and I"]
CLOSE = ["ran it after, all good.", "it's running fine now.", "that's it, back in production.",
         "should be okay now, keep an eye on it.", "tested it, no alarm since."]
SELF_CORRECT = ["the left, sorry, the", "the top, no, the", "the, uh, the"]


def load_catalogue():
    faults = json.loads((CATALOGUE / "faults.json").read_text(encoding="utf-8"))
    hazards = {h["hazard_id"]: h for h in json.loads((CATALOGUE / "hazards.json").read_text(encoding="utf-8"))}
    return faults, hazards


def spoken_code(code: str) -> str:
    """ALM-410 -> 'the four one oh alarm'."""
    digits = re.sub(r"\D", "", code)
    return "the " + " ".join(DIGIT_WORDS[int(d)] for d in digits) + " alarm"


def is_safety_step(step: str) -> bool:
    return bool(re.search(r"lock out|lockout|isolate|depressurise|vent .* to zero|cool to a safe", step))


def mmss(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60:02d}:{s % 60:02d}"


class Builder:
    """Builds one narrative while tracking word offsets for step timestamps."""

    def __init__(self, rng: random.Random, tech: dict, spoken: bool):
        self.rng, self.tech, self.spoken = rng, tech, spoken
        self.parts: list[str] = []
        self.words = 0
        self.corrected = False

    def jargon(self, text: str) -> str:
        for pattern, repl in JARGON:
            text = re.sub(pattern, lambda m: repl if self.rng.random() < self.tech["jargon_rate"] else m.group(0), text)
        return text

    def fill(self, text: str) -> str:
        if not self.spoken or self.tech["filler_rate"] == 0:
            return text
        out, gap = [], 0
        for i, w in enumerate(text.split(" ")):
            # Fillers go between words, never right before a symbol, and at least 4 words apart.
            if i and gap >= 4 and w[:1].isalnum() and not out[-1].endswith(",") \
                    and self.rng.random() < self.tech["filler_rate"] * 0.6:
                out.append(self.rng.choice(FILLERS) + ",")
                gap = 0
            out.append(w)
            gap += 1
        text = " ".join(out)
        if not self.corrected and " the " in text and self.rng.random() < self.tech["filler_rate"]:
            text = text.replace(" the ", " " + self.rng.choice(SELF_CORRECT) + " ", 1)
            self.corrected = True  # at most one self-correction per narrative
        return text

    def add(self, text: str) -> float:
        """Append a sentence; return its start offset in seconds."""
        start = self.words / WORDS_PER_SECOND
        text = self.fill(self.jargon(text))
        self.parts.append(text)
        self.words += len(text.split())
        return start

    def text(self) -> str:
        return " ".join(self.parts)


def build_episode(rng: random.Random, tech: dict, fault: dict, hazards: dict, machine_id: str, num: int):
    spoken = tech["source"] == "audio_debrief"
    prefix, nick, _ = MACHINES[fault["machine_type"]]
    b = Builder(rng, tech, spoken)
    asset_no = DIGIT_WORDS[int(machine_id[-1])] if spoken else machine_id

    # Symptoms the technician actually mentions.
    n_sym = 1 + (rng.random() < 0.6) + (rng.random() < tech["verbosity"] * 0.5)
    mentioned_sym = rng.sample(fault["symptoms"], min(n_sym, len(fault["symptoms"])))
    code = fault["error_codes"][0] if fault["error_codes"] and rng.random() < 0.8 else None

    if spoken:
        b.add(f"{rng.choice(OPENERS)}, {rng.choice(CALLED)} {nick} {asset_no}, {fault['lay_description']}.")
        if len(mentioned_sym) > 1:
            b.add("also " + ", and ".join(mentioned_sym[1:]) + ".")
        if code:
            b.add(f"it was throwing {spoken_code(code)}.")
    else:
        line = f"{machine_id}: {mentioned_sym[0]}"
        if len(mentioned_sym) > 1:
            line += "; " + "; ".join(mentioned_sym[1:])
        b.add(line + (f". {code} active." if code else "."))

    # Failed attempts.
    failed = []
    if rng.random() < tech["wrong_fix_rate"]:
        k = 2 if rng.random() < 0.2 and len(fault["wrong_fixes"]) > 1 else 1
        for wf in rng.sample(fault["wrong_fixes"], k):
            by_other = rng.random() < 0.3
            failed.append({"action": wf["action"], "outcome": "problem came back" if "symptom" in wf["why_it_fails"] else "no change",
                           "why_failed": wf["why_it_fails"], "by_other_shift": by_other})
            if spoken:
                tmpl = rng.choice(WRONG_OTHER if by_other else WRONG_SELF)
                b.add(tmpl.format(action=wf["action"]) + " " + rng.choice(WRONG_RESULT).format(why=wf["why_it_fails"]))
            else:
                who = "prev shift" if by_other else "tried"
                b.add(f"{who} {wf['action']} - no good ({wf['why_it_fails']}).")

    b.add(rng.choice(DIAGNOSIS).format(root=fault["root_cause"]) if spoken else f"Cause: {fault['root_cause']}.")

    # Steps, with omissions. The safety step is dropped according to safety_mention.
    steps = fault["valid_fixes"]
    said = []
    for i, step in enumerate(steps):
        if is_safety_step(step):
            keep = rng.random() < tech["safety_mention"]
        elif i == len(steps) - 1:
            keep = rng.random() > tech["omission_rate"] * 0.6
        else:
            keep = rng.random() > tech["omission_rate"]
        said.append(keep)
    non_safety = [i for i, s in enumerate(steps) if not is_safety_step(s)]
    while sum(said[i] for i in non_safety) < min(2, len(non_safety)):  # always describe some real work
        said[rng.choice(non_safety)] = True

    timestamps = [None] * len(steps)
    if spoken:
        first = True
        for i, step in enumerate(steps):
            if said[i]:
                lead = "so I" if first else rng.choice(CONNECT)
                timestamps[i] = mmss(b.add(f"{lead} {step}."))
                first = False
    else:
        b.add("Fix: " + "; ".join(s for s, k in zip(steps, said) if k) + ".")

    # Optional tools / parts / hazard remarks.
    if fault["tools"] and rng.random() < tech["verbosity"]:
        b.add(("used " if spoken else "Tools: ") + ", ".join(fault["tools"]) + ".")
    parts_said = bool(fault["parts"]) and rng.random() < 0.4 + tech["verbosity"] * 0.5
    if parts_said:
        b.add(("had to grab " if spoken else "Parts: ") + ", ".join(fault["parts"]) + (" from stores." if spoken else "."))
    hazard_remark = None
    if fault["hazards"] and spoken and rng.random() < tech["verbosity"] * 0.5:
        hazard_remark = rng.choice(fault["hazards"])
        b.add(f"watch out for the {hazards[hazard_remark]['name'].lower()} on that one.")
    b.add(rng.choice(CLOSE) if spoken else "OK, returned to production.")

    source = tech["source"]
    evidence_id = f"EV-{prefix}-{num:04d}"
    record = {
        "machine_type": fault["machine_type"],
        "machine_id": machine_id,
        "symptoms": fault["symptoms"] + ([f"alarm {fault['error_codes'][0]}"] if fault["error_codes"] else []),
        "steps": [{"step_number": i + 1, "description": s, "timestamp": timestamps[i],
                   "evidence_id": f"{evidence_id}-S{i + 1}"} for i, s in enumerate(steps)],
        "tools": fault["tools"],
        "parts": fault["parts"],
        "root_cause": fault["root_cause"],
        "failed_attempts": [{k: f[k] for k in ("action", "outcome", "why_failed")} for f in failed],
        "safety_warnings": [hazards[h]["required_control"] for h in fault["hazards"]],
        "confidence": 1.0,
        "evidence_id": evidence_id,
        "source": source,
    }
    coverage = {
        "symptoms_mentioned": mentioned_sym,
        "error_code_mentioned": code,
        "steps_mentioned": [i + 1 for i, k in enumerate(said) if k],
        "steps_omitted": [i + 1 for i, k in enumerate(said) if not k],
        "safety_step_mentioned": all(k for s, k in zip(steps, said) if is_safety_step(s)),
        "failed_attempts_by_other_shift": [f["action"] for f in failed if f["by_other_shift"]],
        "parts_mentioned": parts_said,
        "hazard_remark": hazard_remark,
    }
    return b.text(), record, coverage


def generate(seed: int = DEFAULT_SEED, n: int = DEFAULT_N):
    """Return {filename: text} for every output file. Pure function of (seed, n, catalogue)."""
    rng = random.Random(seed)
    faults, hazards = load_catalogue()
    fault_w = [f["recurrence_weight"] for f in faults]
    tech_w = [t["activity"] for t in TECHNICIANS]

    drafts = []
    for _ in range(n):
        tech = rng.choices(TECHNICIANS, tech_w)[0]
        fault = rng.choices(faults, fault_w)[0]
        prefix, _, assets = MACHINES[fault["machine_type"]]
        machine_id = f"{prefix}-{rng.randint(1, assets):02d}"
        day = START_DATE + timedelta(days=rng.randrange(SPAN_DAYS))
        drafts.append((day.isoformat(), tech, fault, machine_id, rng.random()))
    drafts.sort(key=lambda d: (d[0], d[4]))  # chronological; tie-break is seeded

    narratives, truths = [], []
    for num, (day, tech, fault, machine_id, _) in enumerate(drafts, 1):
        text, record, coverage = build_episode(rng, tech, fault, hazards, machine_id, num)
        ep = f"EP-{num:04d}"
        narratives.append({"episode_id": ep, "date": day, "technician_id": tech["technician_id"],
                           "shift": tech["shift"], "machine_type": fault["machine_type"],
                           "machine_id": machine_id, "source": record["source"], "narrative": text})
        truths.append({"episode_id": ep, "fault_id": fault["fault_id"], "cause_family": fault["cause_family"],
                       "hazard_ids": fault["hazards"], "record": record, "narrative_coverage": coverage})

    def jsonl(rows):
        return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)

    first_per_tech = {}
    for nrow, trow in zip(narratives, truths):
        first_per_tech.setdefault(nrow["technician_id"], {"narrative": nrow, "ground_truth": trow})
    samples = [first_per_tech[t["technician_id"]] for t in TECHNICIANS if t["technician_id"] in first_per_tech]

    files = {
        "narratives.jsonl": jsonl(narratives),
        "ground_truth.jsonl": jsonl(truths),
        "technicians.json": json.dumps(TECHNICIANS, indent=2) + "\n",
        "samples.json": json.dumps(samples, indent=2, ensure_ascii=False) + "\n",
    }
    # Normalise line endings so a Windows (CRLF) checkout hashes the same as Linux/macOS.
    catalogue_hash = hashlib.sha256(b"".join(
        (CATALOGUE / name).read_bytes().replace(b"\r\n", b"\n") for name in ("faults.json", "hazards.json")
    )).hexdigest()
    stats = {
        "episodes": n,
        "technicians": len({r["technician_id"] for r in narratives}),
        "faults_covered": len({t["fault_id"] for t in truths}),
        "by_machine": _count(r["machine_type"] for r in narratives),
        "by_source": _count(r["source"] for r in narratives),
        "with_failed_attempts": sum(bool(t["record"]["failed_attempts"]) for t in truths),
        "safety_step_omitted": sum(not t["narrative_coverage"]["safety_step_mentioned"] for t in truths),
        "date_range": [narratives[0]["date"], narratives[-1]["date"]] if narratives else [],
    }
    manifest = {
        "synthetic": True,
        "disclosure": "All episodes are synthetic, generated from the team's fault catalogue. "
                      "No real technicians, machines or incidents are described.",
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "catalogue_sha256": catalogue_hash,
        "stats": stats,
        "files_sha256": {name: hashlib.sha256(text.encode("utf-8")).hexdigest() for name, text in files.items()},
    }
    files["manifest.json"] = json.dumps(manifest, indent=2) + "\n"
    return files


def _count(items):
    out = {}
    for i in items:
        out[i] = out.get(i, 0) + 1
    return dict(sorted(out.items()))


def write(files: dict, out: pathlib.Path):
    out.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (out / name).write_bytes(text.encode("utf-8"))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument("--n", type=int, default=DEFAULT_N)
    p.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    p.add_argument("--check", action="store_true", help="verify files on disk match a fresh run")
    a = p.parse_args(argv)
    files = generate(a.seed, a.n)
    if a.check:
        bad = [name for name, text in files.items()
               if not (a.out / name).exists() or (a.out / name).read_bytes() != text.encode("utf-8")]
        print("reproducible: files match" if not bad else f"MISMATCH: {bad}")
        return 1 if bad else 0
    write(files, a.out)
    stats = json.loads(files["manifest.json"])["stats"]
    print(f"wrote {stats['episodes']} episodes to {a.out}")
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
