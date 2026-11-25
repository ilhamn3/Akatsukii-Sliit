"""A3 validation report contract (K4). Additive; RepairRecord itself is unchanged.

The confidence value is a transparent rule-based heuristic, NOT a calibrated
probability. Every report requires human review; no score or label auto-verifies.
"""

from typing import Literal
from pydantic import Field
from project.core.extraction_schemas import StrictModel
from project.core.schemas import RepairRecord

CONFIDENCE_METHOD = "rule_based_v1_uncalibrated"


class ValidationIssue(StrictModel):
    """One finding. Blocking issues must be resolved by a reviewer correction of
    the named field (or by rejection) before approval can succeed."""

    code: str = Field(min_length=1)
    severity: Literal["blocking", "warning"]
    field_path: str | None = None  # Draft field path, e.g. "failed_attempts.0.why_failed"
    message: str = Field(min_length=1)
    related_ids: list[str] = Field(default_factory=list)


class ReviewBlocked(ValueError):
    """A verification/publication step refused because blocking issues remain."""

    def __init__(self, issues: list[ValidationIssue]):
        self.issues = issues
        super().__init__("Unresolved blocking issues: " + ", ".join(
            f"{i.code}@{i.field_path or '-'}" for i in issues))


class ConfidenceBreakdown(StrictModel):
    method: Literal["rule_based_v1_uncalibrated"] = CONFIDENCE_METHOD
    required_completeness: float = Field(ge=0, le=1)
    failed_attempt_completeness: float = Field(ge=0, le=1)
    grounding: float = Field(ge=0, le=1)
    capture_quality: float = Field(ge=0, le=1)
    score: float = Field(ge=0, le=1)


class ValidationReport(StrictModel):
    episode_id: str = Field(min_length=1)
    source_sha256: str = Field(min_length=1)
    # Present only when every required RepairRecord fact came from the draft.
    candidate_record: RepairRecord | None
    outcome: str | None
    issues: list[ValidationIssue]
    confidence: ConfidenceBreakdown
    threshold: float = Field(ge=0, le=1)
    priority: Literal["low_confidence", "standard"]
    human_review_required: Literal[True] = True

    @property
    def blocking(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "blocking"]
