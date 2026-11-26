# K7: extraction precision / recall / F1 for the benchmark harness

Owner: Kavibarath S. Reviewer: Ilham MM. Branch: `wp3/extraction-metrics`. Subtask 3.7.
Consumer: Kajanan's harness (WP7), which reads the JSON and per-episode JSONL below.
Code: `project/evaluation/extraction_metrics.py`. Every number comes from this code,
not from hand-typed values.

## What is scored

Agent 2 reads each synthetic narrative (`project/data/episodes/narratives.jsonl`).
The narrative is split into sentence segments; shift logs become `media_type: "text"`
captures with 0.0 offsets, since they have no timing. The draft is compared with
gold built from `ground_truth.jsonl`. That file is read only by the scorer and is
never given to an agent.

The gold contains **only what the narrative actually says**, taken from the
generator's `narrative_coverage`. A technician who skips a step or never lists the
tools does not cost the extractor recall.

| Field | Gold |
|---|---|
| `machine_type` | Catalogue ID. The prediction is resolved with the K5 resolver, so "conveyor two" counts as `conveyor_line` |
| `symptoms` | Mentioned symptoms, plus `alarm <code>` if the code was said. In spoken debriefs the first symptom is narrated as the fault's lay description, so the gold uses that text |
| `root_cause` | Always narrated |
| `steps` | Only `steps_mentioned` |
| `tools` | Only if the narrative lists them. This is detected in the text, because the generator doesn't record it. It matched the generator's literal tools line in 381/400 narratives; all 19 differences are filler words inside that line, and the token check handles them |
| `parts` | Only if `parts_mentioned` |
| `failed_attempts` | Every failed attempt, including other shifts' (all are narrated), scored on the action |
| `safety_warnings` | The spoken hazard remark ("watch out for the electrical energy") as the hazard name. Catalogue control texts are never narrated |

`machine_id` is not scored: it is not in the O1 field list or the human form.

**Matching.** Text is normalized first:
- lower case, punctuation removed;
- runs of number words become digits ("the four one oh alarm" -> `410`), matching `ALM-410`;
- light stemming, so "replaced" matches "replace";
- stop words and fillers removed.

Within a field, predictions and gold items are then paired one-to-one, greedily by
token Dice similarity:
- **Primary threshold 0.5.** Lists and paraphrases get partial credit for wording but
  no double counting; a repeated item is a false positive.
- **Exact (threshold 1.0)** is reported alongside, for transparency.

A failed extraction (provider error, or a draft that fails grounding) counts every gold
item of that episode as a miss and is listed in `failed_episodes`. No LLM judges
anything here.

**Sanity checks run in tests:**
- Feeding the gold back as the prediction scores F1 = 1.0 on all 400 episodes.
- An empty prediction gives recall 0.
- A real Agent 2 run on EP-0002 (using a scripted test client) is scored field by field.

## Splits

| Split | Episodes | Use |
|---|---|---|
| `dev` | The 12 in `samples.json` (first episode of each technician) | Prompt work only |
| `eval` | 50 = 10 per machine, seeded (`EVAL_SEED = 2026`), disjoint from dev (39 audio debriefs, 11 shift logs) | Reported numbers. **Do not tune prompts on these** |
| `all` | All 388 non-dev episodes | Optional larger run |

Extraction prompt v3 was written against one hand-made synthetic fixture, before
these episodes existed. It has not been tuned on dev or eval.

## Run it (live, shared provider)

The run uses the shared `get_llm_client()`, never a private client. It refuses to
report numbers if FakeLLM is configured. The final benchmark provider and model are a
team decision; the commands below are the Groq development setup from K2.

