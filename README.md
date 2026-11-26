# Factory Memory 🏭🧠
**A Multi-Agent AI System for Preserving and Transferring Industrial Repair Knowledge**

[![Innodata AI Catalyst 2026](https://img.shields.io/badge/Innodata-AI%20Catalyst%20%2726%20University%20Track-blue.svg)](https://innodata.com)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-brightgreen.svg)](https://python.org)
[![Architecture-LangGraph](https://img.shields.io/badge/Architecture-LangGraph%20MAS-orange.svg)](https://github.com/langchain-ai/langgraph)
[![License-MIT](https://img.shields.io/badge/License-MIT-purple.svg)](LICENSE)

---

## 📌 Problem Statement

In modern manufacturing plants, critical maintenance and diagnostic expertise resides primarily in the tacit, unwritten memories of senior technicians. As veteran mechanics retire, decades of experiential knowledge—subtle vibration cues, machine idiosyncrasies, troubleshooting heuristics, and hazardous edge-case workarounds—are permanently lost.

Simultaneously, junior technicians face steep learning curves, frequently resorting to trial-and-error repairs. This causes:
1. **Prolonged Equipment Downtime:** Average mean time to repair (MTTR) increases by up to 40% when experienced technicians are unavailable.
2. **Repeated Ineffective Fixes:** Without historical tracking of previous failed attempts, identical invalid replacement parts are installed repeatedly.
3. **Severe Safety Risks:** Inadequate isolation procedures (Lockout/Tagout - LOTO, stored hydraulic pressure, arc-flash hazards) lead to preventable workplace injuries.

**Factory Memory** solves this crisis by deploying an 8-agent collaborative multi-agent architecture across two specialized pipelines connected to a unified hybrid knowledge store.

---

## 🏗️ System Architecture

Factory Memory separates **Knowledge Capture (Pipeline A)** from **Knowledge Retrieval & Diagnostics (Pipeline B)** to guarantee schema validity, rigorous safety enforcement, and grounded citations before any repair guidance reaches junior technicians.

```
====================================================================================================
                                      FACTORY MEMORY ARCHITECTURE
====================================================================================================

      [ PIPELINE A: INGESTION & CAPTURE ]                     [ PIPELINE B: ASSISTANCE & DIAGNOSTICS ]
  (Audio, Video Notes, Repair Logs, Sensors)                       (Junior Technician Query / Symptoms)
                     │                                                               │
                     ▼                                                               ▼
        ┌─────────────────────────┐                                     ┌─────────────────────────┐
        │ 1. Capture & Transcript │                                     │  5. Diagnostic Agent    │
        │    (Whisper + Sampling) │                                     │  (Clarify & Hypotheses) │
        └────────────┬────────────┘                                     └────────────┬────────────┘
                     │ Raw Transcript & Keyframes                                    │ Fault Hypotheses
                     ▼                                                               ▼
        ┌─────────────────────────┐                                     ┌─────────────────────────┐
        │ 2. Knowledge Extraction │                                     │  6. Retrieval Agent     │
        │    (Pydantic Schema)    │                                     │  (Hybrid Vector + Graph)│
        └────────────┬────────────┘                                     └────────────┬────────────┘
                     │ Structured Candidate JSON                                     │ Grounded Evidence
                     ▼                                                               ▼
        ┌─────────────────────────┐                                     ┌─────────────────────────┐
        │ 3. Validation & Curation│ ──[Low Conf]──> [HITL Queue]        │ 7. Safety Verification  │◄──┐
        │    (Deduplication/Logic)│                                     │    (Rule + LLM Veto)    │   │
        └────────────┬────────────┘                                     └────────────┬────────────┘   │
                     │ Verified Records                                              │ Approved?      │
                     ▼                                                [Vetoed] ──────┴─(≤ 2 loops)────┘
        ┌─────────────────────────┐                                          │ (If > 2 loops: Escalates)
        │ 4. Knowledge Graph Agent│                                          ▼
        │    (Entity Resolution)  │                             ┌─────────────────────────┐
        └────────────┬────────────┘                             │  8. Orchestrator Agent  │
                     │                                          │  (Supervisor & Citations)
                     │ Writes                                   └────────────┬────────────┘
                     ▼                                                       │
  ┌──────────────────────────────────────────────────────────────┐           │ Formatted Response
  │                    SHARED KNOWLEDGE STORE                    │           ▼
  │  ┌───────────────┐ ┌────────────────┐ ┌───────────────────┐  │      [ Junior Technician UI ]
  │  │ SQLite Rel.DB │ │ ChromaDB/FAISS │ │ NetworkX Graph DB │  │   (Safety Check, Ranked Steps,
  │  │ (Repair Episodes│ (Dense Vectors │ │ (Component Causal │  │     Citations & Disclaimers)
  │  │  & Metadata)  │ │  & Symptoms)   │ │   Dependencies)   │  │
  │  └───────────────┘ └────────────────┘ └───────────────────┘  │
  │            JSONL Audit Log (Immutable Decision Trail)        │
  └──────────────────────────────────────────────────────────────┘
====================================================================================================
```

### The 8 Specialized Agents

| # | Agent Name | Pipeline | Primary Responsibility |
|---|---|---|---|
| 1 | **Capture & Transcription Agent** | Pipeline A (Ingestion) | Transcribes spoken audio (OpenAI Whisper) and extracts key visual frames from technician repair recordings. |
| 2 | **Knowledge Extraction Agent** | Pipeline A (Ingestion) | Extracts machine type, symptoms, step-by-step procedures, tools, parts, root cause, failed attempts, and safety warnings into structured Pydantic schemas. |
| 3 | **Validation & Curation Agent** | Pipeline A (Ingestion) | Performs schema validation, units check, duplicate detection, and confidence scoring; routes borderline entries to a Human-in-the-Loop (HITL) review queue. |
| 4 | **Knowledge Graph Agent** | Pipeline A (Ingestion) | Performs entity resolution and updates the SQLite episode database, vector index, and NetworkX component/failure relationship graph. |
| 5 | **Diagnostic Agent** | Pipeline B (Assistance) | Interprets junior worker queries, requests clarifying diagnostic readouts when ambiguous, and formulates prioritized fault hypotheses. |
| 6 | **Retrieval Agent** | Pipeline B (Assistance) | Executes hybrid retrieval (dense vector similarity + graph traversal) to surface verified past repairs, similar symptoms, and historical failed attempts. |
| 7 | **Safety Verification Agent** | Pipeline B (Assistance) | Operates an independent deterministic safety ruleset + LLM check with **veto power** over proposed repair steps (max 2 revision loops before human escalation). |
| 8 | **Orchestrator Agent** | Pipeline B (Assistance) | Serves as the supervisor; coordinates agent flow, synthesizes final responses with verified citations, triggers abstention when evidence is weak, and writes JSONL traces. |

---

## 🗂️ Repository Structure

The repository is structured to adhere to the competition submission specifications:

```
factory-memory/
├── docs/
│   ├── .gitkeep                 # Ensures docs directory tracking
│   ├── Workflow.md              # Detailed pipeline dataflow, protocols, HITL & state machines
│   ├── PROJECT_DETAILS.md       # Target metrics (O1-O5), synthetic dataset, and stack rationales
│   ├── Proposal.docx            # Competition formal project proposal (manually placed)
│   └── project-demo.mp4         # System walkthrough video recording (manually placed)
├── project/
│   ├── main.py                  # Primary application entry point (Streamlit UI + CLI mode)
│   └── requirements.txt         # Production and development dependencies
├── .env.example                 # Environment variable template
├── .gitignore                   # Git exclusion rules (keys, caches, virtualenvs)
└── README.md                    # Project documentation, architecture, and getting started
```

---

## 🚀 Setup & Installation

### Prerequisites

For Agent 1 development, see [Capture setup and handoff contract](docs/CAPTURE_SETUP.md).
For Agent 2, see [Extraction setup and draft contract](docs/EXTRACTION_SETUP.md).
For K3, see [Verified knowledge store and retrieval interface](docs/KNOWLEDGE_STORE.md).
For K4, see [Agent 3 validation and review-queue backend](docs/VALIDATION_REVIEW.md).
For K5, see [Agent 4 entity resolution and graph writes](docs/ENTITY_GRAPH.md).
For K6, see [Agent 6 hybrid retrieval](docs/RETRIEVAL.md).
For K7, see [Extraction precision/recall/F1 export](docs/EXTRACTION_METRICS.md).
For K8, see [Retries, timeouts, fallbacks and tests](docs/HARDENING.md).

- Python 3.10, 3.11, or 3.12
- Git
- FFmpeg (required for local audio transcription via OpenAI Whisper)

### 1. Clone & Create Virtual Environment
```bash
# Clone the repository
git clone https://github.com/your-org/factory-memory.git
cd factory-memory

# Create a virtual environment
python -m venv venv

# Activate virtual environment
# Windows (PowerShell):
.\venv\Scripts\Activate.ps1
# Linux / macOS:
source venv/bin/activate
```

### 2. Install Dependencies
```bash
pip install --upgrade pip
pip install -r project/requirements.txt
```

### 3. Environment Configuration
Copy `.env.example` to `.env` and fill in your API credentials:
```bash
cp .env.example .env
```
Edit `.env` with your parameters:
```env
OPENAI_API_KEY=your_api_key_here
PRIMARY_LLM_MODEL=gpt-4o-mini
REASONING_LLM_MODEL=gpt-4o
WHISPER_MODEL=base
WHISPER_DEVICE=cpu
```

---

## 💻 Execution Guide

### Option A: Interactive Streamlit UI (Recommended)
Run the web application containing the Technician Ingestion Queue, Worker Troubleshooting Assistant, and Live Agent Trace / Benchmark Dashboard:
```bash
streamlit run project/main.py
```
Then navigate to `http://localhost:8501` in your browser.

### Option B: Command Line Interface (CLI Diagnostics)
To execute a self-test diagnostic check, inspect pipeline integrity, and verify connectivity across agents:
```bash
python project/main.py --mode cli
```

---

## 🎯 Target Objectives & Evaluation Metrics

| ID | Objective | Evaluation Metric | Target Benchmark |
|---|---|---|---|
| **O1** | **Structured Knowledge Extraction** | Extraction F1-Score | $\ge 0.85$ on synthetic repair transcripts across 5 machine types |
| **O2** | **Root-Cause Diagnostic Accuracy** | Top-3 Accuracy | $\ge 80\%$ on 60 held-out diagnostic test cases |
| **O3** | **Repair Safety Enforcement** | Safety Recall & Violation Rate | $\ge 95\%$ safety hazard recall; **0** unflagged hazardous instructions |
| **O4** | **Evidence Grounding & Citations** | Citation Precision | $\ge 90\%$ of generated guidance steps mapped to explicit Evidence IDs |
| **O5** | **Multi-Agent Superiority** | 3-Way Comparative Study | Statistically significant improvement ($p < 0.05$) vs Single-Agent and Human Baselines |

---

## 📅 Project Milestones (Nov 20 – Dec 07, 2026)

- [x] **Milestone 1 (20 Nov 2026):** Project Inception, Repository Skeleton, Initial Architecture Specifications.
- [ ] **Milestone 2 (24 Nov 2026):** Synthetic Dataset Generation (5 machine classes, 400 repair episodes) & Hybrid Store initialization.
- [ ] **Milestone 3 (28 Nov 2026):** Pipeline A (Ingestion, Whisper, Schema Extraction, HITL Queue) integration.
- [ ] **Milestone 4 (02 Dec 2026):** Pipeline B (Diagnostic, Hybrid Retrieval, Safety Veto loop, Supervisor) implementation in LangGraph.
- [ ] **Milestone 5 (05 Dec 2026):** 3-Way Comparative Benchmark Execution (MAS vs. Single-Agent vs. Human Baseline).
- [ ] **Milestone 6 (07 Dec 2026):** Final submission freeze: Streamlit demonstration, video demo recording, and proposal documentation.

---

## 📄 License & Attribution
Developed for the **Innodata AI Catalyst '26 University Track Competition**. All open-source tools and foundation models are credited in [docs/PROJECT_DETAILS.md](file:///d:/Desktop/factory%20memory/docs/PROJECT_DETAILS.md).
