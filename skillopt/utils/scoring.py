"""Scoring and hashing utilities."""
from __future__ import annotations

import hashlib


def compute_score(results: list) -> tuple[float, float]:
    """Compute hard and soft accuracy from a list of episode results.

    Accepts both plain dicts and :class:    instances.  hard may be continuous (0.0-1.0) when using smoothed reward.
    """
    if not results:
        return 0.0, 0.0

    def _hard(r: object) -> float:
        return float(r.hard if hasattr(r, "hard") else r.get("hard", 0))

    def _soft(r: object) -> float:
        return float(r.soft if hasattr(r, "soft") else r.get("soft", 0.0))

    hard = sum(_hard(r) for r in results) / len(results)
    soft = sum(_soft(r) for r in results) / len(results)
    return hard, soft


def compute_item_scores(
    results: list,
    *,
    metric: str = "hard",
    mixed_weight: float = 0.5,
) -> list[float]:
    """Return per-item scores projected onto the gate metric.

    CAM's paired-bootstrap gate needs aligned item-level validation scores.
    The aggregate baseline still uses :func:`compute_score`, so this helper is
    intentionally side-effect free and backward compatible.
    """
    if not results:
        return []

    weight = max(0.0, min(1.0, float(mixed_weight)))
    item_scores: list[float] = []
    for result in results:
        hard = float(result.hard if hasattr(result, "hard") else result.get("hard", 0))
        soft = float(result.soft if hasattr(result, "soft") else result.get("soft", 0.0))
        if metric == "hard":
            item_scores.append(hard)
        elif metric == "soft":
            item_scores.append(soft)
        elif metric == "mixed":
            item_scores.append((1.0 - weight) * hard + weight * soft)
        else:
            raise ValueError(
                f"unknown gate metric {metric!r}; expected 'hard', 'soft', or 'mixed'"
            )
    return item_scores


def skill_hash(content: str) -> str:
    """Return a short deterministic hash of skill content (for caching)."""
    return hashlib.sha256(content.encode()).hexdigest()[:16]
