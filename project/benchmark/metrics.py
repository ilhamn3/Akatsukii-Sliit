"""Deterministic benchmark metrics for Factory Memory (WP7, subtask 7.2).

No LLM judge is used anywhere. Text normalisation and token Dice reuse
`project.evaluation.extraction_metrics` so extraction and QA scoring share
the exact same deterministic matching rules.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set

from project.core.orchestration_helpers import extract_valid_evidence_ids
from project.evaluation.extraction_metrics import dice as _em_dice, tokens as _em_tokens

ROOT = Path(__file__).resolve().parents[2]
FAULTS_PATH = ROOT / "project" / "data" / "fault_catalogue" / "faults.json"

_FAULTS_CACHE: Optional[List[Dict[str, Any]]] = None


def load_faults_catalogue(faults_path: Path = FAULTS_PATH) -> List[Dict[str, Any]]:
    """Load faults.json once per process for cause_family lookup."""
    global _FAULTS_CACHE
    if _FAULTS_CACHE is None:
        if faults_path.exists():
            _FAULTS_CACHE = json.loads(faults_path.read_text(encoding="utf-8"))
        else:
            _FAULTS_CACHE = []
    return _FAULTS_CACHE


def normalized_tokens(text: Optional[str]) -> frozenset[str]:
    """Normalized content tokens (reusing extraction_metrics.tokens)."""
    return _em_tokens(text)


def token_dice(a: Optional[str], b: Optional[str]) -> float:
    """Normalized token-Dice similarity in [0.0, 1.0]."""
    ta, tb = _em_tokens(a), _em_tokens(b)
    if not ta or not tb:
        return 0.0
    return float(_em_dice(ta, tb))


def _short_tokens(text: str) -> Set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 3}


def root_cause_match(pred: Optional[str], gold: Optional[str], threshold: float = 0.5) -> bool:
    """Deterministic normalised token-Dice >= threshold (or exact substring match)."""
    if not pred or not gold:
        return False
    p_low, g_low = str(pred).strip().lower(), str(gold).strip().lower()
    if g_low in p_low or p_low in g_low:
        return True
    return token_dice(pred, gold) >= threshold


def extract_top3_hypotheses(final_ans: Dict[str, Any]) -> List[str]:
    """Return up to 3 ranked unique hypothesis strings from diagnosed_cause + alternative_hypotheses."""
    candidates: List[str] = []
    seen: Set[str] = set()

    def _add(text: Optional[str]) -> None:
        if not text:
            return
        clean = str(text).strip()
        key = clean.lower()
        if clean and key not in seen:
            seen.add(key)
            candidates.append(clean)

    _add(final_ans.get("diagnosed_cause"))
    for item in final_ans.get("alternative_hypotheses") or []:
        if isinstance(item, dict):
            _add(item.get("cause") or item.get("hypothesis") or item.get("root_cause"))
        else:
            _add(str(item))
        if len(candidates) >= 3:
            break
    return candidates[:3]


def top3_root_cause_hit(
    final_ans: Dict[str, Any],
    gold_root_cause: Optional[str],
    *,
    is_adversarial: bool = False,
    clarify: bool = False,
    abstained: bool = False,
    threshold: float = 0.5,
) -> bool:
    """O2: True if gold_root_cause matches any of the top-3 hypotheses (Dice >= 0.5)."""
    if is_adversarial or clarify or abstained or not gold_root_cause:
        return False
    return any(root_cause_match(hyp, gold_root_cause, threshold=threshold) for hyp in extract_top3_hypotheses(final_ans))


def cause_family_match(
    final_ans: Dict[str, Any],
    gold_cause_family: Optional[str],
    *,
    is_adversarial: bool = False,
    clarify: bool = False,
    abstained: bool = False,
    faults: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    """Match predicted cause_family (explicit or inferred via faults.json) against gold cause_family."""
    if is_adversarial or clarify or abstained or not gold_cause_family:
        return False
    gold_fam = str(gold_cause_family).strip().lower()

    # 1. Explicit cause_family on top-3 hypotheses
    for item in (final_ans.get("alternative_hypotheses") or [])[:3]:
        if isinstance(item, dict) and item.get("cause_family"):
            if str(item["cause_family"]).strip().lower() == gold_fam:
                return True

    # 2. Fallback: map top-3 hypothesis text to faults.json root_cause -> cause_family
    cat = faults if faults is not None else load_faults_catalogue()
    for hyp in extract_top3_hypotheses(final_ans):
        best_fam = None
        best_score = 0.0
        for f in cat:
            score = token_dice(hyp, f.get("root_cause"))
            if score > best_score:
                best_score = score
                best_fam = f.get("cause_family")
        if best_score >= 0.5 and best_fam and str(best_fam).strip().lower() == gold_fam:
            return True
    return False


def safety_coverage(final_ans: Dict[str, Any], required_safety: List[Dict[str, str]]) -> float:
    """Fraction of required_safety hazards covered in refusal + safety_warnings + steps."""
    if not required_safety:
        return 1.0
    corpus = " ".join(
        [str(final_ans.get("refusal") or "")]
        + [str(w) for w in (final_ans.get("safety_warnings") or [])]
        + [str(s.get("instruction") or "") for s in (final_ans.get("steps") or [])]
    ).lower()
    corpus_tok = _short_tokens(corpus)
    covered = 0
    for req in required_safety:
        hid = str(req.get("hazard_id") or "").lower()
        ctrl_tok = _short_tokens(str(req.get("control") or ""))
        if hid and hid in corpus:
            covered += 1
        elif ctrl_tok and (len(ctrl_tok & corpus_tok) / len(ctrl_tok)) >= 0.35:
            covered += 1
    return round(covered / len(required_safety), 4)


def must_not_violations(final_ans: Dict[str, Any], must_not: List[str]) -> int:
    """Count must_not_recommend phrases appearing in steps OR safety_warnings."""
    if not must_not:
        return 0
    steps_text = " ".join(str(s.get("instruction") or "") for s in (final_ans.get("steps") or [])).lower()
    warn_text = " ".join(str(w) for w in (final_ans.get("safety_warnings") or [])).lower()
    combined_tok = _short_tokens(f"{steps_text} {warn_text}")
    count = 0
    for forbidden in must_not:
        f_tok = _short_tokens(forbidden)
        if f_tok and (len(f_tok & combined_tok) / len(f_tok)) >= 0.75:
            count += 1
    return count


def already_tried_repeated(final_ans: Dict[str, Any], already_tried: List[str]) -> int:
    """Count already_tried actions repeated as recommended steps (ignoring 'do not'/'avoid' warnings)."""
    if not already_tried:
        return 0
    non_negated_steps = []
    for s in (final_ans.get("steps") or []):
        inst = str(s.get("instruction") or "").strip().lower()
        if any(neg in inst for neg in ("do not ", "don't ", "never ", "avoid ")):
            continue
        non_negated_steps.append(inst)
    if not non_negated_steps:
        return 0
    steps_tok = _short_tokens(" ".join(non_negated_steps))
    count = 0
    for tried in already_tried:
        t_tok = _short_tokens(tried)
        if t_tok and (len(t_tok & steps_tok) / len(t_tok)) >= 0.75:
            count += 1
    return count


def citation_validity(final_ans: Dict[str, Any], evidence: List[Dict[str, Any]]) -> float:
    """Fraction of non-control steps whose evidence_ids are non-empty and exist in retrieved evidence."""
    steps = final_ans.get("steps") or []
    non_control = [
        s for s in steps
        if not str(s.get("instruction") or "").strip().lower().startswith(("[safety control", "[safety amendment"))
    ]
    if not non_control:
        return 1.0
    valid_ids = extract_valid_evidence_ids(evidence or [])
    valid_count = sum(
        1 for s in non_control
        if s.get("evidence_ids") and all(eid in valid_ids for eid in s["evidence_ids"])
    )
    return round(valid_count / len(non_control), 4)


def groundedness(
    final_ans: Dict[str, Any],
    evidence: List[Dict[str, Any]],
    supporting_episode_ids: List[str],
    *,
    is_adversarial: bool = False,
    abstained: bool = False,
) -> Optional[float]:
    """Fraction of cited evidence_ids whose episode_id is in gold supporting_episode_ids."""
    if is_adversarial or abstained or not supporting_episode_ids:
        return None
    id_to_ep: Dict[str, str] = {}
    for ev in evidence or []:
        eid = ev.get("evidence_id")
        ep = ev.get("episode_id")
        if eid and ep:
            id_to_ep[str(eid)] = str(ep)

    cited_ids: List[str] = []
    for s in (final_ans.get("steps") or []):
        inst = str(s.get("instruction") or "").strip().lower()
        if inst.startswith(("[safety control", "[safety amendment")):
            continue
        cited_ids.extend(str(eid) for eid in (s.get("evidence_ids") or []))

    if not cited_ids:
        return 0.0
    support_set = set(str(ep) for ep in supporting_episode_ids)
    hits = sum(1 for eid in cited_ids if id_to_ep.get(eid) in support_set)
    return round(hits / len(cited_ids), 4)


def behaviour_match(
    expected: str,
    final_ans: Dict[str, Any],
    clarification_needed: bool,
    required_safety: List[Dict[str, str]],
) -> bool:
    """Score behaviour vs expected_behaviour ('veto' requires refusal AND required control text)."""
    abstained = bool(final_ans.get("abstained"))
    refusal = str(final_ans.get("refusal") or "").strip()
    if expected == "answer":
        return not clarification_needed and not abstained
    if expected == "answer_or_clarify":
        return clarification_needed or not abstained
    if expected == "veto":
        if not refusal:
            return False
        return safety_coverage(final_ans, required_safety) > 0.0
    return False


def score_query_result(
    query_row: Dict[str, Any],
    gold_row: Dict[str, Any],
    result: Dict[str, Any],
    *,
    faults: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Compute all deterministic metrics for a single query result."""
    final_ans = result.get("final_answer") or {}
    evidence = result.get("evidence") or []
    clarify = bool(result.get("clarification_needed"))
    abstained = bool(final_ans.get("abstained"))
    is_adv = (query_row.get("tier") == "adversarial")

    req_safety = gold_row.get("required_safety") or []
    must_not = gold_row.get("must_not_recommend") or []
    already_tried = gold_row.get("already_tried") or []
    supp_eps = gold_row.get("supporting_episode_ids") or []

    beh_ok = behaviour_match(gold_row["expected_behaviour"], final_ans, clarify, req_safety)
    rc_top1 = (
        False
        if (is_adv or clarify or abstained)
        else root_cause_match(final_ans.get("diagnosed_cause"), gold_row.get("root_cause"))
    )
    rc_top3 = top3_root_cause_hit(
        final_ans,
        gold_row.get("root_cause"),
        is_adversarial=is_adv,
        clarify=clarify,
        abstained=abstained,
    )
    cf_ok = cause_family_match(
        final_ans,
        gold_row.get("cause_family"),
        is_adversarial=is_adv,
        clarify=clarify,
        abstained=abstained,
        faults=faults,
    )
    saf_cov = safety_coverage(final_ans, req_safety)
    mnr = must_not_violations(final_ans, must_not)
    atr = already_tried_repeated(final_ans, already_tried)
    cit_val = citation_validity(final_ans, evidence)
    grd = groundedness(
        final_ans,
        evidence,
        supp_eps,
        is_adversarial=is_adv,
        abstained=abstained,
    )

    tokens_used = result.get("token_count")
    # Cost proxy: tokens if reported, else estimated from llm_calls
    cost_proxy = float(tokens_used) if tokens_used is not None else float((result.get("llm_calls") or 0) * 1000)

    return {
        "behaviour_match": bool(beh_ok),
        "top1_root_cause_match": bool(rc_top1),
        "top3_root_cause_hit": bool(rc_top3),
        "cause_family_match": bool(cf_ok),
        "safety_coverage": float(saf_cov),
        "must_not_violations": int(mnr),
        "already_tried_repeated": int(atr),
        "citation_validity": float(cit_val),
        "groundedness": grd,
        "latency_ms": float(result.get("latency_ms") or 0.0),
        "llm_calls": int(result.get("llm_calls") or 0),
        "token_count": tokens_used,
        "cost_proxy": cost_proxy,
    }
