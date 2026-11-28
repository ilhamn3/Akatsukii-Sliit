"""A3: rule-based validation, deduplication, confidence scoring and review routing.

A3 maps an unverified DraftRepairRecord onto the shared RepairRecord field names
WITHOUT inventing facts, checks grounding/consistency, detects exact and semantic
duplicates, computes a transparent (uncalibrated) confidence score and enqueues
EVERY draft for human review. Neither a high score nor any status label verifies a
record: only ReviewQueue.approve() with a reviewer identity creates a
VerifiedEpisode. Rules only, no LLM critic (work-plan cut line); no API key needed.
"""

import argparse
import copy
import json
from pathlib import Path
import re
import sys

from pydantic import ValidationError

from project.agents.a2_extraction import facts
from project.agents.base import BaseAgent
from project.core.extraction_schemas import DraftRepairRecord
from project.core.registry import load_config
from project.core.schemas import RepairRecord
from project.core.state import create_initial_state
from project.agents.a4_graph import ENTITY_CODES, default_resolver, entity_issues
from project.core.validation_schemas import (  # ReviewBlocked re-exported for K4 callers
    ConfidenceBreakdown, ReviewBlocked, ValidationIssue, ValidationReport,
)
from project.store.models import Provenance, VerifiedEpisode, label_key
from project.store.vector_index import normalized

REQUIRED = ("machine_type", "symptoms", "steps", "root_cause")
ATTEMPT_FIELDS = ("action", "outcome", "why_failed")
LIST_FIELDS = ("symptoms", "steps", "tools", "parts", "safety_warnings")
CORRECTABLE = {"machine_type", "machine_id", "symptoms", "steps", "tools", "parts",
               "root_cause", "failed_attempts", "safety_warnings", "outcome"}
ATTEMPT_PATH = re.compile(r"failed_attempts\.(\d+)\.(action|outcome|why_failed)")
# Heuristic weights (sum 1.0). Development defaults, not fitted to labelled data.
WEIGHTS = {"required_completeness": 0.45, "failed_attempt_completeness": 0.15,
           "grounding": 0.25, "capture_quality": 0.15}
# Symptom-cosine threshold for the possible_duplicate warning (same machine). Chosen on the
# dev split on 28 Nov 2026 (docs/VALIDATION_REVIEW.md); config.yaml carries the same value.
DUPLICATE_THRESHOLD = 0.55
# Warnings that indicate conflicting/duplicate knowledge get review priority.
CONFLICT_CODES = {"possible_duplicate", "failed_attempt_conflicts_with_step"}
UNITS = (r"psi|bar|kpa|mpa|nm|n·m|n-m|ft-lbs?|lbf|mm|cm|rpm|kw|hz|°c|°f|degc|degf|"
         r"vac|vdc|amps?|volts?|litres?|liters?|ml")
QUANTITY = re.compile(rf"(\d+(?:[.,]\d+)?)\s*({UNITS})?(?!\w)", re.IGNORECASE)
NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def episode_ids(draft: DraftRepairRecord) -> tuple[str, str]:
    """Deterministic episode and primary evidence IDs from the source hash."""
    key = draft.source_sha256[:16].upper()
    return f"EP-{key}", f"EV-{key}"


def _issue(code, severity, path, message, related=()):
    return ValidationIssue(code=code, severity=severity, field_path=path,
                           message=message, related_ids=list(related))


def _norm(text: str) -> str:
    return label_key(text).rstrip(".")


SEGMENT_SUFFIX = re.compile(r"-S(\d+)$")


def _segment_index(evidence_id: str) -> int:
    """Transcript segment number from an A1 evidence ID (`<capture>-S0003` -> 3); -1 if absent."""
    match = SEGMENT_SUFFIX.search(evidence_id)
    return int(match[1]) if match else -1


def _spans(draft):
    spans = {}
    for span in draft.evidence:
        spans.setdefault(span.field_path, []).append(span)
    return spans


def _quantity_problems(value: str, quotes: str) -> list[str]:
    """Numbers/units in a fact must literally appear in its cited quotes."""
    quoted = set(NUMBER.findall(quotes))
    problems = []
    for number, unit in QUANTITY.findall(value):
        if number not in quoted:
            problems.append(f"Value '{number}' does not appear in the cited source quote")
        elif unit and not re.search(rf"(?<![\d.,]){re.escape(number)}\s*{re.escape(unit)}(?!\w)",
                                    quotes, re.IGNORECASE):
            problems.append(f"Unit '{number} {unit}' does not match the cited source quote")
    return problems


