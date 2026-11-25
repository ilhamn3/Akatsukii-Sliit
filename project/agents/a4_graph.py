"""A4: catalogue entity resolution and verified graph/index writes (K5).

Resolves free-text machine labels to the five catalogue machine IDs, canonicalizes
asset identifiers ("hyd. press 3" -> PRESS-03) and links safety warnings to catalogue
hazard IDs. Matching is deterministic and data-driven (project/data/entities/
aliases.yaml plus the SafetyAgent rule keywords); there is no fuzzy or LLM guessing.
A label that matches no machine, or more than one, is never forced into a
category: the record stays blocked until a reviewer corrects it.

GraphAgent runs when a reviewer approves an item (via ReviewQueue). It rewrites
the record to canonical IDs, keeps the original text in provenance.entity_links,
and writes the episode to SQLite, the vector index and the graph through K3.
"""

from functools import lru_cache, partial
import json
from pathlib import Path
import re

import yaml

from project.agents.base import BaseAgent
from project.core.state import create_initial_state
from project.core.validation_schemas import ReviewBlocked, ValidationIssue
from project.store.models import EntityLink, VerifiedEpisode

DATA = Path(__file__).resolve().parents[1] / "data"
ALIASES = DATA / "entities" / "aliases.yaml"
FAULTS = DATA / "fault_catalogue" / "faults.json"
HAZARDS = DATA / "fault_catalogue" / "hazards.json"
ENTITY_CODES = {"machine_type_unresolved", "machine_type_ambiguous", "asset_type_conflict", "asset_id_unresolved"}
_SPLIT = re.compile(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])")
_PUNCT = re.compile(r"[^0-9a-z]+")


def _contains(phrase: str, text: str) -> bool:
    """Whole-word phrase containment on normalized text."""
    return bool(phrase) and f" {phrase} " in f" {text} "


