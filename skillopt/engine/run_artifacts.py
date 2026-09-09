"""Evidence and invalid-run summaries shared by training and evaluation."""
from __future__ import annotations

import json
import os
import time
from collections import Counter

from skillopt.model.infra_errors import InfraError, detect_infra_error


def write_json(path: str, value) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)


def assert_valid_results(results: list[dict], *, stage: str) -> None:
    """Refuse both new infra records and legacy cached auth failures."""
    for row in results:
        error = None
        if row.get("status") == "infra_error" or row.get("infra_error"):
            info = row.get("infra_error") or row
            error = InfraError(
                info.get("failure_type", "provider_error"),
                info.get("message") or row.get("fail_reason") or "Cached infrastructure failure",
                stage=stage,
                details={"task_id": row.get("id"), "cached": True},
            )
        elif (not row.get("llm_ok", row.get("agent_ok", False))
              and row.get("phase") in {"llm", "agent", "target_codegen", "target_react"}
              and (row.get("error") or row.get("fail_reason"))):
            error = detect_infra_error(
                f"{row.get('fail_reason', '')}\n{row.get('error', '')}", stage=stage,
            )
        if error is not None:
            raise error


def write_stage_stats(out_dir: str, results: list[dict], *, expected: int,
                      status: str, started_at: float) -> dict:
    valid = [row for row in results if row.get("status") != "infra_error"]
    stats = {
        "stage": os.path.basename(out_dir), "status": status,
        "n_expected": expected, "n_recorded": len(results),
        "n_scored": len(valid), "n_not_run": max(0, expected - len(results)),
        "failure_types": dict(Counter(row.get("failure_type") or "none" for row in results)),
        "phase_counts": dict(Counter(row.get("phase") or "unknown" for row in results)),
        "llm_ok": sum(bool(row.get("llm_ok", row.get("agent_ok"))) for row in results),
        "code_ok": sum(bool(row.get("code_ok")) for row in results),
        "exec_ok": sum(bool(row.get("exec_ok")) for row in results),
        "hard": (sum(float(row.get("hard") or 0) for row in valid) / len(valid)
                 if valid and status == "completed" else None),
        "soft": (sum(float(row.get("soft") or 0) for row in valid) / len(valid)
                 if valid and status == "completed" else None),
        "wall_time_s": round(time.time() - started_at, 3),
    }
    write_json(os.path.join(out_dir, "stage_stats.json"), stats)
    return stats


def write_invalid_summary(out_root: str, error: InfraError, *,
                          started_at: float | None = None, context: dict | None = None,
                          filename: str = "summary.json") -> dict:
    """No partial metric from an aborted run is a valid experimental score."""
    validity = "invalid_auth" if "auth" in error.failure_type else "invalid_infra"
    summary = {
        "status": "infra_error", "validity": validity,
        "eligible_for_paper": False, "failure_type": error.failure_type,
        "infra_error": error.to_dict(), "context": context or {},
        "hard": None, "soft": None,
        "baseline_selection_hard": None, "best_selection_hard": None,
        "final_selection_hard": None, "final_selection_soft": None,
        "baseline_test_hard": None, "baseline_test_soft": None,
        "test_hard": None, "test_soft": None,
        "final_test_hard": None, "final_test_soft": None,
        "test_delta_hard": None, "final_test_delta_hard": None,
        "total_wall_time_s": round(time.time() - started_at, 3) if started_at else None,
        "note": "Infrastructure failure aborted the run; completed task artifacts remain diagnostic evidence only.",
    }
    history_path = os.path.join(out_root, "history.json")
    if os.path.exists(history_path):
        with open(history_path, encoding="utf-8") as handle:
            history = json.load(handle)
        summary["completed_steps_before_abort"] = len(history)
    write_json(os.path.join(out_root, filename), summary)
    write_json(os.path.join(out_root, "infra_error.json"), error.to_dict())
    return summary