def static_issues(draft: DraftRepairRecord) -> list[ValidationIssue]:
    """Schema-gap, grounding and internal-consistency checks of one draft."""
    ex, spans, issues = draft.extraction, _spans(draft), []
    for name in REQUIRED:
        if not getattr(ex, name):
            issues.append(_issue("missing_required_fact", "blocking", name,
                                 f"RepairRecord requires {name}; the source draft does not state it"))
    for index, attempt in enumerate(ex.failed_attempts):
        for child in ("outcome", "why_failed"):
            if getattr(attempt, child) is None:
                issues.append(_issue("missing_required_fact", "blocking", f"failed_attempts.{index}.{child}",
                                     f"FailedAttempt requires {child}; the source draft does not state it"))
    # K5 catalogue resolution; approval re-checks it on the reviewer-corrected values.
    issues += entity_issues(default_resolver(), ex.machine_type and ex.machine_type.value,
                            ex.machine_id and ex.machine_id.value)[0]
    fact_map = dict(facts(ex))
    for path, fact in fact_map.items():
        cited = spans.get(path)
        if not cited:
            issues.append(_issue("ungrounded_fact", "blocking", path, "No evidence span supports this fact"))
            continue
        for problem in _quantity_problems(fact.value, " ".join(s.quote for s in cited)):
            issues.append(_issue("quantity_not_in_quote", "blocking", path, problem,
                                 [s.evidence_id for s in cited]))
    for path in sorted(spans.keys() - fact_map.keys()):
        issues.append(_issue("orphan_evidence", "warning", path, "Evidence span refers to no extracted fact",
                             [s.evidence_id for s in spans[path]]))
    for name in LIST_FIELDS:
        seen = {}
        for index, fact in enumerate(getattr(ex, name)):
            key = _norm(fact.value)
            if key in seen:
                issues.append(_issue("repeated_fact", "warning", f"{name}.{index}",
                                     f"Repeats {name}.{seen[key]}"))
            seen.setdefault(key, index)
    steps = {_norm(f.value): i for i, f in enumerate(ex.steps)}
    for index, attempt in enumerate(ex.failed_attempts):
        if (step := steps.get(_norm(attempt.action.value))) is not None:
            issues.append(_issue("failed_attempt_conflicts_with_step", "warning", f"failed_attempts.{index}.action",
                                 f"The same action is also a completed step (steps.{step}); confirm which is correct"))
    # Narration position of each step: its earliest cited span by time, then by segment
    # index. Text captures carry no timing (every span starts at 0 s), so without the
    # segment index the order check could never fire on them.
    starts = [min((s.start_seconds, _segment_index(s.evidence_id)) for s in spans[f"steps.{i}"])
              for i in range(len(ex.steps)) if spans.get(f"steps.{i}")]
    if any(later < earlier for earlier, later in zip(starts, starts[1:])):
        issues.append(_issue("steps_out_of_source_order", "warning", "steps",
                             "Step order differs from narration order; confirm the sequence"))
    if ex.outcome is None:
        issues.append(_issue("outcome_unknown", "warning", "outcome", "Overall repair outcome was not stated"))
    if "capture_quality_flags" in draft.review_reasons:
        issues.append(_issue("capture_quality_flags", "warning", None, "Capture flagged unclear audio or quality problems"))
    if "offline_fixture_output" in draft.review_reasons:
        issues.append(_issue("offline_fixture_output", "warning", None,
                             "Draft came from a canned offline fixture, not live extraction"))
    return issues


def score(draft: DraftRepairRecord, issues: list[ValidationIssue]) -> ConfidenceBreakdown:
    """Transparent heuristic in [0, 1]; components are reported for reviewers."""
    ex = draft.extraction
    attempts = ex.failed_attempts
    paths = [path for path, _ in facts(ex)]
    bad = {i.field_path for i in issues if i.code in ("ungrounded_fact", "quantity_not_in_quote")}
    parts = {
        "required_completeness": sum(bool(getattr(ex, n)) for n in REQUIRED) / len(REQUIRED),
        "failed_attempt_completeness": (sum(a.outcome is not None and a.why_failed is not None
                                            for a in attempts) / len(attempts)) if attempts else 1.0,
        "grounding": (sum(p not in bad for p in paths) / len(paths)) if paths else 0.0,
        "capture_quality": 0.5 if "capture_quality_flags" in draft.review_reasons else 1.0,
    }
    value = sum(WEIGHTS[key] * part for key, part in parts.items())
    return ConfidenceBreakdown(**{k: round(v, 4) for k, v in parts.items()}, score=round(value, 4))