class EntityResolver:
    def __init__(self, vocabulary: dict, faults: list[dict], hazards: list[dict],
                 rule_keywords: dict[str, list[str]] | None = None, assets: list[str] | None = None):
        self.version = vocabulary["version"]
        self.replacements = {str(k).casefold(): str(v).casefold()
                             for k, v in vocabulary.get("replacements", {}).items()}
        self.ignore = {self.normalize(w) for w in vocabulary.get("ignore_words", [])}
        self.filler = {self.normalize(w) for w in vocabulary.get("asset_filler", [])}
        catalogue = {f["machine_type"] for f in faults}
        self.machines = {}
        for machine, spec in vocabulary["machines"].items():
            if machine not in catalogue:
                raise ValueError(f"Alias vocabulary names unknown catalogue machine: {machine}")
            aliases = {self.normalize(a) for a in [machine, *spec.get("aliases", [])]}
            heads = {self.normalize(h) for h in spec.get("head_terms", [])}
            prefix = spec["asset_prefix"].upper()
            self.machines[machine] = {
                "prefix": prefix, "aliases": aliases, "heads": heads,
                "exclude": {self.normalize(x) for x in spec.get("exclude", [])},
                "vocab": set(" ".join(aliases | heads).split()) | {prefix.casefold()},
            }
        if missing := catalogue - set(self.machines):
            raise ValueError(f"Catalogue machines without alias entries: {sorted(missing)}")
        self.hazard_names = {h["hazard_id"]: h["name"] for h in hazards}
        phrases = {hid: {self.normalize(name)} for hid, name in self.hazard_names.items()}
        for source in (rule_keywords or {}, vocabulary.get("hazard_phrases", {})):
            for hid, words in source.items():
                if hid not in phrases:
                    raise ValueError(f"Unknown catalogue hazard ID in vocabulary: {hid}")
                phrases[hid] |= {self.normalize(w) for w in words}
        self.hazard_phrases = phrases
        self.generic = []
        for group in vocabulary.get("generic_hazards", {}).values():
            if unknown := set(group["candidates"]) - set(phrases):
                raise ValueError(f"Unknown generic hazard candidates: {sorted(unknown)}")
            self.generic.append(({self.normalize(p) for p in group["phrases"]}, list(group["candidates"])))
        self.machine_hazards = {}
        for fault in faults:
            self.machine_hazards.setdefault(fault["machine_type"], set()).update(fault.get("hazards", []))
        self.assets = set(assets or [])  # Optional registry of canonical asset IDs.

    def normalize(self, text: str) -> str:
        if not isinstance(text, str):  # e.g. YAML turning a bare `no` into False
            raise TypeError(f"Entity vocabulary/text must be strings, got {text!r}")
        tokens = _PUNCT.sub(" ", _SPLIT.sub(" ", text.casefold())).split()
        return " ".join(self.replacements.get(token, token) for token in tokens)

    def _link(self, kind, field_path, raw, **values):
        return EntityLink(kind=kind, field_path=field_path, raw=raw, resolver_version=self.version, **values)

    def resolve_machine(self, raw: str, field_path: str = "machine_type") -> EntityLink:
        link = partial(self._link, "machine", field_path, raw)
        text = " ".join(t for t in self.normalize(raw).split()
                        if not t.isdigit() and t not in self.ignore and t not in self.filler)
        if not text:
            return link(status="unresolved", method="no_machine_words")
        exact = [m for m, spec in self.machines.items() if text in spec["aliases"]]
        if len(exact) == 1:
            return link(status="resolved", entity_id=exact[0], method="alias")
        matched, excluded = {}, []
        for machine, spec in self.machines.items():
            if any(_contains(x, text) for x in spec["exclude"]):
                excluded.append(machine)
            elif hits := [h for h in spec["heads"] if _contains(h, text)]:
                matched[machine] = max(hits, key=len)
        # A more specific term wins: "injection press" (IMM) over "press" (hydraulic).
        matched = {m: term for m, term in matched.items()
                   if not any(other != term and _contains(term, other) for other in matched.values())}
        if len(matched) == 1:
            return link(status="resolved", entity_id=next(iter(matched)), method="head_term")
        if matched:
            return link(status="ambiguous", method="head_term", candidates=sorted(matched))
        if excluded:
            return link(status="unresolved", method="excluded_term", candidates=sorted(excluded))
        return link(status="unresolved", method="no_catalogue_match")

    def canonical_machine(self, raw: str | None) -> str | None:
        return self.resolve_machine(raw).entity_id if raw else None

    def resolve_asset(self, raw: str, machine: str, field_path: str = "machine_id") -> EntityLink:
        """Canonical <PREFIX>-<NN> asset ID when the text is one number plus words
        that belong to the record's machine; a different machine's words conflict."""
        link = partial(self._link, "asset", field_path, raw)
        tokens = [t for t in self.normalize(raw).split() if t not in self.filler and t not in self.ignore]
        numbers = [t for t in tokens if t.isdigit()]
        words = {t for t in tokens if not t.isdigit()}
        if len(numbers) != 1:
            return link(status="unresolved", method="asset_pattern")
        owners = {m for m, spec in self.machines.items() if words and words <= spec["vocab"]}
        if words and not owners:
            return link(status="unresolved", method="asset_pattern")
        if words and machine not in owners:
            return link(status="conflict", method="asset_pattern", candidates=sorted(owners | {machine}))
        canonical = f"{self.machines[machine]['prefix']}-{int(numbers[0]):02d}"
        if self.assets and canonical not in self.assets:
            return link(status="unresolved", method="asset_registry")
        return link(status="resolved", entity_id=canonical, method="asset_pattern")

    def link_hazards(self, warning: str, machine: str, index: int) -> list[EntityLink]:
        """Specific phrases link directly (possibly several hazards). Generic
        'stored energy'/lockout wording never resolves by elimination; it stays
        ambiguous, listing the candidates that occur in this machine's catalogue
        faults. Links mean 'may involve', not a verified classification."""
        link = partial(self._link, "hazard", f"safety_warnings.{index}", warning)
        text = self.normalize(warning)
        specific = sorted(h for h, phrases in self.hazard_phrases.items()
                          if any(_contains(p, text) for p in phrases))
        if specific:
            return [link(status="resolved", entity_id=h, method="hazard_phrase") for h in specific]
        generic = sorted({c for phrases, candidates in self.generic
                          if any(_contains(p, text) for p in phrases) for c in candidates})
        if generic:
            context = [h for h in generic if h in self.machine_hazards.get(machine, set())]
            return [link(status="ambiguous", method="generic_phrase", candidates=context or generic)]
        return [link(status="unresolved", method="no_hazard_match")]


@lru_cache(maxsize=1)
def default_resolver() -> EntityResolver:
    from project.core.safety_rules.rule_engine import load_safety_rules
    rules = {r.hazard_id: r.trigger_keywords for r in load_safety_rules() if r.hazard_id}
    return EntityResolver(yaml.safe_load(ALIASES.read_text(encoding="utf-8")),
                          json.loads(FAULTS.read_text(encoding="utf-8")),
                          json.loads(HAZARDS.read_text(encoding="utf-8")), rule_keywords=rules)


