# Ilham MM: Work Plan, Progress Tracker and PR Log

**Team Akatsukii · SLIIT · Innodata AI Catalyst '26 (University Track)**  
**Role:** Tech lead and orchestration · **Share:** 30% (30 of 100 planning points) · **PR reviewer:** Kavibarath S  
**Repo:** https://github.com/ilhamn3/Akatsukii-Sliit  
**Deadlines:** MVP / project plan **29 Nov 2026, 11:59 PM** · Grand Finale **7 Dec 2026**

**What you own:** Foundation (repo, schemas, shared state, trace logging, agent registry), Diagnostic agent (5), Safety Verification agent (7), Orchestrator (8).

> **Rule for every PR:** update the **Done %** column in section 1 of this file, add a row to the **PR log** (section 7), run `python scripts/progress.py --write`, and commit the result **in the same PR**. Reviewer checks that the claimed % matches the evidence.

## 1. Progress tracker

<!-- PROGRESS:START -->
**Overall: 28.6 / 30 points · 95% complete**

| WP | Title | Weight | Earned | WP % |
|---|---|---:|---:|---:|
| WP1 | Foundation and architecture | 12 | 12 | 100% |
| WP5 | Assistance agents 5, 7 and 8 | 18 | 16.6 | 92% |
<!-- PROGRESS:END -->

**How to update:** edit only the **Done %** column below (0-100). Points earned = Pts x Done %. A subtask is 100% only when it meets its "done when" in section 3 and has been reviewed. Partial progress is fine (25 / 50 / 75%).

#### WP1 – Foundation and architecture (12 pts)

| ID | Subtask | Pts | Due | Done % |
|---|---|---|---|---|
| 1.1 | Repo scaffold in the required structure, `.gitignore`, `.env.example` | 2 | 22 Nov | 100% |
| 1.2 | Pydantic schemas: repair record and agent message contracts | 3 | 22 Nov | 100% |
| 1.3 | Shared state (blackboard) and LangGraph skeleton | 3 | 22 Nov | 100% |
| 1.4 | JSONL trace logger (every agent handoff logged) | 2 | 22 Nov | 100% |
| 1.5 | Agent registry and config file (add an agent without editing graph code) | 2 | 22 Nov | 100% |

#### WP5 – Assistance agents 5, 7 and 8 (18 pts)