def _timestamp(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def draft_fields(draft: DraftRepairRecord) -> dict:
    """Index-preserving map of draft facts onto RepairRecord names; None = unknown.

    Order is preserved so provenance span paths (e.g. steps.2) keep their meaning.
    """
    ex, spans = draft.extraction, _spans(draft)
    steps = []
    for index, fact in enumerate(ex.steps):
        cited = sorted(spans.get(f"steps.{index}", []), key=lambda s: s.start_seconds)
        steps.append({"step_number": index + 1, "description": fact.value,
                      "timestamp": _timestamp(cited[0].start_seconds) if cited else None,
                      "evidence_id": cited[0].evidence_id if cited else None})
    value = lambda fact: fact.value if fact is not None else None  # noqa: E731
    return {
        "machine_type": value(ex.machine_type), "machine_id": value(ex.machine_id),
        "symptoms": [f.value for f in ex.symptoms], "steps": steps,
        "tools": [f.value for f in ex.tools], "parts": [f.value for f in ex.parts],
        "root_cause": value(ex.root_cause),
        "failed_attempts": [{c: value(getattr(a, c)) for c in ATTEMPT_FIELDS} for a in ex.failed_attempts],
        "safety_warnings": [f.value for f in ex.safety_warnings], "outcome": value(ex.outcome),
    }


def apply_corrections(fields: dict, corrections: dict | None) -> tuple[dict, list[str]]:
    """Apply reviewer-supplied values; returns new fields and corrected keys.

    Keys are RepairRecord field names (+ 'outcome') or failed_attempts.<i>.<child>.
    Steps may be given as strings; reviewer-written steps carry no source evidence.
    """
    fields, supplied = copy.deepcopy(fields), []
    for key, value in (corrections or {}).items():
        if match := ATTEMPT_PATH.fullmatch(key):
            index = int(match[1])
            if index >= len(fields["failed_attempts"]):
                raise ValueError(f"No failed attempt exists at {key}")
            fields["failed_attempts"][index][match[2]] = value
        elif key in CORRECTABLE:
            if key == "steps" and isinstance(value, list):
                value = [{"step_number": i + 1, "description": s} if isinstance(s, str) else s
                         for i, s in enumerate(value)]
            fields[key] = value
        else:
            raise ValueError(f"Unsupported correction field: {key}")
        supplied.append(key)
    return fields, sorted(supplied)


def build_record(draft, confidence: float, corrections=None):
    """Return (RepairRecord, outcome, corrected keys). Raises ValidationError if a
    required fact is still unknown; nothing is filled with placeholder text."""
    fields, supplied = apply_corrections(draft_fields(draft), corrections)
    outcome = fields.pop("outcome")
    record = RepairRecord.model_validate({**fields, "confidence": confidence,
                                          "evidence_id": episode_ids(draft)[1], "source": draft.source_type})
    return record, outcome, supplied


def _schema_issues(exc: ValidationError, covered: set[str]) -> list[ValidationIssue]:
    issues = []
    for error in exc.errors():
        path = ".".join(str(part) for part in error["loc"])
        if not any(path == c or path.startswith(c + ".") for c in covered):
            issues.append(_issue("schema_violation", "blocking", path, error["msg"]))
    return issues


def resolved(issue: ValidationIssue, supplied: list[str]) -> bool:
    path = issue.field_path
    return bool(path) and any(path == key or path.startswith(key + ".") for key in supplied)


def duplicate_issues(draft, fields, *, queue, store, trace_id, threshold, exclude_item=None):
    """Exact (same recording) duplicates block; semantic matches are warnings,
    because a repeated fault on the same machine may be a genuine recurrence."""
    episode_id, issues = episode_ids(draft)[0], []
    related = [] if queue is None else [i.item_id for i in queue.active_by_source(draft.source_sha256)
                                        if i.item_id != exclude_item]
    try:
        if store is not None and store.get(episode_id, trace_id=trace_id) is not None:
            related.append(episode_id)
    except Exception as exc:  # K8 fallback: approval re-checks the store before publishing.
        issues.append(_issue("store_dedup_unavailable", "warning", None,
                             f"Verified-store duplicate check failed ({type(exc).__name__}); "
                             "approval re-checks it before publishing"))
    if related:
        issues.append(_issue("duplicate_source", "blocking", None,
                             "This recording is already pending review or verified", related))
    if not fields["symptoms"] or not fields["machine_type"]:
        return issues
    if store is None:
        issues.append(_issue("semantic_dedup_unavailable", "warning", None,
                             "No knowledge store configured; semantic duplicate check skipped"))
        return issues
    try:
        return issues + _semantic_duplicates(draft, fields, episode_id, queue=queue, store=store,
                                             trace_id=trace_id, threshold=threshold, exclude_item=exclude_item)
    except Exception as exc:
        # K8 fallback: an unavailable embedding model or store must not stop the draft
        # from reaching human review; the reviewer sees that the check did not run.
        return issues + [_issue("semantic_dedup_unavailable", "warning", None,
                                f"Semantic duplicate check failed ({type(exc).__name__}); review manually")]


def _semantic_duplicates(draft, fields, episode_id, *, queue, store, trace_id, threshold, exclude_item):
    issues = []
    resolver = default_resolver()

    def machine_key(label):  # Catalogue ID when resolvable (K5), else normalized label.
        return resolver.canonical_machine(label) or label_key(label)

    text, machine = "\n".join(fields["symptoms"]), machine_key(fields["machine_type"])
    # Verified episodes are stored with canonical machine IDs by A4.
    matches = [hit.episode_id for hit in store.search_symptoms(
        text, trace_id=trace_id, k=3, machine_type=machine, min_score=threshold) if hit.episode_id != episode_id]
    pending = [] if queue is None else [
        item for item in queue.list_items(status="pending")
        if item.item_id != exclude_item and item.source_sha256 != draft.source_sha256
        and item.draft.extraction.machine_type is not None and item.draft.extraction.symptoms
        and machine_key(item.draft.extraction.machine_type.value) == machine]
    if pending:
        texts = ["\n".join(f.value for f in item.draft.extraction.symptoms) for item in pending]
        vectors = normalized(store.embedder.encode([text] + texts), store.embedder.dimension)
        matches += [item.item_id for item, sim in zip(pending, vectors[1:] @ vectors[0]) if sim >= threshold]
    if matches:
        issues.append(_issue("possible_duplicate", "warning", "symptoms",
                             f"Symptoms closely match existing records for the same machine label "
                             f"(cosine >= {threshold}); confirm this is a new episode", matches))
    return issues


def validate_draft(draft, *, trace_id, queue=None, store=None, threshold=0.75,
                   duplicate_threshold=DUPLICATE_THRESHOLD, exclude_item=None) -> ValidationReport:
    draft = DraftRepairRecord.model_validate(
        draft.model_dump(mode="json") if isinstance(draft, DraftRepairRecord) else draft)
    issues = static_issues(draft)
    fields = draft_fields(draft)
    issues += duplicate_issues(draft, fields, queue=queue, store=store, trace_id=trace_id,
                               threshold=duplicate_threshold, exclude_item=exclude_item)
    confidence = score(draft, issues)
    candidate = None
    try:
        candidate = build_record(draft, confidence.score)[0]
    except ValidationError as exc:
        covered = {i.field_path for i in issues if i.code == "missing_required_fact"}
        issues += _schema_issues(exc, covered)
    low = (confidence.score < threshold or any(i.severity == "blocking" for i in issues)
           or any(i.code in CONFLICT_CODES for i in issues))
    return ValidationReport(
        episode_id=episode_ids(draft)[0], source_sha256=draft.source_sha256,
        candidate_record=candidate, outcome=fields["outcome"], issues=issues, confidence=confidence,
        threshold=threshold, priority="low_confidence" if low else "standard")


def verify(draft, *, reviewer: str, verified_at, corrections=None):
    """Build the VerifiedEpisode for a human approval, re-deriving everything from
    the draft (a stored report, label or confidence is never trusted)."""
    draft = DraftRepairRecord.model_validate(draft.model_dump(mode="json"))
    fields, supplied = apply_corrections(draft_fields(draft), corrections)
    static = static_issues(draft)
    unresolved = [i for i in static if i.severity == "blocking" and i.code not in ENTITY_CODES
                  and not resolved(i, supplied)]
    # Entity checks are rerun on the corrected values: editing machine_type only
    # helps if the new value maps to exactly one catalogue machine.
    unresolved += [i for i in entity_issues(default_resolver(), fields["machine_type"], fields["machine_id"])[0]
                   if i.severity == "blocking"]
    if unresolved:
        raise ReviewBlocked(unresolved)
    try:
        record, outcome, supplied = build_record(draft, score(draft, static).score, corrections)
    except ValidationError as exc:
        raise ReviewBlocked(_schema_issues(exc, set())) from exc
    episode_id = episode_ids(draft)[0]
    provenance = Provenance(
        capture_id=draft.capture_id, source_path=draft.source_path, source_sha256=draft.source_sha256,
        source_type=draft.source_type, evidence_spans=draft.evidence, extraction_model=draft.model,
        prompt_version=draft.prompt_version, reviewer_supplied_fields=supplied)
    return VerifiedEpisode(episode_id=episode_id, status="verified", record=record, provenance=provenance,
                           verified_by=reviewer, verified_at=verified_at, outcome=outcome), supplied


class ValidationAgent(BaseAgent):
    """Pipeline A node: draft_record -> validation_report + pending review item."""

    def __init__(self, queue=None, store=None, trace_logger=None, threshold=None, duplicate_threshold=None):
        super().__init__("ValidationAgent", "Validation, dedup, confidence and review routing", "A", trace_logger)
        system = load_config().get("system", {})
        self.queue, self.store = queue, store
        self.threshold = float(threshold if threshold is not None else system.get("confidence_threshold_review", 0.75))
        self.duplicate_threshold = float(duplicate_threshold if duplicate_threshold is not None
                                         else system.get("duplicate_similarity_threshold", DUPLICATE_THRESHOLD))

    def run(self, state):
        state["validation_report"], state["review_item_id"] = None, None
        return super().run(state)

    def process(self, state):
        payload = state.get("draft_record")
        if not payload and state.get("errors"):
            # Upstream failure (e.g. provider timeout) is already traced; skip without
            # adding a derivative error so the trace shows the real cause.
            return {"decision": "SKIPPED_UPSTREAM_FAILURE"}
        if not payload:
            raise ValueError("No extraction draft to validate; Agent 2 did not produce one")
        draft = DraftRepairRecord.model_validate(payload)
        if self.store is None:
            from project.store.knowledge_store import KnowledgeStore
            self.store = KnowledgeStore()
        if self.queue is None:
            from project.store.review_queue import ReviewQueue
            self.queue = ReviewQueue(store=self.store)
        trace_id = state.get("trace_id") or "TR-UNKNOWN"
        if (existing := self.queue.find_identical(draft)) is not None:
            return {"validation_report": existing.report.model_dump(mode="json"),
                    "review_item_id": existing.item_id, "decision": "REVIEW_ITEM_EXISTS"}
        report = validate_draft(draft, trace_id=trace_id, queue=self.queue, store=self.store,
                                threshold=self.threshold, duplicate_threshold=self.duplicate_threshold)
        item = self.queue.enqueue(draft, report, trace_id=trace_id)
        decision = "QUEUED_LOW_CONFIDENCE" if report.priority == "low_confidence" else "QUEUED_FOR_REVIEW"
        return {"validation_report": report.model_dump(mode="json"), "review_item_id": item.item_id,
                "decision": decision}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate an Agent 2 draft and add it to the review queue")
    parser.add_argument("--draft", required=True, help="Agent 2 draft JSON")
    parser.add_argument("--queue-db", help="Review queue SQLite path (default: storage root)")
    parser.add_argument("--store-db", help="Knowledge store SQLite path (default: storage root)")
    parser.add_argument("--local-files-only", action="store_true", help="Use cached MiniLM weights only")
    args = parser.parse_args(argv)
    try:
        from project.store.embeddings import MiniLMEmbedder
        from project.store.knowledge_store import KnowledgeStore
        from project.store.review_queue import ReviewQueue
        store = KnowledgeStore(Path(args.store_db) if args.store_db else None,
                               embedder=MiniLMEmbedder(local_files_only=args.local_files_only))
        queue = ReviewQueue(Path(args.queue_db) if args.queue_db else None, store=store)
        state = create_initial_state("")
        state["draft_record"] = json.loads(Path(args.draft).read_text(encoding="utf-8"))
        result = ValidationAgent(queue=queue, store=store).run(state)
        if result.get("review_item_id") is None:
            raise ValueError("; ".join(result.get("errors") or ["Validation failed; inspect the trace log"]))
        print(json.dumps({"review_item_id": result["review_item_id"],
                          "priority": result["validation_report"]["priority"],
                          "confidence": result["validation_report"]["confidence"],
                          "issues": [f"{i['severity']}:{i['code']}@{i['field_path']}"
                                     for i in result["validation_report"]["issues"]]}, indent=2))
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
