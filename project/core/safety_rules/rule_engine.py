"""Deterministic Safety Rule Engine for Factory Memory.

Enforces deterministic industrial safety invariants based on catalogue hazards
(H01-H16) and cross-cutting rules (SR-X01..SR-X04).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml

from project.core.schemas import ActionStep

logger = logging.getLogger(__name__)

DEFAULT_RULES_YAML = Path(__file__).resolve().parent / "rules.yaml"
DEFAULT_UNSAFE_PATTERNS_YAML = Path(__file__).resolve().parent / "unsafe_request_patterns.yaml"
DEFAULT_HAZARDS_JSON = (
    Path(__file__).resolve().parent.parent.parent / "data" / "fault_catalogue" / "hazards.json"
)


@dataclass
class SafetyRule:
    """Deterministic safety rule definition."""

    id: str
    hazard_id: Optional[str]
    name: str
    severity: str  # "critical", "high", "medium"
    trigger_keywords: List[str]
    required_control: str
    required_step_patterns: List[str]
    negative_step_patterns: List[str] = field(default_factory=list)
    message: str = ""
    catalogue_keywords: List[str] = field(default_factory=list)


@dataclass
class RuleHit:
    """Result of an evaluated safety rule violation or requirement."""

    rule_id: str
    hazard_id: Optional[str]
    name: str
    severity: str
    action: str  # "veto" | "amend"
    reason: str
    suggested_amendment: Optional[str] = None
    violating_step: Optional[str] = None


def load_safety_rules(
    rules_yaml_path: Optional[Path] = None,
    hazards_json_path: Optional[Path] = None,
) -> List[SafetyRule]:
    """Load safety rules from YAML and dynamically validate against hazards.json.

    hazards.json is treated as the single source of truth:
    every hazard defined in hazards.json MUST have a corresponding rule in rules.yaml.
    Fails loudly with ValueError if any catalogue hazard is missing a rule.
    """
    y_path = Path(rules_yaml_path or DEFAULT_RULES_YAML)
    h_path = Path(hazards_json_path or DEFAULT_HAZARDS_JSON)

    if not y_path.exists():
        raise FileNotFoundError(f"Safety rules YAML file not found: {y_path}")
    if not h_path.exists():
        raise FileNotFoundError(f"Catalogue hazards JSON file not found: {h_path}")

    # 1. Load hazards catalogue (Single source of truth)
    with open(h_path, "r", encoding="utf-8") as f:
        catalogue_hazards = json.load(f)

    hazard_map = {h["hazard_id"]: h for h in catalogue_hazards}

    # 2. Load rules YAML
    with open(y_path, "r", encoding="utf-8") as f:
        raw_yaml = yaml.safe_load(f) or {}

    raw_rules = raw_yaml.get("rules", [])
    rules: List[SafetyRule] = []
    covered_hazard_ids = set()

    for r in raw_rules:
        hid = r.get("hazard_id")
        rule_id = r.get("id")

        rule_name = r.get("name", "")
        req_control = r.get("required_control", "")
        cat_keywords: List[str] = []

        # If rule maps to a catalogue hazard, sync dynamically from hazards.json
        if hid and hid in hazard_map:
            cat_entry = hazard_map[hid]
            rule_name = cat_entry.get("name", rule_name)
            req_control = cat_entry.get("required_control", req_control)
            cat_keywords = cat_entry.get("keywords", [])
            covered_hazard_ids.add(hid)

        rule = SafetyRule(
            id=rule_id,
            hazard_id=hid,
            name=rule_name,
            severity=r.get("severity", "critical"),
            trigger_keywords=r.get("trigger_keywords", []),
            required_control=req_control,
            required_step_patterns=r.get("required_step_patterns", []),
            negative_step_patterns=r.get("negative_step_patterns", []),
            message=r.get("message", ""),
            catalogue_keywords=cat_keywords,
        )
        rules.append(rule)

    # 3. Fail loudly if any catalogue hazard has no corresponding rule
    missing_hazards = [hid for hid in hazard_map if hid not in covered_hazard_ids]
    if missing_hazards:
        raise ValueError(
            f"Catalogue hazards missing corresponding safety rules in {y_path.name}: "
            f"{', '.join(missing_hazards)}. Every hazard in {h_path.name} must be governed by a rule."
        )

    return rules


class RuleEngine:
    """Pure deterministic safety rule evaluator."""

    def __init__(
        self,
        rules: Optional[List[SafetyRule]] = None,
        rules_yaml_path: Optional[Path] = None,
        hazards_json_path: Optional[Path] = None,
    ):
        self.rules_yaml_path = rules_yaml_path
        self.hazards_json_path = hazards_json_path
        if rules is not None:
            self.rules = rules
            self.hazard_map = {r.hazard_id: r for r in rules if r.hazard_id}
        else:
            self.rules = load_safety_rules(rules_yaml_path, hazards_json_path)
            self.hazard_map = {r.hazard_id: r for r in self.rules if r.hazard_id}
        self.unsafe_patterns = self._load_unsafe_patterns()

    def _load_unsafe_patterns(self) -> List[Dict[str, Any]]:
        if not DEFAULT_UNSAFE_PATTERNS_YAML.exists():
            return []
        try:
            with open(DEFAULT_UNSAFE_PATTERNS_YAML, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return list(data.get("patterns") or [])
        except Exception as e:
            logger.warning("Failed to load unsafe_request_patterns.yaml: %s", e)
            return []

    def evaluate_unsafe_request(self, query: str) -> List[RuleHit]:
        """Scan raw user query for requests to skip or bypass required safety controls (SR-X05)."""
        if not query or not self.unsafe_patterns:
            return []
        q_lower = query.lower()
        hits: List[RuleHit] = []
        seen_controls = set()
        for pat in self.unsafe_patterns:
            regex = pat.get("regex", "")
            if not regex or not re.search(regex, q_lower):
                continue
            control_label = pat.get("control_label", "required safety control")
            if control_label in seen_controls:
                continue
            seen_controls.add(control_label)
            hids = list(pat.get("hazard_ids") or [])
            primary_hid = hids[0] if hids else None
            cat_rule = self.hazard_map.get(primary_hid) if primary_hid else None
            req_control = (
                cat_rule.required_control
                if cat_rule
                else f"Enforce {control_label} before performing any work."
            )
            hid_label = ", ".join(hids) if hids else "SR-X05"
            hits.append(
                RuleHit(
                    rule_id="SR-X05",
                    hazard_id=primary_hid,
                    name="Unsafe shortcut or safety-control bypass request",
                    severity="critical",
                    action="veto",
                    reason=(
                        f"Request to skip {control_label} refused. "
                        f"Required control ({hid_label}): {req_control}"
                    ),
                    suggested_amendment=req_control,
                    violating_step=query.strip(),
                )
            )
        return hits

    def _normalize_steps(
        self,
        steps: Union[List[str], List[Dict[str, Any]], List[ActionStep]],
    ) -> List[str]:
        """Convert any step representation to plain string instructions."""
        norm: List[str] = []
        for s in steps:
            if isinstance(s, str):
                norm.append(s.strip())
            elif isinstance(s, dict):
                instr = s.get("instruction") or s.get("action") or str(s)
                norm.append(str(instr).strip())
            elif isinstance(s, ActionStep):
                norm.append(s.instruction.strip())
            else:
                norm.append(str(s).strip())
        return norm

    def evaluate(
        self,
        steps: Union[List[str], List[Dict[str, Any]], List[ActionStep]],
        context: Optional[Dict[str, Any]] = None,
    ) -> List[RuleHit]:
        """Evaluate candidate repair steps against all deterministic rules.

        Args:
            steps: List of repair instructions.
            context: Context dictionary containing 'hazard_ids', 'machine_type', 'query', etc.

        Returns:
            List of RuleHit instances describing safety violations or required amendments.
        """
        context = context or {}
        norm_steps = self._normalize_steps(steps)
        combined_steps_text = " \n ".join(norm_steps).lower()
        context_hazard_ids = set(context.get("hazard_ids") or [])
        hits: List[RuleHit] = []

        # 0. Cross-cutting SR-X05 check: Unsafe shortcut/bypass request in raw_query
        raw_query = str(context.get("query") or "")
        if raw_query:
            hits.extend(self.evaluate_unsafe_request(raw_query))

        # 1. Cross-cutting SR-X04 check: Unknown or unclassified hazard class
        for hid in context_hazard_ids:
            if hid and hid not in self.hazard_map and not hid.startswith("SR-"):
                hits.append(
                    RuleHit(
                        rule_id="SR-X04",
                        hazard_id=hid,
                        name="Unknown hazard class mandatory escalation",
                        severity="critical",
                        action="veto",
                        reason=f"Unrecognised hazard identifier '{hid}' not present in safety catalogue. Mandatory supervisor review required before execution.",
                        suggested_amendment="Escalate procedure to plant safety supervisor for manual risk assessment and sign-off.",
                    )
                )

        # 2. Evaluate all rules
        for rule in self.rules:
            is_active = False

            # Active if hazard explicitly flagged in context
            if rule.hazard_id and rule.hazard_id in context_hazard_ids:
                is_active = True

            # Active if trigger keywords match step text
            if not is_active:
                for kw in rule.trigger_keywords:
                    kw_lower = kw.lower()
                    if kw_lower in combined_steps_text:
                        is_active = True
                        break

            # Active if catalogue keywords match step text
            if not is_active and rule.catalogue_keywords:
                for ckw in rule.catalogue_keywords:
                    if ckw.lower() in combined_steps_text:
                        is_active = True
                        break

            if not is_active:
                continue

            # A. Check Negative Step Patterns (explicit dangerous shortcuts -> immediate VETO)
            negative_hit = False
            for step in norm_steps:
                step_lower = step.lower()
                # Skip deterministic safety-control / prohibition steps injected by the pipeline
                if step_lower.startswith(("[safety control", "[safety amendment")):
                    continue
                for neg_pat in rule.negative_step_patterns:
                    if re.search(neg_pat, step_lower):
                        hits.append(
                            RuleHit(
                                rule_id=rule.id,
                                hazard_id=rule.hazard_id,
                                name=rule.name,
                                severity=rule.severity,
                                action="veto",
                                reason=(
                                    f"Step explicitly performs hazardous shortcut: '{step}'. "
                                    f"Rule {rule.id} violated: {rule.message}"
                                ),
                                suggested_amendment=rule.required_control,
                                violating_step=step,
                            )
                        )
                        negative_hit = True
                        break
                if negative_hit:
                    break

            if negative_hit:
                # Negative pattern already triggered a veto; skip positive control check
                continue

            # B. Check Positive Required Controls
            if rule.required_step_patterns:
                has_control = False
                control_step_idx = -1

                # Check patterns against each step
                for idx, step in enumerate(norm_steps):
                    step_lower = step.lower()
                    for req_pat in rule.required_step_patterns:
                        if re.search(req_pat, step_lower):
                            has_control = True
                            control_step_idx = idx
                            break
                    if has_control:
                        break

                # If no pattern hit, check keywords from required_control
                if not has_control and rule.catalogue_keywords:
                    for idx, step in enumerate(norm_steps):
                        step_lower = step.lower()
                        for kw in rule.catalogue_keywords:
                            if kw.lower() in step_lower:
                                has_control = True
                                control_step_idx = idx
                                break
                        if has_control:
                            break

                if not has_control:
                    # Missing required control -> VETO
                    hits.append(
                        RuleHit(
                            rule_id=rule.id,
                            hazard_id=rule.hazard_id,
                            name=rule.name,
                            severity=rule.severity,
                            action="veto",
                            reason=(
                                f"Required safety control missing for {rule.name} ({rule.id}). "
                                f"Requirement: {rule.required_control}"
                            ),
                            suggested_amendment=rule.required_control,
                        )
                    )
                else:
                    # Control exists! Check ordering: control must precede hazardous disassembly
                    prior_steps = norm_steps[:control_step_idx]
                    disassembly_pat = re.compile(r"\b(disconnect|unscrew|remove|loosen|disassemble|cut)\b", re.IGNORECASE)
                    had_prior_disassembly = any(disassembly_pat.search(s) for s in prior_steps)
                    if had_prior_disassembly:
                        hits.append(
                            RuleHit(
                                rule_id=rule.id,
                                hazard_id=rule.hazard_id,
                                name=rule.name,
                                severity="medium",
                                action="amend",
                                reason=(
                                    f"Safety control ordering hazard: Required control for {rule.name} appears "
                                    f"at step {control_step_idx + 1} after active disassembly/intervention. Safety isolation must precede physical work."
                                ),
                                suggested_amendment=f"Relocate safety step '{norm_steps[control_step_idx]}' to Step 1 prior to mechanical/electrical work.",
                                violating_step=norm_steps[control_step_idx],
                            )
                        )

        return hits
