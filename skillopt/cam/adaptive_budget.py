"""Evidence-driven textual learning-rate control for CAM-SkillOpt."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import log
from typing import Iterable, Mapping


@dataclass(frozen=True)
class FailureConfidence:
    """Observed failure distribution and its normalized concentration score."""

    counts: dict[str, int]
    probabilities: dict[str, float]
    entropy: float
    normalized_entropy: float
    confidence: float
    total_failures: int


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
