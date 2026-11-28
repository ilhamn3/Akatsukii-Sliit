# Dev-split extraction runs, 26-27 Nov 2026

The 12 dev episodes (`samples.json`) extracted by Agent 2 with prompts v3 and v5. These
are the **development** runs used to choose v5 (docs/EXTRACTION_METRICS.md), not a
benchmark result. They are committed so the later evaluations that read them can be
reproduced from the repository (requested in the #48 review):

- `project/evaluation/retrieval_pipeline_eval.py` (retrieval on pipeline-ingested episodes)
- `project/evaluation/entity_eval.py` (K5 rules developed on these drafts)
- `project/evaluation/validation_eval.py` (`dev_cosine_diagnostic`, the split the A3
  duplicate threshold was chosen on)

| | |
|---|---|
| Command | `python -m project.evaluation.extraction_metrics run --split dev --retry-errors` with `EXTRACTION_PROMPT=extraction_v3` or `extraction_v5` |
| Model | `openai/gpt-oss-120b` via Groq, through the shared `get_llm_client()` |
| Result | v3 micro F1 0.887, v5 0.874; root cause F1 0.957 -> 1.000; 0 failures in both |

Files per prompt: `predictions_dev_<prompt>.jsonl` (final row per episode: drafts and
predictions), `extraction_metrics_dev_<prompt>.json` and its per-episode JSONL. The local
logs also held rate-limited attempts; those rows are left out because they contain
provider account details, and scoring uses only the final row. A test re-scores both runs
to the committed reports.

All episodes are synthetic, and the files contain no credentials or account IDs.
