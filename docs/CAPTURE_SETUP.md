# Agent 1: capture and transcription (K1)

Owner: Kavibarath S. Reviewer: Ilham MM.
Branch: `wp3/capture-transcription`. Subtasks: 3.1 and 3.2.

## Current implementation

- Local Whisper adapter, timestamped transcript segments, source SHA-256 and
  evidence IDs, and explicit quality flags.
- Video frame sampling using FFmpeg/ffprobe, with a configurable interval and cap.
- Typed capture request/result schemas and shared blackboard fields.
- Agent 1 registered in Pipeline A. On the K2 branch the graph continues to
  ExtractionAgent and requires an LLM; the standalone capture CLI stays capture-only.
  See `docs/EXTRACTION_SETUP.md`. Validation and persistence follow later.
- Existing `BaseAgent` timing, messages and JSONL trace logging are reused.
- CLI entry point and offline tests with a clearly synthetic Whisper response.

Local setup and actual-media smoke checks passed on 22 Nov 2026. The generated
audio was decoded by Whisper itself; no mocked transcription was used in those
smoke checks. The adapter tests do **not** measure speech-recognition accuracy.
Tharshanth's sample compatibility and Ilham's contract review remain acceptance
checks. Do not mark K1 as 100% yet.

## Windows setup

### E-drive storage policy

On this machine, use the project activation helper in every existing/new terminal:

```powershell
. .\scripts\Activate-FactoryMemory.ps1
```

It activates `.venv` and directs package/model caches and temporary files to
`E:\Factory memory\.local-data` through the `FACTORY_MEMORY_STORAGE` user setting.
This also handles terminals or Codex processes that inherited old C-drive settings.
For other machines, the helper defaults to a `.local-data` directory beside the
repository unless the user has configured `FACTORY_MEMORY_STORAGE`.

Storage locations on this machine:

| Data | Location |
|---|---|
| Python environment and FFmpeg tools | Repository `.venv` on E: |
| Whisper weights | Repository `whisper_cache` on E: |
| pip cache | `E:\Factory memory\.local-data\cache\pip` |
| Default `pip download` destination | `E:\Factory memory\.local-data\downloads` |
| Hugging Face model/dataset cache | `E:\Factory memory\.local-data\cache\huggingface` |
| Chroma default model cache | `E:\Factory memory\.local-data\cache\chroma` |
| Torch, Numba, uv, sentence-transformers caches | Named folders under `.local-data\cache` |
| New user-level temporary files | `E:\Factory memory\.local-data\tmp` |

The migration keeps legacy C-drive cache paths working through directory junctions
to E:. User environment settings apply to newly launched applications; the helper
applies them immediately in the current PowerShell session. Existing running apps
may need to be restarted to use the new temporary-directory settings.

The local migration report and previous environment settings are stored outside
Git under `E:\Factory memory\.local-data\migration`. Installer leftovers moved
to `.local-data\setup-leftovers` are preserved for now. Browser download locations
are configured separately by each browser.

### Python and media tools

Run commands from the repository root in PowerShell. The locally prepared `.venv`
uses Python 3.12; the system's default Python is 3.14. Use the explicit executable
below so commands use the correct environment. For capture, activate `.venv` as
shown below so Whisper can find this checkout's FFmpeg executables.

```powershell
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -m pip install -r project/requirements-capture.txt
.\.venv\Scripts\python.exe -m pytest -q
```

For another checkout, create `.venv` with an installed Python 3.12 first:

```powershell
py -3.12 -m venv .venv
```

If the PyPI transfer for PyTorch stalls, install the CPU build from PyTorch's
official index first, then install the capture requirements:

```powershell
.\.venv\Scripts\python.exe -m pip install 'torch==2.15.0+cpu' --index-url https://download.pytorch.org/whl/cpu
.\.venv\Scripts\python.exe -m pip install -r project/requirements-capture.txt
```

During this setup, the large `llvmlite==0.50.0` wheel was downloaded from the
Tsinghua University PyPI mirror after the default transfer stalled. Its contents
were verified against the official PyPI SHA-256
`c20595cc3a76e3c85140fdafbf9246c732ddf8e0e646ba2f4e4881f87567300d` before
installation. No persistent pip index settings were changed.

This checkout has FFmpeg and ffprobe 9.1 installed in `.venv/Scripts`. The Gyan
essentials build was obtained through the Windows-build link on
https://ffmpeg.org/download.html and checked against its published SHA-256:
`b9bbbb3acece000cbeec994f856649354e52c773807841e78908374b24d658fd`.
The binaries and original archive are ignored local environment files, not Git
assets. No system-wide PATH change was made.

Activate the environment in each new PowerShell terminal and verify the tools:

```powershell
. .\scripts\Activate-FactoryMemory.ps1
ffmpeg -version
ffprobe -version
```

For a fresh checkout, install an FFmpeg Windows build from the official download
page's links and put its `ffmpeg.exe` and `ffprobe.exe` into `.venv/Scripts`, or add
its existing `bin` directory to PATH.

Whisper requires FFmpeg even for audio. Video frame sampling additionally uses
ffprobe. No API key is needed for this agent. The first model load downloads
weights into ignored `whisper_cache/`; subsequent transcription runs locally.
This checkout already has the verified `base` model cached (SHA-256
`ed3a0b6b1c0edf879ad9b11b1af5a0e6ab5db9205f891f668f8b0e6c6326e34e`).
Reference: https://github.com/openai/whisper

## Run a recording

Replace the example source path with an actual recording you are permitted to use.