def entity_issues(resolver: EntityResolver, machine_type: str | None, machine_id: str | None):
    """Shared A3/A4 check. Returns (issues, machine_link, asset_link)."""
    if not machine_type:
        return [], None, None  # A3 reports the missing required fact.
    machine = resolver.resolve_machine(machine_type)
    if machine.status != "resolved":
        choices = ", ".join(sorted(resolver.machines))
        return [ValidationIssue(
            code=f"machine_type_{machine.status}", severity="blocking", field_path="machine_type",
            message=(f"'{machine_type}' does not map to exactly one catalogue machine ({machine.method}); "
                     f"correct machine_type to one of: {choices}"), related_ids=machine.candidates)], machine, None
    if machine_id:
        asset = resolver.resolve_asset(machine_id, machine.entity_id, "machine_id")
    else:  # An asset number inside the machine label, e.g. "hyd. press 3".
        asset = resolver.resolve_asset(machine_type, machine.entity_id, "machine_type")
        asset = asset if asset.status in ("resolved", "conflict") else None
    issues = []
    if asset is not None and asset.status == "conflict":
        issues.append(ValidationIssue(
            code="asset_type_conflict", severity="blocking", field_path="machine_id",
            message=f"Asset '{asset.raw}' names a different machine than {machine.entity_id}; correct machine_id or machine_type",
            related_ids=asset.candidates))
    elif asset is not None and asset.status != "resolved":
        issues.append(ValidationIssue(
            code="asset_id_unresolved", severity="warning", field_path="machine_id",
            message=f"Asset '{asset.raw}' kept as written; it does not match the <PREFIX>-<NN> convention or registry"))
    return issues, machine, asset


class GraphAgent(BaseAgent):
    """Approval-time node: verified episode -> canonical entities -> K3 store writes.

    Also acts as the ReviewQueue publisher (put_verified/get), so every approved
    record is resolved and traced before persistence.
    """

    def __init__(self, store=None, resolver: EntityResolver | None = None, trace_logger=None):
        super().__init__("GraphAgent", "Entity resolution and verified graph/index writes", "A", trace_logger)
        self.store = store
        self.resolver = resolver or default_resolver()
        self._failure = None  # Calls are serialized by the review queue's write lock.

    def resolve_episode(self, episode: VerifiedEpisode) -> tuple[VerifiedEpisode, list[EntityLink]]:
        record = episode.record
        issues, machine, asset = entity_issues(self.resolver, record.machine_type, record.machine_id)
        if blocking := [i for i in issues if i.severity == "blocking"]:
            raise ReviewBlocked(blocking)
        # Re-resolving an already canonical episode keeps the original raw text.
        prior = {(l.kind, l.field_path): l for l in episode.provenance.entity_links}

        def keep(link):
            old = prior.get((link.kind, link.field_path))
            return old if old and link.status == "resolved" and old.entity_id == link.entity_id else link

        links = [keep(machine)] + ([keep(asset)] if asset is not None else [])
        for index, warning in enumerate(record.safety_warnings):
            links += self.resolver.link_hazards(warning, machine.entity_id, index)
        machine_id = asset.entity_id if asset is not None and asset.status == "resolved" else record.machine_id
        data = episode.model_dump(mode="json")
        data["record"].update(machine_type=machine.entity_id, machine_id=machine_id)
        data["provenance"]["entity_links"] = [l.model_dump(mode="json") for l in links]
        return VerifiedEpisode.model_validate(data), links

    def process(self, state):
        try:
            episode = VerifiedEpisode.model_validate(state.get("verified_episode"))
            resolved, links = self.resolve_episode(episode)
            if self.store is None:
                raise RuntimeError("GraphAgent needs a knowledge store to write verified episodes")
            self.store.put_verified(resolved, trace_id=state.get("trace_id") or "TR-UNKNOWN")
        except Exception as exc:
            self._failure = exc
            raise
        return {"verified_episode": resolved.model_dump(mode="json"),
                "entity_links": [l.model_dump(mode="json") for l in links],
                "decision": "GRAPH_WRITE_COMPLETED"}

    def put_verified(self, episode, *, trace_id: str) -> str:
        state = create_initial_state("", trace_id=trace_id)
        state["verified_episode"] = episode.model_dump(mode="json") if hasattr(episode, "model_dump") else episode
        self._failure = None
        result = self.run(state)
        if self._failure is not None:
            failure, self._failure = self._failure, None
            raise failure
        return result["verified_episode"]["episode_id"]

    def get(self, episode_id: str, *, trace_id: str):
        return self.store.get(episode_id, trace_id=trace_id)
