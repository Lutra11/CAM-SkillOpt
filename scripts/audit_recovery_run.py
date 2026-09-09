#!/usr/bin/env python3
"""One-shot, offline, allowlisted audit of a recovery P0 run.

Does not import the experiment runtime, execute models, monitor, or modify raw
run artifacts. Optional exports must be outside the audited run directory.
Only numeric metrics, task IDs, controlled labels and relative evidence paths
are exported; instructions, workbook cells, prompts and trace text stay local.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import itertools
import json
import math
from pathlib import Path
import re


FAILURES = {
    "none", "auth_error", "network_error", "model_unavailable", "provider_error",
    "llm_timeout", "worker_timeout", "artifact_missing", "code_syntax_error",
    "code_missing", "llm_output_error", "execution_error", "score_mismatch",
    "task_timeout", "dataset_error", "agent_error", "unexpected_error", "no_patch",
}
CONFIG_NUMERIC = (
    "train_size", "batch_size", "sel_env_num", "test_env_num", "num_epochs",
    "steps_per_epoch", "accumulation", "limit", "seed", "split_seed", "workers",
    "analyst_workers", "exec_timeout", "edit_budget", "cam_min_budget", "cam_max_budget",
)
SUMMARY_METRICS = (
    "baseline_selection_hard", "best_selection_hard", "final_selection_hard",
    "final_selection_soft", "baseline_test_hard", "baseline_test_soft", "test_hard",
    "test_soft", "final_test_hard", "final_test_soft", "total_wall_time_s",
)


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def nonnegative(value):
    value = number(value)
    return value if value is not None and value >= 0 else None


def score(value):
    value = number(value)
    return value if value is not None and 0 <= value <= 1 else None


def mapping(value):
    return value if isinstance(value, dict) else {}


def assistant_observed(conversation):
    return isinstance(conversation, list) and any(
        isinstance(message, dict) and message.get("role") == "assistant"
        and isinstance(message.get("content"), str) and message["content"].strip()
        for message in conversation
    )


def read_json(path: Path, issues: list, default=None):
    if not path.exists():
        return default
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if default is not None and not isinstance(value, type(default)):
            issues.append({"kind": "unexpected_json_shape", "file": path.name})
            return default
        return value
    except (OSError, ValueError):
        issues.append({"kind": "unreadable_or_partial_json", "file": path.name})
        return default


def read_rows(path: Path, issues: list) -> list:
    rows = []
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        issues.append({"kind": "unreadable_jsonl", "file": path.name})
        return rows
    for index, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
        except ValueError:
            issues.append({"kind": "partial_jsonl_row", "file": path.name, "line": index})
    return rows


def label(value, allowed, fallback="unknown"):
    return value if isinstance(value, str) and value in allowed else fallback


def task_id(value):
    text = str(value or "")
    return text if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", text) else "redacted_nonstandard_id"


def origin_label(value):
    """Only source-generated origin labels, never arbitrary summary text."""
    return value if isinstance(value, str) and re.fullmatch(
        r"initial_skill|step_\d{4}|slow_update(?:_placeholder)?_epoch_\d{2}", value
    ) else "unknown"


def paired_test_diagnostic(tasks, stages, core_verified):
    """Exact n=4 paired bootstrap enumeration; not a population efficacy CI."""
    result = {"status": "unavailable_or_unverified", "baseline_stage": "test_eval_baseline",
        "best_stage": "test_eval", "n_pairs": None, "pairs": [], "metrics": {},
        "confidence_level": 0.95, "resamples": None,
        "method": "exact_enumeration_of_all_ordered_paired_bootstrap_resamples_linear_percentiles",
        "scope": "diagnostic_only_four_observed_tasks_not_formal_efficacy_evidence",
        "note": "A degenerate [0,0] interval means the four observed paired differences are all zero; it does not prove equivalence or generalize beyond these tasks."}
    baseline = [row for row in tasks if row["stage"] == result["baseline_stage"]]
    best = [row for row in tasks if row["stage"] == result["best_stage"]]
    stage_map = {row["stage"]: row for row in stages}
    ids = [row["task_id"] for row in baseline]
    other = {row["task_id"]: row for row in best}
    if (not core_verified or len(ids) != 4 or len(best) != 4 or len(set(ids)) != 4
        or set(ids) != set(other)
        or not all(stage_map.get(name, {}).get("complete_scored_artifacts")
                   for name in (result["baseline_stage"], result["best_stage"]))
        or not all(row.get("scored") and score(row.get("hard")) is not None
                   and score(row.get("soft")) is not None for row in baseline + best)):
        return result
    result.update(status="verified_exploratory_diagnostic", n_pairs=4, resamples=4 ** 4)
    result["pairs"] = [{"task_id": row["task_id"], "baseline_hard": row["hard"],
        "best_hard": other[row["task_id"]]["hard"], "hard_difference": other[row["task_id"]]["hard"] - row["hard"],
        "baseline_soft": row["soft"], "best_soft": other[row["task_id"]]["soft"],
        "soft_difference": other[row["task_id"]]["soft"] - row["soft"]} for row in baseline]
    for metric in ("hard", "soft"):
        differences = [row[metric + "_difference"] for row in result["pairs"]]
        means = sorted(sum(sample) / 4 for sample in itertools.product(differences, repeat=4))
        def percentile(q):
            position = (len(means) - 1) * q
            lower = math.floor(position)
            upper = math.ceil(position)
            return means[lower] + (means[upper] - means[lower]) * (position - lower)
        result["metrics"][metric] = {"mean_difference_best_minus_baseline": sum(differences) / 4,
            "lower": percentile(0.025), "upper": percentile(0.975),
            "all_observed_differences_zero": all(value == 0 for value in differences)}
    return result


def task_failure(row):
    failure = row.get("failure_type")
    if failure:
        return label(failure, FAILURES, "other_failure")
    if row.get("status") == "infra_error" or row.get("infra_error"):
        return label((row.get("infra_error") or {}).get("failure_type"), FAILURES, "provider_error")
    if row.get("ok"):
        return "none"
    return {"setup": "dataset_error", "llm": "llm_output_error", "extract": "code_missing",
            "exec": "execution_error" if not row.get("exec_ok") else "score_mismatch"}.get(row.get("phase"), "other_failure")


def usage_from_raw(path: Path | None) -> dict:
    """Parse usage/error events from ONE canonical raw file, never its copies."""
    result = {"raw_file_present": bool(path), "completed_turn_events": 0,
              "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0,
              "error_event_types": {}, "usage_observed": False, "usage_complete": False,
              "usage_events_with_complete_fields": 0, "usage_events_with_missing_fields": 0,
              "network_reconnect_notice_count": 0, "recovered_network_notice_count": 0,
              "transport_verified": False, "transport_failed": False}
    if path is None:
        return result
    failures = Counter()
    channel = "stdout"
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip() in {"[stdout]", "[stderr]"}:
            channel = line.strip()[1:-1]
            continue
        if line.startswith("===== CODEX CLI ATTEMPT"):
            channel = "stdout"
        try:
            event = json.loads(line)
        except ValueError:
            terminal = re.match(r"\s*(?:ERROR:|FATAL:)", line, re.I)
            authentication = channel == "stderr" and re.search(r"\b401\b|invalid_refresh_token|refresh_token_reused|unauthorized", line, re.I)
            if terminal or authentication:
                failures[_terminal_failure_type(line)] += 1
            elif channel == "stderr" and _is_reconnect_notice(line):
                result["network_reconnect_notice_count"] += 1
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "turn.completed":
            usage = mapping(event.get("usage"))
            result["completed_turn_events"] += 1
            complete_fields = all(nonnegative(usage.get(key)) is not None for key in ("input_tokens", "output_tokens"))
            result["usage_events_with_complete_fields" if complete_fields else "usage_events_with_missing_fields"] += 1
            for key in ("input_tokens", "cached_input_tokens", "output_tokens"):
                if nonnegative(usage.get(key)) is not None:
                    result[key] += usage[key]
                    result["usage_observed"] = True
        if event.get("type") in {"error", "turn.failed"}:
            text = json.dumps(event, ensure_ascii=False).lower()
            failure = _terminal_failure_type(text)
            if event.get("type") == "error" and failure not in {"auth_error", "model_unavailable"} and _is_reconnect_notice(str(event.get("message", ""))):
                result["network_reconnect_notice_count"] += 1
            else:
                failures[failure] += 1
    result["error_event_types"] = dict(failures)
    result["transport_evidence"] = _transport_evidence(path, result)
    result["transport_verified"] = result["transport_evidence"]["verified"]
    result["transport_failed"] = result["transport_evidence"]["status"] == "failed"
    if result["transport_evidence"]["verification"] == "recovered_with_matching_evidence":
        result["recovered_network_notice_count"] = result["network_reconnect_notice_count"]
    complete_fields = result["completed_turn_events"] > 0 and result["usage_events_with_missing_fields"] == 0
    result["usage_complete"] = complete_fields and not failures and result["transport_verified"]
    result["input_plus_output_tokens"] = result["input_tokens"] + result["output_tokens"] if complete_fields else None
    return result


def _terminal_failure_type(text):
    if re.search(r"\b401\b|invalid_refresh_token|refresh_token_reused|unauthorized", text, re.I):
        return "auth_error"
    if re.search(r"model_not_found|unsupported_model|model.{0,80}(?:not available|unavailable|not supported|does not exist)", text, re.I):
        return "model_unavailable"
    if re.search(r"disconnect|connect|network|socket|10054", text, re.I):
        return "network_error"
    return "provider_error"


def _is_reconnect_notice(message):
    return bool(re.match(r"\s*Reconnecting\b", message, re.I) and re.search(r"network|connection|stream|error sending request", message, re.I))


def _transport_evidence(path, usage):
    sidecar = path.parent / "transport_warnings.json"
    present = sidecar.is_file()
    evidence = {"file_present": present, "status": "unknown", "notice_count": None,
        "max_recovery_notices": None, "returncode": None, "recovered": None,
        "verified": False, "verification": "missing_or_inconsistent"}
    notices = usage["network_reconnect_notice_count"]
    terminal = bool(usage["error_event_types"])
    if present:
        try:
            record = mapping(json.loads(sidecar.read_text(encoding="utf-8-sig")))
        except (ValueError, OSError):
            record = {}
        evidence.update(status=label(record.get("status"), {"completed", "failed"}),
            notice_count=nonnegative(record.get("notice_count")),
            max_recovery_notices=nonnegative(record.get("max_recovery_notices")),
            returncode=number(record.get("returncode")),
            recovered=record.get("recovered") if isinstance(record.get("recovered"), bool) else None)
        consistent = (evidence["notice_count"] == notices and evidence["max_recovery_notices"] == 2
                      and evidence["returncode"] == 0 and evidence["status"] == "completed"
                      and evidence["recovered"] is (notices > 0)
                      and notices <= 2 and usage["completed_turn_events"] > 0)
        if consistent and not terminal:
            evidence.update(verified=True, verification="recovered_with_matching_evidence" if notices else "no_notices_matching_evidence")
    elif notices == 0 and not terminal:
        # Historical successful traces have no sidecar; no recovery is inferred.
        evidence.update(verified=True, verification="no_notices_observed_no_recovery_claim")
    if terminal:
        evidence.update(verified=False, verification="terminal_infrastructure_error")
    return evidence


def sum_usage(records):
    keys = ("completed_turn_events", "input_tokens", "cached_input_tokens", "output_tokens")
    total = {key: sum(record.get(key, 0) for record in records) for key in keys}
    total["request_artifacts"] = len(records)
    total["canonical_files"] = sum(bool(record.get("raw_file_present")) for record in records)
    total["files_with_usage"] = sum(bool(record.get("usage_observed")) for record in records)
    total["usage_events_with_missing_fields"] = sum(record.get("usage_events_with_missing_fields", 0) for record in records)
    total["input_plus_output_tokens"] = total["input_tokens"] + total["output_tokens"] if total["files_with_usage"] and not total["usage_events_with_missing_fields"] else None
    total["coverage"] = "complete" if records and all(record.get("usage_complete") for record in records) else "partial_or_unavailable"
    total["recovered_network_notice_count"] = sum(record.get("recovered_network_notice_count", 0) for record in records)
    total["recovered_network_affected_requests"] = sum(record.get("recovered_network_notice_count", 0) > 0 for record in records)
    total["unverified_transport_requests"] = sum(not record.get("transport_verified") for record in records)
    return total


def source_evidence(source_root: Path) -> dict:
    """Line references from current source; not claims inferred from config."""
    checks = {
        "skillopt/engine/trainer.py": {
            "rejected_memory_initialization": "cam_memory = (",
            "rejected_memory_write_call": "cam_memory.add_rejected_step(",
            "rejected_memory_retrieve_call": "cam_memory.retrieve(",
            "separate_step_buffer_context": "step_buffer_context = _format_step_buffer(step_buffer)",
            "budget_computation": "edit_budget = compute_adaptive_budget(",
            "memory_write_stat": 'buf_entry["cam_memory_items_added"]',
            "baseline_selection_call_before_loop_timer": "baseline_results = adapter.rollout(sel_env, skill_init, baseline_dir)",
            "trainer_wall_timer_start": "t_loop_start = time.time()",
            "trainer_wall_timer_end": "total_wall = time.time() - t_loop_start",
        },
        "scripts/cam_recovery.py": {"wrapper_wall_timer_start": "started = time.monotonic()",
            "wrapper_wall_timer_end": "summary.update(manifest=manifest, wall_seconds="},
        "skillopt/cam/rejected_memory.py": {"memory_retrieve_definition": "def retrieve("},
        "skillopt/model/__init__.py": {"codex_tracker_summary_added": "codex_summary = _codex.get_token_summary()", "claude_tracker_summary_added": "claude_summary = _claude.get_token_summary()"},
        "skillopt/model/codex_harness.py": {"target_tracker_zero_tokens": '_openai.tracker.record("rollout", 0, 0)'},
    }
    found = {}
    for relative, operations in checks.items():
        path = source_root / relative
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        for operation, needle in operations.items():
            found[operation] = {"file": relative, "lines": [i for i, line in enumerate(lines, 1) if needle in line]}
    files = list((source_root / "skillopt").rglob("*.py")) + [source_root / "scripts" / name for name in ("train.py", "eval_only.py", "cam_recovery.py")]
    digest = hashlib.sha256()
    if all(path.exists() for path in files):
        for path in sorted(files):
            digest.update(path.relative_to(source_root).as_posix().encode())
            digest.update(path.read_bytes())
        found["current_source_sha256"] = digest.hexdigest()
    return found


def skill_fingerprint(path: Path | None, run: Path):
    if path is None:
        return None
    resolved = path.resolve()
    if run.resolve() not in resolved.parents or resolved.suffix != ".md" or not resolved.is_file():
        return None
    try:
        return hashlib.sha256(resolved.read_text(encoding="utf-8").encode()).hexdigest()[:16]
    except (OSError, UnicodeError):
        return None


def evaluation_provenance(run, cfg, summary, stage_map, step_records, issues):
    """Require direct complete evidence or prove same-skill reuse with hashes."""
    def usable(name):
        row = stage_map.get(name, {})
        return bool(row.get("complete_scored_artifacts") and row.get("n_assistant_observed") == row.get("expected")
                    and row.get("n_raw_trace_files") == row.get("expected"))

    sources = []
    initial_hash = skill_fingerprint(run / "skills/skill_v0000.md", run)
    if initial_hash and usable("selection_eval_baseline"):
        sources.append((initial_hash, "selection_eval_baseline"))
    candidate_records = []
    for step_dir, record in step_records:
        name = step_dir.relative_to(run).as_posix() + "/selection_eval"
        fingerprint = skill_fingerprint(step_dir / "candidate_skill.md", run)
        reported = score(record.get("selection_hard"))
        source = next((stage for key, stage in sources if key == fingerprint and fingerprint), None)
        if usable(name) and reported == stage_map[name]["completed_task_hard"]:
            candidate_records.append({"stage": name, "verified": True, "kind": "direct", "source": name})
            if fingerprint:
                sources.append((fingerprint, name))
        elif source and reported == stage_map[source]["completed_task_hard"]:
            candidate_records.append({"stage": name, "verified": True, "kind": "same_skill_cache", "source": source})
        else:
            candidate_records.append({"stage": name, "verified": False, "kind": "missing_or_unproven", "source": None})
    state = read_json(run / "runtime_state.json", issues, {})
    current_path = state.get("current_skill_path")
    if isinstance(current_path, str) and current_path:
        current_path = Path(current_path)
        current_path = current_path if current_path.is_absolute() else run / current_path
    else:
        current_path = None
    current_hash = skill_fingerprint(current_path, run)
    best_hash = skill_fingerprint(run / "best_skill.md", run)
    same_current_best = bool(current_hash and best_hash and current_hash == best_hash)
    final_selection = {"verified": False, "kind": "missing_or_unproven", "source": None}
    if usable("final_selection_eval"):
        row = stage_map["final_selection_eval"]
        if score(summary.get("final_selection_hard")) == row["completed_task_hard"] and score(summary.get("final_selection_soft")) == row["completed_task_soft"]:
            final_selection = {"verified": True, "kind": "direct", "source": "final_selection_eval"}
    elif same_current_best:
        source = next((stage for key, stage in sources if key == best_hash), None)
        if (source and score(summary.get("final_selection_hard")) == stage_map[source]["completed_task_hard"]
            and (summary.get("final_selection_soft") is None
                 or score(summary.get("final_selection_soft")) == stage_map[source]["completed_task_soft"])):
            final_selection = {"verified": True, "kind": "same_skill_reuse", "source": source}
    final_test = {"verified": False, "kind": "missing_or_unproven", "source": None}
    if usable("test_eval_final"):
        row = stage_map["test_eval_final"]
        if score(summary.get("final_test_hard")) == row["completed_task_hard"] and score(summary.get("final_test_soft")) == row["completed_task_soft"]:
            final_test = {"verified": True, "kind": "direct", "source": "test_eval_final"}
    elif same_current_best and usable("test_eval"):
        row = stage_map["test_eval"]
        reuse_marker = read_json(run / "test_eval_final/summary.json", issues, {})
        overall = mapping(reuse_marker.get("overall"))
        if (score(summary.get("final_test_hard")) == row["completed_task_hard"]
            and score(summary.get("final_test_soft")) == row["completed_task_soft"]
            and score(overall.get("hard_acc")) == row["completed_task_hard"]
            and number(overall.get("total")) == number(cfg.get("test_env_num"))):
            final_test = {"verified": True, "kind": "same_skill_reuse", "source": "test_eval"}
    for item in (final_selection, final_test):
        if item["verified"]:
            row = stage_map[item["source"]]
            item.update(derived_hard=row["completed_task_hard"], derived_soft=row["completed_task_soft"],
                derived_from_verified_source=True)
    return {"candidate_selection": candidate_records, "final_selection": final_selection,
            "final_test": final_test, "current_best_skill_hashes_match": same_current_best}


def audit(run: Path, source_root: Path) -> dict:
    issues = []
    cfg = read_json(run / "config.json", issues, {})
    summary = read_json(run / "summary.json", issues, {})
    recovery = read_json(run / "recovery_summary.json", issues, {})
    manifest = read_json(run / "manifest.json", issues, {})
    evidence = source_evidence(source_root)
    invalid = summary.get("status") == "infra_error" or recovery.get("status") == "infra_error"
    finished = bool(summary) and not invalid and summary.get("total_steps") is not None
    stage_records, public_tasks, target_usage = [], [], []
    canonical_target_paths = set()
    for path in sorted(run.rglob("results.jsonl")):
        rows = read_rows(path, issues)
        rows = [row for row in rows if "id" in row and "hard" in row and ("llm_ok" in row or "agent_ok" in row)]
        if not rows:
            continue
        stage = path.parent.relative_to(run).as_posix()
        safe_rows = []
        for row in rows:
            infra = row.get("status") == "infra_error" or bool(row.get("infra_error"))
            completed = row.get("status") in (None, "completed") and row.get("phase") not in {"running", "pending"}
            score_ok = completed and not infra and score(row.get("hard")) is not None and score(row.get("soft")) is not None
            task = path.parent / "predictions" / task_id(row["id"])
            conversation = read_json(task / "conversation.json", issues, [])
            observed = assistant_observed(conversation)
            canonical = next((task / name for name in ("codex_raw.txt", "infra_raw_trace.txt", "raw_trace.txt") if (task / name).is_file()), None)
            if canonical and canonical not in canonical_target_paths:
                canonical_target_paths.add(canonical)
                usage = usage_from_raw(canonical)
                usage.update(stage=stage, task_id=task_id(row["id"]), source=canonical.relative_to(run).as_posix())
                target_usage.append(usage)
            record = {"stage": stage, "task_id": task_id(row["id"]), "status": "infra_error" if infra else "completed" if completed else "unknown_or_incomplete",
                      "failure_type": task_failure(row), "hard": number(row.get("hard")) if score_ok else None,
                      "soft": number(row.get("soft")) if score_ok else None, "scored": score_ok,
                      "llm_ok": bool(row.get("llm_ok", row.get("agent_ok"))), "code_ok": bool(row.get("code_ok")),
                      "exec_ok": bool(row.get("exec_ok")), "n_cases": number(row.get("n_cases")),
                      "n_exec_pass": number(row.get("n_exec_pass")), "n_pass": number(row.get("n_pass")),
                      "conversation_file": (task / "conversation.json").exists(), "assistant_observed": observed,
                      "raw_trace_file": canonical is not None,
                      "raw_trace_nonempty": canonical is not None and canonical.stat().st_size > 0,
                      "wall_seconds": number(row.get("wall_time_s"))}
            safe_rows.append(record)
        public_tasks.extend(safe_rows)
        scored = [row for row in safe_rows if row["scored"]]
        stats = read_json(path.parent / "stage_stats.json", issues, {})
        expected = nonnegative(stats.get("n_expected"))
        if expected is None:
            expected = nonnegative(cfg.get("batch_size") if stage.endswith("rollout") else cfg.get("test_env_num") if "test_eval" in stage else cfg.get("sel_env_num"))
        unique_ids = len({row["task_id"] for row in safe_rows})
        complete = expected is not None and len(safe_rows) == expected == unique_ids and len(scored) == expected
        stage_records.append({"stage": stage, "expected": expected, "n_recorded": len(safe_rows), "n_unique_ids": unique_ids,
            "n_scored": len(scored), "complete_scored_artifacts": complete,
            "n_assistant_observed": sum(row["assistant_observed"] for row in safe_rows),
            "n_conversation_files": sum(row["conversation_file"] for row in safe_rows),
            "n_raw_trace_files": sum(row["raw_trace_file"] for row in safe_rows),
            "failure_counts": dict(Counter(row["failure_type"] for row in safe_rows)),
            "completed_task_hard": sum(row["hard"] for row in scored) / len(scored) if scored else None,
            "completed_task_soft": sum(row["soft"] for row in scored) / len(scored) if scored else None,
            "stage_score_for_comparison": complete and not invalid and finished})

    # Include a started request whose result row was not flushed before abort;
    # count the same per-prediction trace only once, even with duplicate rows.
    for predictions in sorted(run.rglob("predictions")):
        if not predictions.is_dir():
            continue
        for task in sorted(predictions.iterdir()):
            if not task.is_dir():
                continue
            canonical = next((task / name for name in ("codex_raw.txt", "infra_raw_trace.txt", "raw_trace.txt") if (task / name).is_file()), None)
            if canonical and canonical not in canonical_target_paths:
                canonical_target_paths.add(canonical)
                usage = usage_from_raw(canonical)
                usage.update(stage=predictions.parent.relative_to(run).as_posix(), task_id=task_id(task.name),
                             source=canonical.relative_to(run).as_posix(), missing_result_row=True)
                target_usage.append(usage)

    steps, digest_counts, step_records = [], [], []
    for path in sorted((run / "steps").glob("**/step_record.json")):
        record = read_json(path, issues, {})
        step_records.append((path.parent, record))
        gate = mapping(record.get("cam_gate"))
        confidence = mapping(record.get("cam_failure_confidence"))
        steps.append({"step": number(record.get("step")), "patches": number(record.get("n_patches")),
            "action": label(record.get("action"), {"accept", "accept_new_best", "reject", "cam_re_evaluate", "skip_no_patches", "force_accept", "skip_no_change", "reject_no_change"}),
            "candidate_selection_hard": score(record.get("selection_hard")),
            "candidate_accepted_by_recorded_action": True if record.get("action") in {"accept", "accept_new_best", "force_accept"}
                else False if record.get("action") in {"reject", "cam_re_evaluate", "skip_no_patches", "skip_no_change", "reject_no_change"} else None,
            "cam_gate_used": record.get("cam_gate_used") is True,
            "gate": {key: number(gate.get(key)) for key in ("mean_improvement", "lower_confidence_bound", "upper_confidence_bound", "n_pairs", "bootstrap_samples")},
            "gate_action": label(gate.get("action"), {"accept", "reject", "re_evaluate", "fallback"}),
            "adaptive_budget_observed": record.get("lr_control_mode") == "cam_adaptive"
                and number(confidence.get("confidence")) is not None and nonnegative(record.get("edit_budget")) is not None,
            "edit_budget": number(record.get("edit_budget")),
            "failure_confidence": {key: number(confidence.get(key)) for key in ("confidence", "entropy", "normalized_entropy", "total_failures")},
            "n_edits_merged": number(record.get("n_edits_merged")), "n_edits_ranked": number(record.get("n_edits_ranked")),
            "wall_seconds": number(record.get("wall_time_s"))})
        digest = read_json(path.parent / "trajectory_digest.json", issues, {})
        if "cam_memory_items_added" in digest:
            digest_counts.append({"step": number(record.get("step")), "items_added": number(digest["cam_memory_items_added"])})
    groups = []
    for path in sorted(run.glob("**/patches/minibatch_*.json")):
        data = read_json(path, issues)
        payload = mapping(mapping(data).get("patch")).get("edits", [])
        meta = read_json(path.with_suffix("") / "request_meta.json", issues, {})
        groups.append({"source": path.relative_to(run).as_posix(), "payload_edits": len(payload) if isinstance(payload, list) else 0,
            "request_meta_present": bool(meta), "optimizer_called": meta.get("optimizer_called") is True,
            "status": label(meta.get("status"), {"completed", "infra_error", "optimizer_error", "running"}, "historical_uninstrumented")})
    # A failed optimizer may have request_meta but no saved patch file.
    known_groups = {item["source"] for item in groups}
    for path in sorted(run.glob("**/patches/minibatch_*/request_meta.json")):
        group_source = path.parent.with_suffix(".json").relative_to(run).as_posix()
        if group_source not in known_groups:
            meta = read_json(path, issues, {})
            groups.append({"source": group_source, "payload_edits": 0, "request_meta_present": True,
                "optimizer_called": meta.get("optimizer_called") is True,
                "status": label(meta.get("status"), {"completed", "infra_error", "optimizer_error", "running"})})
    optimizer_usage, request_stages, completed_request_stages = [], Counter(), Counter()
    incomplete_optimizer_requests = 0
    for path in sorted((run / "model_calls").glob("*/conversation.json")):
        request = read_json(path, issues, {})
        stage = label(request.get("stage"), {"analyst", "merge", "ranking", "rewrite", "meta_skill", "slow_update", "lr_autonomous", "appendix_consolidate"})
        request_stages[stage] += 1
        canonical = next((path.parent / name for name in ("raw_trace.txt", "infra_raw_trace.txt") if (path.parent / name).is_file()), None)
        usage = usage_from_raw(canonical)
        request_completed = (request.get("status") == "ok" and assistant_observed(request.get("messages"))
                             and usage["completed_turn_events"] > 0 and not usage["error_event_types"]
                             and usage["transport_verified"])
        if request_completed:
            completed_request_stages[stage] += 1
        else:
            incomplete_optimizer_requests += 1
        usage["completed_request_verified"] = request_completed
        usage.update(stage=stage, source=canonical.relative_to(run).as_posix() if canonical else None)
        optimizer_usage.append(usage)
    tracker = summary.get("token_summary") or {}
    safe_tracker = {stage: {key: number(values.get(key)) for key in ("calls", "prompt_tokens", "completion_tokens", "total_tokens")}
                    for stage, values in tracker.items() if stage in {"_total", "rollout", "analyst", "merge", "ranking", "rewrite", "meta_skill", "slow_update", "lr_autonomous", "appendix_consolidate"} and isinstance(values, dict)}
    memory = read_json(run / "cam_rejected_memory.json", issues, {})
    memory_items = memory.get("items", []) if isinstance(memory, dict) else []
    memory_items = memory_items if isinstance(memory_items, list) else []
    retrieve_wired = bool(evidence["rejected_memory_retrieve_call"]["lines"])
    transitions = []
    previous_budget = number(cfg.get("edit_budget"))
    for step in steps:
        if step["adaptive_budget_observed"]:
            chosen = step["edit_budget"]
            transitions.append({"step": step["step"], "previous_or_configured_budget": previous_budget, "chosen_budget": chosen,
                "changed": chosen != previous_budget if previous_budget is not None else None,
                "baseline_kind": "previous_observed" if transitions else "configured_initial_not_previous_observation"})
            previous_budget = chosen
    stage_map = {stage["stage"]: stage for stage in stage_records}
    required = {name: bool(stage_map.get(name, {}).get("complete_scored_artifacts")) for name in ("selection_eval_baseline", "test_eval_baseline", "test_eval")}
    evidence_complete = bool(public_tasks) and all(row["conversation_file"] and row["assistant_observed"] and row["raw_trace_nonempty"] for row in public_tasks)
    infra_count = sum(row["status"] == "infra_error" for row in public_tasks)
    raw_infra_count = sum(sum(record["error_event_types"].values()) for record in target_usage + optimizer_usage)
    transport_records = target_usage + optimizer_usage
    invalid = invalid or infra_count > 0 or raw_infra_count > 0 or any(record["transport_failed"] for record in transport_records)
    actual_analyst = request_stages.get("analyst", 0)
    completed_analyst = completed_request_stages.get("analyst", 0)
    payload_groups = sum(group["payload_edits"] > 0 for group in groups)
    attempted_groups = sum(group["optimizer_called"] for group in groups)
    completed_payload_groups = sum(group["payload_edits"] > 0 and group["optimizer_called"] and group["status"] == "completed" for group in groups)
    source_matches = (evidence.get("current_source_sha256") == manifest.get("source_sha256")) if re.fullmatch(r"[0-9a-f]{64}", str(manifest.get("source_sha256", ""))) else None
    provenance = evaluation_provenance(run, cfg, summary, stage_map, step_records, issues)
    expected_config = {"train_size": 4, "batch_size": 4, "sel_env_num": 4, "test_env_num": 4,
        "limit": 4, "num_epochs": 1, "steps_per_epoch": 1, "accumulation": 1, "workers": 1,
        "analyst_workers": 1, "seed": 42, "split_seed": 42, "exec_timeout": 420}
    train_stage_names = [path.relative_to(run).as_posix() + "/rollout" for path, _ in step_records]
    summary_agreement = all(score(summary.get(metric)) is not None and score(summary.get(metric)) == stage_map.get(stage, {}).get(field)
        for metric, stage, field in (("baseline_selection_hard", "selection_eval_baseline", "completed_task_hard"),
                                    ("baseline_test_hard", "test_eval_baseline", "completed_task_hard"),
                                    ("baseline_test_soft", "test_eval_baseline", "completed_task_soft"),
                                    ("test_hard", "test_eval", "completed_task_hard"),
                                    ("test_soft", "test_eval", "completed_task_soft")))
    core_checks = {
        "training_summary_present": finished,
        "recovery_summary_completed": recovery.get("status") in {"passed", "completed_pending_audit"},
        "no_infrastructure_errors": not invalid,
        "transport_recovery_evidence_consistent": bool(transport_records) and all(record["transport_verified"] for record in transport_records),
        "exact_four_sample_protocol": all(number(cfg.get(key)) == expected for key, expected in expected_config.items()),
        "matching_frozen_source": source_matches is True,
        "complete_single_training_step": len(steps) == 1 and steps[0]["step"] == 1 and number(summary.get("total_steps")) == 1
            and len(train_stage_names) == 1 and all(stage_map.get(name, {}).get("complete_scored_artifacts") for name in train_stage_names),
        "all_task_stages_complete": bool(stage_records) and all(stage["complete_scored_artifacts"] for stage in stage_records),
        "required_selection_test_stages_complete": all(required.values()),
        "candidate_selection_evidence_verified": bool(provenance["candidate_selection"]) and all(item["verified"] for item in provenance["candidate_selection"]),
        "final_selection_evidence_verified": provenance["final_selection"]["verified"],
        "final_test_evidence_verified": provenance["final_test"]["verified"],
        "summary_scores_match_artifacts": summary_agreement,
        "nonempty_assistant_transcripts_and_raw_files": evidence_complete,
        "no_orphan_target_request_traces": all(not record.get("missing_result_row") for record in target_usage),
        "completed_optimizer_request": completed_analyst > 0 and incomplete_optimizer_requests == 0,
        "patch_from_completed_reflection": completed_payload_groups > 0,
        "no_parse_issues": not issues,
    }
    core_pass = all(core_checks.values())
    for row in stage_records:
        row["stage_score_for_comparison"] = bool(row["complete_scored_artifacts"] and not invalid and core_pass)
    return {
        "audit_schema": 4, "captured_at": datetime.now(timezone.utc).isoformat(), "run_name": run.name,
        "observation_status": "invalid_infrastructure" if invalid else "completed_core_evidence_verified" if core_pass else "completed_pending_validation" if finished else "partial_snapshot",
        "study_scope": "exploratory_four_sample_P0_not_formal_ablation", "paper_formal_eligible": False,
        "configuration": {key: number(cfg.get(key)) for key in CONFIG_NUMERIC},
        "manifest_source_sha256": manifest.get("source_sha256") if re.fullmatch(r"[0-9a-f]{64}", str(manifest.get("source_sha256", ""))) else None,
        "current_source_matches_manifest": source_matches,
        "summary_metrics": {key: number(summary.get(key)) if not invalid or key == "total_wall_time_s" else None for key in SUMMARY_METRICS},
        "skill_origins_as_recorded": {key: origin_label(summary.get(key)) for key in ("best_origin", "current_origin")},
        "paired_baseline_best_test_diagnostic": paired_test_diagnostic(public_tasks, stage_records, core_pass),
        "task_stage_counts": {"completed_task_attempts": sum(row["status"] == "completed" for row in public_tasks), "n_scored": sum(row["scored"] for row in public_tasks), "infra_error_attempts": infra_count,
            "failure_counts": dict(Counter(row["failure_type"] for row in public_tasks)), "note": "Repeated tasks across stages are separate attempts, not independent test samples."},
        "stages": stage_records, "per_task_metrics": public_tasks,
        "reflection": {"groups_with_patch_artifact_or_request": len(groups), "groups_with_observed_optimizer_call": attempted_groups,
            "groups_with_payload": payload_groups, "payload_edits": sum(group["payload_edits"] for group in groups),
            "groups_with_payload_and_completed_request": completed_payload_groups,
            "patch_yield_per_attempted_group": payload_groups / attempted_groups if attempted_groups and attempted_groups == len(groups) else None,
            "actual_analyst_request_artifacts": actual_analyst, "completed_analyst_request_artifacts": completed_analyst, "groups": groups},
        "mechanisms": {"steps": steps, "cam_gate_used_count": sum(step["cam_gate_used"] for step in steps),
            "adaptive_budget_computations": sum(step["adaptive_budget_observed"] for step in steps), "budget_transitions": transitions,
            "rejected_memory": {"file_present": (run / "cam_rejected_memory.json").exists(), "stored_items": len(memory_items),
                "observed_write_events": sum((record["items_added"] or 0) > 0 for record in digest_counts),
                "reported_items_added": sum(record["items_added"] or 0 for record in digest_counts), "write_records": digest_counts,
                "retrieval_wiring": "trainer_call_present" if retrieve_wired else "no_trainer_retrieve_call",
                "retrieval_calls": None, "retrieval_hits": None,
                "note": "Missing retrieval instrumentation is null, not zero measured hits. Step-buffer context is distinct from persistent rejected-memory retrieval. Initialization is not activation."}},
        "cost": {"target_canonical_raw_usage": sum_usage(target_usage), "optimizer_canonical_raw_usage": sum_usage(optimizer_usage),
            "combined_canonical_raw_usage": sum_usage(transport_records),
            "wall_time": {"wrapper_wall_seconds": nonnegative(recovery.get("wall_seconds")),
                "trainer_wall_seconds": nonnegative(summary.get("total_wall_time_s")),
                "note": "Different scopes: wrapper spans the probe; trainer timer starts after initial selection-baseline evaluation and includes later evaluations. Do not sum these nested timings or call their difference pure model time. Verify source references and manifest before attributing this scope."},
            "target_request_trace_files": len(target_usage), "optimizer_request_artifact_counts": dict(request_stages),
            "completed_optimizer_request_counts": dict(completed_request_stages), "incomplete_optimizer_requests": incomplete_optimizer_requests,
            "target_usage_by_attempt": target_usage, "optimizer_usage_by_request": optimizer_usage, "tracker_summary_as_recorded": safe_tracker,
            "monetary_cost": None, "note": "No price assumed. Prefer canonical raw events: target tracker records zero tokens; Codex and Claude expose the same common tracker and the trainer sums both, potentially doubling optimizer counters. No blanket correction factor applied. Cached input is a subset, never added twice. Raw event counts are observed turns, not billed-request counts."},
        "transport": {"terminal_infrastructure_events": raw_infra_count,
            "observed_reconnect_notice_count": sum(record["network_reconnect_notice_count"] for record in transport_records),
            "recovered_network_notice_count": sum(record["recovered_network_notice_count"] for record in transport_records),
            "recovered_network_affected_requests": sum(record["recovered_network_notice_count"] > 0 for record in transport_records),
            "unverified_transport_requests": sum(not record["transport_verified"] for record in transport_records),
            "note": "Only explicit Reconnecting network notices may recover, with completed turn, matching sidecar count <=2, max=2, recovered=true, status=completed and exit=0. Auth, turn.failed and terminal ERROR/FATAL always invalidate. Missing/inconsistent recovery evidence fails the core gate; recovered notices remain reported."},
        "completeness": {"required_selection_test_stages": required, "all_task_transcript_and_trace_files_present": evidence_complete,
            "core_probe_evidence_pass": core_pass, "core_checks": core_checks, "evaluation_provenance": provenance,
            "boot_process_continuity": "requires_external_start_end_snapshots",
            "final_selection_metric_present": number(summary.get("final_selection_hard")) is not None,
            "final_test_metric_present": number(summary.get("final_test_hard")) is not None,
            "conditional_reuse_note": "Reuse requires matching local skill fingerprints, matching scores and source task artifacts; final-test reuse additionally requires its persisted summary marker. Missing or unproven reuse fails the core gate. Reuse adds no independent samples.",
            "formal_prerequisite": "Persistent rejected-memory retrieval must be wired and observed before claiming CAM-Full/No-Memory formal activation; do not infer from config."},
        "source_evidence": evidence, "parse_issues": issues,
        "privacy": "Allowlisted aggregate/per-task metrics only; no task instructions, workbook contents, prompts, responses, credentials, raw trace text or memory content exported.",
    }


def markdown(data):
    lines = ["# P0 recovery audit", "", f"Run: {data['run_name']}", "", f"Status: {data['observation_status']}", "",
             "Scope: exploratory n=4; not a formal CAM ablation result.", "",
             "| Stage | Recorded / expected | Scored | Hard (completed tasks) | Soft (completed tasks) | Complete |",
             "|---|---:|---:|---:|---:|---|"]
    for row in data["stages"]:
        lines.append(f"| {row['stage']} | {row['n_recorded']} / {row['expected']} | {row['n_scored']} | {row['completed_task_hard']} | {row['completed_task_soft']} | {row['complete_scored_artifacts']} |")
    reflection, mechanism, cost = data["reflection"], data["mechanisms"], data["cost"]
    lines += ["", "Skill origins as recorded: " + json.dumps(data["skill_origins_as_recorded"]),
              "", "Step decisions (a scored candidate is not necessarily an accepted skill): " + json.dumps(mechanism["steps"]),
              "", "Paired baseline-versus-best test diagnostic (not generalizable): " + json.dumps(data["paired_baseline_best_test_diagnostic"]),
              "", f"Patch yield: {reflection['groups_with_payload']} payload groups / {reflection['groups_with_observed_optimizer_call']} observed called groups; actual analyst request artifacts={reflection['actual_analyst_request_artifacts']}.",
              "", f"CAM Gate used={mechanism['cam_gate_used_count']}; adaptive budget computations={mechanism['adaptive_budget_computations']}.",
              "", "Budget transitions: " + json.dumps(mechanism["budget_transitions"], ensure_ascii=False),
              "", "Persistent rejected memory: " + json.dumps(mechanism["rejected_memory"], ensure_ascii=False),
              "", "Target canonical raw usage: " + json.dumps(cost["target_canonical_raw_usage"]),
              "", "Optimizer canonical raw usage: " + json.dumps(cost["optimizer_canonical_raw_usage"]),
              "", "Combined canonical raw usage: " + json.dumps(cost["combined_canonical_raw_usage"]),
              "", "Wall-time scopes: " + json.dumps(cost["wall_time"]),
              "", "Transport recovery evidence: " + json.dumps(data["transport"], ensure_ascii=False),
              "", cost["note"], "", "Completeness: " + json.dumps(data["completeness"], ensure_ascii=False),
              "", "Source references (current source; compare manifest hash before attributing to the run):", ""]
    for name, entry in data["source_evidence"].items():
        if isinstance(entry, dict):
            lines.append(f"- {name}: `{entry['file']}` lines {entry['lines']}")
    lines += ["", "Partial observations must not be promoted to completed scores. Completed task means execution/scoring pipeline produced a numeric outcome, not that the task succeeded.", "", data["privacy"], ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[2] / "SkillOpt")
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--md-out", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    run = args.run.resolve()
    if not run.is_dir():
        parser.error("Run directory does not exist")
    data = audit(run, args.source_root.resolve())
    if args.reference:
        reference = audit(args.reference.resolve(), args.source_root.resolve())
        data["historical_reference"] = {key: reference[key] for key in ("run_name", "summary_metrics", "reflection", "mechanisms", "cost")}
        data["historical_reference_note"] = "Historical probe is descriptive, not a paired treatment comparison; protocol/transport/source may differ. Uninstrumented analyst call counts remain unknown."
    for target, body in ((args.json_out, json.dumps(data, ensure_ascii=False, indent=2) + "\n"), (args.md_out, markdown(data))):
        if target is None:
            continue
        target = target.resolve()
        if target == run or run in target.parents:
            parser.error("Exports must be outside the raw run directory")
        if target.exists() and not args.overwrite:
            parser.error("Export exists; use a new path or explicit --overwrite")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    if not args.json_out:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(json.dumps({"run_name": data["run_name"], "status": data["observation_status"], "n_scored": data["task_stage_counts"]["n_scored"],
            "patch_groups": data["reflection"]["groups_with_payload"], "core_probe_evidence_pass": data["completeness"]["core_probe_evidence_pass"]}))


if __name__ == "__main__":
    main()
