"""Pure, ID-paired CAM selection decisions with an independent best-skill gate.

No rollout, model request, cache mutation, or automatic resampling occurs here.
The caller owns evidence collection and applies the returned immutable state.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
import hashlib
import math
from numbers import Real
from statistics import fmean

from skillopt.cam.bootstrap_gate import paired_bootstrap_gate
from skillopt.evaluation.gate import GateResult
from skillopt.model.infra_errors import FAILURE_TYPES, detect_infra_error
from skillopt.utils.scoring import skill_hash


class SelectionEvidenceError(ValueError):
    """Selection evidence is missing, incomparable, or infrastructure-invalid."""


def _finite_number(value, label: str, *, unit_interval: bool = False) -> float:
    if not isinstance(value, Real) or isinstance(value, bool):
        raise SelectionEvidenceError(f"{label} must be a finite numeric value")
    result = float(value)
    if not math.isfinite(result) or (unit_interval and not 0 <= result <= 1):
        raise SelectionEvidenceError(f"{label} is outside its valid finite range")
    return result


def indexed_scores(results, *, metric: str = "hard", mixed_weight: float = 0.5) -> dict[str, float]:
    """Validate selected metric values and index a nonempty held-out set by ID."""
    if not isinstance(metric, str) or metric not in {"hard", "soft", "mixed"}:
        raise SelectionEvidenceError("Metric must be hard, soft, or mixed")
    weight = _finite_number(mixed_weight, "mixed_weight", unit_interval=True) if metric == "mixed" else 0.0
    if not isinstance(results, (list, tuple)) or not results:
        raise SelectionEvidenceError("A nonempty list of per-item selection results is required")
    indexed = {}
    for row in results:
        if not isinstance(row, Mapping):
            raise SelectionEvidenceError("Every selection result must be a mapping")
        raw_id = row.get("id")
        if isinstance(raw_id, bool) or not isinstance(raw_id, (str, int)):
            raise SelectionEvidenceError("Every selection result requires a nonempty string/integer ID")
        item_id = str(raw_id).strip()
        if not item_id or item_id in indexed:
            raise SelectionEvidenceError("Selection IDs must be nonempty and unique")
        for field in ("split", "dataset_split", "source_split"):
            if field in row and (not isinstance(row[field], str) or row[field] not in {"valid_seen", "selection", "validation"}):
                raise SelectionEvidenceError("Only explicitly held-out selection rows may enter the CAM gate")
        status = row.get("status")
        if status is not None and (not isinstance(status, str) or status not in {"completed", "ok"}):
            raise SelectionEvidenceError("Incomplete or invalid selection result cannot enter the CAM gate")
        failure_type = row.get("failure_type")
        if failure_type is not None and not isinstance(failure_type, str):
            raise SelectionEvidenceError("Selection failure_type must be a string when provided")
        if row.get("infra_error") or failure_type in FAILURE_TYPES:
            raise SelectionEvidenceError("Infrastructure failures cannot be represented as selection hard=0")
        diagnostics = " ".join(str(row.get(field, "")) for field in ("failure_type", "fail_reason", "error"))
        if detect_infra_error(diagnostics) is not None:
            raise SelectionEvidenceError("Historical infrastructure failure found in selection diagnostics")
        if metric == "hard":
            value = _finite_number(row.get("hard"), "hard", unit_interval=True)
        elif metric == "soft":
            value = _finite_number(row.get("soft"), "soft", unit_interval=True)
        else:
            hard = _finite_number(row.get("hard"), "hard", unit_interval=True)
            soft = _finite_number(row.get("soft"), "soft", unit_interval=True)
            value = (1 - weight) * hard + weight * soft
        indexed[item_id] = value
    return dict(sorted(indexed.items()))


def _gate_config(cfg: Mapping) -> dict:
    if not isinstance(cfg, Mapping):
        raise SelectionEvidenceError("CAM configuration must be a mapping")
    confidence = _finite_number(cfg.get("cam_confidence_level", 0.95), "cam_confidence_level")
    improvement = _finite_number(cfg.get("cam_meaningful_improvement", 0.0), "cam_meaningful_improvement")
    samples = cfg.get("cam_bootstrap_samples", 10000)
    seed = cfg.get("cam_seed", cfg.get("seed", 42))
    if not 0 < confidence < 1 or improvement < 0:
        raise SelectionEvidenceError("CAM confidence must be inside (0,1) and improvement threshold nonnegative")
    if not isinstance(samples, int) or isinstance(samples, bool) or samples < 1:
        raise SelectionEvidenceError("CAM bootstrap_samples must be a positive integer")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise SelectionEvidenceError("CAM seed must be an integer")
    return dict(confidence_level=confidence, meaningful_improvement=improvement,
                bootstrap_samples=samples, seed=seed)


def decide_cam_update(*, candidate_skill: str, current_skill: str, best_skill: str,
                      current_results, candidate_results, best_results, best_step: int,
                      global_step: int, metric: str = "hard", mixed_weight: float = 0.5,
                      cfg: Mapping) -> tuple[GateResult, dict]:
    """Compare current first; independently authorize any best-skill promotion.

    An uncertain first comparison preserves both skills. An accepted current
    update with an uncertain best comparison may update current but preserves
    best. Neither case retries the same pairs with another bootstrap draw.
    """
    if any(not isinstance(skill, str) for skill in (candidate_skill, current_skill, best_skill)):
        raise SelectionEvidenceError("Skill states must be strings")
    if (not isinstance(global_step, int) or isinstance(global_step, bool) or global_step < 1
            or not isinstance(best_step, int) or isinstance(best_step, bool) or not 0 <= best_step <= global_step):
        raise SelectionEvidenceError("Global step must be positive and best_step a historical nonnegative step")
    options = _gate_config(cfg)
    current = indexed_scores(current_results, metric=metric, mixed_weight=mixed_weight)
    candidate = indexed_scores(candidate_results, metric=metric, mixed_weight=mixed_weight)
    best = indexed_scores(best_results, metric=metric, mixed_weight=mixed_weight)
    pair_ids = list(current)
    if set(candidate) != set(current) or set(best) != set(current):
        raise SelectionEvidenceError("Current, candidate and best selection ID sets must match exactly")
    if current_skill == best_skill and current != best:
        raise SelectionEvidenceError("Identical current/best skill requires the same recorded paired evidence")
    current_score, candidate_score, best_score = fmean(current.values()), fmean(candidate.values()), fmean(best.values())
    decision = paired_bootstrap_gate(
        [current[item_id] for item_id in pair_ids], [candidate[item_id] for item_id in pair_ids], **options,
    )
    audit = asdict(decision)
    audit.update(
        candidate_hash=skill_hash(candidate_skill), current_hash=skill_hash(current_skill), best_hash=skill_hash(best_skill),
        candidate_skill_sha256=hashlib.sha256(candidate_skill.encode("utf-8")).hexdigest(),
        current_skill_sha256=hashlib.sha256(current_skill.encode("utf-8")).hexdigest(),
        best_skill_sha256=hashlib.sha256(best_skill.encode("utf-8")).hexdigest(),
        pair_ids=pair_ids, metric=metric, mixed_weight=mixed_weight if metric == "mixed" else None,
        current_score=current_score, candidate_score=candidate_score, best_score=best_score,
        promotion_authorized=False, automatic_retry=False, pending_additional_paired_evidence=False,
        best_comparison={"status": "not_needed", "performed": False, "decision": None, "pair_ids": []},
    )
    next_current_skill, next_current_score = current_skill, current_score
    next_best_skill, next_best_score, next_best_step = best_skill, best_score, best_step
    if decision.action == "accept":
        next_current_skill, next_current_score = candidate_skill, candidate_score
        action = "accept"
        if candidate_score > best_score:
            if best_skill == current_skill:
                best_decision = decision
                comparison_status, performed = "reused_current_comparison", False
            else:
                best_decision = paired_bootstrap_gate(
                    [best[item_id] for item_id in pair_ids], [candidate[item_id] for item_id in pair_ids], **options,
                )
                comparison_status, performed = "independent_best_comparison", True
            audit["best_comparison"] = {"status": comparison_status, "performed": performed,
                                        "decision": asdict(best_decision), "pair_ids": pair_ids}
            if best_decision.action == "accept":
                next_best_skill, next_best_score, next_best_step = candidate_skill, candidate_score, global_step
                action = "accept_new_best"
                audit["promotion_authorized"] = True
            elif best_decision.action == "re_evaluate":
                audit["pending_additional_paired_evidence"] = True
    elif decision.action == "reject":
        action = "reject"
    else:
        action = "cam_re_evaluate"
        audit["pending_additional_paired_evidence"] = True
    audit["result_action"] = action
    return GateResult(action=action, current_skill=next_current_skill, current_score=next_current_score,
                      best_skill=next_best_skill, best_score=next_best_score, best_step=next_best_step), audit