```powershell
. .\scripts\Activate-FactoryMemory.ps1
$env:WHISPER_MODEL = 'base'
$env:WHISPER_DEVICE = 'cpu'
.\.venv\Scripts\python.exe -m project.agents.a1_capture --source 'D:\samples\repair.wav'
.\.venv\Scripts\python.exe -m project.agents.a1_capture --source 'D:\samples\repair.mp4' --media-type video --output 'logs\video_capture.json'
```

The default output is `logs/capture_result.json`. Frames go into a unique directory
under `logs/capture_frames/`. Traces append to `logs/audit_trace.jsonl`. These runtime
artifacts are already ignored by Git. A failed run returns exit code 1 and prints
its error; it does not produce a new successful capture result. If reusing an output
filename, a JSON file from a previous successful run may still be present: check
the exit code rather than treating file existence as proof of success.

## Contract for A2 and the orchestrator

Input: `state['capture_request']`, validated as `CaptureRequest` from
`project.core.capture_schemas`:

```json
{
  "source_path": "D:/samples/repair.mp4",
  "media_type": "video",
  "language": "en",
  "frame_interval_seconds": 10,
  "max_frames": 12,
  "frame_output_dir": "logs/capture_frames"
}
```

Only `source_path` is required. Audio is the default media type.

Output: `state['capture_result']`, a JSON-compatible `CaptureResult` dictionary:

- `capture_id`, full `source_sha256`, `source_path`, `media_type`, `language`;
- `text` and `segments` with `evidence_id`, start/end offsets in seconds, text,
  raw Whisper diagnostics and per-segment `quality_flags`;
- `frames` with `evidence_id`, requested seek timestamp in seconds and image path;
- capture-level `quality_flags` and `review_required`.

For an in-process call, use `CaptureAgent().run(state)` rather than `process(state)`
so execution goes through the trace wrapper. The registered graph is also usable:

```python
from project.core.registry import build_graph
from project.core.state import create_initial_state

state = create_initial_state(query="")
state["capture_request"] = {"source_path": "D:/samples/repair.wav"}
result = build_graph("A").invoke(state)
if result.get("errors") or result.get("capture_result") is None:
    raise RuntimeError(result.get("errors"))
```

On failure, `capture_result` is cleared and the base wrapper records an error in
`errors`, `messages` and the JSONL trace. Downstream agents must check for a result
before extracting anything. A2 should carry segment evidence IDs/timestamps into
its draft record; do not invent timestamps for text that lacks them.

## Quality handling and limits

- Mark low average log probability (< -1.0), high no-speech probability (> 0.6)
  and repetitive output (compression ratio > 2.4). These are initial heuristic
  thresholds, not calibrated confidence or a measure of factual correctness.
- Missing diagnostics, empty segments and missing speech/timestamps are explicit
  review signals. Dubious text is retained for inspection, not silently removed.
- A clean flag set does not mean a repair is verified. A3 still validates the record.
- Frame selection is periodic, not semantic key-frame detection or OCR. Frames are
  the nearest decoded frames at requested seek offsets, capped at 12 by default.
- Each media subprocess has a 30-second timeout. Whisper inference itself is not
  yet bounded by a worker timeout; that hardening belongs to K8.
- Video with no audio currently fails transcription. Support for video-only
  ingestion would require a separate agreed contract.

The fixture `tests/fixtures/capture/whisper_response.json` is hand-authored synthetic
test data, not the result of a recorded speech-recognition run and not benchmark
ground truth. `test_capture.py` uses controlled transcription responses.
`test_capture_media.py` exercises real FFmpeg decoding with generated media and
skips if the tools are not on PATH.

## Verified local setup: 22 Nov 2026

- Python 3.12.14, openai-whisper 20250625, PyTorch 2.15.0+cpu, FFmpeg/ffprobe 9.1.
- Whisper `base` checkpoint downloaded and checksum-verified; CPU inference works.
- `python -m pip check`: no broken requirements.
- `python -m pytest -q` in the activated environment: 52 passed.

Actual Whisper and the registered Pipeline A were exercised using a locally
generated Windows text-to-speech sample, a video containing that audio and a blue
test frame, and three seconds of silence. No API key or external transcription
service was used.

| Smoke case | Observed result |
|---|---|
| Synthetic repair audio | 5 timestamped segments; expected repair narrative recovered |
| Synthetic repair video | 5 timestamped segments; JPEG frames at 0 and 10 seconds |
| Silence | Empty transcript; `no_speech_detected`; `review_required=true` |

All cases produced the expected agent handoff and completion trace. This is an
installation/integration check on synthetic media, not an extraction benchmark or
a claim about accent/noise robustness.

Local evidence is in ignored `logs/capture_smoke/`: `smoke_report.json`,
`smoke_trace.jsonl`, input WAV/MP4 files, result JSON and decoded frames.
To try the prepared audio in this checkout:

```powershell
. .\scripts\Activate-FactoryMemory.ps1
python -m project.agents.a1_capture --source 'logs\capture_smoke\synthetic_repair.wav'
```

The local smoke fixtures are not committed; teammates use their own recordings
or run the generated-media integration tests.

## Review and remaining acceptance work

1. Run on Tharshanth's provided samples when available, including unclear audio.
2. Check timestamps, failed attempts and uncertainty flags against those recordings;
   do not use adapter test counts as an accuracy score.
3. Have Ilham review the additive capture schema and blackboard handoff.
4. Update the tracked work plan and PR log with actual evidence, run
   `python scripts/progress.py --write`, and submit K1 for review.

Planning note: the existing README milestone dates differ from the supplied
proposal/work plan. This component follows the work plan: K1 by 24 Nov 2026 and
MVP submission by 29 Nov 2026. The team should reconcile the general README dates.
