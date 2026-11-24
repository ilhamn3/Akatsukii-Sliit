"""Unverified extraction contract. Unknown facts stay null, not invented strings."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)


class SourceQuote(StrictModel):
    segment_id: str = Field(min_length=1)
    quote: str = Field(min_length=1)


class ExtractedFact(StrictModel):
    value: str = Field(min_length=1)
    sources: list[SourceQuote] = Field(min_length=1)


class DraftFailedAttempt(StrictModel):
    action: ExtractedFact
    outcome: ExtractedFact | None
    why_failed: ExtractedFact | None


class ExtractedRepair(StrictModel):
    # All keys are required, but missing source information uses null / [].
    machine_type: ExtractedFact | None = Field(description=(
        "Equipment category explicitly named in the source, including ordinary "
        "equipment nouns. No brand, model number or catalogue match is required. "
        "Null only if the equipment category is unstated or ambiguous."
    ))
    machine_id: ExtractedFact | None = Field(description=(
        "Explicit asset identifier, independently nullable even when the equipment "
        "category is known. Do not invent an identifier."
    ))
    symptoms: list[ExtractedFact]
    steps: list[ExtractedFact]
    tools: list[ExtractedFact]
    parts: list[ExtractedFact] = Field(description=(
        "Parts explicitly installed, replaced or consumed, including failed attempts. "
        "Exclude components merely inspected or adjusted."
    ))
    root_cause: ExtractedFact | None
    failed_attempts: list[DraftFailedAttempt]
    safety_warnings: list[ExtractedFact]
    outcome: ExtractedFact | None


class EvidenceSpan(StrictModel):
    field_path: str
    evidence_id: str
    quote: str
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)


class DraftRepairRecord(StrictModel):
    status: Literal["draft"] = "draft"
    capture_id: str
    source_sha256: str
    source_path: str
    source_type: str
    extraction: ExtractedRepair
    evidence: list[EvidenceSpan]
    missing_fields: list[str]
    review_reasons: list[str]
    review_required: Literal[True] = True
    # A3 owns scoring; no invented or calibrated-confidence claim from A2.
    confidence: None = None
    model: str
    prompt_version: str = "extraction_v3"
