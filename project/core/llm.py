"""LLM client interface, OpenAI integration, and deterministic FakeLLM for Factory Memory.

Provides robust, retrying LLM interaction for structured schema generation,
with timeout management and offline simulation capabilities.
"""

from abc import ABC, abstractmethod
import json
import os
import time
from typing import Any, Dict, List, Optional, Type, TypeVar
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMClient(ABC):
    """Abstract base interface for LLM backends."""

    @abstractmethod
    def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """Generate unstructured text from a prompt."""
        pass

    @abstractmethod
    def generate_structured(
        self,
        prompt: str,
        response_model: Type[T],
        system_prompt: Optional[str] = None,
    ) -> T:
        """Generate structured data validated against a Pydantic model."""
        pass


class OpenAILLMClient(LLMClient):
    """Production OpenAI client with exponential backoff retries and structured validation."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.0,
        timeout: float = 30.0,
        max_retries: int = 3,
    ):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        if not self.api_key or self.api_key.startswith("your_"):
            raise ValueError("OPENAI_API_KEY is unset or default template in environment.")
        self.model = model or os.getenv("PRIMARY_LLM_MODEL", "gpt-4o-mini")
        self.model_name = self.model
        self.temperature = float(os.getenv("TEMPERATURE", str(temperature)))
        self.timeout = timeout
        self.max_retries = max_retries

    def _call_api_with_retry(self, messages: List[Dict[str, str]], json_mode: bool = False) -> str:
        try:
            import openai
        except ImportError:
            raise ImportError("openai package is required to run OpenAILLMClient.")

        client = openai.OpenAI(api_key=self.api_key, timeout=self.timeout)
        last_exception = None

        for attempt in range(self.max_retries):
            try:
                kwargs: Dict[str, Any] = {
                    "model": self.model,
                    "messages": messages,
                    "temperature": self.temperature,
                }
                if json_mode:
                    kwargs["response_format"] = {"type": "json_object"}

                response = client.chat.completions.create(**kwargs)
                content = response.choices[0].message.content
                return content or ""
            except Exception as e:
                last_exception = e
                wait_sec = (2 ** attempt) * 0.5
                time.sleep(wait_sec)

        raise RuntimeError(f"OpenAI API call failed after {self.max_retries} retries: {last_exception}")

    def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return self._call_api_with_retry(messages, json_mode=False)

    def generate_structured(
        self,
        prompt: str,
        response_model: Type[T],
        system_prompt: Optional[str] = None,
    ) -> T:
        schema_json = json.dumps(response_model.model_json_schema())
        augmented_system = (
            f"{system_prompt or ''}\n\n"
            f"You MUST output valid JSON complying strictly with this JSON Schema:\n{schema_json}"
        ).strip()

        messages = [
            {"role": "system", "content": augmented_system},
            {"role": "user", "content": prompt},
        ]

        raw_output = self._call_api_with_retry(messages, json_mode=True)
        try:
            parsed = json.loads(raw_output)
            return response_model.model_validate(parsed)
        except Exception as e:
            raise ValueError(f"Failed to validate LLM output against {response_model.__name__}: {e}\nRaw: {raw_output}")


class FakeLLM(LLMClient):
    """Deterministic offline LLM simulation for unit tests and local runs."""

    def __init__(self, should_fail: bool = False, canned_response: Optional[Any] = None):
        self.should_fail = should_fail
        self.canned_response = canned_response
        self.model_name = "fake"

    def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        if self.should_fail:
            raise ConnectionError("Simulated LLM connection timeout / failure.")
        if self.canned_response and isinstance(self.canned_response, str):
            return self.canned_response
        return "Simulated response for prompt."

    def generate_structured(
        self,
        prompt: str,
        response_model: Type[T],
        system_prompt: Optional[str] = None,
    ) -> T:
        if self.should_fail:
            raise ConnectionError("Simulated LLM structured output failure.")

        if self.canned_response and isinstance(self.canned_response, response_model):
            return self.canned_response

        # Determine realistic response based on prompt text
        p_lower = prompt.lower()

        # If model expects DiagnosticOutput (from a5_diagnostic)
        if response_model.__name__ == "DiagnosticOutput":
            from project.core.schemas import Hypothesis

            # Missing or ambiguous machine check
            if (
                "machine classification: unspecified" in p_lower
                or "machine classification: unknown" in p_lower
                or "machine classification: none" in p_lower
            ):
                return response_model.model_validate({
                    "needs_clarification": True,
                    "clarifying_question": "Could you specify the target machine category (e.g. CNC Lathe, Hydraulic Press, Air Compressor)?",
                    "hypotheses": [],
                })

            # Ambiguous/vague symptom check
            if "something feels strange" in p_lower or len(prompt.strip()) < 35:
                return response_model.model_validate({
                    "needs_clarification": True,
                    "clarifying_question": "Could you describe the specific observed symptoms, acoustic changes, or error codes?",
                    "hypotheses": [],
                })

            # Domain-grounded deterministic hypotheses based on machine type
            if "cnc lathe" in p_lower or "spindle" in p_lower or "turret" in p_lower:
                return response_model.model_validate({
                    "needs_clarification": False,
                    "clarifying_question": None,
                    "hypotheses": [
                        Hypothesis(
                            rank=1,
                            fault="Rotary union seal wear causing hydraulic micro-leakage and cavitation",
                            subsystem="Main Spindle & Chuck Clamp",
                            confidence=0.89,
                            rationale="High-pitched whining noise at high RPM accompanied by clamp pressure fluctuation is canonical rotary union degradation.",
                        ),
                        Hypothesis(
                            rank=2,
                            fault="Spindle ceramic hybrid bearing race spalling",
                            subsystem="Main Spindle",
                            confidence=0.64,
                            rationale="Vibration harmonics match bearing pass frequencies under high rotational speed.",
                        ),
                    ],
                })
            elif "hydraulic press" in p_lower or "ram" in p_lower or "accumulator" in p_lower:
                return response_model.model_validate({
                    "needs_clarification": False,
                    "clarifying_question": None,
                    "hypotheses": [
                        Hypothesis(
                            rank=1,
                            fault="Proportional pilot spool varnishing from thermal oil oxidation",
                            subsystem="Proportional Directional Valve & Main Cylinder",
                            confidence=0.91,
                            rationale="Ram chattering on descent and velocity loss under light load indicates sticking proportional pilot spool.",
                        ),
                        Hypothesis(
                            rank=2,
                            fault="Main cylinder rod seal extrusion and pressure bypass",
                            subsystem="Main Hydraulic Cylinder",
                            confidence=0.58,
                            rationale="Internal bypass can cause platen speed drops.",
                        ),
                    ],
                })
            elif "injection-moulding" in p_lower or "barrel" in p_lower:
                return response_model.model_validate({
                    "needs_clarification": False,
                    "clarifying_question": None,
                    "hypotheses": [
                        Hypothesis(
                            rank=1,
                            fault="Thermocouple sensor junction drift and solid-state relay short circuit",
                            subsystem="Barrel Heating & Temperature Control",
                            confidence=0.88,
                            rationale="Continuous heater band energization with temperature override alarms typically results from shorted SSR contacts.",
                        )
                    ],
                })
            elif "conveyor" in p_lower:
                return response_model.model_validate({
                    "needs_clarification": False,
                    "clarifying_question": None,
                    "hypotheses": [
                        Hypothesis(
                            rank=1,
                            fault="Idler roller bearing seizure causing mechanical friction overload",
                            subsystem="Drive Motor & Tensioning Assembly",
                            confidence=0.86,
                            rationale="Thermal overload trip on drive motor accompanied by belt slip indicates downstream mechanical binding.",
                        )
                    ],
                })
            elif "compressor" in p_lower:
                return response_model.model_validate({
                    "needs_clarification": False,
                    "clarifying_question": None,
                    "hypotheses": [
                        Hypothesis(
                            rank=1,
                            fault="Air/oil coalescing separator element rupture",
                            subsystem="Air/Oil Separator & Thermostatic Valve",
                            confidence=0.90,
                            rationale="Excessive oil carryover in downstream air paired with high discharge temperature points to ruptured separator media.",
                        )
                    ],
                })
            else:
                return response_model.model_validate({
                    "needs_clarification": False,
                    "clarifying_question": None,
                    "hypotheses": [
                        Hypothesis(
                            rank=1,
                            fault="Mechanical assembly degradation requiring multi-point inspection",
                            subsystem="General Mechanical Drive",
                            confidence=0.70,
                            rationale="Symptom pattern aligns with mechanical drivetrain wear.",
                        )
                    ],
                })

        # Generic default model creation
        try:
            return response_model.model_validate({})
        except Exception:
            raise NotImplementedError(f"FakeLLM does not have a default simulation for {response_model.__name__}.")


def get_llm(force_fake: Optional[bool] = None) -> LLMClient:
    """Return configured LLMClient.

    Raises ValueError if OPENAI_API_KEY is missing/unset unless force_fake or
    FACTORY_FAKE_LLM=1 is set (or running under pytest).
    """
    if force_fake is True or os.getenv("FACTORY_FAKE_LLM") == "1":
        return FakeLLM()

    key = os.getenv("OPENAI_API_KEY", "").strip()
    if key and not key.startswith("your_"):
        try:
            return OpenAILLMClient(api_key=key)
        except Exception:
            if "PYTEST_CURRENT_TEST" in os.environ or os.getenv("FACTORY_FAKE_LLM") == "1":
                return FakeLLM()
            raise

    # API key is missing or placeholder
    if "PYTEST_CURRENT_TEST" in os.environ or os.getenv("FACTORY_FAKE_LLM") == "1":
        return FakeLLM()

    raise ValueError("OPENAI_API_KEY not set; set it in .env, or pass --fake-llm for offline demo")


def get_llm_client() -> LLMClient:
    """Alias for get_llm for backwards compatibility."""
    return get_llm()
