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
| `possible_duplicate` | warning, raises priority | Symptom cosine >= 0.90 against verified episodes or pending items with the same normalized machine label. A warning, because a repeated fault can be a genuine recurrence. |
| `failed_attempt_conflicts_with_step` | warning, raises priority | The same action is recorded as both failed and completed. |
| `repeated_fact`, `steps_out_of_source_order`, `orphan_evidence`, `outcome_unknown`, `capture_quality_flags`, `offline_fixture_output`, `semantic_dedup_unavailable` | warning | Shown to the reviewer and recorded as acknowledged on approval. |

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
Duplicate threshold: `system.duplicate_similarity_threshold` (0.90).

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

## Pending

- Ilham's review. Kajanan's UI integration against this API.
- Representative recordings and drafts from the team, plus evaluation of the score
  and duplicate threshold on labelled data.
- Automatic promotion is a team decision. (K5 entity resolution before persistence is
  now in `wp3/graph-agent`.)
