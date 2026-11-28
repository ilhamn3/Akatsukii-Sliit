"""K5 evaluation: Agent 4 machine and asset resolution against the generator's labels.

Compares the current vocabulary (entities_v2: the data's asset prefixes, bare-prefix
labels, number words, machine type from an asset ID) with the previous behaviour
(entities_v1: provisional LATHE/PRESS/IMM/CONV/COMP prefixes, none of those rules),
rebuilt from the same code so both use identical matching logic.

1. All 400 gold records: the gold machine_id (e.g. CMP-02) must stay unchanged and name
   the gold machine.
2. Agent 2 drafts (dev split, where the rules were developed; eval split, measured
   once): does A3/A4 arrive at the gold machine and asset, or block the draft?

    python -m project.evaluation.entity_eval --output-dir logs/entity_eval
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[2]
GOLD = ROOT / "project/data/episodes/ground_truth.jsonl"
EVAL = ROOT / "project/evaluation/results/2026-11-27_eval_groq-gpt-oss-120b_v5"
DEV = ROOT / "project/evaluation/results/2026-11-27_dev_groq-gpt-oss-120b/predictions_dev_extraction_v5.jsonl"
V1_PREFIXES = {"cnc_lathe": "LATHE", "hydraulic_press": "PRESS", "injection_moulding": "IMM",
               "conveyor_line": "CONV", "air_compressor": "COMP"}
SCHEMA = "entity_eval/v1"


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def resolvers():
    """(v2 = current default, v1 = previous behaviour rebuilt with the same code)."""
    from project.agents.a4_graph import ALIASES, FAULTS, HAZARDS, EntityResolver, default_resolver
    vocabulary = yaml.safe_load(ALIASES.read_text(encoding="utf-8"))
    old = copy.deepcopy(vocabulary)
    old.pop("number_words", None)
    for machine, prefix in V1_PREFIXES.items():
        old["machines"][machine]["asset_prefix"] = prefix
    v1 = EntityResolver(old, json.loads(FAULTS.read_text(encoding="utf-8")),
                        json.loads(HAZARDS.read_text(encoding="utf-8")))
    return default_resolver(), v1


def resolve(resolver, machine_type: str | None, machine_id: str | None, *, v2: bool) -> dict:
    """What A3/A4 end up with for one record: machine, asset and whether it is blocked."""
    machine = derived = None
    if machine_type:
        link = resolver.resolve_machine(machine_type, allow_prefix=v2)
        machine = link.entity_id if link.status == "resolved" else None
    elif machine_id and v2:
        link = resolver.machine_from_asset(machine_id)
        machine = derived = link.entity_id if link.status == "resolved" else None
    asset = None
    if machine:
        raw = machine_id or machine_type
        link = resolver.resolve_asset(raw, machine)
        asset = link.entity_id if link.status == "resolved" else None
    return {"machine": machine, "asset": asset, "derived_from_asset": derived is not None,
            "blocked": machine is None}


def gold_records() -> dict[str, dict]:
    return {g["episode_id"]: g["record"] for g in _jsonl(GOLD)}


def gold_id_check(gold: dict[str, dict]) -> dict:
    v2, v1 = resolvers()
    out = {}
    for name, resolver, is_v2 in (("v2", v2, True), ("v1", v1, False)):
        kept = machine_ok = 0
        for record in gold.values():
            kept += resolver.resolve_asset(record["machine_id"], record["machine_type"]).entity_id == record["machine_id"]
            if is_v2:
                machine_ok += resolver.machine_from_asset(record["machine_id"]).entity_id == record["machine_type"]
        out[name] = {"records": len(gold), "asset_id_kept": kept,
                     "machine_from_asset_id": machine_ok if is_v2 else None}
    return out


def draft_check(rows: list[dict], gold: dict[str, dict]) -> dict:
    v2, v1 = resolvers()
    summary, per = {}, []
    for name, resolver, is_v2 in (("v2", v2, True), ("v1", v1, False)):
        counts = {"drafts": 0, "machine_correct": 0, "machine_wrong": 0, "blocked": 0,
                  "asset_correct": 0, "derived_from_asset": 0, "derived_correct": 0}
        for row in rows:
            ex = row["draft"]["extraction"]
            mt = ex["machine_type"] and ex["machine_type"]["value"]
            mi = ex["machine_id"] and ex["machine_id"]["value"]
            g = gold[row["episode_id"]]
            got = resolve(resolver, mt, mi, v2=is_v2)
            counts["drafts"] += 1
            counts["blocked"] += got["blocked"]
            counts["machine_correct"] += got["machine"] == g["machine_type"]
            counts["machine_wrong"] += got["machine"] is not None and got["machine"] != g["machine_type"]
            counts["asset_correct"] += got["asset"] == g["machine_id"]
            counts["derived_from_asset"] += got["derived_from_asset"]
            counts["derived_correct"] += got["derived_from_asset"] and got["machine"] == g["machine_type"]
            if is_v2:
                per.append({"episode_id": row["episode_id"], "machine_type": mt, "machine_id": mi,
                            "gold": [g["machine_type"], g["machine_id"]], **got})
        summary[name] = counts
    return {**summary, "per_draft": per}


def _last_rows(path: Path) -> list[dict]:
    last = {}
    for row in _jsonl(path):
        last[row["episode_id"]] = row
    return [r for r in last.values() if r.get("draft")]


def run(*, dev: Path = DEV, log=print) -> dict:
    gold = gold_records()
    report = {"gold_ids": gold_id_check(gold)}
    log(f"gold IDs: {report['gold_ids']}")
    if dev.exists():
        report["dev"] = draft_check(_last_rows(dev), gold)
        log(f"dev: v1 {report['dev']['v1']} | v2 {report['dev']['v2']}")
    report["eval"] = draft_check(_last_rows(next(EVAL.glob("predictions_*.jsonl"))), gold)
    log(f"eval: v1 {report['eval']['v1']} | v2 {report['eval']['v2']}")
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                                text=True, check=True).stdout.strip()
    except Exception:
        commit = None
    from project.agents.a4_graph import default_resolver
    return {"schema_version": SCHEMA, "synthetic_data_only": True,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "code_commit": commit,
            "vocabulary_version": default_resolver().version, **report}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "logs" / "entity_eval")
    args = parser.parse_args(argv)
    report = run()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    out = args.output_dir / "entity_eval.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
