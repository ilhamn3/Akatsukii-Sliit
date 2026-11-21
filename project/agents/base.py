"""Base agent interface for Factory Memory.

Provides the abstract BaseAgent class with automatic execution timing,
thread-safe trace logging, and robust error trapping.
"""

from abc import ABC, abstractmethod
import time
from typing import Any, Dict, Optional
from project.core.schemas import AgentMessage
from project.core.state import BlackboardState
from project.core.trace import TraceLogger


class BaseAgent(ABC):
    """Abstract base class for all specialized agents in Pipeline A and B."""

    def __init__(
        self,
        name: str,
        role: str,
        pipeline: str,
        trace_logger: Optional[TraceLogger] = None,
        is_stub: bool = False,
        llm_name: Optional[str] = None,
    ):
        self.name = name
        self.role = role
        self.pipeline = pipeline
        self.trace_logger = trace_logger or TraceLogger.get_instance()
        self.is_stub = is_stub
        self.llm_name = llm_name

    @abstractmethod
    def process(self, state: BlackboardState) -> Dict[str, Any]:
        """Execute agent-specific domain logic and return state updates.

        Args:
            state: Current read-only view of the BlackboardState.

        Returns:
            Dictionary of key-value updates to apply to the blackboard state.
        """
        pass

    def run(self, state: BlackboardState) -> BlackboardState:
        """Execute the agent wrapped with automatic timing, tracing, and error handling.

        Args:
            state: The current shared BlackboardState.

        Returns:
            The updated BlackboardState with mutations and trace records applied.
        """
        trace_id = state.get("trace_id", "TR-UNKNOWN")
        session_id = state.get("session_id", "SES-UNKNOWN")
        start_time = time.perf_counter()
        error_msg: Optional[str] = None
        decision: Optional[str] = None
        updates: Dict[str, Any] = {}

        # Log entry/start event
        self.trace_logger.log(
            agent=self.name,
            event="AGENT_START",
            input_data={"query": state.get("raw_query"), "machine": state.get("machine_type")},
            trace_id=trace_id,
            session_id=session_id,
            stub=self.is_stub,
            llm=self.llm_name,
        )

        try:
            updates = self.process(state)
            if not isinstance(updates, dict):
                raise TypeError(f"Agent '{self.name}' must return a dict of updates, got {type(updates)}")
            decision = updates.get("decision") or updates.get("verdict")
        except Exception as e:
            error_msg = f"{type(e).__name__}: {str(e)}"
            if "errors" not in state:
                state["errors"] = []
            state["errors"].append(f"[{self.name}] {error_msg}")

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        # Apply state updates
        for k, v in updates.items():
            state[k] = v

        # Construct AgentMessage
        msg = AgentMessage(
            sender=self.name,
            receiver="blackboard",
            type=f"{self.name.upper()}_OUTPUT",
            payload={"updates": updates, "error": error_msg},
            trace_id=trace_id,
        )
        if "messages" not in state:
            state["messages"] = []
        state["messages"].append(msg.model_dump())

        # Log completion event
        log_record = self.trace_logger.log(
            agent=self.name,
            event="AGENT_COMPLETED" if error_msg is None else "AGENT_ERROR",
            input_data={"query": state.get("raw_query")},
            output_data=updates,
            decision=str(decision) if decision else ("ERROR" if error_msg else "SUCCESS"),
            latency_ms=elapsed_ms,
            error=error_msg,
            trace_id=trace_id,
            session_id=session_id,
            stub=self.is_stub,
            llm=self.llm_name,
        )

        if "audit_trace" not in state:
            state["audit_trace"] = []
        state["audit_trace"].append(log_record)

        return state
