"""Generate an integrity-gated report for the focused CAM v2r1 study.

The report is deliberately read-only with respect to experiment outputs. It
only writes aggregate JSON/Markdown under reports/v2r1 and never includes
prompts, memory contents, credentials, or per-request records.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
import os
from pathlib import Path
import statistics
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.experiment_integrity import phase_rows, validate_cell
from scripts.paired_compare import bootstrap_ci, mcnemar_exact

STUDY_ID = "cam_v2r1_qwen"
REPORT_DIR = Path("reports/v2r1")
MATRIX_DIR = Path("outputs/matrix_v2r1/spreadsheetbench/qwen")
SEEDS = (42, 43, 44)
MAIN_METHODS = ("baseline", "cam_full")
ABLATIONS = ("cam_no_bootstrap", "cam_no_memory", "cam_no_adaptive_budget")
BOOTSTRAP_RESAMPLES = 20_000
PHASES = {"test_hard": "test_eval", "final_test_hard": "test_eval_final"}


def study_cells() -> list[dict[str, Any]]:
    """The nine frozen cells, matching scripts/run_study.py's public plan."""
    cells = []
    for seed in SEEDS:
        for method in MAIN_METHODS:
            cells.append({
                "cell_id": f"qwen_{method}_{seed}", "method": method,
                "seed": seed, "block": f"paired_seed{seed}",
                "out_root": (MATRIX_DIR / f"{method}_seed{seed}").as_posix(),
            })
    for method in ABLATIONS:
        cells.append({
            "cell_id": f"qwen_{method}_42", "method": method,
            "seed": 42, "block": "ablations",
            "out_root": (MATRIX_DIR / f"{method}_seed42").as_posix(),
        })
    return cells


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def holm_adjust(p_values: list[float]) -> list[float]:
    """Return Holm-adjusted p-values in original order, preserving monotonicity."""
    if any(not _number(p) or p < 0 or p > 1 for p in p_values):
        raise ValueError("p-values must be finite numbers in [0, 1]")
    n = len(p_values)
    ordered = sorted(enumerate(p_values), key=lambda pair: pair[1])
    adjusted = [0.0] * n
    running = 0.0
    for rank, (index, p_value) in enumerate(ordered):
        running = max(running, min(1.0, (n - rank) * p_value))
        adjusted[index] = running
    return adjusted


def holm_adjust_family(p_values: dict[str, float], family_names: tuple[str, ...]) -> dict[str, float | None]:
    """Adjust a prespecified family only when every planned comparison exists."""
    if not all(name in p_values for name in family_names):
        return {name: None for name in family_names}
    adjusted = holm_adjust([p_values[name] for name in family_names])
    return dict(zip(family_names, adjusted))


def paired_effect(a_rows: dict[str, Any], b_rows: dict[str, Any], *, n_boot: int = BOOTSTRAP_RESAMPLES,
                  seed: int = 42) -> dict[str, Any]:
    """Paired B-A hard-score effect on identical test IDs."""
    if set(a_rows) != set(b_rows):
        raise ValueError("paired comparison requires identical test item IDs")
    ids = sorted(a_rows)
    if not ids:
        raise ValueError("paired comparison has no test items")
    diffs = [int(b_rows[item_id]["hard"]) - int(a_rows[item_id]["hard"]) for item_id in ids]
    only_a = sum(1 for item_id in ids if a_rows[item_id]["hard"] == 1 and b_rows[item_id]["hard"] == 0)
    only_b = sum(1 for item_id in ids if a_rows[item_id]["hard"] == 0 and b_rows[item_id]["hard"] == 1)
    lo, hi = bootstrap_ci(diffs, n_boot=n_boot, seed=seed)
    return {
        "n_items": len(ids),
        "score_a": sum(int(a_rows[i]["hard"]) for i in ids) / len(ids),
        "score_b": sum(int(b_rows[i]["hard"]) for i in ids) / len(ids),
        "delta_b_minus_a": sum(diffs) / len(diffs),
        "task_item_bootstrap_95_ci": [lo, hi],
        "only_a_correct": only_a,
        "only_b_correct": only_b,
        "mcnemar_exact_p_descriptive": mcnemar_exact(only_a, only_b),
    }


