# K4: Agent 3 validation and review-queue backend

Owner: Kavibarath S. Reviewer: Ilham MM. Branch: `wp3/validation-review-queue`.
Subtasks 3.4 and 3.6. Stacked on K3 (PR #12, `wp4/knowledge-store`) because K4
publishes approved records through the K3 store. Retarget to `main` once #12 merges.

## Flow

```
A1 Capture -> A2 Extraction (DraftRepairRecord, always unverified)
           -> A3 ValidationAgent: map, check, dedup, score -> ReviewQueue (pending)
           -> human approve()/reject() -> VerifiedEpisode -> KnowledgeStore (K3)
```

Pipeline A in `config.yaml` now ends with `ValidationAgent`. It writes
`validation_report` and `review_item_id` to the blackboard. Re-running the identical
draft returns the existing item (`REVIEW_ITEM_EXISTS`) and creates no new item.

## Review policy: no automatic verification

Every draft is queued for a human decision. `docs/Workflow.md` sketches automatic
promotion when C >= 0.75. That is deliberately **not** enabled, for three reasons.
The score is an uncalibrated heuristic. No representative labelled data exists yet.
A verified record must name a real reviewer. Low-confidence or conflicting items get
`priority = low_confidence` and are listed first. Turning on automatic promotion is
a team decision for Ilham/Kajanan once the score has been evaluated.

A caller cannot bypass review:
- `DraftRepairRecord` only allows `status="draft"`, `review_required=True` and
  `confidence=None`.
- `approve()` re-derives the record and issues from the stored **draft**. It never
  trusts the stored report, its score, its priority or any label, so a forged
  "clean" report is still blocked.
- Only `approve()` creates a `VerifiedEpisode`, and it requires a reviewer identity.
  The backend records that identity but **does not authenticate it**. The UI or
  service calling it must authenticate the reviewer first.

## What A3 checks (rules only; no LLM critic, no API key)

| Code | Severity | Meaning |
|---|---|---|
| `missing_required_fact` | blocking | RepairRecord needs machine_type, symptoms, steps, root_cause; FailedAttempt needs outcome and why_failed. Never filled with placeholders. |
| `ungrounded_fact` | blocking | An extracted fact has no evidence span. |
| `quantity_not_in_quote` | blocking | A number or unit (psi, bar, Nm, mm, rpm, ...) in a fact is not literally in its cited quote, e.g. "30 bar" became "300 bar" or "30 Nm". Spelled-out numbers ("thirty") are flagged for review. |
| `schema_violation` | blocking | A value violates RepairRecord (e.g. a step shorter than 3 characters). |
| `duplicate_source` | blocking | The same recording (source SHA-256) is already pending/approved or verified. Reject the item; corrections cannot resolve this. |
| `possible_duplicate` | warning | Symptom cosine >= 0.55 (chosen on the dev split) against verified episodes or pending items with the same normalized machine label; lists the matching records. A warning that does not raise priority, because at this threshold it mostly means the fault is already known (a recurrence). |
| `failed_attempt_conflicts_with_step` | warning, raises priority | The same action is recorded as both failed and completed. |
| `steps_out_of_source_order` | warning | Steps listed in a different order from the narration, by span start time and then A1 segment index (text captures have no timing). Steps cited from one segment cannot be ordered and are not flagged. |
| `repeated_fact`, `orphan_evidence`, `outcome_unknown`, `capture_quality_flags`, `offline_fixture_output`, `semantic_dedup_unavailable` | warning | Shown to the reviewer and recorded as acknowledged on approval. |

K5 adds blocking `machine_type_unresolved` / `machine_type_ambiguous` /
`asset_type_conflict` checks and an `asset_id_unresolved` warning. These are
re-checked on the corrected values at approval. Duplicate detection compares
canonical catalogue machine IDs, so "conveyor" and "conveyor belt" are compared.
See `docs/ENTITY_GRAPH.md`.

## Confidence (`rule_based_v1_uncalibrated`)

```
C = 0.45 x required_completeness        (share of the 4 required facts present)
  + 0.15 x failed_attempt_completeness  (attempts with outcome and why_failed)
  + 0.25 x grounding                    (facts without grounding/quantity problems)
  + 0.15 x capture_quality              (0.5 if capture flagged unclear audio)
```

The components are stored in every report so a reviewer can see why a score is low.
The weights are development defaults, not fitted. The synthetic fixture draft
(missing root cause and why_failed) scores 0.7375. A complete, grounded draft scores
1.0. This is not a probability of correctness. `RepairRecord.confidence` stores this
pre-review extraction score. Threshold: `system.confidence_threshold_review` (0.75).
Duplicate threshold: `system.duplicate_similarity_threshold` (0.55 since 28 Nov, chosen on
the dev split; previously 0.90).

**Calibration check (28 Nov, 50 eval drafts).** The score tracks completeness, not
correctness. 42 drafts scored 1.0 and 37 of them still had at least one extraction error
(mean per-draft F1 0.898); the 8 drafts between 0.75 and 1.0 had mean F1 0.853; none
scored below 0.75. So a lower score does mean a somewhat weaker draft, but a 1.0 is no
assurance. The weights were not refitted: the ranking is the useful part, and every draft
is reviewed anyway.

## Draft -> RepairRecord mapping

Facts map one-to-one and keep their order, so span paths such as `steps.2` or
`failed_attempts.0.action` keep their original draft meaning in
`provenance.evidence_spans`. `RepairStep.timestamp`/`evidence_id` come from the
earliest cited segment. Episode and primary evidence IDs are deterministic:
`EP-<first 16 hex of SHA-256>` / `EV-<same>`. `source` is the capture media type.
The overall outcome goes to `VerifiedEpisode.outcome`. Reviewer-entered values
are listed in `provenance.reviewer_supplied_fields` and the APPROVED audit event.

## Backend API for the review page (Kajanan)

```python
from project.store.knowledge_store import KnowledgeStore
from project.store.review_queue import ReviewQueue
from project.agents.a3_validation import ReviewBlocked

queue = ReviewQueue(store=KnowledgeStore())       # storage root from activation script
items = queue.list_items(status="pending")       # low_confidence first, then oldest
item = queue.get(items[0].item_id)               # ReviewItem: draft, report, status, decision
item.report.issues, item.report.confidence, item.report.candidate_record

try:
    episode = queue.approve(item.item_id, reviewer="<authenticated user id>",
                            trace_id="TR-UI-0001", note="optional",
                            corrections={"root_cause": "...",
                                         "failed_attempts.0.why_failed": "..."})
except ReviewBlocked as blocked:
    blocked.issues   # list of unresolved ValidationIssue; item stays pending

queue.reject(item.item_id, reviewer="<id>", reason="Duplicate narration", trace_id="TR-UI-0002")
queue.events(item.item_id)   # ENQUEUED / APPROVE_FAILED / APPROVED / REJECTED with actor, time, trace
```

Correction keys are RepairRecord field names (`machine_type`, `machine_id`,
`symptoms`, `steps` (strings or RepairStep dicts), `tools`, `parts`, `root_cause`,
`failed_attempts`, `safety_warnings`), plus `outcome`, or
`failed_attempts.<i>.<action|outcome|why_failed>`. A blocking issue is resolved only
by correcting its field. To accept an extracted value flagged by a quantity check,
the reviewer re-enters it, which records it as reviewer-supplied. Other keys
(e.g. `status`) are refused.

Decisions are serialized (`BEGIN IMMEDIATE`). Approved and rejected items are final.
On approval, the verified episode goes through Agent 4 (K5 `GraphAgent`, which
resolves catalogue entities) and is written to the store **before** the item is
marked approved. `approve()` returns the stored, canonical episode. If the store write fails, the item stays pending and an
APPROVE_FAILED event records the error type. The queue lives in
`<storage root>/review/review_queue.sqlite3`, separate from the verified store.

### What the review page must show (`project/store/review_view.py`)

The Streamlit page (`project/ui/pages/1_Review_Queue.py`) uses these helpers, so the
rules are unit-tested without a UI:

- **Every fact approval publishes.** `fact_sections` lists all `ExtractedRepair`
  fields in order, including empty ones ("none extracted") and unknown failed-attempt
  children. A test fails if the schema gains a field the page would not show.
- **What the reviewer must know about the draft:** `review_reasons` and
  `missing_fields`, with banners for `offline_fixture_output` (fixture, not a model
  run) and `extraction_retried_after_invalid_output` (K8 retry). Confidence is always
  shown with its method and as "not a probability".
- **Corrections for blocking issues.** `correction_targets` maps each blocking issue
  to the key A3 accepts: scalars and `failed_attempts.<i>.<child>` by name, list
  fields as a whole list (an issue at `steps.2` is fixed through `steps`, prefilled
  with the current values). `build_corrections` leaves blank inputs out, so their
  issue stays blocking; nothing is filled in for the reviewer. Issues with no field
  (e.g. `duplicate_source`) can only be rejected (`uncorrectable`).
- **A named reviewer.** The page has no default reviewer; Approve and Reject stay
  disabled until a name is entered, because it is written to `verified_by` and the
  audit events.

`tests/ui/test_review_page.py` runs the page against a real `ReviewQueue` and
`KnowledgeStore` (only storage is redirected to `tmp_path`): approve with a named
reviewer, approve a blocked draft only after both corrections, and reject a duplicate.

## Commands

```powershell
Set-Location 'E:\Factory memory\Akatsukii-Sliit'
. .\scripts\Activate-FactoryMemory.ps1
python -m pytest tests/ingestion/test_validation.py tests/store/test_review_queue.py -q
```

Synthetic end-to-end demo in a dedicated folder (never the real store):

```powershell
$demo = Join-Path $env:FACTORY_MEMORY_STORAGE 'review-demo'
python -m project.agents.a2_extraction --capture tests/fixtures/extraction/capture.json --fixture-output tests/fixtures/extraction/model_output.json --output "$demo\draft.json"
python -m project.agents.a3_validation --draft "$demo\draft.json" --queue-db "$demo\queue.sqlite3" --store-db "$demo\repairs.sqlite3" --local-files-only
python -m project.store.review_queue --queue-db "$demo\queue.sqlite3" list
python -m project.store.review_queue --queue-db "$demo\queue.sqlite3" --store-db "$demo\repairs.sqlite3" --local-files-only approve <RQ-ID> --reviewer synthetic-demo-reviewer --corrections "$demo\corrections.json"
```

## Observed development validation (25 Nov 2026)

- 150 repository tests and pip check passed. The 24 new K4 tests use real A2 drafts,
  real SQLite/FAISS stores and a test-only keyword embedder.
- Cached-MiniLM synthetic run: the fixture draft was queued as `low_confidence`
  (C = 0.7375, two blocking missing facts). Approval without corrections was refused
  and audited. Approval with reviewer-supplied root cause and why_failed produced a
  verified episode. A paraphrased symptom query then found it (cosine about 0.81),
  carrying `reviewer_supplied_fields`.

These are smoke checks on a synthetic fixture, not an accuracy evaluation.

## Agent 3 on the 50 eval drafts (28 Nov 2026)

How many real problems A3 flags in real Agent 2 output. The input is the 50 committed v5
eval-split drafts (Groq `openai/gpt-oss-120b`, 27 Nov), so **no model was called** (no
Gemini, no Groq). Sections 1-3 are the **first measurement** (code `11c0b41`, duplicate
cosine 0.90), unchanged. Section 4 gives the fixes made after it and the re-run; the
committed JSON is the re-run. Nothing was tuned on these drafts. All data is synthetic.

| | |
|---|---|
| Command | `python -m project.evaluation.validation_eval --output-dir project/evaluation/results/2026-11-28_validation_eval` |
| Results | `project/evaluation/results/2026-11-28_validation_eval/validation_eval.json` (re-run, code commit `eddb013`) |
| Truth | Low confidence: K7 per-episode errors (fp/fn per field) against the narrative gold. Contradictions: injected. Duplicates: generator `fault_id` labels |
| Embedder | Cached MiniLM (`HF_HUB_OFFLINE=1`) for the semantic checks |

### 1. Low confidence vs real extraction errors

A3 put **13 of 50** drafts in `low_confidence`. 44 drafts have at least one K7 error
(a missed or unmatched item in any field). Since the gold is strict, most drafts have one.

| Truth | Flagged and wrong | Flagged, no K7 error | Missed | Not flagged, correct | Precision | Recall |
|---|---:|---:|---:|---:|---:|---:|
| Any field error | 12 | 1 | 32 | 5 | 0.92 | 0.27 |
| Error in a required field (machine type, symptoms, steps, root cause) | 12 | 1 | 28 | 9 | 0.92 | 0.30 |

By kind of error:
- **Missing or unresolvable facts: all caught.** Machine type wrong or missing: 10 of 10
  drafts flagged (`missing_required_fact` or `machine_type_unresolved`; mostly shift logs
  naming only an asset code). Root cause missing: 1 of 1 flagged. Every
  `missing_required_fact` flag on a required field was a fact the narrative does state.
- **Wrong or extra values: not caught.** Errors only in steps, symptoms, parts, tools or
  warnings (e.g. an extra "grab X from stores" step, a merged symptom) were never the
  reason for a flag; the 5 flagged drafts with such errors were flagged for their machine
  type. A3 checks completeness and grounding, not whether a grounded value is right, so
  these need the human reviewer.
- The one flag without a K7 error (EP-0293) is a failed attempt with no stated reason
  (`why_failed`), which K7 does not score. It is a real gap the reviewer must fill.

### 2. Contradictions (injected, one per draft copy)

| Injected problem | Expected A3 code | Detected | False alarms on the 50 clean drafts |
|---|---|---:|---:|
| A failed attempt's action also listed as a completed step | `failed_attempt_conflicts_with_step` | 14 / 14 | 1 (a real one, below) |
| A step value gains a quantity ("to 87 psi") its quote lacks | `quantity_not_in_quote` | 50 / 50 | 0 |
| A tool with no evidence span | `ungrounded_fact` | 50 / 50 | 0 |
| Steps reversed against narration order | `steps_out_of_source_order` | **0 / 50** | 0 |

- The one natural `failed_attempt_conflicts_with_step` on the clean drafts is a **true
  error**: in EP-0351 the model listed "added more coolant to the tank" both as a failed
  attempt and as the first completed step, although the narrative says it didn't help.
- **The step-order check cannot work on text captures.** It compares the start times of
  the steps' evidence spans, and text narratives (the K7 adapter) give every span
  `start_seconds = 0`. It only works on timestamped audio and video. A fix would order by
  segment index when times are equal.

### 3. Duplicates

- **Exact re-submission** (same recording, through a real `ReviewQueue`): 0 of 50 flagged
  on first submission, **50 of 50** flagged `duplicate_source` (blocking) on re-submission.
- **Semantic** (`possible_duplicate`, warning): a store of the 400 gold episodes, with and
  without each draft's own episode. A3 flagged 3 of 50 drafts either way. All 9 matched
  IDs were **other episodes of the same fault**; none was a different fault. The draft's
  own episode was found 0 times.
- Why: in the gold store, every episode of one fault has identical symptom text (the
  catalogue's), so symptoms alone cannot tell "the same repair recorded twice" from
  "the same fault on another day". That is why A3 makes this a warning, not a block.
- Cosine diagnostic (draft symptoms vs gold, same machine), not used to set anything:

  | Pair | n | Min | Median | Max | At or above 0.90 |
  |---|---:|---:|---:|---:|---:|
  | Draft vs same-fault episodes (own or other) | 44 | 0.37 | 0.74 | 0.95 | 3 |
  | Draft vs closest other-fault episode | 40 | 0.26 | 0.44 | 0.67 | 0 |

  At 0.90 the check is precise (no other-fault match) but finds few same-fault records.
  A lower threshold would find more; it should be chosen on the dev split, not here.

### 4. Fixes and re-run (28 Nov, same day)

Three changes, each made without looking at the eval drafts for its setting:

1. **Step order on text captures.** The check now breaks start-time ties by the A1
   segment index in the evidence ID (`…-S0003`). Reversed steps are now caught in
   **40 of 50** drafts (was 0). The 10 misses are shift logs that narrate every step in
   one segment; order inside a segment cannot be read from the draft (A3 sees quotes, not
   the segment text), so nothing is flagged. On the clean drafts it fires once, on
   EP-0278, and that is a **real** error: the model listed "grab intake filter element
   from stores" before "restart and check the build-up time", the reverse of the narration.
2. **Duplicate threshold from the dev split: 0.90 -> 0.55.** On the 12 dev drafts (9 with
   symptoms and a resolvable machine), the closest same-fault gold episode scored
   0.676-0.932 and the closest other-fault episode on the same machine 0.280-0.434. The
   rule, fixed before re-running eval, was the midpoint of that gap rounded to 0.05: 0.55.
   Only 9 dev points, so this is a first setting, not a calibration.
3. **A known-fault match no longer raises priority.** At 0.55, `possible_duplicate`
   mostly means "this fault is already known". Measured against a store of the 350 other
   gold episodes, it pushed **47 of 50** drafts to `low_confidence`, 34 of them for that
   reason alone, which would make the priority meaningless. It now stays a warning listing
   the matching records; exact duplicate recordings still block.

Re-run on the eval drafts:

| | First measurement (0.90) | Re-run (0.55, fixes) |
|---|---:|---:|
| Reversed steps detected | 0 / 50 | **40 / 50** |
| Drafts with a `possible_duplicate` warning (store without own episode) | 3 / 50 | **36 / 50** |
| Matched IDs that are a different fault | 0 of 9 | **0 of 105** |
| Own episode among the matches (store with own episode) | 0 / 50 | 8 / 50 |
| `low_confidence` drafts, validated against the store | not measured | 13 / 50 |
| Other results (low confidence without a store, other injections, exact duplicates) | as in sections 1-3 | unchanged |

- Every match at 0.55 is still the same fault. The 5 drafts whose closest other-fault
  episode is above 0.55 (diagnostic max 0.674) still rank same-fault episodes first in the
  top 3, so no other-fault ID was reported.
- The own episode is found only 8 times because same-fault gold records have identical
  symptom text: the top 3 is a tie among them. Telling one repair from a recurrence needs
  more than symptoms (time, asset, technician), which A3 does not compare.

### 5. After the K5 asset-prefix change (`entities_v2`, 28 Nov)

`docs/ENTITY_GRAPH.md` aligned A4 with the data's asset IDs and lets an asset ID supply a
missing machine type. The committed `validation_eval.json` is re-run with that change:

| | Section 4 | With `entities_v2` |
|---|---:|---:|
| `low_confidence` drafts | 13 / 50 | **6 / 50** |
| vs any K7 error: flagged and wrong / precision / recall | 12 / 0.92 / 0.27 | 5 / 0.83 / 0.11 |
| `missing_required_fact` flags on required fields | 7 | 2 |
| Drafts with a `possible_duplicate` warning | 36 / 50 | 42 / 50 (0 of 123 matches a different fault) |
| Injections, exact duplicates | as before | unchanged |

The lower recall is expected, not a regression: the 7 drafts that A3 flagged only for
their machine type are now resolved to the correct machine by A4 (5 from the asset ID,
2 from a bare "CNC"), so there is nothing left to flag. K7 still counts them as A2
machine-type errors because A2 did not name the category. What remains unflagged are
grounded-but-wrong values, which need the reviewer.

### Summary

A3 reliably catches what rules can see: missing or unresolvable facts, ungrounded facts,
unquoted quantities, failed attempts repeated as steps, steps out of narration order
(where the narration has more than one segment), and exact duplicate recordings. With the
dev-chosen threshold it tells the reviewer when a fault is already known, without false
other-fault matches here. It does not judge whether a grounded value is correct, and its
confidence score tracks completeness rather than correctness. That is why every draft
still goes to a human.

## Pending

- Review of the page fixes. The UI is connected to this API (#32); the fixes above
  address the #32 review.
- Representative recordings from the team. The 28 Nov evaluation covers the 50 synthetic
  eval drafts. The duplicate threshold rests on 9 dev points; recalibrate it on real data.
  Step order inside a single segment would need the capture's segment text.
- Automatic promotion is a team decision. (K5 entity resolution before persistence is
  now in `wp3/graph-agent`.)
