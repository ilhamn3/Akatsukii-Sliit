# Development extraction run, 26 Nov 2026

This is a **development** result, not the final benchmark. The final provider and
model are a team decision.

| | |
|---|---|
| Command | `python -m project.evaluation.extraction_metrics run --split eval --retry-errors` |
| Model | `openai/gpt-oss-120b` via Groq's OpenAI-compatible endpoint, through the shared `get_llm_client()` |
| Prompt | `extraction_v3`; not tuned on the dev or eval episodes |
| Episodes | Eval split: 50 synthetic narratives, 10 per machine (39 audio debriefs, 11 shift logs) |
| Scorer | `extraction_metrics/v1`, code commit `ba6b3d6` |

The first attempt failed on every episode because the old key was rejected (401). Those
rows were re-run with `--retry-errors`. Every score below is from the successful run.

Files:
- `extraction_metrics_eval.json`: the aggregate report the harness reads.
- `extraction_metrics_eval_per_episode.jsonl`: per-episode, per-field counts with matched pairs.
- `predictions_eval.jsonl`: drafts and predictions, which allow re-scoring without API calls.

All episodes are synthetic, and the files contain no credentials.
