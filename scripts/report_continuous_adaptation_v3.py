#!/usr/bin/env python3
"""Report validated outcomes for the four-cell continuous adaptation v3 study.

This reporter does not start training or call a model API. A cell contributes
scores only when its runner reports status=accepted and the runner's
``validate_complete_cell`` accepts its frozen artifacts.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import random
import statistics
import sys
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
STUDY = "continuous_adaptation_v3"
DEFAULT_STUDY_ROOT = ROOT / "outputs" / "studies" / STUDY
DEFAULT_RUNNER = ROOT / "scripts" / "run_continuous_adaptation_v3.py"
REPORT_ROOT = ROOT / "reports" / STUDY
TYPE_COUNTS = {"cell_level": 83, "sheet_level": 37}
EXPECTED_TEST_SIZE = 120
BOOTSTRAP_DRAWS = 1000
BOOTSTRAP_BASE_SEED = 2026101803


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    temp.replace(path)


def canonical_task_type(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    token = value.strip().lower().replace("-", "_").replace(" ", "_")
    if token in {"cell_level", "cell_level_manipulation", "cell"}:
        return "cell_level"
    if token in {"sheet_level", "sheet_level_manipulation", "sheet"}:
        return "sheet_level"
    return None


def _read_usage(folder: Path, queue_record: dict[str, Any]) -> dict[str, Any]:
    summary = None
    summary_path = folder / "summary.json"
    if summary_path.is_file():
        try:
            summary = read_json(summary_path)
        except (OSError, json.JSONDecodeError):
            summary = None
    summary = summary if isinstance(summary, dict) else {}
    return {
        "token_summary": summary.get("token_summary"),
        "token_accounting": summary.get("token_accounting"),
        "returned_token_totals": (summary.get("token_summary", {}).get("_total")
                                  if isinstance(summary.get("token_summary"), dict) else None),
        "token_coverage": (summary.get("token_accounting", {}).get("coverage")
                           if isinstance(summary.get("token_accounting"), dict) else None),
        "token_usage_complete": (summary.get("token_accounting", {}).get("usage_complete")
                                 if isinstance(summary.get("token_accounting"), dict) else None),
        "total_wall_time_s": summary.get("total_wall_time_s"),
        "queue_wall_time_s": queue_record.get("wall_time_s"),
        "queue_started_at": queue_record.get("started_at"),
        "queue_finished_at": queue_record.get("finished_at"),
    }


def _load_test_rows(folder: Path, fixed_ids: list[str], fixed_ids_hash: str | None,
                    summary: dict[str, Any]) -> tuple[dict[str, dict], str | None]:
    results_path = folder / "test_eval" / "results.jsonl"
    rows: dict[str, dict[str, Any]] = {}
    try:
        for line_number, line in enumerate(results_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict) or row.get("id") is None:
                return {}, f"invalid result row at line {line_number}"
            task_id = str(row["id"]).strip()
            if not task_id:
                return {}, f"empty task ID at line {line_number}"
            if task_id in rows:
                return {}, f"duplicate task ID: {task_id}"
            hard = row.get("hard")
            if isinstance(hard, bool) or hard not in (0, 1):
                return {}, f"non-binary hard score for task {task_id}"
            task_type = canonical_task_type(row.get("task_type"))
            if task_type is None:
                return {}, f"unrecognized task type for task {task_id}: {row.get('task_type')!r}"
            rows[task_id] = {**row, "id": task_id, "hard": int(hard), "task_type": task_type}
    except (OSError, json.JSONDecodeError) as exc:
        return {}, f"cannot read test results: {exc}"

    if len(fixed_ids) != EXPECTED_TEST_SIZE or len(set(fixed_ids)) != EXPECTED_TEST_SIZE:
        return {}, "freeze must contain exactly 120 unique test task IDs"
    expected_hash = hashlib.sha256("\n".join(fixed_ids).encode("utf-8")).hexdigest()
    if fixed_ids_hash != expected_hash:
        return {}, "freeze test task ID hash does not match its fixed 120 IDs"
    if set(rows) != set(fixed_ids) or len(rows) != EXPECTED_TEST_SIZE:
        return {}, "test result task IDs do not match the frozen 120-ID subset"
    type_counts = {kind: sum(row["task_type"] == kind for row in rows.values()) for kind in TYPE_COUNTS}
    if type_counts != TYPE_COUNTS:
        return {}, f"test task type counts differ from frozen 83/37 strata: {type_counts}"

    computed_score = sum(row["hard"] for row in rows.values()) / EXPECTED_TEST_SIZE
    try:
        recorded_score = float(summary["test_hard"])
    except (KeyError, TypeError, ValueError):
        return {}, "summary is missing a numeric test_hard score"
    if not math.isfinite(recorded_score) or not math.isclose(recorded_score, computed_score, rel_tol=0.0, abs_tol=1e-12):
        return {}, f"score disagreement: computed {computed_score}, summary {recorded_score}"
    summary_ids_hash = summary.get("test_subset_ids_sha256")
    if summary_ids_hash != expected_hash:
        return {}, "summary test task ID hash differs from freeze"
    test_summary_path = folder / "test_eval" / "summary.json"
    if test_summary_path.is_file():
        try:
            test_summary = read_json(test_summary_path)
        except (OSError, json.JSONDecodeError) as exc:
            return {}, f"cannot read test evaluation summary: {exc}"
        try:
            test_summary_score = float(test_summary.get("test_hard"))
        except (TypeError, ValueError):
            return {}, "test_eval summary is missing a numeric test_hard score"
        if not math.isfinite(test_summary_score) or not math.isclose(test_summary_score, computed_score, rel_tol=0.0, abs_tol=1e-12):
            return {}, "test_eval summary score disagrees with result rows"
        if test_summary.get("test_subset_size") != EXPECTED_TEST_SIZE:
            return {}, "test_eval summary subset size differs from 120"
        if test_summary.get("task_ids_sha256") != expected_hash:
            return {}, "test_eval summary task ID hash differs from freeze"
    return rows, None


def paired_task_bootstrap(differences: list[int], *, seed: int, draws: int = BOOTSTRAP_DRAWS) -> dict[str, Any]:
    """Descriptive task-resampling interval; it does not increase the seed n."""
    if len(differences) != EXPECTED_TEST_SIZE:
        raise ValueError("paired task bootstrap requires exactly 120 differences")
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choice(differences) for _ in differences) for _ in range(draws))

    def quantile(p: float) -> float:
        at = (len(means) - 1) * p
        lo, hi = math.floor(at), math.ceil(at)
        return means[lo] if lo == hi else means[lo] + (means[hi] - means[lo]) * (at - lo)

    return {"method": "paired task-ID bootstrap; descriptive only",
            "draws": draws, "seed": seed,
            "lower_95": quantile(0.025), "upper_95": quantile(0.975)}


def analyze_study(
    freeze: dict[str, Any],
    queue_state: dict[str, Any],
    *,
    workspace_root: Path,
    validate_complete_cell: Callable[[dict, dict, Path], tuple[bool, str]],
) -> dict[str, Any]:
    frozen_cells = freeze.get("cells")
    if not isinstance(frozen_cells, list) or not frozen_cells:
        raise ValueError("freeze has no cell list")
    fixed_ids = freeze.get("test_subset", {}).get("task_ids")
    if not isinstance(fixed_ids, list):
        raise ValueError("freeze is missing the fixed test task IDs")
    queue_records = queue_state.get("cells", []) if isinstance(queue_state, dict) else []
    queue_by_id = {str(row.get("cell_id")): row for row in queue_records if isinstance(row, dict)}
    cells_report = []
    accepted_data: dict[str, dict[str, Any]] = {}

    for frozen_cell in frozen_cells:
        cell = dict(frozen_cell)
        cell_id = str(cell.get("cell_id", ""))
        queue_record = dict(queue_by_id.get(cell_id, {}))
        queue_status = queue_record.get("status", "missing_record")
        folder = workspace_root / str(cell.get("out_root", ""))
        usage = _read_usage(folder, queue_record)
        row = {
            "cell_id": cell_id,
            "method": cell.get("method"),
            "seed": cell.get("seed"),
            "block": cell.get("block"),
            "out_root": cell.get("out_root"),
            "queue_status": queue_status,
            "queue_record": queue_record,
            "runner_validation": {"status": "not_checked", "reason": None},
            "performance": None,
            **usage,
        }
        if queue_status != "accepted":
            row["runner_validation"] = {"status": "not_accepted", "reason": queue_record.get("acceptance")}
            cells_report.append(row)
            continue

        valid, reason = validate_complete_cell(cell, freeze, folder)
        row["runner_validation"] = {"status": "accepted" if valid else "rejected", "reason": reason}
        if not valid:
            cells_report.append(row)
            continue
        summary_path = folder / "summary.json"
        try:
            summary = read_json(summary_path)
            rows, rows_error = _load_test_rows(folder, fixed_ids,
                                               freeze.get("test_subset", {}).get("ids_sha256"), summary)
        except (OSError, json.JSONDecodeError) as exc:
            rows, rows_error = {}, f"cannot read cell summary: {exc}"
        if rows_error:
            row["runner_validation"] = {"status": "rejected", "reason": rows_error}
            cells_report.append(row)
            continue
        pass_counts = {kind: sum(item["hard"] for item in rows.values() if item["task_type"] == kind)
                       for kind in TYPE_COUNTS}
        cell_performance = {
            "hard_passes": sum(item["hard"] for item in rows.values()),
            "test_size": EXPECTED_TEST_SIZE,
            "hard_rate": sum(item["hard"] for item in rows.values()) / EXPECTED_TEST_SIZE,
            "per_type": {kind: {"hard_passes": pass_counts[kind], "count": TYPE_COUNTS[kind],
                                "hard_rate": pass_counts[kind] / TYPE_COUNTS[kind]}
                         for kind in TYPE_COUNTS},
            "task_id_order_sha256": hashlib.sha256("\n".join(fixed_ids).encode("utf-8")).hexdigest(),
        }
        # Keep single-arm scores internal until their matching seed arm is accepted.
        accepted_data[cell_id] = {"cell": cell, "rows": rows, "performance": cell_performance}
        cells_report.append(row)

    # Pair only baseline and CAM v3 cells for each seed; there is no online v2 arm.
    seeds = sorted({cell.get("seed") for cell in frozen_cells if isinstance(cell.get("seed"), int)})
    pairs = []
    for seed in seeds:
        seed_cells = [cell for cell in frozen_cells if cell.get("seed") == seed]
        baseline = next((cell for cell in seed_cells if cell.get("method") == "baseline"), None)
        cam = next((cell for cell in seed_cells if cell.get("method") in {"cam_v3", "cam_full_v3", "cam_full"}), None)
        if baseline is None or cam is None:
            pairs.append({"seed": seed, "status": "invalid_freeze_pair",
                          "reason": "expected one baseline and one CAM v3 cell",
                          "baseline_cell_id": baseline.get("cell_id") if baseline else None,
                          "cam_cell_id": cam.get("cell_id") if cam else None,
                          "performance": None})
            continue
        baseline_id, cam_id = str(baseline["cell_id"]), str(cam["cell_id"])
        if baseline_id not in accepted_data or cam_id not in accepted_data:
            statuses = {str(cell["cell_id"]): next(
                (record for record in cells_report if record["cell_id"] == str(cell["cell_id"])), {})
                        for cell in (baseline, cam)}
            pairs.append({"seed": seed, "status": "incomplete_seed_pair",
                          "baseline_cell_id": baseline_id, "cam_cell_id": cam_id,
                          "cell_statuses": {key: {"queue_status": value.get("queue_status"),
                                                   "validation": value.get("runner_validation")}
                                            for key, value in statuses.items()},
                          "performance": None})
            continue
        baseline_rows = accepted_data[baseline_id]["rows"]
        cam_rows = accepted_data[cam_id]["rows"]
        if set(baseline_rows) != set(cam_rows):
            pairs.append({"seed": seed, "status": "invalid_pair_id_mismatch",
                          "baseline_cell_id": baseline_id, "cam_cell_id": cam_id,
                          "baseline_only_ids": sorted(set(baseline_rows) - set(cam_rows)),
                          "cam_only_ids": sorted(set(cam_rows) - set(baseline_rows)),
                          "performance": None})
            continue
        type_mismatches = [task_id for task_id in baseline_rows
                           if baseline_rows[task_id]["task_type"] != cam_rows[task_id]["task_type"]]
        if type_mismatches:
            pairs.append({"seed": seed, "status": "invalid_pair_type_mismatch",
                          "baseline_cell_id": baseline_id, "cam_cell_id": cam_id,
                          "task_type_mismatch_ids": type_mismatches, "performance": None})
            continue
        differences = {task_id: cam_rows[task_id]["hard"] - baseline_rows[task_id]["hard"]
                       for task_id in baseline_rows}
        wins = sum(value > 0 for value in differences.values())
        losses = sum(value < 0 for value in differences.values())
        ties = sum(value == 0 for value in differences.values())
        per_type = {}
        for kind in TYPE_COUNTS:
            selected = [task_id for task_id in differences if baseline_rows[task_id]["task_type"] == kind]
            per_type[kind] = {
                "count": len(selected),
                "baseline_passes": sum(baseline_rows[task_id]["hard"] for task_id in selected),
                "cam_v3_passes": sum(cam_rows[task_id]["hard"] for task_id in selected),
                "wins": sum(differences[task_id] > 0 for task_id in selected),
                "losses": sum(differences[task_id] < 0 for task_id in selected),
                "ties": sum(differences[task_id] == 0 for task_id in selected),
            }
        delta_passes = sum(cam_rows[task_id]["hard"] for task_id in cam_rows) - sum(
            baseline_rows[task_id]["hard"] for task_id in baseline_rows)
        diff_vector = [differences[task_id] for task_id in sorted(differences)]
        pairs.append({
            "seed": seed, "status": "complete_seed_pair",
            "baseline_cell_id": baseline_id, "cam_cell_id": cam_id,
            "performance": {
                "baseline_hard_passes": sum(row["hard"] for row in baseline_rows.values()),
                "cam_v3_hard_passes": sum(row["hard"] for row in cam_rows.values()),
                "test_size": EXPECTED_TEST_SIZE,
                "baseline_hard_rate": sum(row["hard"] for row in baseline_rows.values()) / EXPECTED_TEST_SIZE,
                "cam_v3_hard_rate": sum(row["hard"] for row in cam_rows.values()) / EXPECTED_TEST_SIZE,
                "cam_v3_minus_baseline_passes": delta_passes,
                "cam_v3_minus_baseline_hard_rate": delta_passes / EXPECTED_TEST_SIZE,
                "paired_task_id_wins": wins, "paired_task_id_losses": losses,
                "paired_task_id_ties": ties, "per_type": per_type,
                "paired_task_bootstrap": paired_task_bootstrap(
                    diff_vector, seed=BOOTSTRAP_BASE_SEED + int(seed)),
                "inference_note": "Task bootstrap is descriptive only; the study has two training seeds.",
            },
        })

    complete_pairs = [pair for pair in pairs if pair["status"] == "complete_seed_pair"]
    all_expected_pairs_complete = len(pairs) == 2 and len(complete_pairs) == 2
    result = {
        "study_id": STUDY,
        "status": "complete_seed_pairs" if all_expected_pairs_complete else "awaiting_complete_seed_pairs",
        "primary_online_contrast": "CAM v3 versus baseline; no contemporaneous online v2 arm",
        "interpretation": ("Two seed-pair differences and their mean are descriptive only; task bootstrap intervals do not replace the training-seed sample size of two."
                           if all_expected_pairs_complete else
                           "No performance conclusion is reported until both seed pairs contain validated accepted cells."),
        "fixed_test_protocol": {"size": EXPECTED_TEST_SIZE, "type_counts": TYPE_COUNTS,
                                "ids_sha256": freeze.get("test_subset", {}).get("ids_sha256")},
        "queue_status": {key: queue_state.get(key) for key in ("status", "started_at", "finished_at", "error")},
        "queue_cells": queue_records,
        "cells": cells_report,
        "seed_pairs": pairs,
        "complete_seed_pair_count": len(complete_pairs),
        "seed_level_summary": None,
    }
    if all_expected_pairs_complete:
        deltas = [pair["performance"]["cam_v3_minus_baseline_hard_rate"] for pair in complete_pairs]
        result["seed_level_summary"] = {
            "seed_differences": {str(pair["seed"]): pair["performance"]["cam_v3_minus_baseline_hard_rate"]
                                 for pair in complete_pairs},
            "mean_seed_difference": statistics.fmean(deltas),
            "n_training_seed_pairs": len(deltas),
            "interpretation": "descriptive only; n=2 training seed pairs",
        }
    return result


def load_runner_validator(runner_path: Path):
    if not runner_path.is_file():
        raise FileNotFoundError(f"v3 runner not found: {runner_path}")
    spec = importlib.util.spec_from_file_location("continuous_adaptation_v3_runner", runner_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import study runner: {runner_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if getattr(module, "STUDY", None) != STUDY:
        raise ValueError(f"runner namespace must be {STUDY!r}")
    validator = getattr(module, "validate_complete_cell", None)
    if not callable(validator):
        raise AttributeError("v3 runner does not expose validate_complete_cell")
    return validator


def markdown_report(result: dict[str, Any]) -> str:
    lines = [
        "# Continuous adaptation v3 report",
        "",
        f"Status: **{result['status']}**",
        "",
        result["primary_online_contrast"] + ".",
        "",
        result["interpretation"],
        "",
        f"Frozen test subset: {result['fixed_test_protocol']['size']} IDs "
        f"({result['fixed_test_protocol']['type_counts']['cell_level']} Cell-Level, "
        f"{result['fixed_test_protocol']['type_counts']['sheet_level']} Sheet-Level).",
        "",
        "Performance rows include only cells with queue status `accepted`, runner validation accepted, and matching fixed test evidence.",
        "Failed, pending, missing, or invalid cells retain their status and returned usage metadata, but do not contribute a score.",
        "",
        "## Cell status and returned usage",
        "",
        "| Cell | Queue status | Runner validation | Token coverage | Returned total tokens | Total wall time (s) |",
        "|---|---|---|---|---:|---:|",
    ]
    for cell in result["cells"]:
        total = cell.get("returned_token_totals")
        total_tokens = total.get("total_tokens") if isinstance(total, dict) else None
        lines.append(f"| {cell['cell_id']} | {cell['queue_status']} | {cell['runner_validation']['status']} "
                     f"({cell['runner_validation'].get('reason') or ''}) | {cell.get('token_coverage')} | "
                     f"{total_tokens if total_tokens is not None else 'unknown'} | "
                     f"{cell.get('total_wall_time_s') if cell.get('total_wall_time_s') is not None else 'unknown'} |")
    if result["status"] == "complete_seed_pairs":
        lines += ["", "## Validated paired results", "",
                  "| Seed | Baseline passes / 120 | CAM v3 passes / 120 | CAM v3 − baseline | Task-ID wins / losses / ties | Cell-Level pass counts | Sheet-Level pass counts |",
                  "|---:|---:|---:|---:|---:|---:|---:|"]
        for pair in result["seed_pairs"]:
            if pair["status"] != "complete_seed_pair":
                continue
            p = pair["performance"]
            cell = p["per_type"]["cell_level"]
            sheet = p["per_type"]["sheet_level"]
            lines.append(f"| {pair['seed']} | {p['baseline_hard_passes']} / 120 | {p['cam_v3_hard_passes']} / 120 | "
                         f"{p['cam_v3_minus_baseline_passes']:+d} ({p['cam_v3_minus_baseline_hard_rate']:+.3%}) | "
                         f"{p['paired_task_id_wins']} / {p['paired_task_id_losses']} / {p['paired_task_id_ties']} | "
                         f"{cell['baseline_passes']} → {cell['cam_v3_passes']} / 83 | "
                         f"{sheet['baseline_passes']} → {sheet['cam_v3_passes']} / 37 |")
        seed_summary = result.get("seed_level_summary")
        if seed_summary:
            lines += ["", f"Mean of the two seed differences: **{seed_summary['mean_seed_difference']:+.3%}** "
                      "(descriptive, n=2 training seed pairs). Task-ID bootstrap intervals are reported in JSON as descriptive only."]
    else:
        lines += ["", "## Paired analysis", "", "Awaiting two complete validated seed pairs. No performance conclusion is reported."]
    lines += ["", "## Cell outcomes", ""]
    for pair in result["seed_pairs"]:
        lines.append(f"- Seed {pair['seed']}: `{pair['status']}`")
        if pair.get("reason"):
            lines.append(f"  Reason: {pair['reason']}")
        if pair.get("cell_statuses"):
            for cell_id, status in pair["cell_statuses"].items():
                lines.append(f"  - {cell_id}: queue `{status.get('queue_status')}`, validation `{status.get('validation', {}).get('status')}`")
    lines += ["", "The JSON report preserves full queue records, validation reasons, token summaries/accounting and coverage flags, returned token totals, and recorded timing per cell.", ""]
    return "\n".join(lines)


def build_pending_report(reason: str) -> dict[str, Any]:
    return {
        "study_id": STUDY, "status": "awaiting_complete_seed_pairs",
        "primary_online_contrast": "CAM v3 versus baseline; no contemporaneous online v2 arm",
        "interpretation": "No performance conclusion is reported until both seed pairs contain validated accepted cells.",
        "pending_reason": reason, "fixed_test_protocol": {"size": EXPECTED_TEST_SIZE, "type_counts": TYPE_COUNTS},
        "queue_status": {}, "queue_cells": [], "cells": [], "seed_pairs": [],
        "complete_seed_pair_count": 0, "seed_level_summary": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-root", type=Path, default=DEFAULT_STUDY_ROOT,
                        help="Study artifact directory containing freeze.json (default: outputs/studies/continuous_adaptation_v3)")
    parser.add_argument("--runner", type=Path, default=DEFAULT_RUNNER,
                        help="v3 runner exposing validate_complete_cell (imported lazily)")
    args = parser.parse_args()
    study_root = args.study_root if args.study_root.is_absolute() else ROOT / args.study_root
    runner_path = args.runner if args.runner.is_absolute() else ROOT / args.runner
    freeze_path = study_root / "freeze.json"
    queue_path = study_root / "queue_state.json"
    if not freeze_path.is_file():
        result = build_pending_report(f"freeze.json is not present at {freeze_path}")
    elif not runner_path.is_file():
        result = build_pending_report(f"v3 runner is not present at {runner_path}")
    else:
        validator = load_runner_validator(runner_path)
        freeze = read_json(freeze_path)
        queue_state = read_json(queue_path) if queue_path.is_file() else {"status": "missing_queue_state", "cells": []}
        result = analyze_study(freeze, queue_state, workspace_root=ROOT,
                               validate_complete_cell=validator)
        result["freeze_path"] = str(freeze_path)
        result["runner_path"] = str(runner_path)
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    write_json(REPORT_ROOT / "results.json", result)
    (REPORT_ROOT / "report.md").write_text(markdown_report(result), encoding="utf-8")
    print(f"Study {STUDY}: {result['status']}; complete seed pairs={result['complete_seed_pair_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
