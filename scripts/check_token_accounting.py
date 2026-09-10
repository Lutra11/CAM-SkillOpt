"""Offline accounting replay of the historical n=4 P0, never a new experiment.

The historical run is read-only. Only a fresh output directory is written.
Public outputs are allowlisted metadata; private replay traces are gitignored.
This accounting acceptance does not validate or rehabilitate the old CAM Gate.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

NEW_TESTS = ("tests.test_token_accounting", "tests.test_token_accounting_cli",
             "tests.test_token_accounting_trainer")
REGRESSIONS = ("tests.test_cam_selection_guard", "tests.test_cam_gate_trainer",
               "tests.test_persistent_memory_context", "tests.test_persistent_memory_reflection",
               "tests.test_persistent_memory_trainer", "tests.test_cam_recovery",
               "tests.test_codex_infra_failfast", "tests.test_optimizer_infra_propagation",
               "tests.test_spreadsheetbench_infra_failfast", "tests.test_audit_recovery_run")
STAGES = {"selection_eval_baseline", "final_selection_eval", "test_eval_baseline", "test_eval",
          "steps/step_0001/rollout", "steps/step_0001/selection_eval"}
TOKEN_FIELDS = ("input_tokens", "output_tokens", "cached_input_tokens",
                "reasoning_output_tokens", "cache_write_input_tokens")
SUMMARY_FIELDS = ("calls", "cli_invocations", "legacy_calls", "unknown_calls", "running_calls",
                  "failed_calls", "input_tokens", "output_tokens", "total_tokens", "cached_input_tokens",
                  "reasoning_output_tokens", "cache_write_input_tokens", "known_tokens",
                  "recovered_warning_count", "usage_complete", "cache_usage_complete")
EXPECTED = {"calls": 27, "input_tokens": 374840, "output_tokens": 9540,
            "total_tokens": 384380, "cached_input_tokens": 86528}


class AcceptanceError(ValueError):
    """A controlled, non-sensitive acceptance failure."""


def require(condition, code):
    if not condition:
        raise AcceptanceError(code)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def hash_tree(root):
    output = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            require(root in path.resolve().parents, "historical_symlink_escapes_run")
            output[path.relative_to(root).as_posix()] = sha256(path.read_bytes())
    return output


def tree_digest(values):
    return sha256(json.dumps(values, sort_keys=True, separators=(",", ":")).encode())


def code_hashes(modules):
    names = [path.relative_to(ROOT).as_posix() for path in (ROOT / "skillopt").rglob("*.py")]
    names += ["scripts/check_token_accounting.py"]
    names += [module.replace(".", "/") + ".py" for module in modules]
    return {name: sha256((ROOT / name).read_bytes()) for name in sorted(set(names)) if (ROOT / name).is_file()}


def safe_error(exc):
    # Never export arbitrary exception messages: they can contain raw responses.
    return {"type": type(exc).__name__, "code": str(exc) if isinstance(exc, AcceptanceError) else "see_test_id_or_exception_type"}


def integer(value):
    require(isinstance(value, int) and not isinstance(value, bool) and value >= 0,
            "historical_usage_not_nonnegative_integer")
    return value


def independent_events(raw):
    """Independent event summation, deliberately not calling the new parser."""
    turns, threads, notices = [], [], 0
    for line in raw.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            require(not re.match(r"\s*(?:ERROR:|FATAL:)", line, re.I), "historical_terminal_error")
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "thread.started":
            threads.append(event.get("thread_id"))
        elif kind == "turn.completed":
            usage = event.get("usage")
            require(isinstance(usage, dict), "historical_missing_usage")
            # Support standard aliases independently of parse_codex_usage.
            values = {
                "input_tokens": usage.get("input_tokens", usage.get("prompt_tokens")),
                "output_tokens": usage.get("output_tokens", usage.get("completion_tokens")),
                "cached_input_tokens": usage.get("cached_input_tokens",
                    (usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}).get("cached_tokens")),
                "reasoning_output_tokens": usage.get("reasoning_output_tokens",
                    (usage.get("output_tokens_details") or usage.get("completion_tokens_details") or {}).get("reasoning_tokens")),
                "cache_write_input_tokens": usage.get("cache_write_input_tokens", usage.get("cache_creation_input_tokens")),
            }
            turns.append({key: integer(value) for key, value in values.items()})
        elif kind == "error":
            message = str(event.get("message", ""))
            require(bool(re.match(r"\s*Reconnecting\b", message, re.I))
                    and not re.search(r"\b401\b|invalid_refresh_token|unauthorized", message, re.I),
                    "historical_nonrecoverable_error")
            notices += 1
        elif kind == "turn.failed":
            raise AcceptanceError("historical_failed_turn")
    # The fixture is known to contain exactly one CLI turn per canonical file.
    # General multi-turn/retry behavior belongs to the new parser's unit tests.
    require(len(turns) == 1 and len(threads) == 1 and isinstance(threads[0], str),
            "historical_expected_one_thread_one_completed_turn")
    total = {key: sum(turn[key] for turn in turns) for key in TOKEN_FIELDS}
    require(total["cached_input_tokens"] <= total["input_tokens"], "historical_cache_exceeds_input")
    require(total["reasoning_output_tokens"] <= total["output_tokens"], "historical_reasoning_exceeds_output")
    total.update(total_tokens=total["input_tokens"] + total["output_tokens"], calls=1)
    return total, notices, sha256(threads[0].encode())


def canonical_requests(run):
    cfg = read_json(run / "config.json")
    require(cfg.get("target_model") == "gpt-5.6-terra", "unexpected_historical_target_model")
    requests, duplicates = [], []
    for path in sorted(run.glob("**/predictions/*/codex_raw.txt")):
        stage = path.parent.parent.parent.relative_to(run).as_posix()
        require(stage in STAGES, "unexpected_historical_target_stage")
        require(bool(re.fullmatch(r"[0-9]+(?:-[0-9]+)*", path.parent.name)), "unexpected_historical_task_id")
        requests.append({"path": path, "actor": "target", "role": "target", "stage": "target", "source_stage": stage,
                         "task_id": path.parent.name, "model": cfg["target_model"], "source_request_id": None})
        for name in ("raw.txt", "raw_trace.txt"):
            copy = path.with_name(name)
            require(copy.is_file() and copy.read_bytes() == path.read_bytes(), "historical_duplicate_copy_mismatch")
            duplicates.append({"path": copy.relative_to(run).as_posix(),
                               "canonical": path.relative_to(run).as_posix(), "sha256": sha256(path.read_bytes())})
    for path in sorted(run.glob("model_calls/*/raw_trace.txt")):
        request = read_json(path.with_name("conversation.json"))
        require(request.get("stage") in {"analyst", "merge"}, "unexpected_optimizer_stage")
        require(request.get("model_requested") == "gpt-5.6-sol", "unexpected_historical_optimizer_model")
        require(request.get("status") == "ok", "historical_optimizer_incomplete")
        require(bool(re.fullmatch(r"[0-9a-f]{32}", path.parent.name)), "unexpected_optimizer_local_request_id")
        requests.append({"path": path, "actor": "optimizer", "role": request["stage"], "stage": request["stage"],
                         "source_stage": request["stage"], "task_id": None,
                         "model": request["model_requested"], "source_request_id": path.parent.name})
    require(Counter(row["actor"] for row in requests) == {"target": 24, "optimizer": 3}, "historical_request_count_mismatch")
    require(Counter(row["source_stage"] for row in requests if row["role"] == "target") == {stage: 4 for stage in STAGES},
            "historical_stage_count_mismatch")
    require(Counter(row["stage"] for row in requests) == {"target": 24, "analyst": 2, "merge": 1}, "historical_role_count_mismatch")
    require(len(duplicates) == 48, "historical_duplicate_count_mismatch")
    selected = {row["path"] for row in requests}
    excluded = {run / row["path"] for row in duplicates}
    for path in run.rglob("*.txt"):
        if path.name in {"raw.txt", "codex_raw.txt", "raw_trace.txt", "infra_raw_trace.txt"}:
            if path not in selected and path not in excluded:
                require('"turn.completed"' not in path.read_text(encoding="utf-8", errors="replace"),
                        "unaccounted_historical_usage_trace")
    return requests, duplicates


def project_summary(summary):
    require(isinstance(summary, dict), "summary_not_mapping")
    return {stage: {key: row.get(key) for key in SUMMARY_FIELDS}
            for stage, row in summary.items() if stage in {"target", "analyst", "merge", "_total"} and isinstance(row, dict)}


def verify_summary(summary, expected):
    require(set(summary) == set(expected), "summary_stage_set_mismatch")
    for stage, values in expected.items():
        row = summary[stage]
        for key, value in values.items():
            require(row.get(key) == value, "summary_numeric_mismatch_" + key)
        require(row.get("usage_complete") is True and row.get("cache_usage_complete") is True,
                "summary_incomplete_usage")
        require(all(row.get(key) == 0 for key in ("legacy_calls", "unknown_calls", "running_calls", "failed_calls")),
                "summary_unexpected_legacy_running_failed_or_unknown")
        require(row.get("cli_invocations") == values["calls"], "summary_invocation_count_mismatch")


def replay(run, output, report):
    from skillopt.model.usage_accounting import UsageLedger, parse_codex_usage
    from skillopt.model.common import TokenTracker
    import skillopt.model as model
    import skillopt.model.common as common

    requests, duplicates = canonical_requests(run)
    report["excluded_duplicate_trace_copies"] = duplicates
    ledger = UsageLedger(output / "private_ledger")
    tracker = TokenTracker()
    expected, thread_ids, safe_records = {}, set(), []
    for item in requests:
        path = item["path"]
        raw = path.read_text(encoding="utf-8", errors="strict")
        independent, notices, thread_hash = independent_events(raw)
        require(thread_hash not in thread_ids, "historical_thread_id_not_unique")
        thread_ids.add(thread_hash)
        transport = read_json(path.with_name("transport_warnings.json"))
        require(transport.get("notice_count") == notices and transport.get("max_recovery_notices") == 2
                and transport.get("recovered") is (notices > 0) and transport.get("status") == "completed"
                and transport.get("returncode") == 0 and notices <= 2, "historical_transport_evidence_mismatch")
        parsed = parse_codex_usage(raw)
        for key in (*TOKEN_FIELDS, "total_tokens"):
            require(parsed.get(key) == independent[key], "parser_independent_mismatch_" + key)
        require(parsed.get("usage_complete") is True and parsed.get("cache_usage_complete") is True,
                "parser_usage_incomplete")
        relative = path.relative_to(run).as_posix()
        request_id = "replay_" + sha256((report["historical_inputs"]["before_sha256"] + "\0" + relative).encode())[:32]
        ledger_id = ledger.start(item["stage"], "codex_cli", item["model"], request_id=request_id)
        record = ledger.finish(ledger_id, raw, status="completed", recovered_warning_count=notices)
        for key in (*TOKEN_FIELDS, "total_tokens"):
            require(record.get(key) == independent[key], "ledger_independent_mismatch_" + key)
        tracker.record_request(record)
        for stage in (item["stage"], "_total"):
            row = expected.setdefault(stage, {key: 0 for key in (*TOKEN_FIELDS, "total_tokens", "calls", "recovered_warning_count")})
            for key, value in independent.items():
                row[key] += value
            row["recovered_warning_count"] += notices
        safe_records.append({"request_id": ledger_id, "source_request_id": item["source_request_id"],
            "source_thread_id_sha256": thread_hash, "actor": item["actor"], "role": item["role"], "stage": item["stage"],
            "source_stage": item["source_stage"], "task_id": item["task_id"], "model": item["model"],
            "backend": "codex_cli", "status": "completed", "failure_type": None,
            "raw_trace": relative, "source_trace": relative, "source_raw_sha256": sha256(path.read_bytes()),
            "replay_raw_text_sha256": record.get("raw_sha256"), "recovered_warning_count": notices,
            **independent, "usage_complete": record.get("usage_complete"),
            "cache_usage_complete": record.get("cache_usage_complete")})
    require(all(expected["_total"][key] == value for key, value in EXPECTED.items()), "historical_expected_total_mismatch")
    require(len(ledger.records()) == 27, "ledger_record_count_mismatch")
    ledger_summary, tracker_summary = ledger.summary(), tracker.summary()
    verify_summary(ledger_summary, expected)
    verify_summary(tracker_summary, expected)
    # Exercise the real aggregation implementation with the production shared
    # Codex/Claude tracker alias AND the same records loaded from the env ledger.
    # Empty other trackers are fresh objects: no zero-token record() calls.
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {"SKILLOPT_USAGE_ROOT": str(ledger.root)}))
        stack.enter_context(patch.object(common, "tracker", tracker))
        for backend in (model._codex, model._claude):
            stack.enter_context(patch.object(backend, "tracker", tracker))
        for backend in (model._openai, model._qwen, model._minimax):
            stack.enter_context(patch.object(backend, "tracker", type(backend.tracker)()))
        require(model._codex.tracker is model._claude.tracker is common.tracker, "shared_tracker_alias_not_exercised")
        model_summary = model.get_token_summary()
        verify_summary(model_summary, expected)
    report["replay"] = {"status": "passed", "canonical_requests": len(requests), "excluded_duplicates": len(duplicates),
        "target_calls": 24, "optimizer_calls": 3, "distinct_thread_ids": len(thread_ids),
        "independent_event_totals": expected, "ledger_summary": project_summary(ledger_summary),
        "tracker_summary": project_summary(tracker_summary), "model_summary": project_summary(model_summary),
        "shared_tracker_and_env_ledger_deduplicated": True,
        "count_unit": "historical_CLI_invocations_with_completed_turn_not_provider_billing_requests",
        "source_request_identity": "target=run/stage/task; optimizer=local_UUID_directory; thread_id is correlation only",
        "private_raw_location": "private_ledger/ (local only, excluded by .gitignore)",
        "cached_and_reasoning_tokens_are_subsets_not_extra_total_tokens": True}
    with (output / "requests.jsonl").open("w", encoding="utf-8") as handle:
        for record in safe_records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


class MetadataTestResult(unittest.TestResult):
    """Discard arbitrary test stdout/tracebacks; retain only safe diagnostics."""
    def __init__(self):
        super().__init__()
        self.lines = []

    def _line(self, test, status, error=None):
        identity = test.id()
        if not re.fullmatch(r"[A-Za-z0-9_.]+", identity):
            identity = "redacted_nonstandard_test_id"
        self.lines.append(f"{status} {identity}" + (f" exception={error[0].__name__}" if error else ""))

    def addSuccess(self, test):
        super().addSuccess(test)
        self._line(test, "PASS")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._line(test, "FAIL", err)

    def addError(self, test, err):
        super().addError(test, err)
        self._line(test, "ERROR", err)

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._line(test, "SKIP")

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err:
            self._line(test, "SUBTEST_FAIL", err)


def run_tests(modules, output):
    started = time.perf_counter()
    result = MetadataTestResult()
    # The listed CLI tests intentionally run tiny Python fake-event children;
    # they are not model executables. Do not break them by banning all Popen.
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), patch(
            "socket.socket.connect", side_effect=AssertionError("Offline acceptance forbids network connections")):
        suite = unittest.TestLoader().loadTestsFromNames(modules)
        suite.run(result)
    (output / "test_output.txt").write_text("\n".join(result.lines) + "\n", encoding="utf-8")
    return {"modules": list(modules), "tests_run": result.testsRun, "failures": len(result.failures),
            "errors": len(result.errors), "skipped": len(result.skipped),
            "status": "passed" if result.wasSuccessful() and not result.skipped else "failed",
            "wall_time_s": round(time.perf_counter() - started, 3),
            "output_policy": "test_ID_status_exception_class_only_no_test_stdout_or_tracebacks"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True, help="Read-only historical p0_newcli_retry_01 directory")
    parser.add_argument("--out", type=Path, required=True, help="New output directory; existing paths are refused")
    parser.add_argument("--include-regressions", action="store_true", help="Also run the previous 165-test module set; requires mirror audit tests")
    args = parser.parse_args()
    run, output = args.run.resolve(), args.out.resolve()
    if not run.is_dir():
        parser.error("Historical run directory does not exist")
    if output == run or run in output.parents:
        parser.error("Output must be outside the historical run")
    if output.exists():
        parser.error("Output already exists; select a fresh directory")
    output.mkdir(parents=True, exist_ok=False)
    (output / ".gitignore").write_text("/private_ledger/\n", encoding="utf-8")
    modules = NEW_TESTS + (REGRESSIONS if args.include_regressions else ())
    started, before, sources = time.perf_counter(), None, None
    report = {"schema_version": 1, "stage": "token_accounting_offline_acceptance", "status": "failed",
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "external_model_calls": 0,
        "evidence_type": "historical_trace_accounting_replay_not_new_P0_or_benchmark_training",
        "historical_validity_note": "No historical Gate, Memory, or scientific validity claim is changed by this replay.",
        "privacy": "Public files contain only allowlisted metadata; no prompts, workbooks, model responses, credentials or raw trace text.",
        "errors": []}
    try:
        before = hash_tree(run)
        report["historical_inputs"] = {"file_count_before": len(before), "before_sha256": tree_digest(before)}
        manifest = read_json(run / "manifest.json")
        historical_source = manifest.get("source_sha256")
        require(isinstance(historical_source, str) and bool(re.fullmatch(r"[0-9a-f]{64}", historical_source)),
                "historical_manifest_source_hash_missing")
        report["historical_recorded_source_sha256"] = historical_source
        report["historical_source_note"] = "Original manifest identity, not the new replay implementation's hash; not relabeled as current code."
        sources = code_hashes(modules)
        report["replay_code"] = {"before_sha256": tree_digest(sources), "sha256_by_file": sources}
        try:
            report["tests"] = run_tests(modules, output)
        except Exception as exc:
            report["errors"].append({"phase": "tests", **safe_error(exc)})
        try:
            with patch("socket.socket.connect", side_effect=AssertionError("Offline replay forbids network connections")):
                replay(run, output, report)
        except Exception as exc:
            report["errors"].append({"phase": "replay", **safe_error(exc)})
    except Exception as exc:
        report["errors"].append({"phase": "setup", **safe_error(exc)})
    finally:
        if before is not None:
            try:
                after = hash_tree(run)
                report["historical_inputs"].update(file_count_after=len(after), after_sha256=tree_digest(after),
                    unchanged=after == before, changed_file_count=sum(before.get(key) != after.get(key) for key in set(before) | set(after)))
            except Exception as exc:
                report["errors"].append({"phase": "historical_postcheck", **safe_error(exc)})
        if sources is not None:
            try:
                current_sources = code_hashes(modules)
                report["replay_code"].update(after_sha256=tree_digest(current_sources), unchanged=current_sources == sources)
            except Exception as exc:
                report["errors"].append({"phase": "replay_code_postcheck", **safe_error(exc)})
        for name in ("requests.jsonl", "test_output.txt"):
            if not (output / name).exists():
                (output / name).write_text("", encoding="utf-8")
        passed = (not report["errors"] and report.get("tests", {}).get("status") == "passed"
                  and report.get("replay", {}).get("status") == "passed"
                  and report.get("historical_inputs", {}).get("unchanged") is True
                  and report.get("replay_code", {}).get("unchanged") is True)
        report.update(status="passed" if passed else "failed", wall_time_s=round(time.perf_counter() - started, 3))
        (output / "acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "tests_run": report.get("tests", {}).get("tests_run"),
        "canonical_requests": report.get("replay", {}).get("canonical_requests"),
        "historical_inputs_unchanged": report.get("historical_inputs", {}).get("unchanged"),
        "errors": report["errors"]}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
