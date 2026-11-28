# K7: extraction precision / recall / F1 for the benchmark harness

Owner: Kavibarath S. Reviewer: Ilham MM. Branch: `wp3/extraction-metrics`. Subtask 3.7.
Consumer: Kajanan's harness (WP7), which reads the JSON and per-episode JSONL below.
Without `--extraction-json`, `project/benchmark/harness.py` loads the newest committed
report, `project/evaluation/results/<YYYY-MM-DD_...>/extraction_metrics_eval*.json`
(currently the v5 run, micro F1 0.897); a test in `tests/benchmark` checks this.
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

If the provider rejects the key (401), the run stops after the first episode instead of
retrying every one. A run where every episode fails writes no metrics.

Outputs go to `logs/extraction_eval/` (git-ignored). Copy a run you want to share into
`project/evaluation/results/<date>_<split>_<model>_<prompt>/` with a README:

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

## Development result (26 Nov 2026)

Run details:
- **Model and split:** `openai/gpt-oss-120b` via Groq, prompt `extraction_v3`, eval split (50 episodes).
- **Scorer:** `extraction_metrics/v1`, commit `ba6b3d6`.
- **Files:** `project/evaluation/results/2026-11-26_eval_groq-gpt-oss-120b_v3/`.
- **Status:** a development configuration. The final benchmark model is a team decision.

Three episodes failed Agent 2's own checks and count as complete misses:
- EP-0150 and EP-0199: a quote didn't match its segment.
- EP-0336: invalid structured output.

| | Precision | Recall | F1 |
|---|---:|---:|---:|
| **Micro, threshold 0.5 (O1 headline)** | 0.848 | 0.818 | **0.833** |
| Macro F1 over the 8 fields | | | 0.829 |
| Micro, exact normalized match | 0.617 | 0.596 | 0.606 |
| Audio debriefs (39) | 0.838 | 0.815 | 0.826 |
| Shift logs (11) | 0.898 | 0.832 | 0.863 |

| Field | P | R | F1 |
|---|---:|---:|---:|
| machine_type | 0.881 | 0.740 | 0.804 |
| symptoms | 0.899 | 0.795 | 0.844 |
| root_cause | 1.000 | 0.760 | 0.864 |
| steps | 0.827 | 0.889 | 0.857 |
| tools | 0.889 | 0.738 | 0.807 |
| parts | 0.596 | 0.778 | 0.675 |
| failed_attempts | 1.000 | 0.875 | 0.933 |
| safety_warnings | 0.824 | 0.875 | 0.848 |

The headline is **0.833, just below the O1 target of 0.85**, on templated synthetic
narratives. Expect lower on real recordings. This v3 result stays as recorded; the later
v5 run on the same split is reported separately below.

### Error analysis

These findings come from inspecting the eval misses. The reported numbers above were
**not** changed after this inspection.

1. **Root cause left empty (9 episodes).** The narratives state the cause plainly
   ("turned out, clogged pump suction strainer"), but prompt v3's strict
   "explicit causal statement" rule kept it null. This is an extractor recall gap.
   Fixing the prompt must be done on the **dev** split, not eval.
2. **Machine type in shift logs (7 of 10 misses).** Shift logs name the machine only by
   asset code (`CMP-02:`, `HYD-02:`, `CNC-03:`), and v3 deliberately doesn't infer a
   category from asset IDs. Mapping these prefixes in K5 would be a resolver change,
   not an extraction change. The data's prefixes (`CNV`/`CMP`/`HYD`/`CNC`) also differ
   from K5's provisional asset convention, which should be aligned with the team's data.
3. **Parts precision is probably understated.** 15 of the 19 unmatched predicted parts
   are words that appear in a narrated step ("replace the thermocouple"). The gold only
   counts parts from the generator's separate "parts" line. A rule such as "parts named
   in a narrated replace/install step also count" needs team agreement and a check on
   the dev split before any re-scoring. Until then, v1 stands.

## Prompt development on the dev split (26 Nov 2026)

Goal: fix the root causes v3 left empty, using **only** the 12 dev episodes. The eval
split stays untouched until a prompt is final. The prompt is selected with
`EXTRACTION_PROMPT`; the default is now `extraction_v5` (set `extraction_v3` to
reproduce the earlier results). Every prompt version stays fixed once it has been run,
so recorded results always name an exact prompt.

| Prompt | Change | Dev result |
|---|---|---|
| v3 (baseline, with the K8 retry) | none | micro F1 **0.887** (P 0.870, R 0.905), root cause F1 0.957, 0 failures |
| v4 | Any wording presenting a cause as the finding counts ("problem was", "turned out", "Cause:"); use the corrected statement after self-corrections; quotes must be exact. Unstated or hedged causes stay null | **Invalid run.** 8 of 12 calls hit Groq's free-tier daily token limit (429, 200k tokens/day). On the 4 episodes both prompts completed, root cause improved (EP-0003: null -> "worn check ring") but steps fell (0.92 -> 0.74) |
| v5 | v4, plus: values are cleaned of fillers while quotes stay exact (v4 had leaked "you know", "uh" into values); an action that didn't help goes only in failed_attempts, never also in steps | micro F1 **0.874**, root cause F1 **1.000**, 0 failures, all 12 episodes (27 Nov 2026) |

The v4 step losses came from two general issues, not episode-specific wording: fillers
copied into values, and a failed attempt repeated as a step. v5 addresses both.

**v5 vs v3 on dev** (`compare`, same 12 episodes, same model, no failures in either run):

