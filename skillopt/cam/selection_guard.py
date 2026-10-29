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


def _v3_config(cfg: Mapping) -> dict:
    """Read opt-in v3 safeguards; absence preserves the v2 soft-accept policy."""
    policy = cfg.get("cam_soft_accept_policy", "legacy")
    if not isinstance(policy, str) or policy not in {"legacy", "noninferiority"}:
        raise SelectionEvidenceError("cam_soft_accept_policy must be legacy or noninferiority")
    margin = _finite_number(cfg.get("cam_noninferiority_margin", 0.05), "cam_noninferiority_margin", unit_interval=True)
    group_guard = cfg.get("cam_group_guard", False)
    if not isinstance(group_guard, bool):
        raise SelectionEvidenceError("cam_group_guard must be a boolean")
    group_margin = _finite_number(cfg.get("cam_group_max_regression", 0.1), "cam_group_max_regression", unit_interval=True)
    min_pairs = cfg.get("cam_group_min_pairs", 5)
    if not isinstance(min_pairs, int) or isinstance(min_pairs, bool) or min_pairs < 1:
        raise SelectionEvidenceError("cam_group_min_pairs must be a positive integer")
    return dict(policy=policy, margin=margin, group_guard=group_guard,
                group_margin=group_margin, min_pairs=min_pairs)


def _canonical_task_type(raw):
    if not isinstance(raw, str):
        return None
    value = raw.strip().lower()
    return {"cell_level": "cell_level", "cell-level manipulation": "cell_level",
            "sheet_level": "sheet_level", "sheet-level manipulation": "sheet_level"}.get(value)


def _task_type_audit(current_rows, candidate_rows, best_rows, ids, *, cfg,
                     current_scores, candidate_scores, best_scores):
    """Return ID-aligned per-task-type descriptive deltas and type assignments."""
    rows_by_id = []
    for rows in (current_rows, candidate_rows, best_rows):
        rows_by_id.append({str(row["id"]).strip(): row for row in rows})
    supplied = cfg.get("cam_task_type_by_id", {})
    issues = []
    if not isinstance(supplied, Mapping):
        supplied = {}
        issues.append("cam_task_type_by_id must be a mapping")
    types = {}
    for item_id in ids:
        resolved = []
        for rows in rows_by_id:
            raw = rows[item_id].get("task_type") or rows[item_id].get("instruction_type")
            # A caller-provided ID mapping is another declaration and must agree with row metadata.
            mapped = supplied.get(item_id, supplied.get(rows[item_id]["id"]))
            if mapped is not None:
                if raw is not None and _canonical_task_type(raw) != _canonical_task_type(mapped):
                    issues.append(f"inconsistent task_type metadata for ID {item_id}")
                raw = mapped if raw is None else raw
            kind = _canonical_task_type(raw)
            if kind not in {"cell_level", "sheet_level"}:
                resolved.append(None)
            else:
                resolved.append(kind)
        if any(kind is None for kind in resolved) or len(set(resolved)) != 1:
            issues.append(f"missing or inconsistent task_type metadata for ID {item_id}")
        else:
            types[item_id] = resolved[0]
    groups = {}
    for kind in ("cell_level", "sheet_level"):
        group_ids = [item_id for item_id in ids if types.get(item_id) == kind]
        if not group_ids:
            continue
        means = {label: fmean(scores[item_id] for item_id in group_ids)
                 for label, scores in (("current", current_scores), ("candidate", candidate_scores),
                                       ("best", best_scores))}
        groups[kind] = {
            "n_pairs": len(group_ids), "pair_ids": group_ids,
            "current_mean": means["current"], "candidate_mean": means["candidate"],
            "best_mean": means["best"],
            "candidate_minus_current_mean": means["candidate"] - means["current"],
            "best_minus_current_mean": means["best"] - means["current"],
            "candidate_minus_best_mean": means["candidate"] - means["best"],
        }
    complete = len(types) == len(ids) and not issues
    return {"status": "complete" if complete else "incomplete_metadata",
            "groups": groups, "issues": list(dict.fromkeys(issues))}, types


def _group_guard_failure(group_audit, *, comparison, min_pairs, max_regression):
    """Return an explanatory fail-closed reason for descriptive group checks."""
    if group_audit["status"] != "complete":
        return "missing_or_inconsistent_task_type_metadata"
    groups = group_audit["groups"]
    expected = {"cell_level", "sheet_level"}
    if set(groups) != expected:
        return "task_type_groups_incomplete"
    for kind in sorted(expected):
        group = groups[kind]
        if group["n_pairs"] < min_pairs:
            return f"{kind}_insufficient_pairs"
        delta_key = ("candidate_minus_current_mean" if comparison == "current" else
                     "candidate_minus_best_mean")
        if group[delta_key] < -max_regression - 1e-12:
            return f"{kind}_{comparison}_mean_regression_exceeds_tolerance"
    return None


def _soft_accept_enabled(cfg: Mapping) -> bool:
    """v2: adopt an inconclusive candidate as `current` instead of discarding it.

    Measured on v1 (2026-10-18): 6-8 of 8 training steps were judged inconclusive
    and the candidate was thrown away, so a run effectively made ONE update.
    Soft acceptance lets later steps build on the closest available state while
    `best` still requires strong paired evidence.

    Disable with `cam.soft_accept=false` to restore v1 behaviour.
    """
    value = cfg.get("cam_soft_accept", cfg.get("soft_accept", True))
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off", ""}
    return bool(value)


