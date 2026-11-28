"""What a reviewer must see and may correct before approving a queued draft (K4).

Pure functions over ReviewItem JSON (`ReviewItem.model_dump(mode="json")`), so the
Streamlit review page stays thin and this logic is unit-tested without a UI.

Approval publishes every field of `draft.extraction` to the verified store, so
`fact_sections` lists all of them, including empty ones: a reviewer has to see what
is missing as well as what is there. Blocking issues are resolved only by a
correction of their field (`a3_validation.resolved`); `correction_targets` maps each
issue to the correction key A3 accepts.
"""

from __future__ import annotations

import re
from typing import Any

# (draft field, label, kind) in reading order. Every ExtractedRepair field is here;
# a test fails if the schema gains a field this list does not show.
FACT_FIELDS = (
    ("machine_type", "Machine type", "scalar"),
    ("machine_id", "Machine ID", "scalar"),
    ("symptoms", "Symptoms", "list"),
    ("steps", "Steps", "list"),
    ("tools", "Tools", "list"),
    ("parts", "Parts", "list"),
    ("root_cause", "Root cause", "scalar"),
    ("failed_attempts", "Failed attempts", "attempts"),
    ("safety_warnings", "Safety warnings", "list"),
    ("outcome", "Outcome", "scalar"),
)
ATTEMPT_CHILDREN = ("action", "outcome", "why_failed")
LIST_FIELDS = {name for name, _, kind in FACT_FIELDS if kind == "list"}
SCALAR_FIELDS = {name for name, _, kind in FACT_FIELDS if kind == "scalar"}
_ATTEMPT_PATH = re.compile(r"failed_attempts\.(\d+)\.(action|outcome|why_failed)")

# Review reasons that change how much the reviewer can trust the draft itself.
FLAG_NOTES = {
    "offline_fixture_output": ("error", "Fixture output, not a model run. Do not publish as real knowledge."),
    "extraction_retried_after_invalid_output": (
        "warning", "The model's first output was invalid; this draft came from a corrective retry."),
}


def fact_view(fact: dict | None) -> dict | None:
    """{value, quotes} for one ExtractedFact, or None when the fact is unknown."""
    if not fact:
        return None
    quotes = [s.get("quote", "") for s in fact.get("sources") or [] if s.get("quote")]
    return {"value": fact.get("value", ""), "quotes": quotes}


def fact_sections(extraction: dict) -> list[dict]:
    """Every extraction field, in order, as {field, label, kind, items}.

    items: scalar -> [fact_view] or []; list -> [fact_view, ...];
    attempts -> [{action, outcome, why_failed}] of fact_view-or-None.
    """
    sections = []
    for name, label, kind in FACT_FIELDS:
        raw = extraction.get(name)
        if kind == "scalar":
            items = [fact_view(raw)] if raw else []
        elif kind == "list":
            items = [fact_view(f) for f in raw or []]
        else:
            items = [{c: fact_view(a.get(c)) for c in ATTEMPT_CHILDREN} for a in raw or []]
        sections.append({"field": name, "label": label, "kind": kind, "items": items})
    return sections


def correction_targets(issues: list[dict], extraction: dict) -> list[dict]:
    """One input per correctable blocking issue, deduplicated by correction key.

    Returns [{key, kind ("text" | "lines"), current, issues: [...]}]. List fields are
    corrected as a whole list (A3 resolves "steps.2" through a "steps" correction),
    prefilled with the current values. Issues without a field (e.g. duplicate_source)
    are not correctable here; see `uncorrectable`.
    """
    targets: dict[str, dict] = {}
    for issue in issues:
        if issue.get("severity") != "blocking" or not issue.get("field_path"):
            continue
        path = issue["field_path"]
        if match := _ATTEMPT_PATH.fullmatch(path):
            attempts = extraction.get("failed_attempts") or []
            index, child = int(match[1]), match[2]
            fact = attempts[index].get(child) if index < len(attempts) else None
            key, kind, current = path, "text", (fact or {}).get("value", "")
        else:
            key = path.split(".")[0]
            if key in LIST_FIELDS:
                kind = "lines"
                current = "\n".join(f.get("value", "") for f in extraction.get(key) or [])
            elif key in SCALAR_FIELDS:
                kind, current = "text", (extraction.get(key) or {}).get("value", "")
            else:
                continue
        target = targets.setdefault(key, {"key": key, "kind": kind, "current": current, "issues": []})
        target["issues"].append(issue)
    return list(targets.values())


def uncorrectable(issues: list[dict]) -> list[dict]:
    """Blocking issues no field correction can resolve: the draft can only be rejected."""
    keys = LIST_FIELDS | SCALAR_FIELDS | {"failed_attempts"}
    return [i for i in issues if i.get("severity") == "blocking"
            and (not i.get("field_path") or i["field_path"].split(".")[0] not in keys)]


def build_corrections(targets: list[dict], inputs: dict[str, str]) -> dict[str, Any]:
    """Corrections for ReviewQueue.approve from reviewer inputs keyed by target key.

    Blank inputs are left out, so their blocking issue stays and approval is refused;
    nothing is filled in for the reviewer. "lines" inputs become one item per line.
    """
    corrections: dict[str, Any] = {}
    for target in targets:
        text = (inputs.get(target["key"]) or "").strip()
        if not text:
            continue
        if target["kind"] == "lines":
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            if lines:
                corrections[target["key"]] = lines
        else:
            corrections[target["key"]] = text
    return corrections


def review_flags(draft: dict) -> list[tuple[str, str]]:
    """(level, message) banners for review reasons the reviewer must not miss."""
    reasons = draft.get("review_reasons") or []
    return [FLAG_NOTES[r] for r in reasons if r in FLAG_NOTES]


def confidence_label(item: dict) -> str:
    """Confidence text that always states the method; it is not a probability."""
    method = ((item.get("report") or {}).get("confidence") or {}).get("method", "unknown method")
    return f"{float(item.get('confidence', 0.0)):.0%} (uncalibrated rule-based score `{method}`, not a probability)"
