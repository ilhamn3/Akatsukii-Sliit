# Eval-split extraction run with prompt v5, 27 Nov 2026

This is the one eval-split run for prompt v5. v5 was developed on the 12 dev episodes
only, and no prompt change was made after seeing these results. It is still a
**development** result on synthetic data, not the final benchmark: the final provider
and model are a team decision.

| | |
|---|---|
| Command | `python -m project.evaluation.extraction_metrics run --split eval --retry-errors --wait-on-rate-limit 1200` |
| Model | `openai/gpt-oss-120b` via Groq's OpenAI-compatible endpoint, through the shared `get_llm_client()` |
| Prompt | `extraction_v5` (the default from PR #27), with A2's one corrective retry on invalid output (K8) |
| Episodes | Eval split: the same 50 synthetic narratives as the v3 run, 10 per machine (39 audio debriefs, 11 shift logs) |
| Scorer | `extraction_metrics/v1`, code commit `c49957d` |
| Failures | 0 of 50 (EP-0185 needed A2's corrective retry and is flagged for reviewers in its draft) |

**Result:** micro F1 **0.897** (P 0.869, R 0.927), macro F1 0.897, exact-match F1 0.683.

The run took most of a day because Groq's free tier allows 200k tokens a day. Rate-limited
calls were waited out and retried on the same episode; they produced no scores.

Files:
- `extraction_metrics_eval_extraction_v5.json`: the aggregate report the harness reads.
- `extraction_metrics_eval_extraction_v5_per_episode.jsonl`: per-episode, per-field counts with matched pairs.
- `predictions_eval_extraction_v5.jsonl`: the final row for each episode (drafts and
  predictions), which allows re-scoring without API calls. The superseded rate-limit rows
  from the local log are left out because they contain provider account details; scoring
  only uses the final row. Re-scoring this file with `score` reproduces the report exactly.

All episodes are synthetic, and the files contain no credentials or account IDs.
