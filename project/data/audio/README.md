# Audio subset (speech-to-text path)

**All audio is synthetic:** Windows built-in computer voices reading synthetic narratives.
No real people were recorded.

12 clips in `clips/` (7.3 minutes in total, 16 kHz, 16-bit, mono WAV):
- All 5 machines, 7 technicians, two voices (David and Zira) at three speaking rates.
- 6 clean and 6 noisy. Noisy clips add broadband plant noise and a 100 Hz hum at 15 dB SNR.
- Every clip is a spoken-style debrief with fillers, jargon and self-corrections.

`manifest.json` lists each clip's episode, technician, voice, rate, noise, duration,
SHA-256 and the **reference transcript** (the narrative text), for word-error-rate scoring.
The episode's ground truth is in `project/data/episodes/ground_truth.jsonl` under the same
`episode_id`.

Transcribe one with Agent 1:

```
python -m project.agents.a1_capture --source project/data/audio/clips/EP-0001_clean.wav
```

Rebuild (Windows only): `python project/data/audio/make_audio.py`. The clip choice and
noise are seeded, but speech synthesis can differ slightly between Windows versions, so
rebuilt files may not be byte-identical. The committed clips are the reference set.