def _status_for_cell(root: Path, cell: dict[str, Any], queue: dict[str, Any]) -> dict[str, Any]:
    folder = root / cell["out_root"]
    exists = folder.is_dir()
    queue_record = queue.get(cell["cell_id"], {})
    result: dict[str, Any] = {
        **cell,
        "queue_status": queue_record.get("status"),
        "integrity_valid": False,
        "eligible": False,
        "reason": "cell directory is missing" if not exists else "",
    }
    if not exists:
        return result
    valid, reason = validate_cell(folder)
    result["integrity_valid"] = bool(valid)
    if not valid:
        result["reason"] = reason
        return result

    summary = _read_json(folder / "summary.json")
    if not isinstance(summary, dict):
        result["reason"] = "summary.json is unreadable"
        return result
    phase_data: dict[str, dict[str, Any]] = {}
    try:
        for metric, phase in PHASES.items():
            rows = phase_rows(folder, phase)
            phase_data[metric] = rows
            score = sum(row["hard"] for row in rows.values()) / len(rows)
            reported = summary.get(metric)
            if not _number(reported) or not math.isclose(score, reported, abs_tol=1e-12):
                raise ValueError(f"{metric} aggregate does not match phase rows")
    except (OSError, ValueError, TypeError, KeyError, ZeroDivisionError) as exc:
        result["reason"] = f"test evidence unavailable: {exc}"
        return result
    result.update({
        "eligible": True,
        "reason": reason,
        "scores": {key: summary.get(key) for key in PHASES},
        "baseline_test_hard": summary.get("baseline_test_hard"),
        "summary": summary,
        "phase_rows": phase_data,
        "history": _read_json(folder / "history.json"),
        "cell_path": folder,
        "queue_record": queue_record,
    })
    return result


def _queue_cells(root: Path) -> dict[str, dict[str, Any]]:
    data = _read_json(root / REPORT_DIR / "queue_state.json")
    if not isinstance(data, dict) or not isinstance(data.get("cells"), list):
        return {}
    return {str(row.get("cell_id")): row for row in data["cells"] if isinstance(row, dict) and row.get("cell_id")}


def _safe_token_totals(token_summary: Any) -> dict[str, Any] | None:
    if not isinstance(token_summary, dict):
        return None
    total = token_summary.get("_total")
    if not isinstance(total, dict):
        # Some wrappers store a plain aggregate summary.
        total = token_summary
    result = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens",
                "calls", "requests"):
        value = total.get(key)
        if _number(value):
            result[key] = value
    if "total_tokens" not in result and ("prompt_tokens" in result or "completion_tokens" in result):
        result["total_tokens"] = result.get("prompt_tokens", 0) + result.get("completion_tokens", 0)
    return result or None