def decide_cam_update(*, candidate_skill: str, current_skill: str, best_skill: str,
                      current_results, candidate_results, best_results, best_step: int,
                      global_step: int, metric: str = "hard", mixed_weight: float = 0.5,
                      cfg: Mapping) -> tuple[GateResult, dict]:
    """Compare current first; independently authorize any best-skill promotion.

    An uncertain first comparison may soft-accept current; it preserves best. An accepted current
    update with an uncertain best comparison may update current but preserves
    best. Neither case retries the same pairs with another bootstrap draw.
    """
    if any(not isinstance(skill, str) for skill in (candidate_skill, current_skill, best_skill)):
        raise SelectionEvidenceError("Skill states must be strings")
    if (not isinstance(global_step, int) or isinstance(global_step, bool) or global_step < 1
            or not isinstance(best_step, int) or isinstance(best_step, bool) or not 0 <= best_step <= global_step):
        raise SelectionEvidenceError("Global step must be positive and best_step a historical nonnegative step")
    options = _gate_config(cfg)
    policy = _v3_config(cfg)
    current = indexed_scores(current_results, metric=metric, mixed_weight=mixed_weight)
    candidate = indexed_scores(candidate_results, metric=metric, mixed_weight=mixed_weight)
    best = indexed_scores(best_results, metric=metric, mixed_weight=mixed_weight)
    pair_ids = list(current)
    if set(candidate) != set(current) or set(best) != set(current):
        raise SelectionEvidenceError("Current, candidate and best selection ID sets must match exactly")
    if current_skill == best_skill and current != best:
        raise SelectionEvidenceError("Identical current/best skill requires the same recorded paired evidence")
    current_score, candidate_score, best_score = fmean(current.values()), fmean(candidate.values()), fmean(best.values())
    task_type_audit, _task_types = _task_type_audit(
        current_results, candidate_results, best_results, pair_ids, cfg=cfg,
        current_scores=current, candidate_scores=candidate, best_scores=best,
    )
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
        per_task_type=task_type_audit,
        promotion_authorized=False, automatic_retry=False, pending_additional_paired_evidence=False,
        best_comparison={"status": "not_needed", "performed": False, "decision": None, "pair_ids": []},
    )
    next_current_skill, next_current_score = current_skill, current_score
    next_best_skill, next_best_score, next_best_step = best_skill, best_score, best_step
    group_failure = (_group_guard_failure(task_type_audit, comparison="current",
                                          min_pairs=policy["min_pairs"],
                                          max_regression=policy["group_margin"])
                     if policy["group_guard"] else None)
    if policy["group_guard"]:
        audit["group_guard"] = {"enabled": True, "status": "blocked" if group_failure else "passed",
                                "reason": group_failure,
                                "max_regression": policy["group_margin"],
                                "min_pairs": policy["min_pairs"],
                                "interpretation": "descriptive_mean_tolerance_not_a_confidence_guarantee"}
    else:
        audit["group_guard"] = {"enabled": False, "status": "not_enabled"}
    if decision.action == "accept":
        if group_failure:
            action = "cam_re_evaluate"
            audit["reason"] = f"group_guard:{group_failure}"
            audit["pending_additional_paired_evidence"] = True
        else:
            next_current_skill, next_current_score = candidate_skill, candidate_score
            action = "accept"
        if not group_failure and candidate_score > best_score:
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
                best_group_failure = (_group_guard_failure(task_type_audit, comparison="best",
                                                           min_pairs=policy["min_pairs"],
                                                           max_regression=policy["group_margin"])
                                      if policy["group_guard"] else None)
                if best_group_failure:
                    audit["group_guard"].update(status="best_promotion_blocked", reason=f"{best_group_failure}")
                    audit["best_comparison"]["group_guard_reason"] = best_group_failure
                else:
                    next_best_skill, next_best_score, next_best_step = candidate_skill, candidate_score, global_step
                    action = "accept_new_best"
                    audit["promotion_authorized"] = True
            elif best_decision.action == "re_evaluate":
                audit["pending_additional_paired_evidence"] = True
    elif decision.action == "reject":
        action = "reject"
    elif policy["policy"] == "noninferiority":
        if group_failure:
            action = "cam_re_evaluate"
            audit["reason"] = f"group_guard:{group_failure}"
            audit["pending_additional_paired_evidence"] = True
        elif decision.lower_confidence_bound >= -policy["margin"]:
            action = "soft_accept"
            next_current_skill, next_current_score = candidate_skill, candidate_score
            audit["soft_accept"] = True
            audit["soft_accept_policy"] = "noninferiority"
            audit["noninferiority_margin"] = policy["margin"]
            audit["noninferiority_passed"] = True
        else:
            action = "cam_re_evaluate"
            audit["reason"] = "noninferiority_lcb_below_negative_margin"
            audit["soft_accept_policy"] = "noninferiority"
            audit["noninferiority_margin"] = policy["margin"]
            audit["noninferiority_passed"] = False
            audit["pending_additional_paired_evidence"] = True
    elif _soft_accept_enabled(cfg):
        # v2 soft accept: adopt the candidate as `current` so the following steps
        # continue from the closest state, while `best` stays untouched and
        # promotion remains unauthorized (strong paired evidence still required).
        action = "soft_accept"
        next_current_skill, next_current_score = candidate_skill, candidate_score
        audit["soft_accept"] = True
    else:
        action = "cam_re_evaluate"
        audit["pending_additional_paired_evidence"] = True
    audit["result_action"] = action
    return GateResult(action=action, current_skill=next_current_skill, current_score=next_current_score,
                      best_skill=next_best_skill, best_score=next_best_score, best_step=next_best_step), audit
