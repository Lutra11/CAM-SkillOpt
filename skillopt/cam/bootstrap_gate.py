"""Paired-bootstrap confidence-aware validation for CAM-SkillOpt.

The baseline SkillOpt gate accepts a candidate whenever its aggregate
selection score is greater than the current score.  This module operates on
per-item *paired* scores, so the uncertainty estimate captures that both
skills are evaluated on the same selection examples.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil, floor
from random import Random
from statistics import fmean
from typing import Iterable, Literal


CAMGateAction = Literal["accept", "reject", "re_evaluate"]


@dataclass(frozen=True)
class CAMGateDecision:
    """Result of a confidence-aware paired validation comparison."""

    action: CAMGateAction
    mean_improvement: float
    lower_confidence_bound: float
    upper_confidence_bound: float
    confidence_level: float
    meaningful_improvement: float
    n_pairs: int
    bootstrap_samples: int
    positive_bootstrap_fraction: float


def _quantile(sorted_values: list[float], probability: float) -> float:
    """Return a linearly interpolated quantile without a NumPy dependency."""
    if not sorted_values:
        raise ValueError("cannot take a quantile of an empty sequence")
    p = min(1.0, max(0.0, float(probability)))
    index = (len(sorted_values) - 1) * p
    lo, hi = floor(index), ceil(index)
    if lo == hi:
        return sorted_values[lo]
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (index - lo)


def paired_bootstrap_gate(
    current_scores: Iterable[float],
    candidate_scores: Iterable[float],
    *,
    confidence_level: float = 0.95,
    meaningful_improvement: float = 0.0,
    bootstrap_samples: int = 10_000,
    seed: int = 42,
) -> CAMGateDecision:
    """Use paired bootstrap to return accept/reject/re-evaluate.

    ``current_scores`` and ``candidate_scores`` must be aligned per selection
    example.  The three-way rule is exactly the proposed CAM-SkillOpt gate:

    * accept if LCB is larger than ``meaningful_improvement``;
    * reject if UCB is smaller than ``-meaningful_improvement``;
    * otherwise request additional paired validation examples.
    """
    old = [float(value) for value in current_scores]
    new = [float(value) for value in candidate_scores]
    if len(old) != len(new):
        raise ValueError("current_scores and candidate_scores must have equal length")
    if not old:
        raise ValueError("at least one paired selection score is required")
    if not 0.0 < confidence_level < 1.0:
        raise ValueError("confidence_level must be strictly between 0 and 1")
    if bootstrap_samples < 1:
        raise ValueError("bootstrap_samples must be positive")

    differences = [candidate - current for current, candidate in zip(old, new)]
    rng = Random(seed)
    n_pairs = len(differences)
    bootstrap_means = [
        fmean(differences[rng.randrange(n_pairs)] for _ in range(n_pairs))
        for _ in range(bootstrap_samples)
    ]
    bootstrap_means.sort()
    alpha = 1.0 - confidence_level
    lcb = _quantile(bootstrap_means, alpha / 2.0)
    ucb = _quantile(bootstrap_means, 1.0 - alpha / 2.0)
    delta = float(meaningful_improvement)
    if lcb > delta:
        action: CAMGateAction = "accept"
    elif ucb < -delta:
        action = "reject"
    else:
        action = "re_evaluate"

    return CAMGateDecision(
        action=action,
        mean_improvement=fmean(differences),
        lower_confidence_bound=lcb,
        upper_confidence_bound=ucb,
        confidence_level=confidence_level,
        meaningful_improvement=delta,
        n_pairs=n_pairs,
        bootstrap_samples=bootstrap_samples,
        positive_bootstrap_fraction=sum(value > 0.0 for value in bootstrap_means)
        / bootstrap_samples,
    )