def _costs(cell: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    folder = cell["cell_path"]
    cost = _read_json(folder / "cost_summary.json")
    warnings = []
    if isinstance(cost, dict):
        return ({
            "source": "cost_summary.json",
            "token_totals": _safe_token_totals(cost.get("token_summary")),
            "attempts": cost.get("attempts") if _number(cost.get("attempts")) else None,
            "wall_time_s": cost.get("wall_time_s") if _number(cost.get("wall_time_s")) else None,
            "coverage": cost.get("coverage"),
        }, warnings)
    summary = cell["summary"]
    queue_record = cell["queue_record"]
    warnings.append("cost_summary.json missing; token total is from final summary and may omit retry attempts")
    wall = queue_record.get("wall_time_s")
    if not _number(wall):
        wall = summary.get("total_wall_time_s")
    return ({
        "source": "summary.json fallback",
        "token_totals": _safe_token_totals(summary.get("token_summary")),
        "attempts": None,
        "wall_time_s": wall if _number(wall) else None,
        "coverage": "retry-attempt coverage unknown",
    }, warnings)


def _memory_stats(cell: dict[str, Any]) -> dict[str, Any]:
    folder, summary = cell["cell_path"], cell["summary"]
    memory_data = _read_json(folder / "cam_rejected_memory.json")
    items = memory_data.get("items") if isinstance(memory_data, dict) else None
    kinds = Counter(str(item.get("kind", "unknown")) for item in items if isinstance(item, dict)) if isinstance(items, list) else None
    mem_summary = summary.get("persistent_memory")
    mem_summary = mem_summary if isinstance(mem_summary, dict) else {}
    retrieval_files = 0
    retrieval_hits = 0
    retrieval_hit_calls = 0
    retrieval_incomplete = 0
    for path in folder.rglob("memory_retrieval.json"):
        retrieval_files += 1
        record = _read_json(path)
        if not isinstance(record, dict):
            retrieval_incomplete += 1
            continue
        count = record.get("hit_count")
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
            retrieval_hits += count
            retrieval_hit_calls += int(count > 0)
        else:
            retrieval_incomplete += 1
    return {
        "enabled": mem_summary.get("enabled"),
        "stored_item_count": mem_summary.get("stored_item_count"),
        "stored_item_kinds": dict(sorted(kinds.items())) if kinds is not None else None,
        "retrieval_files": retrieval_files,
        "retrieval_hit_calls": retrieval_hit_calls if retrieval_files else None,
        "retrieval_hits": retrieval_hits if retrieval_files and retrieval_incomplete == 0 else None,
        "retrieval_evidence_incomplete_files": retrieval_incomplete,
        "injected_groups": mem_summary.get("injected_groups"),
        "injected_items": mem_summary.get("injected_items"),
        "completed_injected_groups": mem_summary.get("completed_injected_groups"),
        "memory_coverage": mem_summary.get("coverage"),
    }


def _guard_rollbacks(cell: dict[str, Any]) -> int | None:
    summary_guard = cell["summary"].get("cam_drift_guard")
    if isinstance(summary_guard, dict) and isinstance(summary_guard.get("rollbacks"), int):
        return summary_guard["rollbacks"]
    history = cell.get("history")
    if not isinstance(history, list):
        return None
    values = [step.get("drift_guard", {}).get("rollbacks") for step in history
              if isinstance(step, dict) and isinstance(step.get("drift_guard"), dict)
              and isinstance(step["drift_guard"].get("rollbacks"), int)]
    # The field is checkpoint-cumulative, so the final recorded value is the total.
    return values[-1] if values else None


def _mean_sd(values: list[float]) -> dict[str, float | None]:
    return {"mean": statistics.mean(values), "sample_sd": statistics.stdev(values) if len(values) > 1 else None}


def _analysis(cells: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    by_id = {cell["cell_id"]: cell for cell in cells}
    warnings: list[str] = []
    main: dict[str, Any] = {}
    for metric in PHASES:
        per_seed = []
        for seed in SEEDS:
            a = by_id[f"qwen_baseline_{seed}"]
            b = by_id[f"qwen_cam_full_{seed}"]
            if not (a["eligible"] and b["eligible"]):
                per_seed.append({"seed": seed, "available": False,
                                 "missing_cells": [c["cell_id"] for c in (a, b) if not c["eligible"]]})
                continue
            effect = paired_effect(a["phase_rows"][metric], b["phase_rows"][metric])
            effect.update({"seed": seed, "available": True})
            per_seed.append(effect)
        available = [row for row in per_seed if row["available"]]
        delta_summary = _mean_sd([row["delta_b_minus_a"] for row in available]) if available else None
        main[metric] = {
            "comparison": "cam_full - baseline",
            "per_seed": per_seed,
            "across_training_seeds": delta_summary,
            "training_seed_count": len(available),
            "interpretation": "Item bootstrap intervals describe test-item uncertainty within each trained seed. The across-seed mean and sample SD describe variation across the observed training seeds; they are distinct quantities.",
        }
        if len(available) < len(SEEDS):
            warnings.append(f"{metric}: only {len(available)}/{len(SEEDS)} complete paired training seeds; no full-study conclusion is available")

    ablations: dict[str, Any] = {}
    p_values = []
    names = []
    for method in ABLATIONS:
        full = by_id["qwen_cam_full_42"]
        ablated = by_id[f"qwen_{method}_42"]
        if not (full["eligible"] and ablated["eligible"]):
            ablations[method] = {"available": False,
                                 "missing_cells": [c["cell_id"] for c in (full, ablated) if not c["eligible"]]}
            continue
        primary = paired_effect(ablated["phase_rows"]["test_hard"], full["phase_rows"]["test_hard"])
        row = {"available": True, "comparison": "cam_full - ablation", "seed": 42,
               "primary_test_hard": primary,
               "final_test_hard": paired_effect(ablated["phase_rows"]["final_test_hard"], full["phase_rows"]["final_test_hard"])}
        ablations[method] = row
        p_values.append(primary["mcnemar_exact_p_descriptive"])
        names.append(method)
    adjusted = holm_adjust_family(dict(zip(names, p_values)), ABLATIONS)
    for method in ABLATIONS:
        if ablations[method].get("available"):
            ablations[method]["primary_test_hard"]["mcnemar_holm_adjusted_p"] = adjusted[method]
    if len(names) < len(ABLATIONS):
        warnings.append(f"Ablations: Holm-adjusted p-values withheld until all {len(ABLATIONS)} planned seed-42 comparisons are available")
    return {"primary": main["test_hard"], "secondary": main["final_test_hard"],
            "ablations_seed42": ablations}, warnings


def build_report(root: Path = ROOT) -> dict[str, Any]:
    """Collect integrity-checked aggregates and write the JSON/Markdown report."""
    root = Path(root)
    queue = _queue_cells(root)
    records = [_status_for_cell(root, cell, queue) for cell in study_cells()]
    analysis, analysis_warnings = _analysis(records)
    warnings = list(analysis_warnings)
    public_cells = []
    for cell in records:
        public = {key: cell.get(key) for key in ("cell_id", "method", "seed", "block", "out_root",
                                                  "queue_status", "integrity_valid", "eligible", "reason")}
        if cell["eligible"]:
            cost, cost_warnings = _costs(cell)
            warnings.extend(f"{cell['cell_id']}: {warning}" for warning in cost_warnings)
            public.update({"scores": cell["scores"], "baseline_test_hard": cell.get("baseline_test_hard"),
                           "actions": _action_stats(cell), "drift_guard_rollbacks": _guard_rollbacks(cell),
                           "cost": cost, "persistent_memory": _memory_stats(cell)})
        public_cells.append(public)
    report = {
        "study_id": STUDY_ID,
        "protocol": "docs/experiments/CAM_V2_PLAN.md",
        "status": "complete" if all(cell["eligible"] for cell in records) else "incomplete",
        "primary_endpoint": "test_hard (validation-gate-authorized incumbent)",
        "secondary_endpoint": "final_test_hard",
        "selection_rule": "Report test_eval for the validation-gate-authorized incumbent as primary and test_eval_final as secondary; never select the larger test result.",
        "inference": {
            "training_seeds": list(SEEDS),
            "paired_item_bootstrap_resamples": BOOTSTRAP_RESAMPLES,
            "confidence_level": 0.95,
            "scope": "Exploratory: three paired training seeds on one fixed test split. Per-seed intervals resample paired test items and do not estimate training randomness.",
            "mcnemar": "Descriptive paired discordance summary; not a strong significance claim.",
        },
        "cells": public_cells,
        "analysis": analysis,
        "warnings": sorted(set(warnings)),
    }
    out_dir = root / REPORT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    _atomic_json(out_dir / "paired_results.json", report)
    (out_dir / "EXPERIMENT_RESULTS.md").write_text(render_markdown(report), encoding="utf-8")
    return report


def _action_stats(cell: dict[str, Any]) -> dict[str, Any]:
    history = cell.get("history")
    if not isinstance(history, list):
        return {"accept": None, "accept_new_best": None, "soft_accept": None, "reject": None, "skip": None}
    counts = Counter()
    for row in history:
        if not isinstance(row, dict):
            continue
        action = row.get("action")
        if action in {"accept", "accept_new_best", "soft_accept", "reject"}:
            counts[action] += 1
        elif action in {"skip", "skip_no_patches"}:
            counts["skip"] += 1
    return {key: counts.get(key, 0) for key in ("accept", "accept_new_best", "soft_accept", "reject", "skip")}


def _fmt(value: Any, digits: int = 3) -> str:
    return "unknown" if not _number(value) else f"{value:.{digits}f}"


def render_markdown(report: dict[str, Any]) -> str:
    cells = report["cells"]
    lines = ["# CAM v2r1 focused study results", "",
             f"Study status: **{report['status']}**. Protocol: [`{report['protocol']}`](../../{report['protocol']}).", "",
             "The primary endpoint is `test_hard`, the test score for the validation-gate-authorized incumbent. `final_test_hard` is secondary. The larger test result is never selected after looking at test data.", "",
             "## Cell status and descriptive metrics", "",
             "| Cell | Status | Best test hard | Final test hard | Tokens | Wall time (s) | Actions A/A-B/S/R/skip | Drift rollbacks | Memory evidence |",
             "|---|---|---:|---:|---:|---:|---|---:|---|"]
    for cell in cells:
        if not cell["eligible"]:
            lines.append(f"| `{cell['cell_id']}` | incomplete: {cell['reason']} | — | — | — | — | — | — | — |")
            continue
        cost = cell.get("cost", {})
        tokens = (cost.get("token_totals") or {}).get("total_tokens")
        actions = cell.get("actions", {})
        action_text = "/".join(str(actions.get(k, "unknown")) for k in ("accept", "accept_new_best", "soft_accept", "reject", "skip"))
        memory = cell.get("persistent_memory", {})
        mem_text = (f"stored={memory.get('stored_item_count', 'unknown')}; injected/completed="
                    f"{memory.get('injected_groups', 'unknown')}/{memory.get('completed_injected_groups', 'unknown')}; "
                    f"retrieval hits={memory.get('retrieval_hits', 'unknown')}")
        lines.append(f"| `{cell['cell_id']}` | eligible | {_fmt(cell['scores'].get('test_hard'))} | {_fmt(cell['scores'].get('final_test_hard'))} | {tokens if tokens is not None else 'unknown'} | {_fmt(cost.get('wall_time_s'), 1)} | {action_text} | {cell.get('drift_guard_rollbacks') if cell.get('drift_guard_rollbacks') is not None else 'unknown'} | {mem_text} |")
    lines.extend(["", "## Paired effects", "", "Effects are treatment minus reference. Per-seed 95% intervals bootstrap matched test items (20,000 resamples); McNemar discordance p-values are descriptive. Across-seed mean and sample SD summarize only the observed training seeds.", ""])
    for title, key in (("Primary: `test_hard`", "primary"), ("Secondary: `final_test_hard`", "secondary")):
        section = report["analysis"][key]
        lines.extend([f"### {title}", "", "| Seed | Baseline | CAM full | Delta (pp) | Paired item 95% CI (pp) | A-only / CAM-only | McNemar p |", "|---:|---:|---:|---:|---:|---:|---:|"])
        for row in section["per_seed"]:
            if not row["available"]:
                lines.append(f"| {row['seed']} | — | — | — | — | — | — |")
                continue
            ci = row["task_item_bootstrap_95_ci"]
            lines.append(f"| {row['seed']} | {_fmt(row['score_a'])} | {_fmt(row['score_b'])} | {100*row['delta_b_minus_a']:+.2f} | [{100*ci[0]:+.2f}, {100*ci[1]:+.2f}] | {row['only_a_correct']} / {row['only_b_correct']} | {_fmt(row['mcnemar_exact_p_descriptive'], 4)} |")
        across = section.get("across_training_seeds")
        if across:
            sd = _fmt(100 * across["sample_sd"], 2) if across["sample_sd"] is not None else "unknown"
            lines.append(f"\nAcross available seeds: mean delta **{100*across['mean']:+.2f} pp**, sample SD **{sd} pp** ({section['training_seed_count']} paired seeds).")
        else:
            lines.append("\nAcross-seed effect unavailable until paired cells complete.")
        lines.append("")
    lines.extend(["## Seed-42 ablations", "", "Positive deltas favor CAM full. Holm adjustment is reported only when all three planned primary-endpoint McNemar comparisons are available; these single-seed component comparisons are exploratory.", "",
                  "| Ablation | Primary delta (pp) | 95% paired item CI (pp) | McNemar p | Holm adjusted p | Secondary delta (pp) |", "|---|---:|---:|---:|---:|---:|"])
    for method, row in report["analysis"]["ablations_seed42"].items():
        if not row["available"]:
            lines.append(f"| `{method}` | — | — | — | — | — |")
            continue
        primary, secondary = row["primary_test_hard"], row["final_test_hard"]
        ci = primary["task_item_bootstrap_95_ci"]
        lines.append(f"| `{method}` | {100*primary['delta_b_minus_a']:+.2f} | [{100*ci[0]:+.2f}, {100*ci[1]:+.2f}] | {_fmt(primary['mcnemar_exact_p_descriptive'], 4)} | {_fmt(primary.get('mcnemar_holm_adjusted_p'), 4)} | {100*secondary['delta_b_minus_a']:+.2f} |")
    lines.extend(["", "## Interpretation and limits", "",
                  "This is an exploratory focused study: three paired training seeds share one fixed test split. Item bootstrap intervals measure item-level uncertainty conditional on a trained seed; they do not represent training-seed variability. The across-seed mean and sample SD use only three observed seeds and should not be read as strong evidence of generalization.", "",
                  "The v2 protocol was motivated by v1 observations and changes to soft acceptance, drift protection, inconclusive memory, and action accounting. This report therefore evaluates the frozen v2r1 configuration; it is not a preregistered confirmation of a hypothesis independent of prior experiments. See the protocol for the rationale and prior evidence.", "",
                  "Costs use `cost_summary.json` when available, including its attempt coverage. A summary fallback can omit failed/retried process usage and is marked in the JSON warnings. Persistent-memory hits, injected groups, and completed injected groups are reported from their respective evidence; unknown or absent evidence remains unknown.", ""])
    if report["warnings"]:
        lines.extend(["## Data coverage notes", ""])
        lines.extend(f"- {warning}" for warning in report["warnings"])
        lines.append("")
    return "\n".join(lines)


def _atomic_json(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="study workspace root (defaults to this repository)")
    args = parser.parse_args()
    report = build_report(args.root)
    print(f"Wrote {args.root / REPORT_DIR / 'EXPERIMENT_RESULTS.md'} ({report['status']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