The steps:
1. Open the repository folder, activate the environment and set the provider:

   ```powershell
   Set-Location 'E:\Factory memory\Akatsukii-Sliit'
   . .\scripts\Activate-FactoryMemory.ps1
   $env:LLM_PROVIDER = 'openai'
   $env:OPENAI_BASE_URL = 'https://api.groq.com/openai/v1'
   $env:PRIMARY_LLM_MODEL = 'openai/gpt-oss-120b'
   Remove-Item Env:FACTORY_FAKE_LLM -ErrorAction SilentlyContinue
   ```

2. Copy the Groq key with its copy button. Then **type** (don't paste) this line:
   `$env:OPENAI_API_KEY = (Get-Clipboard -Raw).Trim()`

3. Check the connection, then run the eval split (50 API calls):

   ```powershell
   python -m scripts.diagnose_extraction_api --connection-only
   python -m project.evaluation.extraction_metrics run --split eval
   ```

   The narratives are synthetic team data, so sending them to the provider is fine.
   Never send confidential competition data without authorization.

If the run is interrupted, re-run the same command: finished episodes are kept. Add
`--retry-errors` to retry episodes that failed with a transient provider error.

Outputs go to `logs/extraction_eval/` (git-ignored):

| File | Content |
|---|---|
| `predictions_eval.jsonl` | One row per episode: `prediction`, full `draft` with evidence spans, `model`, `prompt_version`, `error` |
| `extraction_metrics_eval.json` | Aggregate report (below) |
| `extraction_metrics_eval_per_episode.jsonl` | Per-episode, per-field `tp`/`fp`/`fn` plus matched pairs, for bootstrap CIs |

Re-score saved predictions at a different threshold without new API calls:
`python -m project.evaluation.extraction_metrics --threshold 0.6 score --predictions logs/extraction_eval/predictions_eval.jsonl`

## Human extraction forms (human study)

The human study's `extraction_results.csv` (columns from `project/human_study/protocol.md`)
is scored with the **same** gold and matching. Multi-value cells may be split by new
lines or `;`:

```powershell
python -m project.evaluation.extraction_metrics score-human --csv project/human_study/extraction_results.csv
```

Per-episode rows carry `participant_code`.

## Output format (`extraction_metrics/v1`) for the harness

```json
{
  "schema_version": "extraction_metrics/v1",
  "system": "multi_agent_a2 | human",
  "synthetic_data_only": true,
  "split": "eval", "models": ["openai:openai/gpt-oss-120b"], "prompt_versions": ["extraction_v3"],
  "generated_at": "...", "code_commit": "...",
  "n_episodes": 50, "episodes": ["EP-...", "..."], "failed_episodes": [],
  "matching": {"method": "greedy one-to-one token Dice", "threshold": 0.5,
               "machine_type": "K5 resolver to catalogue ID"},
  "fields_scored": ["machine_type", "symptoms", "root_cause", "steps", "tools", "parts",
                    "failed_attempts", "safety_warnings"],
  "primary":     {"fields": {"<field>": {"tp": 0, "fp": 0, "fn": 0, "precision": 0.0, "recall": 0.0, "f1": 0.0}},
                  "micro": {"tp": 0, "fp": 0, "fn": 0, "precision": 0.0, "recall": 0.0, "f1": 0.0},
                  "macro_f1": 0.0},
  "exact_match": {"...": "same shape at threshold 1.0"},
  "micro_by_source": {"audio_debrief": {"...": "..."}, "shift_log": {"...": "..."}}
}
```

- The O1 headline is `primary.micro.f1` (target >= 0.85).
- `precision` is `null` when nothing was predicted. `f1` is `2TP/(2TP+FP+FN)`, which
  is `null` only when there was nothing to find and nothing was predicted.

## Status and limits

- The scorer, runner, splits and export are built and tested. **No live numbers are
  reported yet.** They need a run with the team's real model (the command above). Until
  then, no F1 is claimed.
- Matching on token overlap is a documented approximation of semantic equivalence. It
  can under-credit heavy paraphrase and over-credit items that share words.
- The narratives come from seeded templates. Scores on them will likely be higher than
  on real technician recordings.
