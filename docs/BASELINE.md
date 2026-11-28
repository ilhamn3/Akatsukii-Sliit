# Single-Agent Baseline & Equal Tuning Effort Log (`docs/BASELINE.md`)

This document tracks prompt versions and dev-split evaluation scores for both the **Single-Agent Baseline** (`project/benchmark/baseline_single_agent.py`) and the **Multi-Agent System** (`project/core/api.py`), demonstrating equal tuning effort on `dev_*.jsonl` ONLY (never `test_*.jsonl`).

## 1. Single-Agent Baseline Prompt Versions

| Version | Prompt File | What Changed | Dev Behaviour Match | Dev Top-1 RC | Dev Top-3 RC | Dev Safety Cov | Source |
|---|---|---|---:|---:|---:|---:|---|
| `baseline_v1` | `project/benchmark/prompts/baseline_v1.md` | Initial consolidated prompt: hazard catalogue reference, top-3 ranked hypotheses with `cause_family`, strict `evidence_id` grounding, explicit unsafe-shortcut refusal + required control text | 95.00% (19/20) | 70.00% | 70.00% | 83.33% | `results/bench/dev_dry/report.md` (`gemini-3.5-flash-lite`, `minimal`, `temp=0.0`) |

## 2. Multi-Agent System Dev Iterations (from Git History & `docs/DEV_RESULTS.md`)

| Version | PR / Commit | What Changed | Dev Behaviour Match | Dev Top-1 RC | Dev Safety Cov | Source |
|---|---|---|---|---|---|---|
| `v1` | `I3` / `I5a` (`924e914`, `6cf8f06`) | Initial `diagnostic_v1.md`, `orchestrator_v1.md`, real `HybridRetriever` wiring, `SafetyAgent` | - | - | - | Git history (`I5a`) |
| `v2` | `I5b` (`d75b834`) | Safety-control-aware planning, LLM-guided revise loop, `SR-X05` unsafe-request refusal | - | - | - | Git history (`I5b`, partial dev `n=10`) |
| `v3` | `I6` (`929ed15`) | Deterministic catalogue control insertion in plan/revise | 55.00% (11/20) | 40.00% | 60.00% | `docs/DEV_RESULTS.md` (`I6`) |
| `v4` | `wp5/safety-llm-fix` (`I6b`, pending merge into `main`) | Evidence-first `diagnosed_cause` + `safety`/`other` concern split in `SafetyAgent`, `SR-X05` v2 (`main` @ `5221b84` in `dev_dry` is pre-`I6b`: 65.00% beh / 40.00% RC) | 80.00% (Run 1) / 90.00% (Run 2) | 73.33% / 86.67% | 45.00% / 56.67% | `docs/DEV_RESULTS.md` (`I6b` branch) |

## 3. Free-Tier Gemini Benchmark Run Recipe (`wp7/quota-aware-harness`)

Because Gemini free-tier enforces both per-minute (RPM) and per-day (RPD) quotas, `project/benchmark/harness.py` executes queries **paired** (`multi` then `baseline` back-to-back) in a fixed seeded cross-tier shuffle (`--seed 42`):

```bash
# Step through frozen test set safely under free-tier limits (resumable across days)
python -m project.benchmark.harness \
  --system both --split test --runs 3 --final \
  --model gemini-3.5-flash-lite --thinking minimal \
  --rpm 10 --max-calls 200 --resume \
  --out results/bench/final
```

- `--rpm 10` (default): spaces LLM calls across all agents (`project/core/llm.py`) to stay under 10 requests/minute.
- `--max-calls N`: stops cleanly with exit code `0` after `N` LLM calls; re-run the exact same command with `--resume` to continue.
- **Daily-quota 429 (RPD):** stops immediately without retrying/waiting, marks the interrupted query `degraded=True` (so `--resume` re-runs it cleanly), and reports the midnight Pacific (`00:00 America/Los_Angeles`) reset time.
