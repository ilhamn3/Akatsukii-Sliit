"""K1 evaluation: Agent 1 (local Whisper) on the synthetic audio subset.

For each clip in project/data/audio/manifest.json this runs the real CaptureAgent,
times the transcription, scores word error rate (WER) against the clip's reference
transcript, and records whether A1 routed the capture to review.

Two WER figures are reported:
- `wer` (primary): both texts pass through Whisper's own EnglishTextNormalizer, the
  usual convention for Whisper WER (spelled-out numbers become digits; fillers such as
  "um"/"uh" are dropped; punctuation and case are ignored).
- `wer_basic`: lowercase and punctuation only, so fillers and number formatting count.

All audio is synthetic (Windows computer voices); these numbers say nothing about real
technicians, accents or plant acoustics.

    python -m project.evaluation.capture_metrics --output-dir logs/capture_eval
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
AUDIO = ROOT / "project" / "data" / "audio"
SCHEMA = "capture_metrics/v1"


def basic_words(text: str) -> list[str]:
    """Lowercase words; hyphens split words; other punctuation is removed."""
    text = text.lower().replace("-", " ")
    return re.sub(r"[^a-z0-9' ]+", " ", text).split()


def whisper_words(text: str) -> list[str]:
    from whisper.normalizers import EnglishTextNormalizer
    return EnglishTextNormalizer()(text).split()


def word_errors(reference: list[str], hypothesis: list[str]) -> dict:
    """Levenshtein alignment over words -> substitutions, deletions, insertions."""
    rows, cols = len(reference) + 1, len(hypothesis) + 1
    cost = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        cost[i][0] = i
    for j in range(cols):
        cost[0][j] = j
    for i in range(1, rows):
        for j in range(1, cols):
            same = reference[i - 1] == hypothesis[j - 1]
            cost[i][j] = min(cost[i - 1][j - 1] + (0 if same else 1), cost[i - 1][j] + 1, cost[i][j - 1] + 1)
    i, j, sub, dele, ins = len(reference), len(hypothesis), 0, 0, 0
    while i > 0 or j > 0:  # Backtrace for the error breakdown.
        if i > 0 and j > 0 and cost[i][j] == cost[i - 1][j - 1] + (reference[i - 1] != hypothesis[j - 1]):
            sub += reference[i - 1] != hypothesis[j - 1]
            i, j = i - 1, j - 1
        elif i > 0 and cost[i][j] == cost[i - 1][j] + 1:
            dele, i = dele + 1, i - 1
        else:
            ins, j = ins + 1, j - 1
    return {"reference_words": len(reference), "substitutions": sub, "deletions": dele,
            "insertions": ins, "errors": sub + dele + ins}


def wer(errors: dict) -> float | None:
    return round(errors["errors"] / errors["reference_words"], 4) if errors["reference_words"] else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def score_clip(clip: dict, capture: dict | None, seconds: float, error: str | None = None) -> dict:
    reference = clip["reference_transcript"]
    hypothesis = (capture or {}).get("text", "")
    primary = word_errors(whisper_words(reference), whisper_words(hypothesis))
    basic = word_errors(basic_words(reference), basic_words(hypothesis))
    segments = (capture or {}).get("segments") or []
    segment_flags = sorted({f for s in segments for f in s.get("quality_flags", [])})
    return {
        "file": clip["file"], "episode_id": clip["episode_id"], "noise": clip["noise"],
        "noisy": clip["noise"] != "none", "voice": clip["voice"], "rate": clip["rate"],
        "duration_seconds": clip["duration_seconds"],
        "transcribe_seconds": round(seconds, 2),
        "real_time_factor": round(seconds / clip["duration_seconds"], 3),
        "wer": wer(primary), "errors": primary,
        "wer_basic": wer(basic), "errors_basic": basic,
        "segments": len(segments),
        # A1 flags a segment when avg_logprob < -1.0, no_speech_prob > 0.6 or compression > 2.4.
        "min_avg_logprob": min((s["avg_logprob"] for s in segments if s.get("avg_logprob") is not None),
                               default=None),
        "max_no_speech_prob": max((s["no_speech_prob"] for s in segments if s.get("no_speech_prob") is not None),
                                  default=None),
        "segments_flagged": sum(bool(s.get("quality_flags")) for s in segments),
        "segment_flags": segment_flags,
        "capture_flags": (capture or {}).get("quality_flags", []),
        "review_required": (capture or {}).get("review_required"),
        "transcript": hypothesis, "reference": reference, "error": error,
    }


def aggregate(rows: list[dict]) -> dict:
    def pooled(group, key="errors"):
        ref = sum(r[key]["reference_words"] for r in group)
        return round(sum(r[key]["errors"] for r in group) / ref, 4) if ref else None

    out = {}
    for name, group in (("all", rows), ("clean", [r for r in rows if not r["noisy"]]),
                        ("noisy", [r for r in rows if r["noisy"]])):
        ok = [r for r in group if r["error"] is None]
        out[name] = {
            "clips": len(group), "failed": len(group) - len(ok),
            "wer_pooled": pooled(ok), "wer_basic_pooled": pooled(ok, "errors_basic"),
            "wer_mean": round(sum(r["wer"] for r in ok) / len(ok), 4) if ok else None,
            "audio_seconds": round(sum(r["duration_seconds"] for r in ok), 2),
            "transcribe_seconds": round(sum(r["transcribe_seconds"] for r in ok), 2),
            "review_required": sum(bool(r["review_required"]) for r in ok),
        }
        audio = out[name]["audio_seconds"]
        out[name]["real_time_factor"] = round(out[name]["transcribe_seconds"] / audio, 3) if audio else None
    noisy = [r for r in rows if r["error"] is None and r["noisy"]]
    clean = [r for r in rows if r["error"] is None and not r["noisy"]]
    out["noisy_flagging"] = {  # Does A1's review flag separate the noisy clips?
        "noisy_flagged": sum(bool(r["review_required"]) for r in noisy), "noisy_total": len(noisy),
        "clean_flagged": sum(bool(r["review_required"]) for r in clean), "clean_total": len(clean),
    }
    return out


def run(manifest_path: Path = AUDIO / "manifest.json", *, agent=None, log=print) -> dict:
    from project.agents.a1_capture import CaptureAgent
    from project.core.state import create_initial_state

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    agent = agent or CaptureAgent()
    load_seconds = None
    if getattr(agent, "_model", None) is None:  # Load once so per-clip times are transcription only.
        start = time.perf_counter()
        import whisper
        agent._model = whisper.load_model(os.getenv("WHISPER_MODEL", "base"),
                                          device=os.getenv("WHISPER_DEVICE", "cpu"),
                                          download_root=str(ROOT / "whisper_cache"))
        load_seconds = round(time.perf_counter() - start, 2)
    rows = []
    for clip in manifest["clips"]:
        path = AUDIO / clip["file"]
        if _sha256(path) != clip["sha256"]:
            raise ValueError(f"{clip['file']} does not match its manifest SHA-256")
        state = create_initial_state(query="")
        state["capture_request"] = {"source_path": str(path), "media_type": "audio"}
        start = time.perf_counter()
        result = agent.run(state)
        seconds = time.perf_counter() - start
        capture = result.get("capture_result")
        error = None if capture else "; ".join(result.get("errors") or ["no capture_result"])
        row = score_clip(clip, capture, seconds, error)
        rows.append(row)
        log(f"{clip['file']}: WER {row['wer']} (basic {row['wer_basic']}), "
            f"{row['transcribe_seconds']} s for {clip['duration_seconds']} s, review={row['review_required']}")
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                                text=True, check=True).stdout.strip()
    except Exception:
        commit = None
    return {
        "schema_version": SCHEMA, "synthetic_data_only": True,
        "disclosure": manifest.get("disclosure"),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "code_commit": commit,
        "whisper_model": os.getenv("WHISPER_MODEL", "base"), "device": os.getenv("WHISPER_DEVICE", "cpu"),
        "machine": {"platform": platform.platform(), "processor": platform.processor(),
                    "cpu_count": os.cpu_count()},
        "model_load_seconds": load_seconds,
        "normalization": {"wer": "whisper.normalizers.EnglishTextNormalizer on both texts",
                          "wer_basic": "lowercase, hyphen split, punctuation removed"},
        "summary": aggregate(rows), "clips": rows,
    }


def markdown_table(report: dict) -> str:
    """Per-clip and summary tables for docs, generated from the report (no hand-typed numbers)."""
    pct = lambda v: "-" if v is None else f"{v:.1%}"  # noqa: E731
    lines = ["| Clip | Noise | Duration (s) | Transcribe (s) | RTF | WER | WER basic | Min avg_logprob "
             "| Max no_speech | A1 review flag |", "|---|---|---:|---:|---:|---:|---:|---:|---:|---|"]
    for r in report["clips"]:
        lines.append(f"| `{Path(r['file']).name}` | {r['noise']} | {r['duration_seconds']:.1f} | "
                     f"{r['transcribe_seconds']:.1f} | {r['real_time_factor']:.2f} | {pct(r['wer'])} | "
                     f"{pct(r['wer_basic'])} | {r['min_avg_logprob']:.2f} | {r['max_no_speech_prob']:.3f} | "
                     f"{'yes' if r['review_required'] else 'no'} |")
    lines += ["", "| Group | Clips | Failed | WER (pooled) | WER basic (pooled) | Audio (s) | Transcribe (s) "
              "| RTF | A1 review flags |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name in ("all", "clean", "noisy"):
        s = report["summary"][name]
        lines.append(f"| {name} | {s['clips']} | {s['failed']} | {pct(s['wer_pooled'])} | "
                     f"{pct(s['wer_basic_pooled'])} | {s['audio_seconds']:.1f} | {s['transcribe_seconds']:.1f} | "
                     f"{s['real_time_factor']:.2f} | {s['review_required']} |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", type=Path, default=AUDIO / "manifest.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "logs" / "capture_eval")
    args = parser.parse_args(argv)
    report = run(args.manifest)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    out = args.output_dir / "capture_metrics_audio.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (args.output_dir / "capture_metrics_audio.md").write_text(markdown_table(report), encoding="utf-8")
    print(json.dumps({"output": str(out), **report["summary"]}, indent=2))
    return 0 if all(r["error"] is None for r in report["clips"]) else 1


if __name__ == "__main__":
    sys.exit(main())