| ID | Subtask | Pts | Due | Done % |
|---|---|---|---|---|
| 5.1 | Diagnostic agent: ranked fault hypotheses with confidence | 3 | 24 Nov | 100% |
| 5.2 | Diagnostic agent: clarifying-question flow | 2 | 24 Nov | 100% |
| 5.3 | Safety rule base (from Tharshanth's hazard list) | 3 | 24 Nov | 100% |
| 5.4 | Safety Verification agent: approve / amend / veto with reason | 3 | 26 Nov | 100% |
| 5.5 | Orchestrator: task delegation and adaptive routing | 3 | 26 Nov | 100% |
| 5.6 | Orchestrator: conflict ranking, veto loop (max 2), abstention, human escalation | 3 | 26 Nov | 85% |
| 5.7 | End-to-end integration run and adversarial safety tests | 1 | 28 Nov | 0% |

## 2. Files and folders you own

Proposed repo layout (adjust if the existing repo differs, but keep the competition-required files: `project/main.py`, `project/requirements.txt`, `README.md`, `docs/Proposal.docx`, `docs/Workflow.md`, `docs/PROJECT_DETAILS.md`, `project-demo.mp4`).

| Path | What it is |
|---|---|
| `project/main.py` | Entry point (runs the whole system end to end) |
| `project/core/ (schemas.py, state.py, trace.py, registry.py, config.yaml)` | Pydantic schemas, message contracts, shared state, JSONL trace logger, agent registry |
| `project/agents/a5_diagnostic.py` | Agent 5 – Diagnostic |
| `project/agents/a7_safety.py + project/core/safety_rules/` | Agent 7 – Safety Verification and rule base |
| `project/agents/a8_orchestrator.py` | Agent 8 – Orchestrator |
| `.gitignore, .env.example, .github/, scripts/progress.py` | Repo hygiene, secrets safety, PR template, progress script |

## 3. Planned PRs and PR descriptions

One PR per row, small and focused. Branch from `main`, title format `[WP<N>] what`, reviewer **Kavibarath S**. Use the PR template (`.github/pull_request_template.md`) and start from the description below.

| PR | Branch | Subtasks | Pts | Merge by |
|---|---|---|---|---|
| I1 | `wp1/scaffold-schemas` | 1.1, 1.2 | 5 | 22 Nov |
| I2 | `wp1/state-trace-registry` | 1.3, 1.4, 1.5 | 7 | 22 Nov |
| I3 | `wp5/diagnostic-agent` | 5.1 (partial 60%), 5.2 | 3.8 | 24 Nov |
| I3b | `wp5/llm-gemini` | 5.1 (finish 100%) | 1.2 | 24 Nov |
| I4 | `wp5/safety-agent` | 5.3, 5.4 | 6 | 26 Nov |
| I5 | `wp5/orchestrator` | 5.5, 5.6 | 6 | 26 Nov |
| I6 | `wp5/e2e-integration` | 5.7 | 1 | 28 Nov |
| I7 | `release/final-checks` | (admin) | 0 | 29 Nov |

### I1: [WP1] Repo scaffold and Pydantic schemas

- **Branch:** `wp1/scaffold-schemas` · **Merge by:** 22 Nov · **Subtasks:** 1.1, 1.2 · **Reviewer:** Kavibarath S
- **Summary:** Creates the required folder structure (project/, docs/, tests/, scripts/), `.gitignore`, `.env.example`, and the Pydantic schemas for repair records and agent messages.
- **Done when:** Structure matches the competition guidelines; schemas import cleanly; an example record and message validate; no secrets in the repo. **Merge first: everyone else is blocked until this is in.**

### I2: [WP1] Shared state, LangGraph skeleton, trace logger, agent registry

- **Branch:** `wp1/state-trace-registry` · **Merge by:** 22 Nov · **Subtasks:** 1.3, 1.4, 1.5 · **Reviewer:** Kavibarath S
- **Summary:** Adds the shared state, a LangGraph skeleton, the JSONL trace logger and the agent registry/config.
- **Done when:** A dummy two-agent graph runs through `main.py` and writes a JSONL trace showing each handoff; a new agent can be registered through config without touching graph code.

### I3: [WP5] Diagnostic agent with clarifying question

- **Branch:** `wp5/diagnostic-agent` · **Merge by:** 24 Nov · **Subtasks:** 5.1, 5.2 · **Reviewer:** Kavibarath S
- **Summary:** Implements Agent 5: turns a worker query into ranked fault hypotheses and asks one clarifying question when the machine or symptom is missing.
- **Done when:** Returns ranked hypotheses with confidence for 10 sample queries (dev split only, never the frozen test set); clarifying question triggers correctly; handoff logged in the trace.

### I4: [WP5] Safety rule base and Safety Verification agent

- **Branch:** `wp5/safety-agent` · **Merge by:** 26 Nov · **Subtasks:** 5.3, 5.4 · **Reviewer:** Kavibarath S
- **Summary:** Builds the safety rule base from the hazard list and implements Agent 7 with approve / amend / veto verdicts.
- **Done when:** Rule base covers every hazard in the catalogue; agent returns a verdict plus reason; adversarial examples (e.g. missing isolation step) are vetoed.

### I5: [WP5] Orchestrator: routing, conflict ranking, veto loop, abstention

- **Branch:** `wp5/orchestrator` · **Merge by:** 26 Nov · **Subtasks:** 5.5, 5.6 · **Reviewer:** Kavibarath S
- **Summary:** Implements Agent 8: adaptive routing, ranking of conflicting records, the veto loop (max 2 revisions), abstention and human escalation.
- **Done when:** Simple lookups skip deep reasoning; conflicting records are ranked by outcome, verification, recency and similarity; veto loop stops after 2 rounds and escalates; weak evidence produces an abstention; every decision appears in the trace.

### I6: [WP5] End-to-end integration and adversarial safety tests

- **Branch:** `wp5/e2e-integration` · **Merge by:** 28 Nov · **Subtasks:** 5.7 · **Reviewer:** Kavibarath S
- **Summary:** Wires agents 1-8 together and adds adversarial safety tests (integration run 27 Nov, fixes 28 Nov).
- **Done when:** A recording goes in and a cited, safety-checked answer comes out via `main.py`; adversarial tests documented; traces verified; system handed to Kajanan for benchmark runs by 27 Nov.

### I7: [Release] Final merge, tag, secrets scan, repo access

- **Branch:** `release/final-checks` · **Merge by:** 29 Nov · **Subtasks:** (admin) · **Reviewer:** Kavibarath S
- **Summary:** Final merge to `main`, release tag, secrets scan and access check (0 scored points, but mandatory).
- **Done when:** Secrets scan clean; tag `v1.0-mvp` created; Innodata account has "member" access; `requirements.txt` installs in a clean virtual environment; the final team progress rollup is regenerated.

## 4. Handoffs

**You deliver**

| By | To | What |
|---|---|---|
| 22 Nov | All | Schemas, message contracts, shared state, trace logger (blocks everything) |
| 27 Nov | Kajanan | Working end-to-end system for benchmark runs |

**You depend on**

| By | From | What |
|---|---|---|
| by 23 Nov | Tharshanth | Hazard list for the Safety rule base |
| 26 Nov | Kavibarath | Store and retrieval interface for the Orchestrator |
| 27 Nov | Kajanan | Harness for the final end-to-end run |

## 5. Your timeline

| Date | Plan |
|---|---|
| 21-22 Nov | Repo scaffold, schemas, contracts, shared state, trace; **merge by 22 Nov** |
| 23-24 Nov | Diagnostic agent; Safety rule base |
| 25-26 Nov | Safety agent (7); Orchestrator routing, conflict ranking, veto, abstention |
| 27 Nov | End-to-end integration run |
| 28 Nov | Fix bugs; verify traces; adversarial safety tests |
| 29 Nov | Final merge, tag, repo access, secrets scan |
| 30 Nov - 6 Dec | Finale readiness: confirm a new agent can be added through the registry within an hour |
| 7 Dec | Integrate the organisers' feature at 8:00 AM |

## 6. Rules and competition items you own

- Owns secrets hygiene: no API keys, passwords or tokens in the repo. Use environment variables.
- Owns the final release tag and making sure the Innodata GitHub account has "member" access.
- Runs `python scripts/progress.py --rollup` after merges to refresh the team table in `docs/team/README.md`.
- At least 2 agents with distinct roles must exchange outputs, and execution logs/traces must be available.
- Do not cut the Safety agent or the trace logs, even if the team falls behind.
- Original work built during the competition; reused components and pre-existing code must be disclosed.
- Declare every AI model, service and coding assistant you use (send your list to Tharshanth for `docs/PROJECT_DETAILS.md`).
- Everyone must be able to explain the full architecture and benchmark method at the finale Q&A, not only their own modules.

## 7. PR log (add one row per PR, newest at the bottom)

| PR # | Date | Title | Subtasks | Work done in this PR | My progress before -> after | Reviewer |
|---|---|---|---|---|---|---|
| I1 | 21 Nov | [WP1] Repo scaffold and Pydantic schemas | 1.1 (0->100%), 1.2 (0->100%) | core/agents/store/tests packages, schemas.py, 9 pytest tests | 0% -> 17% | Kavibarath S |
| I2 | 21 Nov | [WP1] Shared state, JSONL trace logger and dynamic agent registry | 1.3 (0->100%), 1.4 (0->100%), 1.5 (0->100%) | BlackboardState, TraceLogger, registry.py, config.yaml, BaseAgent, main.py rewrite | 17% -> 40% | Kavibarath S |
| I3 | 21 Nov | [WP5] Diagnostic agent with hypothesis ranking and clarifying flow | 5.1 (0->60%), 5.2 (0->100%) | a5_diagnostic.py, llm.py (OpenAI+FakeLLM), diagnostic_v1.md prompt, dev queries, unit tests; 60%: prompt + agent + schema + tests complete; dev_queries run on FakeLLM pending real OpenAI key; real-model verification deferred to I5/I8 | 40% -> 53% | Kavibarath S |
| I3b | 24 Nov | [WP5] Add GeminiLLMClient via google-genai and finish subtask 5.1 | 5.1 (60->100%) | llm.py (GeminiLLMClient via google-genai + OpenAILLMClient + FakeLLM), provider routing (LLM_PROVIDER), 10 dev queries verified against live gemini-3.5-flash API, 13 diagnostic tests (65 total in repo) | 53% -> 57% | Kavibarath S |
| I3b-fix | 24 Nov | [WP5] Fix Gemini latency via minimal thinking and add fallback diagnostics | 5.1 (100%), 5.2 (100%) | GEMINI_THINKING (minimal/off/low), GEMINI_TIMEOUT_S, token & latency logging in trace, dev query evaluation with fallback honesty (0 fallbacks on gemini-3.5-flash-lite, 2.93s median latency), PR 7 compatibility check | 57% -> 57% | Kavibarath S |
| I4 | 24 Nov | [WP5] Safety rule base and Safety Verification agent | 5.3 (0->100%), 5.4 (0->100%) | Deterministic safety rules (SR-H01..SR-H16 + SR-X01..SR-X04), rule_engine.py with runtime hazards.json sync, a7_safety.py with immutable rule veto priority, adversarial safety benchmark (100% hazard recall across 24 cases), 27 safety tests (105 total in repo) | 57% -> 77% | Kavibarath S |
| I5a | 25 Nov | [WP5] Orchestrator: real retrieval wiring, routing, conflict ranking and safety-wins finalize | 5.5 (0->70%) | Real: HybridRetriever wiring in RetrievalNode (no silent stub fallback), normalize_evidence for K6 metadata, step-level citations, FAST/FULL routing, conflict ranking, safety-wins finalize/revise, n400 store loader, 222 pytest tests; Pending (5.6 / I5b): full LLM-guided revision loop, escalation UX, and adversarial end-to-end suite | 77% -> 84% | Kavibarath S |
| I5b | 25 Nov | [WP5] Safety-aware revise loop, unsafe-request rule, escalation tickets, dev eval | 5.5 (70->100%), 5.6 (0->85%) | Safety-control-aware planning, LLM-guided revise loop, diagnosis from top episode root_cause + disagreement trace, SR-X05 unsafe-request refusal (5/5 dev adversarial caught, 0/15 FP), LLM-unavailable disclosure, escalation tickets (logs/escalations.jsonl), embedder/snapshot caching (cold 17.4s -> warm 95ms), dev eval on n=10 (flash-lite: 0.80 behaviour, 0.60 root cause, 0 must-not violations; flash hit 429 quota), 228 pytest tests | 84% -> 95% | Kavibarath S |

*Example row:* `| #4 | 22 Nov | [WP1] Repo scaffold and Pydantic schemas | 1.1 (0->100%), 1.2 (0->100%) | Folder structure, .gitignore, schemas | 0% -> 17% | Kavibarath S |` *(delete this example line if you copy it).*
