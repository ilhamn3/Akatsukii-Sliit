# K5: Agent 4 entity resolution and graph writes

Owner: Kavibarath S. Reviewer: Ilham MM. Branch: `wp3/graph-agent`. Subtask 3.5.
Stacked on K4 (PR #13), which is stacked on K3 (PR #12). Retarget to `main` as
those merge.

## Where A4 runs

```
A3 -> review queue -> human approve() -> A4 GraphAgent -> KnowledgeStore (SQLite + FAISS + NetworkX)
```

`ReviewQueue` publishes every approval through `GraphAgent`. A4 is therefore traced
with the approval's trace ID. It is not a node in Pipeline A, because nothing may be
written to the store before a human approves. A3 runs the same resolver at
validation time, so reviewers see entity problems before they decide.

## What gets resolved

| Field | Result | Example |
|---|---|---|
| `machine_type` | One of `cnc_lathe`, `hydraulic_press`, `injection_moulding`, `conveyor_line`, `air_compressor` | "hyd. press 3", "Hydraulic Press", "press" -> `hydraulic_press`; "injection molding machine", "injection press" -> `injection_moulding` |
| `machine_id` | Canonical `<PREFIX>-<NN>` asset ID | "hyd. press 3", "Press-03", "PRESS 03", "press no. 3", "#3" -> `PRESS-03`; "Line 2" on a conveyor -> `CONV-02` |
| `safety_warnings[i]` | Catalogue hazard IDs (H01-H16) the warning *may involve* | "Isolate electrical power before repair" -> H02 |

Matching is deterministic and data-driven, using these sources:
- `project/data/entities/aliases.yaml` (team-curated): aliases, head terms, excluded
  look-alikes, abbreviations (`hyd.` -> hydraulic), spelling (molding -> moulding),
  asset prefixes and extra hazard phrases.
- The SafetyAgent's `trigger_keywords` per hazard (`core/safety_rules/rules.yaml`),
  so A4 and A7 use the same hazard vocabulary.
- The fault catalogue (`faults.json`, `hazards.json`). The vocabulary fails to load
  if it names an unknown machine or hazard ID, or misses a catalogue machine.

There is no fuzzy matching, embedding or LLM guessing. Phrases match whole words
after normalization. A more specific term wins: "injection press" beats "press".

### Ambiguity and unknowns are never forced into a category

| Situation | Result |
|---|---|
| Label matches no machine ("cooling pump", "belt") | `machine_type_unresolved`, blocking |
| Look-alike outside the catalogue ("drill press", "refrigeration compressor") | `machine_type_unresolved` (method `excluded_term`), blocking |
| Two machines named ("conveyor and compressor") | `machine_type_ambiguous`, candidates listed, blocking |
| Asset names a different machine (Press-03 on a conveyor) | `asset_type_conflict`, blocking |
| Asset in an unknown format ("SN-88231-X") | kept as written, `asset_id_unresolved` warning |
| Generic hazard wording ("stored energy", "lock out") | ambiguous link listing candidates from that machine's catalogue faults; never resolved by elimination |

A blocking entity issue keeps the item pending. Approval re-checks entities on the
corrected values, so the reviewer must enter a label that maps to exactly one
catalogue machine. Re-typing "cooling pump 2" is still refused. Calling
`GraphAgent.put_verified` directly with an unresolvable label is also refused.

Assets follow the convention LATHE / PRESS / IMM / CONV / COMP plus a two-digit
number. It is provisional. If the plant has an asset register, pass
`EntityResolver(..., assets=[...])`; IDs not in the register then stay unresolved.

## What is stored

- `record.machine_type` and `record.machine_id` hold the canonical IDs. K3
  `by_machine("conveyor_line")` and machine-filtered search now find records that
  were narrated as "conveyor belt".
- `provenance.entity_links[]` keeps, for each field: the raw text, the entity ID or
  status, the method, the candidates and the vocabulary version (`entities_v1`).
  Re-resolving an already canonical episode keeps the original raw text, and an
  identical rewrite is a `STORE_NOOP`.
- The graph gains shared nodes:
  - `machine -has_asset-> asset -has_episode-> episode`
  - `hazard (episode warning) -may_involve-> catalogue_hazard`

The full chain is machine -> fault -> cause -> fix -> hazard -> catalogue hazard.
Every edge keeps its episode and evidence IDs, so facts from different repairs are
never joined.

```python
store.related("asset", "PRESS-03", trace_id=...)            # all repairs on that asset
store.related("catalogue_hazard", "H02", trace_id=...)      # repairs whose warnings may involve H02
```

A3 duplicate detection now compares canonical machine IDs, so "conveyor" and
"conveyor belt" drafts are compared with each other.

## Limits

- The vocabulary and asset convention are development defaults. They need checking
  against the team's real recordings and asset names (Tharshanth).
- Hazard links are keyword-based and deliberately over-inclusive, like the safety
  rules: "the ram could drop" -> H03 and H12. They are labelled `may_involve`, not
  verified classifications, and are not a substitute for A7.
- Symptoms are not yet linked to catalogue fault IDs (L01...). K6 retrieval may add
  that.

## Commands

```powershell
. .\scripts\Activate-FactoryMemory.ps1
python -m pytest tests/ingestion/test_graph_agent.py -q
```

The K4 demo in `docs/VALIDATION_REVIEW.md` now exercises A4 on approval.

## Observed development validation (25 Nov 2026)

- 183 repository tests and pip check passed, including 33 new K5 tests on the real
  vocabulary, catalogue and SQLite/FAISS/NetworkX with test-only embeddings.
- Cached-MiniLM synthetic run in `.local-data/graph-demo`. The draft narrated
  "conveyor" was approved with reviewer-supplied root cause, why_failed and asset
  "Line 2". It was stored as `conveyor_line` / `CONV-02` with raw text kept. The
  "stored energy" warning stayed ambiguous (candidate H13). Machine-filtered symptom
  search and the asset graph query both returned the episode.

These are synthetic smoke checks, not an accuracy evaluation of entity resolution.
