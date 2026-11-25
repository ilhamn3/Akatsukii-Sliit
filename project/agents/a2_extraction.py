"""A2: source-linked, unverified repair extraction using the shared LLM client."""

import argparse
import json
from pathlib import Path
import sys

from project.agents.base import BaseAgent
from project.core.capture_schemas import CaptureResult
from project.core.extraction_schemas import (
    DraftRepairRecord, EvidenceSpan, ExtractedFact, ExtractedRepair,
)
from project.core.llm import FakeLLM, get_llm_client
from project.core.state import create_initial_state

PROMPT = Path(__file__).resolve().parents[1] / "core" / "prompts" / "extraction_v3.md"


def facts(record):
    """Yield stable JSON field paths, including separate failed-attempt facts."""
    for name in type(record).model_fields:
        value = getattr(record, name)
        if isinstance(value, ExtractedFact):
            yield name, value
        elif isinstance(value, list):
            for index, item in enumerate(value):
                path = f"{name}.{index}"
                if isinstance(item, ExtractedFact):
                    yield path, item
                else:
                    for child in ("action", "outcome", "why_failed"):
                        fact = getattr(item, child)
                        if fact is not None:
                            yield f"{path}.{child}", fact


def ground(record, capture):
    """Validate quote membership; semantic support still needs A3/human review."""
    segments = {s.evidence_id: s for s in capture.segments}
    if len(segments) != len(capture.segments):
        raise ValueError("Capture contains duplicate evidence IDs")
    evidence = []
    for path, fact in facts(record):
        for ref in fact.sources:
            segment = segments.get(ref.segment_id)
            if segment is None:
                raise ValueError(f"Unknown evidence ID for {path}: {ref.segment_id}")
            if ref.quote not in segment.text:
                raise ValueError(f"Source quote does not match segment for {path}")
            evidence.append(EvidenceSpan(
                field_path=path, evidence_id=ref.segment_id, quote=ref.quote,
                start_seconds=segment.start_seconds, end_seconds=segment.end_seconds,
            ))
    return evidence


class ExtractionAgent(BaseAgent):
    def __init__(self, llm_client=None, trace_logger=None):
        super().__init__("ExtractionAgent", "Structured repair extraction", "A", trace_logger)
        self.llm_client = llm_client
        self.llm_name = getattr(llm_client, "model_name", "unconfigured")
        self.is_stub = isinstance(llm_client, FakeLLM)

    def run(self, state):
        state["draft_record"] = None
        return super().run(state)

    def process(self, state):
        capture = CaptureResult.model_validate(state.get("capture_result"))
        if not capture.segments:
            raise ValueError("No timestamped speech available for extraction; review capture first")
        if len({s.evidence_id for s in capture.segments}) != len(capture.segments):
            raise ValueError("Capture contains duplicate evidence IDs")
        client = self.llm_client or get_llm_client()
        self.llm_name = getattr(client, "model_name", "unknown")
        self.is_stub = isinstance(client, FakeLLM)
        # No path, raw recording, credentials or unrelated state sent to the LLM.
        prompt = json.dumps({"segments": [
            {"segment_id": s.evidence_id, "text": s.text, "quality_flags": s.quality_flags}
            for s in capture.segments
        ]}, ensure_ascii=False)
        generated = client.generate_structured(
            prompt=prompt, response_model=ExtractedRepair,
            system_prompt=PROMPT.read_text(encoding="utf-8"),
        )
        record = ExtractedRepair.model_validate(generated)
        evidence = ground(record, capture)
        missing = [name for name in type(record).model_fields if not getattr(record, name)]
        for index, attempt in enumerate(record.failed_attempts):
            for field in ("outcome", "why_failed"):
                if getattr(attempt, field) is None:
                    missing.append(f"failed_attempts.{index}.{field}")
        reasons = ["awaiting_validation"]
        if missing:
            reasons.append("missing_source_information")
        if capture.review_required or capture.quality_flags or any(s.quality_flags for s in capture.segments):
            reasons.append("capture_quality_flags")
        if self.is_stub:
            reasons.append("offline_fixture_output")
        draft = DraftRepairRecord(
            capture_id=capture.capture_id, source_sha256=capture.source_sha256,
            source_path=capture.source_path, source_type=capture.media_type,
            extraction=record, evidence=evidence, missing_fields=missing,
            review_reasons=reasons, model=self.llm_name, prompt_version=PROMPT.stem,
        )
        return {"draft_record": draft.model_dump(mode="json"), "decision": "EXTRACTION_DRAFT_CREATED"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", required=True, help="Agent 1 output JSON")
    parser.add_argument("--output", default="logs/extraction_draft.json")
    parser.add_argument("--fixture-output", help="Explicit offline canned output JSON; NOT live extraction")
    args = parser.parse_args(argv)
    try:
        client = None
        if args.fixture_output:
            fixture = ExtractedRepair.model_validate_json(Path(args.fixture_output).read_text(encoding="utf-8"))
            client = FakeLLM(canned_response=fixture)
        state = create_initial_state("")
        state["capture_result"] = json.loads(Path(args.capture).read_text(encoding="utf-8"))
        result = ExtractionAgent(llm_client=client).run(state)
        if result.get("draft_record") is None:
            raise ValueError("Extraction failed; inspect the trace log for details")
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(result["draft_record"], indent=2), encoding="utf-8")
        print(f"Unverified draft saved: {destination}; model={result['draft_record']['model']}")
        return 0
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
