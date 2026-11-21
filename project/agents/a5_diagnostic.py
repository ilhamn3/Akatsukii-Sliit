"""Agent 5: Diagnostic Agent for Factory Memory.

Interprets junior worker troubleshooting queries and machine symptoms,
requests clarification when machine type or critical context is missing,
and proposes prioritized, ranked fault hypotheses with confidence scores.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from project.agents.base import BaseAgent
from project.core.llm import LLMClient, get_llm_client
from project.core.schemas import Hypothesis
from project.core.state import BlackboardState

PROMPT_PATH = Path(__file__).resolve().parent.parent / "core" / "prompts" / "diagnostic_v1.md"


class DiagnosticOutput(BaseModel):
    """Structured output expected from the LLM diagnostic reasoning step."""

    needs_clarification: bool = Field(
        default=False,
        description="Whether a clarifying question must be asked before diagnosing."
    )
    clarifying_question: Optional[str] = Field(
        default=None,
        description="Single concise question if clarification is required."
    )
    hypotheses: List[Hypothesis] = Field(
        default_factory=list,
        description="Ranked fault hypotheses with confidence in [0.0, 1.0]."
    )


class DiagnosticAgent(BaseAgent):
    """Agent 5: Generates ranked fault hypotheses from symptoms or requests clarification."""

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        prompt_path: Optional[str] = None,
        trace_logger=None,
    ):
        super().__init__(
            name="DiagnosticAgent",
            role="Fault interpretation & hypothesis ranking",
            pipeline="B",
            trace_logger=trace_logger,
            is_stub=False,  # Real agent
        )
        self.llm_client = llm_client or get_llm_client()
        self.llm_name = getattr(self.llm_client, "model_name", "fake")
        self.prompt_template = self._load_prompt(prompt_path)

    def _load_prompt(self, path: Optional[str] = None) -> str:
        p = Path(path) if path else PROMPT_PATH
        if p.exists():
            return p.read_text(encoding="utf-8")
        return (
            "You are a Senior Industrial Diagnostics Specialist. "
            "Analyze symptoms and provide ranked hypotheses or request clarification."
        )

    def process(self, state: BlackboardState) -> Dict[str, Any]:
        """Execute diagnostic reasoning on the current blackboard state."""
        machine_type = state.get("machine_type")
        raw_query = state.get("raw_query", "")

        # 1. Enforce strict clarification policy: missing or unspecified machine
        if not machine_type or machine_type.strip().lower() in ("unspecified", "unknown", "none"):
            clarifying_q = (
                "Could you please specify which machine type (e.g., CNC Lathe, Hydraulic Press, "
                "Injection-Moulding Machine, Conveyor Line, or Air Compressor) is experiencing this issue?"
            )
            return {
                "clarification_needed": True,
                "clarification_question": clarifying_q,
                "ranked_hypotheses": [],
                "selected_hypothesis": None,
                "decision": "CLARIFICATION_REQUESTED",
            }

        # 2. Prepare structured LLM prompt
        user_prompt = (
            f"Machine Classification: {machine_type}\n"
            f"Machine Asset ID: {state.get('machine_id') or 'N/A'}\n"
            f"Operator Observed Symptoms / Alarm: {raw_query}\n\n"
            "Formulate 1 to 3 ranked fault hypotheses with calibrated confidence scores, "
            "or request clarification if the symptom is completely ambiguous."
        )

        try:
            output: DiagnosticOutput = self.llm_client.generate_structured(
                prompt=user_prompt,
                response_model=DiagnosticOutput,
                system_prompt=self.prompt_template,
            )

            # Check if LLM requested clarification
            if output.needs_clarification and output.clarifying_question:
                return {
                    "clarification_needed": True,
                    "clarification_question": output.clarifying_question,
                    "ranked_hypotheses": [],
                    "selected_hypothesis": None,
                    "decision": "CLARIFICATION_REQUESTED",
                }

            # Valid hypotheses produced
            ranked = [h.model_dump() for h in output.hypotheses]
            selected = ranked[0] if ranked else None

            return {
                "clarification_needed": False,
                "clarification_question": None,
                "ranked_hypotheses": ranked,
                "selected_hypothesis": selected,
                "decision": "HYPOTHESES_RANKED",
            }

        except Exception as e:
            # Deterministic safe fallback on LLM failure
            fallback_hyp = Hypothesis(
                rank=1,
                fault=f"General mechanical inspection required for {machine_type} (LLM diagnostic fallback)",
                subsystem="General Mechanical Subsystem",
                confidence=0.30,
                rationale="Automated diagnostic fallback triggered due to LLM service failure.",
            )
            return {
                "clarification_needed": False,
                "clarification_question": None,
                "ranked_hypotheses": [fallback_hyp.model_dump()],
                "selected_hypothesis": fallback_hyp.model_dump(),
                "decision": "FALLBACK_DIAGNOSIS",
            }


def resume_with_answer(
    state: BlackboardState,
    answer: str,
    agent: Optional[DiagnosticAgent] = None,
) -> BlackboardState:
    """Resume a halted diagnostic workflow with operator's clarification answer.

    Args:
        state: BlackboardState currently paused on clarification_needed=True.
        answer: Operator's clarification response (e.g. machine type or additional symptom).
        agent: Optional DiagnosticAgent instance to invoke.

    Returns:
        Updated BlackboardState with resumed diagnosis.
    """
    state["clarification_turns"] = state.get("clarification_turns", 0) + 1
    state["clarification_needed"] = False
    state["clarification_question"] = None

    # Check if answer clarifies machine type
    ans_lower = answer.lower()
    known_machines = [
        "CNC Lathe (Turning Center)",
        "Hydraulic Press (Stamping & Forging)",
        "Injection-Moulding Machine",
        "Conveyor Line (Automated Material Handling)",
        "Rotary Screw Air Compressor",
    ]

    current_machine = state.get("machine_type")
    if not current_machine or current_machine.lower() in ("unspecified", "unknown"):
        for m in known_machines:
            # Match keywords
            key = m.split()[0].lower()
            if key in ans_lower:
                state["machine_type"] = m
                break
        if state.get("machine_type") in ("unspecified", "unknown", None):
            state["machine_type"] = answer.strip()

    # Append clarification context to query
    state["raw_query"] = f"{state.get('raw_query', '')} [Operator Clarification: {answer.strip()}]"

    diag_agent = agent or DiagnosticAgent()
    return diag_agent.run(state)
