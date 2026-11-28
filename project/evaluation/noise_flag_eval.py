"""K1 / subtask 3.2: a background-noise flag for Agent 1 that does not rely on Whisper.

The 28 Nov audio evaluation showed A1 flagged none of the 6 noisy clips, because its flags
come from Whisper's confidence and Whisper stayed confident. A1 now also estimates the
signal-to-noise ratio from the audio itself (`a1_capture.estimate_snr_db`).

Its threshold is chosen on DEVELOPMENT clips made here, never on the 12 committed clips:
12 other audio debriefs (seed 2811, none of the committed clip episodes), each rendered
clean and with the same plant noise as the committed set at 20, 15 and 10 dB SNR. The
WAVs go to local storage (too large for Git); their estimates are written to the report.
The committed 12 clips are then measured once with the chosen threshold.

Windows only for making the dev clips (System.Speech, via project/data/audio/make_audio.py
helpers; that script itself is not run, because it rewrites the committed clips).

    python -m project.evaluation.noise_flag_eval make-dev --out <folder>
    python -m project.evaluation.noise_flag_eval measure --dev <folder> --output-dir <dir>
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import random
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
AUDIO = ROOT / "project/data/audio"
DEV_SEED = 2811
DEV_LEVELS_DB = (20.0, 15.0, 10.0)
SCHEMA = "noise_flag_eval/v1"


def _make_audio():
    spec = importlib.util.spec_from_file_location("make_audio", AUDIO / "make_audio.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_dev_clips(out: Path) -> dict:
    """Render 12 dev debriefs clean, then add the committed set's plant noise at each level."""
    ma = _make_audio()
    committed = {c["episode_id"] for c in json.loads((AUDIO / "manifest.json").read_text(encoding="utf-8"))["clips"]}
    rows = [json.loads(line) for line in ma.NARRATIVES.read_text(encoding="utf-8").splitlines()]
    picked = ma.choose(random.Random(DEV_SEED), [r for r in rows if r["episode_id"] not in committed])
    techs = sorted({r["technician_id"] for r in rows})
    clean = out / "clean"
    clean.mkdir(parents=True, exist_ok=True)
    jobs = [{"text": ma.tts_text(r["narrative"]), "path": str(clean / f"{r['episode_id']}.wav"),
             "voice": ma.VOICES[techs.index(r["technician_id"]) % 2],
             "rate": (techs.index(r["technician_id"]) % 3) - 1} for r in picked]
    ma.speak(jobs)
    rng = random.Random(DEV_SEED)
    clips = []
    for r in picked:
        src = clean / f"{r['episode_id']}.wav"
        clips.append({"episode_id": r["episode_id"], "snr_db": None, "file": str(src.relative_to(out))})
        for level in DEV_LEVELS_DB:
            folder = out / f"snr{level:g}"
            folder.mkdir(exist_ok=True)
            dst = folder / src.name
            shutil.copyfile(src, dst)
            ma.SNR_DB = level  # add_noise reads the module-level SNR.
            ma.add_noise(dst, rng)
            clips.append({"episode_id": r["episode_id"], "snr_db": level, "file": str(dst.relative_to(out))})
    manifest = {"synthetic": True, "seed": DEV_SEED, "levels_db": list(DEV_LEVELS_DB),
                "excluded_committed_episodes": sorted(committed), "clips": clips}
    (out / "dev_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def estimate_file(path: Path) -> float | None:
    from project.agents.a1_capture import estimate_snr_db
    import whisper
    return estimate_snr_db(whisper.load_audio(str(path)))


def measure_dev(dev: Path) -> dict:
    manifest = json.loads((dev / "dev_manifest.json").read_text(encoding="utf-8"))
    rows = [{**c, "estimated_snr_db": estimate_file(dev / c["file"])} for c in manifest["clips"]]
    by_level = {}
    for row in rows:
        key = "clean" if row["snr_db"] is None else f"{row['snr_db']:g} dB"
        by_level.setdefault(key, []).append(row["estimated_snr_db"])
    summary = {k: {"n": len(v), "min": min(v), "max": max(v)} for k, v in by_level.items()}
    return {"seed": manifest["seed"], "clips": rows, "summary": summary}


def choose_threshold(dev_summary: dict) -> float:
    """Midpoint between the lowest clean estimate and the highest 15 dB estimate (the
    committed set's noise level), rounded down to 1 dB. Fixed before the 12 clips are measured."""
    low_clean, high_noisy = dev_summary["clean"]["min"], dev_summary["15 dB"]["max"]
    if high_noisy >= low_clean:
        raise ValueError(f"Dev clips do not separate: 15 dB max {high_noisy} >= clean min {low_clean}")
    return float(int((low_clean + high_noisy) / 2))


def measure_committed(threshold: float) -> dict:
    clips = json.loads((AUDIO / "manifest.json").read_text(encoding="utf-8"))["clips"]
    rows = []
    for c in clips:
        est = estimate_file(AUDIO / c["file"])
        rows.append({"file": c["file"], "noise": c["noise"], "noisy": c["noise"] != "none",
                     "estimated_snr_db": est, "flagged": est is not None and est < threshold})
    noisy = [r for r in rows if r["noisy"]]
    clean = [r for r in rows if not r["noisy"]]
    return {"clips": rows, "noisy_flagged": sum(r["flagged"] for r in noisy), "noisy_total": len(noisy),
            "clean_flagged": sum(r["flagged"] for r in clean), "clean_total": len(clean)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("make-dev")
    make.add_argument("--out", type=Path, required=True)
    measure = sub.add_parser("measure")
    measure.add_argument("--dev", type=Path, required=True)
    measure.add_argument("--output-dir", type=Path, default=ROOT / "logs" / "noise_flag_eval")
    measure.add_argument("--dev-only", action="store_true", help="Measure the dev clips only (before choosing)")
    args = parser.parse_args(argv)
    if args.command == "make-dev":
        if sys.platform != "win32":
            print("Making dev clips needs Windows (System.Speech).", file=sys.stderr)
            return 1
        manifest = make_dev_clips(args.out)
        print(f"wrote {len(manifest['clips'])} dev clips to {args.out}")
        return 0
    dev = measure_dev(args.dev)
    print(json.dumps(dev["summary"], indent=2))
    if args.dev_only:
        return 0
    from project.agents.a1_capture import NOISE_SNR_THRESHOLD_DB
    report = {"schema_version": SCHEMA, "synthetic_data_only": True,
              "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "dev": dev, "rule_threshold_db": choose_threshold(dev["summary"]),
              "a1_threshold_db": NOISE_SNR_THRESHOLD_DB, "committed_clips": measure_committed(NOISE_SNR_THRESHOLD_DB)}
    try:
        report["code_commit"] = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                               capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        report["code_commit"] = None
    args.output_dir.mkdir(parents=True, exist_ok=True)
    out = args.output_dir / "noise_flag_eval.json"
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    c = report["committed_clips"]
    print(f"threshold {NOISE_SNR_THRESHOLD_DB} dB: noisy flagged {c['noisy_flagged']}/{c['noisy_total']}, "
          f"clean flagged {c['clean_flagged']}/{c['clean_total']}. Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
