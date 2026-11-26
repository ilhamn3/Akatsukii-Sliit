"""Build expanded episode stores for scalability tests (WP2, subtask 2.6).

Writes project/data/expanded/n100/, n400/ and n2000/, each holding:

  episodes.jsonl   one VerifiedEpisode envelope per line (the format KnowledgeStore.put_verified takes)
  labels.jsonl     episode_id -> fault_id, for scoring only
  manifest.json    counts per machine and SHA-256 of both files

The stores are nested, so results across sizes are comparable:
  n100  a seeded subset of the base 400 that keeps every one of the 40 faults
  n400  the base episodes in project/data/episodes/ (the store the test set cites)
  n2000 the base 400 plus 1,600 extra episodes from the same generator (seed 4242),
        numbered EP-0401 to EP-2000

The envelopes are marked verified by "synthetic-generator", not by a person. They come
from generator ground truth, so they are correct by construction, but no human reviewed them.

ALL DATA IS SYNTHETIC.

Usage:
  python project/data/expand_stores.py           # write all three
  python project/data/expand_stores.py --check   # verify files on disk match a fresh build
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import random
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from project.data import generator as gen  # noqa: E402

OUT = HERE / "expanded"
SIZES = (100, 400, 2000)
EXTRA_SEED = 4242
SUBSET_SEED = 100
VERIFIED_AT = "2026-11-25T00:00:00Z"
VERIFIED_BY = "synthetic-generator (not human-reviewed)"


def jsonl_rows(text: str):
    return [json.loads(line) for line in text.splitlines()]


def renumber(narr: dict, truth: dict, num: int):
    """Give an extra episode new episode and evidence IDs so they never clash with the base 400."""
    narr, truth = json.loads(json.dumps(narr)), json.loads(json.dumps(truth))
    ep = f"EP-{num:04d}"
    rec = truth["record"]
    prefix = rec["evidence_id"].split("-")[1]
    ev = f"EV-{prefix}-{num:04d}"
    narr["episode_id"] = truth["episode_id"] = ep
    rec["evidence_id"] = ev
    for step in rec["steps"]:
        step["evidence_id"] = re.sub(r"^EV-[A-Z]+-\d+", ev, step["evidence_id"])
    return narr, truth


def seconds(stamp):
    if not stamp:
        return 0.0
    m, s = stamp.split(":")
    return float(int(m) * 60 + int(s))


def envelope(narr: dict, truth: dict) -> dict:
    rec = truth["record"]
    said = set(truth["narrative_coverage"]["steps_mentioned"])
    spans = [{"field_path": "root_cause", "evidence_id": rec["evidence_id"], "quote": rec["root_cause"],
              "start_seconds": 0.0, "end_seconds": 0.0}]
    for i, step in enumerate(rec["steps"]):
        if step["step_number"] in said:
            start = seconds(step["timestamp"])
            spans.append({"field_path": f"steps.{i}", "evidence_id": step["evidence_id"], "quote": step["description"],
                          "start_seconds": start, "end_seconds": start})
    return {
        "episode_id": truth["episode_id"],
        "status": "verified",
        "record": rec,
        "provenance": {
            "capture_id": "CAP-" + truth["episode_id"],
            "source_path": f"synthetic://episodes/{truth['episode_id']}",
            "source_sha256": hashlib.sha256(narr["narrative"].encode("utf-8")).hexdigest(),
            "source_type": rec["source"],
            "evidence_spans": spans,
            "extraction_model": None,
            "prompt_version": None,
        },
        "verified_by": VERIFIED_BY,
        "verified_at": VERIFIED_AT,
        "outcome": "repair completed and machine returned to production",
    }


def base_episodes():
    files = gen.generate(gen.DEFAULT_SEED, gen.DEFAULT_N)
    return list(zip(jsonl_rows(files["narratives.jsonl"]), jsonl_rows(files["ground_truth.jsonl"])))


def extra_episodes(n: int, start: int):
    files = gen.generate(EXTRA_SEED, n)
    pairs = zip(jsonl_rows(files["narratives.jsonl"]), jsonl_rows(files["ground_truth.jsonl"]))
    return [renumber(nr, tr, start + i) for i, (nr, tr) in enumerate(pairs)]


def subset(pairs, n: int):
    """Seeded subset that keeps at least one episode of every fault, in original order."""
    rng = random.Random(SUBSET_SEED)
    by_fault = {}
    for i, (_, tr) in enumerate(pairs):
        by_fault.setdefault(tr["fault_id"], []).append(i)
    keep = {rng.choice(idx) for _, idx in sorted(by_fault.items())}
    rest = [i for i in range(len(pairs)) if i not in keep]
    keep |= set(rng.sample(rest, n - len(keep)))
    return [pairs[i] for i in sorted(keep)]


def store_files(pairs, size: int) -> dict:
    episodes = "".join(json.dumps(envelope(nr, tr), ensure_ascii=False) + "\n" for nr, tr in pairs)
    labels = "".join(json.dumps({"episode_id": tr["episode_id"], "fault_id": tr["fault_id"]}) + "\n" for _, tr in pairs)
    by_machine = {}
    for nr, _ in pairs:
        by_machine[nr["machine_type"]] = by_machine.get(nr["machine_type"], 0) + 1
    manifest = {
        "synthetic": True,
        "size": size,
        "episodes": len(pairs),
        "faults_covered": len({tr["fault_id"] for _, tr in pairs}),
        "by_machine": dict(sorted(by_machine.items())),
        "first_episode": pairs[0][1]["episode_id"],
        "last_episode": pairs[-1][1]["episode_id"],
        "verified_by": VERIFIED_BY,
        "files_sha256": {name: hashlib.sha256(text.encode("utf-8")).hexdigest()
                         for name, text in (("episodes.jsonl", episodes), ("labels.jsonl", labels))},
    }
    return {"episodes.jsonl": episodes, "labels.jsonl": labels, "manifest.json": json.dumps(manifest, indent=2) + "\n"}


def build() -> dict:
    """Return {relative path: text} for every output file."""
    base = base_episodes()
    stores = {100: subset(base, 100), 400: base, 2000: base + extra_episodes(2000 - len(base), len(base) + 1)}
    out = {}
    for size in SIZES:
        for name, text in store_files(stores[size], size).items():
            out[f"n{size}/{name}"] = text
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--check", action="store_true")
    a = p.parse_args(argv)
    files = build()
    if a.check:
        bad = [k for k, v in files.items() if not (OUT / k).exists() or (OUT / k).read_bytes() != v.encode("utf-8")]
        print("expanded stores match" if not bad else f"MISMATCH: {bad}")
        return 1 if bad else 0
    for rel, text in files.items():
        path = OUT / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    for size in SIZES:
        m = json.loads(files[f"n{size}/manifest.json"])
        print(f"n{size}: {m['episodes']} episodes, {m['faults_covered']} faults, {m['first_episode']}..{m['last_episode']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
