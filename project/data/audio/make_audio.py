"""Build the audio subset for the speech-to-text path (WP2, subtask 2.4).

Picks 12 spoken-style episodes from project/data/episodes/narratives.jsonl, covering all
5 machines, and speaks them with the Windows built-in voices (System.Speech, offline,
no API key). Half of the clips get synthetic plant noise (broadband noise plus a 100 Hz
hum, 15 dB SNR) so Agent 1's quality flags have something to catch.

Writes into project/data/audio/:
  clips/EP-xxxx_clean.wav or EP-xxxx_noisy.wav   16 kHz, 16-bit, mono
  manifest.json  episode, technician, voice, rate, noise, duration, reference transcript, SHA-256

The reference transcript is the narrative text, so word error rate can be measured.
Speech synthesis output can differ slightly between Windows versions, so the WAV files
are not byte-reproducible on other machines. The clip choice and the noise are seeded.

ALL AUDIO IS SYNTHETIC (computer voices reading synthetic narratives).

Usage (Windows only):
  python project/data/audio/make_audio.py
"""
from __future__ import annotations

import array
import hashlib
import json
import math
import pathlib
import random
import subprocess
import sys
import tempfile
import wave

HERE = pathlib.Path(__file__).resolve().parent
NARRATIVES = HERE.parent / "episodes" / "narratives.jsonl"
CLIPS = HERE / "clips"
SEED = 24
N_CLIPS = 12
SAMPLE_RATE = 16000
SNR_DB = 15.0
VOICES = ["Microsoft David Desktop", "Microsoft Zira Desktop"]

PS_SCRIPT = r"""
param([string]$JobFile)
Add-Type -AssemblyName System.Speech
$jobs = Get-Content -Raw -Encoding UTF8 $JobFile | ConvertFrom-Json
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
foreach ($j in $jobs) {
  $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
  $s.SelectVoice($j.voice)
  $s.Rate = [int]$j.rate
  $s.SetOutputToWaveFile($j.path, $fmt)
  $s.Speak($j.text)
  $s.Dispose()
}
"""


def choose(rng, rows):
    """12 audio debriefs of 45-110 words: at least 2 per machine, no technician more than twice."""
    pool = [r for r in rows if r["source"] == "audio_debrief" and 45 <= len(r["narrative"].split()) <= 110]
    rng.shuffle(pool)
    picked, per_tech, per_machine = [], {}, {}
    for need_machine in (True, False):
        for r in pool:
            if len(picked) == N_CLIPS or r in picked or per_tech.get(r["technician_id"], 0) >= 2:
                continue
            if need_machine and per_machine.get(r["machine_type"], 0) >= 2:
                continue
            picked.append(r)
            per_tech[r["technician_id"]] = per_tech.get(r["technician_id"], 0) + 1
            per_machine[r["machine_type"]] = per_machine.get(r["machine_type"], 0) + 1
    return sorted(picked, key=lambda r: r["episode_id"])


def tts_text(text: str) -> str:
    return text.replace(" / ", " or ")


def speak(jobs):
    with tempfile.TemporaryDirectory() as tmp:
        job_file = pathlib.Path(tmp) / "jobs.json"
        script = pathlib.Path(tmp) / "speak.ps1"
        job_file.write_text(json.dumps(jobs), encoding="utf-8")
        script.write_text(PS_SCRIPT, encoding="utf-8")
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                        "-File", str(script), str(job_file)], check=True)


def add_noise(path: pathlib.Path, rng: random.Random):
    with wave.open(str(path), "rb") as w:
        params = w.getparams()
        samples = array.array("h", w.readframes(w.getnframes()))
    if sys.byteorder == "big":
        samples.byteswap()
    rms = math.sqrt(sum(s * s for s in samples) / max(len(samples), 1)) or 1.0
    noise_rms = rms / (10 ** (SNR_DB / 20))
    hum = noise_rms * 0.6
    broad = noise_rms * 0.8
    out = array.array("h", (
        max(-32768, min(32767, int(s + rng.gauss(0, broad) + hum * math.sin(2 * math.pi * 100 * i / params.framerate))))
        for i, s in enumerate(samples)))
    if sys.byteorder == "big":
        out.byteswap()
    with wave.open(str(path), "wb") as w:
        w.setparams(params)
        w.writeframes(out.tobytes())


def duration(path: pathlib.Path) -> float:
    with wave.open(str(path), "rb") as w:
        return round(w.getnframes() / w.getframerate(), 2)


def main():
    if sys.platform != "win32":
        print("make_audio.py needs Windows (System.Speech). The committed clips can be used as they are.")
        return 1
    rng = random.Random(SEED)
    rows = [json.loads(line) for line in NARRATIVES.read_text(encoding="utf-8").splitlines()]
    picked = choose(rng, rows)
    CLIPS.mkdir(parents=True, exist_ok=True)
    for old in CLIPS.glob("*.wav"):
        old.unlink()

    techs = sorted({r["technician_id"] for r in rows})
    plan = []
    for i, r in enumerate(picked):
        t = techs.index(r["technician_id"])
        noisy = i % 2 == 1
        plan.append({"row": r, "voice": VOICES[t % 2], "rate": (t % 3) - 1, "noisy": noisy,
                     "path": CLIPS / f"{r['episode_id']}_{'noisy' if noisy else 'clean'}.wav"})
    speak([{"text": tts_text(p["row"]["narrative"]), "path": str(p["path"]), "voice": p["voice"], "rate": p["rate"]}
           for p in plan])

    clips = []
    for p in plan:
        if p["noisy"]:
            add_noise(p["path"], rng)
        r = p["row"]
        clips.append({
            "file": f"clips/{p['path'].name}",
            "episode_id": r["episode_id"],
            "technician_id": r["technician_id"],
            "machine_type": r["machine_type"],
            "voice": p["voice"],
            "rate": p["rate"],
            "noise": f"plant noise, {SNR_DB:g} dB SNR" if p["noisy"] else "none",
            "duration_seconds": duration(p["path"]),
            "reference_transcript": r["narrative"],
            "sha256": hashlib.sha256(p["path"].read_bytes()).hexdigest(),
        })
    manifest = {
        "synthetic": True,
        "disclosure": "Computer voices (Windows System.Speech) reading synthetic narratives. No real recordings.",
        "sample_rate": SAMPLE_RATE, "channels": 1, "bits": 16, "seed": SEED,
        "clips": clips,
        "total_seconds": round(sum(c["duration_seconds"] for c in clips), 1),
    }
    (HERE / "manifest.json").write_bytes((json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    print(f"wrote {len(clips)} clips, {manifest['total_seconds']} s total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
