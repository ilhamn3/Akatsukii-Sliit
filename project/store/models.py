"""Additive storage envelope; A3 owns verification and draft conversion."""

from datetime import datetime
from typing import Literal
from pydantic import Field, model_validator
from project.core.extraction_schemas import EvidenceSpan, StrictModel
from project.core.schemas import RepairRecord


class Provenance(StrictModel):
    capture_id: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    source_type: str = Field(min_length=1)
    # Paths retain their original draft-field meaning through A3 conversion.
    evidence_spans: list[EvidenceSpan] = Field(min_length=1)
    extraction_model: str | None = None
    prompt_version: str | None = None
    # Record fields whose values a human reviewer supplied or replaced (K4). These
    # have no transcript evidence; spans above still describe the original draft.
    reviewer_supplied_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_spans(self):
        for span in self.evidence_spans:
            if not all((span.field_path.strip(), span.evidence_id.strip(), span.quote.strip())):
                raise ValueError("Provenance spans require field path, evidence ID and quote")
            if span.end_seconds < span.start_seconds:
                raise ValueError("Evidence end must not precede its start")
        return self


class VerifiedEpisode(StrictModel):
    episode_id: str = Field(min_length=1)
    status: Literal["verified"]  # Required, never inferred from confidence alone.
    record: RepairRecord
    provenance: Provenance
    verified_by: str = Field(min_length=1)
    verified_at: datetime
    # RepairRecord currently has no overall-outcome field.
    outcome: str | None = None

    @model_validator(mode="after")
    def validate_verification(self):
        if self.verified_at.utcoffset() is None:
            raise ValueError("verified_at must include a timezone")
        return self


def label_key(value: str) -> str:
    """Whitespace/case normalization only; catalogue resolution belongs to K5."""
    return " ".join(value.split()).casefold()


def symptom_text(episode: VerifiedEpisode) -> str:
    """Index observations, not fixes, to avoid contaminating symptom similarity."""
    return "\n".join(episode.record.symptoms)