| Field | v3 | v5 | Delta |
|---|---|---|---|
| root_cause | 0.957 | 1.000 | +0.043 |
| steps | 0.943 | 0.935 | -0.009 |
| symptoms | 0.745 | 0.720 | -0.025 |
| tools | 0.966 | 0.933 | -0.032 |
| parts | 0.800 | 0.750 | -0.050 |
| machine_type, failed_attempts, safety_warnings | unchanged | unchanged | 0 |
| micro F1 | 0.887 | 0.874 | -0.014 |

- **Root cause (the goal):** EP-0003 was null under v3 and is now "worn check ring";
  EP-0004, EP-0005 and EP-0025 match the stated diagnosis more closely.
- **Failed attempts stay out of steps:** EP-0025's "raised the setpoint" appears only in
  failed_attempts, and v4's filler leaks are gone.
- **The losses are single items spread over episodes, not a repeated pattern:** two
  logistics actions listed as steps ("grab drive chain from stores", "grab thermocouple
  from stores"), one extra tool (EP-0006 "feeler gauge"), one extra part (EP-0009 "purge
  compound"), one shortened part name ("check ring" vs "check ring set"), and symptoms
  split or merged differently in EP-0001 and EP-0012. With one run of 12 episodes, part of
  this is likely run-to-run variation; it was not tuned away, to avoid fitting the dev set.

**Decision:** v5 is the default, because it fixes the targeted root-cause misses and
the remaining differences are small and unpatterned. This is a dev-split result only.
It is not the reported benchmark number.

The example causes in v4/v5 were chosen so they don't resemble any catalogue root
cause (token Dice < 0.5, enforced by a test). My first draft's examples were too close
to real causes and were replaced before any run.

**Tooling added after the invalid run:**
- Quota, timeout, network and auth failures are labelled `provider` failures, separate
  from `output` failures (invalid or ungrounded model output).
- Reports list `provider_failed_episodes`, and `compare` refuses any run that has them.
- A rate limit now stops the run, like a rejected key (exit code 3). Finished episodes
  are kept for `--retry-errors`.
- Each prediction row records the call's `tokens`, for budgeting the provider quota.
- `run --wait-on-rate-limit MINUTES` waits as long as the provider suggests, retries the
  same episode and continues, up to that total waiting budget (default 0: stop at once).
- `score` uses the last row per episode, as `run` does. Before this fix, re-scoring a
  log that still held superseded failed attempts counted those episodes twice. A test
  re-scores every committed results folder and requires its reported numbers exactly.

## Eval result with prompt v5 (27 Nov 2026)

The one eval-split run for v5, on the same 50 episodes and model as the v3 run. v5 was
fixed before this run, and no prompt change was made after seeing it.

Run details:
- **Model and split:** `openai/gpt-oss-120b` via Groq, prompt `extraction_v5`, eval split (50 episodes).
- **Scorer:** `extraction_metrics/v1`, commit `c49957d`. Includes A2's one corrective retry (K8).
- **Files:** `project/evaluation/results/2026-11-27_eval_groq-gpt-oss-120b_v5/`.
- **Failures:** 0 of 50. EP-0185 needed the corrective retry and carries a reviewer flag.

| | Precision | Recall | F1 |
|---|---:|---:|---:|
| **Micro, threshold 0.5 (O1 headline)** | 0.869 | 0.927 | **0.897** |
| Macro F1 over the 8 fields | | | 0.897 |
| Micro, exact normalized match | 0.662 | 0.706 | 0.683 |
| Audio debriefs (39) | 0.861 | 0.938 | 0.898 |
| Shift logs (11) | 0.912 | 0.874 | 0.893 |

| Field | v3 F1 | v5 P | v5 R | v5 F1 |
|---|---:|---:|---:|---:|
| machine_type | 0.804 | 0.909 | 0.800 | 0.851 |
| symptoms | 0.844 | 0.887 | 0.839 | 0.862 |
| root_cause | 0.864 | 1.000 | 0.980 | **0.990** |
| steps | 0.857 | 0.873 | 0.965 | 0.917 |
| tools | 0.807 | 0.903 | 1.000 | 0.949 |
| parts | 0.675 | 0.604 | 0.889 | 0.719 |
| failed_attempts | 0.933 | 1.000 | 1.000 | 1.000 |
| safety_warnings | 0.848 | 0.800 | 1.000 | 0.889 |
| **micro** | **0.833** | 0.869 | 0.927 | **0.897** |

**What the improvement comes from.** The v3 run predates K8's corrective retry, and its
3 failed episodes counted as complete misses. Scored on only the **47 episodes both runs
completed**, v5 still improves micro F1 from 0.864 to 0.890 and root cause from 0.894 to
0.989. On those 47, symptoms (-0.014), safety warnings (-0.028) and parts (-0.001) are
slightly lower. So most of the root-cause gain is from the prompt, and the rest of the
full-split gain is from the retry removing failures.

The headline is **0.897, above the O1 target of 0.85**, with the caveats that still
apply: templated synthetic narratives, one run, a development provider, and parts
precision (0.604) as the weakest field. Expect lower on real recordings.

Still open from the v3 error analysis: machine type from shift-log asset codes (K5) and
the parts gold rule (team decision).

Each episode costs roughly 3k tokens. Groq's free tier allows 200k tokens per day, so
dev (12) and eval (50) runs may need separate days, or the team's agreed provider
account.

## Limits
- Matching on token overlap is a documented approximation of semantic equivalence. It
  can under-credit heavy paraphrase and over-credit items that share words.
- The narratives come from seeded templates. Scores on them will likely be higher than
  on real technician recordings.
