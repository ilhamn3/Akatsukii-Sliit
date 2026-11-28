# Tharshanth A: Work Plan, Progress Tracker and PR Log

**Team Akatsukii · SLIIT · Innodata AI Catalyst '26 (University Track)**  
**Role:** Data, human study and documentation · **Share:** 20% (20 of 100 planning points) · **PR reviewer:** Kajanan K  
**Repo:** https://github.com/ilhamn3/Akatsukii-Sliit  
**Deadlines:** MVP / project plan **29 Nov 2026, 11:59 PM** · Grand Finale **7 Dec 2026**

**What you own:** Fault catalogue, synthetic data generator, frozen test set, human-baseline study, PROJECT_DETAILS.md, README and data documentation.

> **Rule for every PR:** update the **Done %** column in section 1 of this file, add a row to the **PR log** (section 7), run `python scripts/progress.py --write`, and commit the result **in the same PR**. Reviewer checks that the claimed % matches the evidence.

## 1. Progress tracker

<!-- PROGRESS:START -->
**Overall: 15 / 20 points · 75% complete**

| WP | Title | Weight | Earned | WP % |
|---|---|---:|---:|---:|
| WP2 | Synthetic data and test set | 12 | 9.8 | 81% |
| WP8 | Human baseline study | 5 | 3.8 | 75% |
| WP9 | Documentation (Tharshanth's part) | 3 | 1.5 | 50% |
<!-- PROGRESS:END -->

**How to update:** edit only the **Done %** column below (0-100). Points earned = Pts x Done %. A subtask is 100% only when it meets its "done when" in section 3 and has been reviewed. Partial progress is fine (25 / 50 / 75%).

#### WP2 – Synthetic data and test set (12 pts)

| ID | Subtask | Pts | Due | Done % |
|---|---|---|---|---|
| 2.1 | Fault catalogue (~40 failure modes) with hazards across 5 machine types | 3 | 23 Nov | 100% |
| 2.2 | Seeded generator script (reproducible) | 2 | 23 Nov | 75% |
| 2.3 | ~400 repair episodes, 12 technicians, spoken-style narratives, ground-truth labels | 2 | 24 Nov | 75% |
| 2.4 | Audio subset for the speech-to-text path | 1 | 26 Nov | 50% |
| 2.5 | 60 held-out queries in 4 tiers, gold answers, dev split, frozen with hash | 2 | 24 Nov | 75% |
| 2.6 | Expanded stores for scalability tests (100 / 400 / 2,000 records) | 1 | 26 Nov | 100% |
| 2.7 | Synthetic-data disclosure text | 1 | 29 Nov | 75% |

#### WP8 – Human baseline study (5 pts)

| ID | Subtask | Pts | Due | Done % |
|---|---|---|---|---|
| 8.1 | Consent form and study protocol | 1 | 26 Nov | 75% |
| 8.2 | Recruit at least 5 volunteers | 1 | 27 Nov | 75% |
| 8.3 | Timed answers and manual extraction runs | 2 | 28 Nov | 75% |
| 8.4 | Anonymised, shuffled answers prepared for blind grading | 1 | 28 Nov | 75% |

#### WP9 – Documentation (Tharshanth's part) (3 pts)

| ID | Subtask | Pts | Due | Done % |
|---|---|---|---|---|
| 9.1 | README.md (overview, setup, execution guide) | 1 | 29 Nov | 75% |
| 9.2 | docs/Workflow.md | 1 | 29 Nov | 75% |
| 9.3 | docs/PROJECT_DETAILS.md with every AI model/tool declared | 1 | 29 Nov | 0% |

## 2. Files and folders you own

Proposed repo layout (adjust if the existing repo differs, but keep the competition-required files: `project/main.py`, `project/requirements.txt`, `README.md`, `docs/Proposal.docx`, `docs/Workflow.md`, `docs/PROJECT_DETAILS.md`, `project-demo.mp4`).

| Path | What it is |
|---|---|
| `project/data/fault_catalogue/` | About 40 failure modes with symptoms, root causes, fixes, wrong fixes, hazards |
| `project/data/generator.py, project/data/episodes/` | Seeded generator and about 400 repair episodes (12 simulated technicians) |
| `project/data/test_set/ (queries, gold answers, dev split, hash file)` | 60 held-out queries in 4 tiers; frozen with a committed hash |
| `project/data/audio/, project/data/expanded/` | Audio subset and expanded stores (100 / 400 / 2,000 records) |
| `project/human_study/ (protocol, anonymised results)` | Human baseline. **Never commit signed consent forms or personal details.** |
| `README.md, docs/Workflow.md, docs/PROJECT_DETAILS.md` | Required documentation, AI-tool declaration, data disclosure |

## 3. Planned PRs and PR descriptions

One PR per row, small and focused. Branch from `main`, title format `[WP<N>] what`, reviewer **Kajanan K**. Use the PR template (`.github/pull_request_template.md`) and start from the description below.

| PR | Branch | Subtasks | Pts | Merge by |
|---|---|---|---|---|
| T1 | `wp2/fault-catalogue` | 2.1 | 3 | 23 Nov |
| T2 | `wp2/generator-episodes` | 2.2, 2.3 | 4 | 24 Nov |
| T3 | `wp2/test-set-freeze` | 2.5 | 2 | 24 Nov |
| T4 | `wp2/audio-expanded-stores` | 2.4, 2.6 | 2 | 26 Nov |
| T5 | `wp8/study-protocol` | 8.1 | 1 | 26 Nov |
| T6 | `wp8/study-results` | 8.2, 8.3, 8.4 | 4 | 28 Nov |
| T7 | `docs/readme-workflow` | 9.1, 9.2 | 2 | 29 Nov |
| T8 | `docs/project-details-disclosure` | 9.3, 2.7 | 2 | 29 Nov |

### T1: [WP2] Fault catalogue with hazards

- **Branch:** `wp2/fault-catalogue` · **Merge by:** 23 Nov · **Subtasks:** 2.1 · **Reviewer:** Kajanan K
- **Summary:** Adds the fault catalogue: about 40 failure modes across 5 machine types, each with symptoms, error codes, true root cause, valid fixes, common wrong fixes and hazards.
- **Done when:** Catalogue validates against Ilham's schema; every failure mode has at least one hazard entry or an explicit "none"; hazard list shared with Ilham for the Safety rule base.

### T2: [WP2] Seeded generator, ~400 episodes and narratives

- **Branch:** `wp2/generator-episodes` · **Merge by:** 24 Nov · **Subtasks:** 2.2, 2.3 · **Reviewer:** Kajanan K
- **Summary:** Adds the seeded generator and produces about 400 repair episodes from 12 simulated technicians, with LLM-written spoken-style narratives and separate ground-truth labels.
- **Done when:** Running the generator with the same seed reproduces the same data; narratives include fillers, jargon and omissions; ground truth is stored separately; a sample set is handed to Kavibarath and Kajanan.

### T3: [WP2] Frozen 60-query test set and dev split

- **Branch:** `wp2/test-set-freeze` · **Merge by:** 24 Nov · **Subtasks:** 2.5 · **Reviewer:** Kajanan K
- **Summary:** Adds the 60 held-out queries (easy / medium / hard / adversarial), gold answers, a separate dev split and the SHA-256 hash of the frozen test set.
- **Done when:** Tier counts documented; hash file committed; a short note states that the test set is never used for prompt tuning (dev split only). **Deadline is strict: blocks extraction evaluation and the benchmark.**

### T4: [WP2] Audio subset and expanded stores

- **Branch:** `wp2/audio-expanded-stores` · **Merge by:** 26 Nov · **Subtasks:** 2.4, 2.6 · **Reviewer:** Kajanan K
- **Summary:** Adds an audio subset for the speech-to-text path and expanded stores at 100, 400 and 2,000 records.
- **Done when:** Audio clips transcribe in Agent 1; expanded stores load into the knowledge store; sizes documented.

### T5: [WP8] Consent form and human-study protocol

- **Branch:** `wp8/study-protocol` · **Merge by:** 26 Nov · **Subtasks:** 8.1 · **Reviewer:** Kajanan K
- **Summary:** Adds the consent form template and the written protocol (tasks, timing, materials, how answers are anonymised).
- **Done when:** Protocol is clear enough for a volunteer to follow without help; no personal data in the repo.

### T6: [WP8] Human baseline: volunteers, timed runs, blind-grading sheets

- **Branch:** `wp8/study-results` · **Merge by:** 28 Nov · **Subtasks:** 8.2, 8.3, 8.4 · **Reviewer:** Kajanan K
- **Summary:** Adds anonymised results from at least 5 volunteers (timed answers and manual extraction) and the shuffled, anonymised sheets for blind grading.
- **Done when:** At least 5 participants; consent obtained for all; results contain no names; time per task recorded; format is readable by Kajanan's harness.

### T7: [Docs] README.md and Workflow.md

- **Branch:** `docs/readme-workflow` · **Merge by:** 29 Nov · **Subtasks:** 9.1, 9.2 · **Reviewer:** Kajanan K
- **Summary:** Writes the project README (overview, setup, how to run) and docs/Workflow.md.
- **Done when:** A teammate can follow the README on a clean machine and run `main.py`; the workflow matches the final architecture.

### T8: [Docs] PROJECT_DETAILS.md and synthetic-data disclosure

- **Branch:** `docs/project-details-disclosure` · **Merge by:** 29 Nov · **Subtasks:** 9.3, 2.7 · **Reviewer:** Kajanan K
- **Summary:** Writes docs/PROJECT_DETAILS.md with every AI model, service and AI coding assistant declared (each member sends their list), third-party components and the synthetic-data disclosure.
- **Done when:** All four members' tool lists are included; the synthetic nature of the data is stated clearly; no secrets.

## 4. Handoffs

**You deliver**

| By | To | What |
|---|---|---|
| by 23 Nov | Ilham | Hazard list for the Safety rule base |
| 24 Nov | Kavibarath, Kajanan | Frozen test set, sample episodes, audio (blocks extraction evaluation and the benchmark) |
| 28 Nov | Kajanan | Anonymised human-baseline results |

**You depend on**

| By | From | What |
|---|---|---|
| 22 Nov | Ilham | Schemas for the record format |
| by 24 Nov | Kavibarath, Kajanan | Confirm the test-set format works for their modules |
| 28 Nov | Kajanan | Final benchmark results to reference in docs |

## 5. Your timeline

| Date | Plan |
|---|---|
| 21-22 Nov | Fault catalogue with hazards; ontology draft; generator script |
| 23-24 Nov | Generate episodes, narratives and 60 queries; **freeze test set by 24 Nov** |
| 25-26 Nov | Audio; expanded stores (100 / 400 / 2,000); consent form and study protocol |
| 27 Nov | Recruit volunteers; run human baseline |
| 28 Nov | Finish human baseline; blind grading sheets |
| 29 Nov | PROJECT_DETAILS.md, README, Workflow.md, data disclosure |
| 30 Nov - 6 Dec | Finale slides (max 10) with Kajanan |
| 7 Dec | Timekeeping, slides, Q&A |

## 6. Rules and competition items you own

- **The test set is never used for prompt tuning.** Tuning uses the separate dev split.
- Synthetic data must be clearly disclosed everywhere it is used.
- Human study: get consent from every volunteer; commit only anonymised results.
- Collect every member's list of AI tools and coding assistants for PROJECT_DETAILS.md.
- Third-party components and reused code must be acknowledged in the documentation.
- Original work built during the competition; reused components and pre-existing code must be disclosed.
- Declare every AI model, service and coding assistant you use (send your list to Tharshanth for `docs/PROJECT_DETAILS.md`).
- Everyone must be able to explain the full architecture and benchmark method at the finale Q&A, not only their own modules.

## 7. PR log (add one row per PR, newest at the bottom)

| PR # | Date | Title | Subtasks | Work done in this PR | My progress before -> after | Reviewer |
|---|---|---|---|---|---|---|
| #6 | 23 Nov | [WP2] Fault catalogue with hazards | 2.1 (0->75%) | 40 failure modes across 5 machine types, hazards.json, build and validate scripts | 0% -> 11% | Kajanan K |
| #16 | 25 Nov | [WP2] Seeded generator, 400 episodes and narratives | 2.2 (0->75%), 2.3 (0->75%) | Seeded stdlib generator, 400 episodes from 12 simulated technicians (audio debriefs and shift logs), separate ground truth validated against RepairRecord, samples.json for hand-off, 8 tests | 11% -> 26% | Ilham MM |
| #17 | 25 Nov | [WP2] Frozen 60-query test set and dev split | 2.5 (0->75%) | 60 test queries (20 easy / 20 medium / 10 hard / 10 adversarial) with gold answers, separate 20-query dev split, FROZEN.sha256 with --verify and an overwrite guard, no-tuning rule in README, 9 tests | 26% -> 34% | Ilham MM |
| #18 | 25 Nov | [WP8] Consent form and human-study protocol | 8.1 (0->75%), 2.1 (75->100%) | Consent form template, full protocol (packets, session script, timing, CSV formats, anonymisation and blind-grading steps), private/ folder git-ignored. 2.1 to 100%: #6 merged and the Safety agent loads hazards.json | 34% -> 41% | Ilham MM |
| #19 | 26 Nov | [WP2] Audio subset and expanded stores | 2.4 (0->50%), 2.6 (0->100%) | 12 synthetic audio clips (7.3 min, 5 machines, 6 clean / 6 with plant noise, reference transcripts); nested stores n100 / n400 / n2000 as VerifiedEpisode envelopes, all 2,500 validated against the store model; 7 tests. All three stores load through scripts/load_expanded_store.py into KnowledgeStore (100 / 400 / 2,000 rows, reload is idempotent). Pending: Agent 1 transcription check (Kavibarath) | 41% -> 49% | Ilham MM |
| #28 | 27 Nov | [Docs] README and Workflow.md match the code on main | 9.1 (0->75%), 9.2 (0->75%) | README rewritten from the code: honest status table, real setup (Python 3.10-3.13, .venv, env variables the code reads), run guide, data checks, troubleshooting, docs index, team. Every command run: smoke test, store load, query, UI, data checks, safety cases, 237 tests pass; 4 errors found and fixed (missing machine type, dev-eval overwriting committed results, demo --fake-llm crash, slow-network model download). Workflow.md rewritten to the real graph (safety after plan, FAISS not ChromaDB, every draft human-reviewed, abstention and LOTO checks, real trace format, HITL status). Pending: real Gemini run, teammate clean-machine check | 49% -> 56% | Ilham MM |
| #39 | 28 Nov | [WP8] Human baseline results and blind-grading sheet | 8.2 (0->75%), 8.3 (0->75%), 8.4 (0->75%) | Remote, self-timed study (protocol 11-11b): 5 valid participants (P01, P03, P04, P05, P06), 60 timed answers and 10 extraction rows, 48 of 60 test queries (packets A, C, D, E; packet A twice). Packet B excluded: two submissions had no or unverifiable times; a labelled AI arm is kept in simulated/, never mixed with human data. Blind grading sheet built (60 answers, key in private/); rebuild with --system once system answers exist. Pending: consent screenshots, participant background, Kajanan review | 56% -> 71% | Kajanan K |
| #TBD | 29 Nov | [Docs] PROJECT_DETAILS.md and synthetic-data disclosure | 2.7 (0->75%) | docs/SYNTHETIC_DATA.md (what is synthetic, how it was generated, what is not, who checked it, limitations, rebuild commands; first added in #28). PROJECT_DETAILS section 2.2 corrected to the generated figures and 2.4 added; section 4.3 rows for Tharshanth's T2-T8 work, the human-study results and both blind sets. Pending: tool lists from Ilham, Kavibarath and Kajanan | 71% -> 75% | Ilham MM |

*Example row:* `| #4 | 22 Nov | [WP1] Repo scaffold and Pydantic schemas | 1.1 (0->100%), 1.2 (0->100%) | Folder structure, .gitignore, schemas | 0% -> 17% | Kavibarath S |` *(delete this example line if you copy it).*
