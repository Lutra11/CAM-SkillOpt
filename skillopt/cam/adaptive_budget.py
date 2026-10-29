"""Evidence-driven textual learning-rate control for CAM-SkillOpt."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from math import isfinite
from math import log
from numbers import Real
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class FailureConfidence:
    """Observed failure distribution and its normalized concentration score."""

    counts: dict[str, int]
    probabilities: dict[str, float]
    entropy: float
    normalized_entropy: float
    confidence: float
    total_failures: int


@dataclass(frozen=True)
class EvidenceBudgetDecision:
    """Auditable v3 edit-budget decision based on distinct task evidence."""

    budget: int
    candidate_count: int
    eligible_candidate_count: int
    eligible_candidates: list[dict[str, Any]]
    eligible_indices: list[int]
    eligible_edits: list[dict[str, Any]]
    evidence_task_ids: list[str]
    unknown_provenance: bool
    duplicate_count: int
    conflicting_count: int
    total_failures: int
    reason: str


def estimate_failure_confidence(
    failure_patterns: Iterable[str] | Mapping[str, int],
) -> FailureConfidence:
    """Estimate failure-pattern concentration as ``1 - H/log(K)``.

    A one-category distribution has confidence 1 by definition.  An empty
    distribution contains no evidence and therefore yields confidence 0.
    """
    if isinstance(failure_patterns, Mapping):
        counts = {
            str(pattern): int(count)
            for pattern, count in failure_patterns.items()
            if int(count) > 0
        }
    else:
        counts = dict(Counter(str(pattern) for pattern in failure_patterns if str(pattern).strip()))
    total = sum(counts.values())
    if total == 0:
        return FailureConfidence({}, {}, 0.0, 1.0, 0.0, 0)

    probabilities = {pattern: count / total for pattern, count in counts.items()}
    entropy = -sum(probability * log(probability) for probability in probabilities.values())
    categories = len(probabilities)
    if categories == 1:
        normalized_entropy, confidence = 0.0, 1.0
    else:
        normalized_entropy = entropy / log(categories)
        confidence = max(0.0, min(1.0, 1.0 - normalized_entropy))
    return FailureConfidence(
        counts=counts,
        probabilities=probabilities,
        entropy=entropy,
        normalized_entropy=normalized_entropy,
        confidence=confidence,
        total_failures=total,
    )


def compute_adaptive_budget(
    confidence: float | FailureConfidence,
    *,
    min_budget: int = 2,
    max_budget: int = 8,
) -> int:
    """Map failure concentration to a bounded integer edit budget.

    ``round`` in the research proposal is implemented as positive half-up
    rounding, avoiding Python's banker's-rounding ambiguity in recorded runs.
    """
    if min_budget < 0:
        raise ValueError("min_budget must be non-negative")
    if max_budget < min_budget:
        raise ValueError("max_budget must be at least min_budget")
    value = confidence.confidence if isinstance(confidence, FailureConfidence) else float(confidence)
    clipped = max(0.0, min(1.0, value))
    raw_budget = min_budget + clipped * (max_budget - min_budget)
    return min(max_budget, max(min_budget, int(raw_budget + 0.5)))


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))


def _task_ids(candidate: Mapping[str, Any]) -> set[str]:
    """Read only explicitly named provenance fields; analyst counts are not provenance."""
    result: set[str] = set()
    for field in ("support_task_ids", "task_ids", "evidence_task_ids"):
        raw = candidate.get(field)
        if isinstance(raw, (str, int)) and not isinstance(raw, bool):
            raw = [raw]
        if not isinstance(raw, Iterable) or isinstance(raw, (bytes, Mapping)):
            continue
        for item in raw:
            if isinstance(item, (str, int)) and not isinstance(item, bool):
                token = str(item).strip()
                if token:
                    result.add(token)
    return result


def _candidate_key(candidate: Mapping[str, Any]) -> tuple[str, str, str]:
    op = str(candidate.get("op", "")).strip().lower()
    path = candidate.get("path", "")
    # `target` is the exact text/heading targeted by the operation in current CAM edits.
    locator = {"path": path, "target": candidate.get("target", candidate.get("locator", ""))}
    value = candidate.get("value", candidate.get("content", candidate.get("new_value", "")))
    return op, _stable_json(locator), _stable_json(value)


def _locator_key(candidate: Mapping[str, Any]) -> str | None:
    path = candidate.get("path")
    target = candidate.get("target", candidate.get("locator", candidate.get("selector")))
    if not (isinstance(path, str) and path.strip()) and not (isinstance(target, str) and target.strip()):
        return None
    return _stable_json({"path": path or "", "target": target or ""})


def compute_evidence_budget(
    candidate_edits: Iterable[Mapping[str, Any]],
    *,
    total_failures: int,
    min_budget: int = 2,
    max_budget: int = 4,
    min_support_tasks: int = 2,
    recent_score_changes: Iterable[float] | None = None,
) -> EvidenceBudgetDecision:
    """Choose an edit budget from distinct edits backed by distinct task IDs.

    ``total_failures`` is recorded for audit only: repeated failure volume does not
    justify adding unrelated edits. If task provenance is absent, return a
    conservative minimum bounded by the available unique, non-conflicting edits.
    """
    for name, value, allow_zero in (
        ("total_failures", total_failures, True),
        ("min_budget", min_budget, True),
        ("max_budget", max_budget, True),
        ("min_support_tasks", min_support_tasks, False),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < (0 if allow_zero else 1):
            qualifier = "non-negative" if allow_zero else "positive"
            raise ValueError(f"{name} must be a {qualifier} integer")
    if max_budget < min_budget:
        raise ValueError("max_budget must be at least min_budget")

    # Keep the first copy of each canonical edit and union valid provenance IDs.
    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    first_index_by_key: dict[tuple[str, str, str], int] = {}
    task_ids_by_key: dict[tuple[str, str, str], set[str]] = {}
    duplicates = 0
    unknown_provenance = False
    for index, edit in enumerate(candidate_edits):
        if not isinstance(edit, Mapping):
            continue
        key = _candidate_key(edit)
        ids = _task_ids(edit)
        if key in by_key:
            duplicates += 1
            task_ids_by_key[key].update(ids)
        else:
            by_key[key] = dict(edit)
            first_index_by_key[key] = index
            task_ids_by_key[key] = ids
        if not ids:
            unknown_provenance = True

    # Distinct payloads targeting the same exact locator can prescribe incompatible
    # changes. Remove the whole conflicting locator group from eligible candidates.
    locator_groups: dict[str, list[tuple[str, str, str]]] = {}
    for key, edit in by_key.items():
        locator = _locator_key(edit)
        if locator is not None:
            locator_groups.setdefault(locator, []).append(key)
    conflicting_keys: set[tuple[str, str, str]] = set()
    for keys in locator_groups.values():
        if len(keys) > 1:
            conflicting_keys.update(keys)

    candidates = [key for key in by_key if key not in conflicting_keys]
    provenance_present = any(task_ids_by_key[key] for key in by_key)
    if provenance_present:
        eligible_keys = [key for key in candidates if len(task_ids_by_key[key]) >= min_support_tasks]
        reason = "distinct_task_support"
    else:
        eligible_keys = candidates
        reason = "provenance_unknown_minimum_fallback"
        unknown_provenance = bool(candidates)

    available = len(eligible_keys)
    if available == 0:
        budget = 0
    elif not provenance_present:
        budget = min(min_budget, available, max_budget)
    else:
        budget = min(max_budget, available)
        if budget < min_budget:
            budget = min(available, min_budget)

    if recent_score_changes is not None:
        changes = list(recent_score_changes)
        for change in changes:
            if isinstance(change, bool) or not isinstance(change, Real) or not isfinite(float(change)):
                raise ValueError("recent_score_changes must contain finite numbers")
        if changes and float(changes[-1]) < 0 and budget > 0:
            budget -= 1
            reason += "; recent_harmful_change_shrink"

    eligible = []
    eligible_edits = []
    eligible_indices = []
    all_evidence: set[str] = set()
    for key in eligible_keys:
        edit = by_key[key]
        ids = sorted(task_ids_by_key[key])
        all_evidence.update(ids)
        original = dict(edit)
        # Preserve merged provenance from duplicate copies in the selected edit.
        if ids:
            original["support_task_ids"] = ids
        eligible_edits.append(original)
        eligible_indices.append(first_index_by_key[key])
        eligible.append({
            "op": edit.get("op"),
            "path": edit.get("path"),
            "target": edit.get("target", edit.get("locator", edit.get("selector"))),
            "value": edit.get("value", edit.get("content", edit.get("new_value"))),
            "support_task_ids": ids,
            "support_task_count": len(ids),
        })
    return EvidenceBudgetDecision(
        budget=budget,
        candidate_count=len(by_key),
        eligible_candidate_count=len(eligible_keys),
        eligible_candidates=eligible,
        eligible_indices=eligible_indices,
        eligible_edits=eligible_edits,
        evidence_task_ids=sorted(all_evidence),
        unknown_provenance=unknown_provenance,
        duplicate_count=duplicates,
        conflicting_count=len(conflicting_keys),
        total_failures=total_failures,
        reason=reason,
    )
