"""Offline token accounting: synthetic CLI traces and local Python workers only."""
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import skillopt.model as model_api
from skillopt.model.common import TokenTracker
from skillopt.model.usage_accounting import (
    UsageAccountingError, UsageLedger, configure_usage_accounting,
    merge_request_records, parse_codex_usage, summarize_usage,
)


def terminal(input_tokens=100, output_tokens=20, cached=30, turn_id=None, **extra):
    usage = {"input_tokens": input_tokens, "output_tokens": output_tokens}
    if cached is not None:
        usage["cached_input_tokens"] = cached
    event = {"type": "turn.completed", "usage": usage, **extra}
    if turn_id is not None:
        event["turn_id"] = turn_id
    return json.dumps(event)


def request(request_id="one", raw=None, stage="target", **extra):
    usage = parse_codex_usage(terminal() if raw is None else raw)
    return {"request_id": request_id, "stage": stage, "role": stage,
            "backend": "codex_exec", "model": "offline-model", "status": "completed",
            "failure_type": None, "recovered_warning_count": 0, "usage": usage, **usage, **extra}


class ParseCodexUsageTests(unittest.TestCase):
    def test_cached_input_is_subset_not_added_to_total(self):
        value = parse_codex_usage(terminal())
        self.assertEqual(value["input_tokens"], 100)
        self.assertEqual(value["cached_input_tokens"], 30)
        self.assertEqual(value["output_tokens"], 20)
        self.assertEqual(value["total_tokens"], 120)
        self.assertEqual(value["prompt_tokens"], 100)
        self.assertEqual(value["completion_tokens"], 20)
        self.assertTrue(value["usage_complete"])

    def test_multiple_distinct_anonymous_turns_are_added_not_last_only(self):
        value = parse_codex_usage(terminal() + "\n" + terminal(10, 2, 3))
        self.assertEqual(value["total_tokens"], 132)
        self.assertEqual(value["distinct_terminal_events"], 2)

    def test_stable_duplicate_is_counted_once(self):
        raw = terminal(turn_id="turn-one")
        value = parse_codex_usage(raw + "\n" + raw)
        self.assertEqual(value["total_tokens"], 120)
        self.assertEqual(value["completed_turn_events"], 2)
        self.assertEqual(value["duplicate_terminal_events"], 1)

    def test_distinct_turn_ids_with_identical_usage_both_count(self):
        value = parse_codex_usage(terminal(turn_id="a") + "\n" + terminal(turn_id="b"))
        self.assertEqual(value["total_tokens"], 240)

    def test_same_turn_id_in_distinct_threads_both_count(self):
        raw = '\n'.join([
            json.dumps({"type": "thread.started", "thread_id": "a"}), terminal(turn_id="1"),
            json.dumps({"type": "thread.started", "thread_id": "b"}), terminal(turn_id="1"),
        ])
        self.assertEqual(parse_codex_usage(raw)["total_tokens"], 240)

    def test_anonymous_identical_events_are_ambiguous_not_assumed_duplicate(self):
        raw = terminal()
        value = parse_codex_usage(raw + "\n" + raw)
        self.assertFalse(value["usage_complete"])
        self.assertIsNone(value["total_tokens"])
        self.assertEqual(value["known_tokens"], 120)
        self.assertEqual(value["ambiguous_terminal_events"], 1)

    def test_conflicting_stable_id_is_unknown_without_unreliable_lower_bound(self):
        value = parse_codex_usage(terminal(turn_id="1") + "\n" + terminal(5, 1, 0, turn_id="1"))
        self.assertIsNone(value["total_tokens"])
        self.assertEqual(value["known_tokens"], 0)
        self.assertIn("conflicting_stable_terminal_id", value["accounting_warnings"])

    def test_missing_usage_is_unknown_never_zero(self):
        for raw in ("", "not json", '{"type":"turn.completed"}', '{"type":"turn.failed"}'):
            with self.subTest(raw=raw):
                value = parse_codex_usage(raw)
                self.assertIsNone(value["input_tokens"])
                self.assertIsNone(value["output_tokens"])
                self.assertIsNone(value["total_tokens"])
                self.assertFalse(value["usage_complete"])

    def test_incomplete_later_event_keeps_known_fields_but_null_total(self):
        raw = terminal() + '\n' + '{"type":"turn.completed","usage":{"input_tokens":7}}'
        value = parse_codex_usage(raw)
        self.assertEqual(value["input_tokens"], 107)
        self.assertIsNone(value["output_tokens"])
        self.assertIsNone(value["total_tokens"])
        self.assertEqual(value["known_tokens"], 127)

    def test_invalid_counts_are_unknown_including_bool_and_negative(self):
        for value in (None, True, -1, 1.5, "100"):
            with self.subTest(value=value):
                parsed = parse_codex_usage(terminal(input_tokens=value))
                self.assertIsNone(parsed["input_tokens"])
                self.assertIsNone(parsed["total_tokens"])
        self.assertEqual(parse_codex_usage(terminal(0, 0, 0))["total_tokens"], 0)

    def test_legacy_aliases_and_optional_subset_details(self):
        raw = json.dumps({"type": "turn.completed", "usage": {
            "prompt_tokens": 100, "completion_tokens": 20,
            "prompt_tokens_details": {"cached_tokens": 30},
            "completion_tokens_details": {"reasoning_tokens": 12},
            "cache_creation_input_tokens": 5, "total_tokens": 999,
        }})
        parsed = parse_codex_usage(raw)
        self.assertEqual(parsed["total_tokens"], 120)
        self.assertEqual(parsed["cached_input_tokens"], 30)
        self.assertEqual(parsed["reasoning_output_tokens"], 12)
        self.assertEqual(parsed["cache_write_input_tokens"], 5)

    def test_cache_absence_does_not_invalidate_known_input_and_output(self):
        parsed = parse_codex_usage(terminal(cached=None))
        self.assertTrue(parsed["usage_complete"])
        self.assertFalse(parsed["cache_usage_complete"])
        self.assertIsNone(parsed["cached_input_tokens"])

    def test_subset_exceeding_parent_is_unknown(self):
        parsed = parse_codex_usage(terminal(cached=101))
        self.assertIsNone(parsed["cached_input_tokens"])
        self.assertEqual(parsed["total_tokens"], 120)
        self.assertIn("cached_input_tokens_exceeds_parent", parsed["accounting_warnings"])

    def test_stderr_fake_terminal_is_ignored_with_attempt_channel_reset(self):
        raw = '\n'.join([
            'COMMAND_JSON: ["offline"]', 'CWD: ignored', '[stdout]', terminal(turn_id="a"),
            '[stderr]', terminal(9999, 9999, 0),
            '===== CODEX CLI ATTEMPT 2 =====', terminal(10, 2, 3, turn_id="b"),
        ])
        parsed = parse_codex_usage(raw)
        self.assertEqual(parsed["total_tokens"], 132)
        self.assertEqual(parsed["completed_turn_events"], 2)


class UsageLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "ledger"
        self.ledger = UsageLedger(self.root)

    def test_start_persists_running_placeholder_and_stable_raw_path(self):
        rid = self.ledger.start("analyst", "codex_cli", "offline")
        record = self.ledger.records()[0]
        self.assertEqual(record["request_id"], rid)
        self.assertEqual(record["role"], "analyst")
        self.assertEqual(record["status"], "running")
        self.assertEqual(Path(record["raw_trace"]), self.ledger.request_dir(rid) / "raw_trace.txt")
        self.assertTrue(Path(record["raw_trace"]).is_file())
        self.assertIsNone(self.ledger.summary()["_total"]["total_tokens"])
        self.assertEqual(self.ledger.summary()["_total"]["running_calls"], 1)

    def test_finish_trace_hash_matches_sanitized_persisted_bytes(self):
        rid = self.ledger.start("target", "codex_exec", "offline")
        raw = terminal() + "\r\n[stderr]\r\n401 refresh_token=synthetic-secret Bearer abcdefghijklmnop"
        record = self.ledger.finish(rid, raw, "infra_error", "auth_error")
        saved = Path(record["raw_trace"]).read_bytes()
        self.assertNotIn(b"synthetic-secret", saved)
        self.assertNotIn(b"abcdefghijklmnop", saved)
        self.assertEqual(record["raw_sha256"], hashlib.sha256(saved).hexdigest())
        self.assertEqual(record, self.ledger.records()[0])

    def test_repeated_identical_finish_is_idempotent_without_file_rewrite(self):
        rid = self.ledger.start("target", "codex_exec", "offline", request_id="fixed")
        first = self.ledger.finish(rid, terminal(), "completed", recovered_warning_count=1)
        path = self.ledger.request_dir(rid) / "request.json"
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        second = self.ledger.finish(rid, terminal(), "completed", recovered_warning_count=1)
        self.assertEqual(first, second)
        self.assertEqual(before, (path.read_bytes(), path.stat().st_mtime_ns))
        self.assertEqual(self.ledger.summary()["_total"]["calls"], 1)

    def test_conflicting_finish_is_rejected_and_original_evidence_preserved(self):
        rid = self.ledger.start("target", "codex_exec", "offline")
        first = self.ledger.finish(rid, terminal(), "completed")
        for kwargs in ({"raw": terminal(101)}, {"status": "failed"}, {"recovered_warning_count": 2}):
            with self.subTest(kwargs=kwargs):
                params = {"raw": terminal(), "status": "completed", **kwargs}
                with self.assertRaises(UsageAccountingError):
                    self.ledger.finish(rid, **params)
                self.assertEqual(self.ledger.records(), [first])

    def test_same_request_start_is_idempotent_but_identity_change_rejected(self):
        self.ledger.start("target", "codex_exec", "offline", request_id="fixed")
        self.assertEqual(self.ledger.start("target", "codex_exec", "offline", request_id="fixed"), "fixed")
        with self.assertRaises(UsageAccountingError):
            self.ledger.start("analyst", "codex_exec", "offline", request_id="fixed")

    def test_unknown_failure_stays_null_and_failure_category_is_counted(self):
        rid = self.ledger.start("target", "codex_exec", "offline")
        self.ledger.finish(rid, "401", "infra_error", "auth_error")
        entry = self.ledger.summary()["_total"]
        self.assertIsNone(entry["total_tokens"])
        self.assertEqual(entry["known_tokens"], 0)
        self.assertEqual(entry["unknown_calls"], 1)
        self.assertEqual(entry["infra_error_calls"], 1)
        self.assertEqual(entry["failure_types"], {"auth_error": 1})

    def test_new_instance_recovers_all_requests_and_subset_summary(self):
        ids = []
        for stage in ("target", "analyst", "merge"):
            rid = self.ledger.start(stage, "codex_cli", "offline")
            self.ledger.finish(rid, terminal(), "completed")
            ids.append(rid)
        restored = UsageLedger(self.root)
        self.assertEqual(restored.summary()["_total"]["total_tokens"], 360)
        selected = [record for record in restored.records() if record["request_id"] == ids[1]]
        self.assertEqual(summarize_usage(selected)["analyst"]["total_tokens"], 120)

    def test_cross_process_workers_have_distinct_durable_request_ids(self):
        code = (
            "import sys; from skillopt.model.usage_accounting import UsageLedger; "
            "l=UsageLedger(sys.argv[1]); r=l.start('target','codex_exec','offline'); "
            "l.finish(r,sys.argv[2],'completed'); print(r)"
        )
        processes = [subprocess.Popen([sys.executable, "-c", code, str(self.root), terminal()],
                     cwd=str(Path(__file__).resolve().parents[1]), stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE, text=True) for _ in range(3)]
        try:
            ids = []
            for process in processes:
                stdout, stderr = process.communicate(timeout=30)
                self.assertEqual(process.returncode, 0, stderr)
                ids.append(stdout.strip())
            self.assertEqual(len(set(ids)), 3)
            self.assertEqual({r["request_id"] for r in self.ledger.records()}, set(ids))
            self.assertEqual(self.ledger.summary()["_total"]["total_tokens"], 360)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.communicate()

    def test_invalid_request_id_and_unstarted_finish_are_rejected(self):
        for rid in ("../escape", "C:\\escape", "a/b", "", "a" * 129):
            with self.subTest(rid=rid), self.assertRaises(UsageAccountingError):
                self.ledger.request_dir(rid)
        with self.assertRaises(UsageAccountingError):
            self.ledger.finish("not-started", terminal(), "completed")

    def test_tampered_trace_or_usage_is_rejected_on_read(self):
        rid = self.ledger.start("target", "codex_exec", "offline")
        record = self.ledger.finish(rid, terminal(), "completed")
        raw_path = Path(record["raw_trace"])
        raw_path.write_text(terminal(101), encoding="utf-8")
        with self.assertRaisesRegex(UsageAccountingError, "hash mismatch"):
            self.ledger.records()
        raw_path.write_text(terminal(), encoding="utf-8")
        record["total_tokens"] = 999
        (self.ledger.request_dir(rid) / "request.json").write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaisesRegex(UsageAccountingError, "does not match raw"):
            self.ledger.records()

    def test_configure_none_disables_without_deleting_evidence(self):
        with patch.dict(os.environ, {}, clear=False):
            ledger = configure_usage_accounting(self.root)
            self.assertEqual(os.environ["SKILLOPT_USAGE_ROOT"], str(self.root.resolve()))
            ledger.start("target", "codex_exec", "offline")
            configure_usage_accounting(None)
            self.assertNotIn("SKILLOPT_USAGE_ROOT", os.environ)
            self.assertEqual(len(ledger.records()), 1)


class TrackerAndSummaryTests(unittest.TestCase):
    def test_duplicate_raw_copies_with_same_request_id_count_once(self):
        tracker = TokenTracker()
        row = request()
        for _ in range(3):
            tracker.record_request(deepcopy(row))
        self.assertEqual(tracker.summary()["_total"]["calls"], 1)
        self.assertEqual(tracker.summary()["_total"]["total_tokens"], 120)

    def test_distinct_actual_invocations_with_same_raw_count_separately(self):
        self.assertEqual(summarize_usage([request("a"), request("b")])["_total"]["total_tokens"], 240)

    def test_running_terminal_merge_is_order_independent(self):
        pending = request(raw="", status="running")
        complete = request()
        for rows in ([pending, complete], [complete, pending]):
            self.assertEqual(summarize_usage(rows)["_total"]["total_tokens"], 120)
            self.assertEqual(summarize_usage(rows)["_total"]["calls"], 1)

    def test_conflicting_request_ids_and_flat_nested_values_are_rejected(self):
        with self.assertRaises(UsageAccountingError):
            merge_request_records([request(), request(raw=terminal(101))])
        broken = request()
        broken["input_tokens"] = 999
        with self.assertRaises(UsageAccountingError):
            merge_request_records([broken])

    def test_unknown_totals_preserve_available_known_tokens(self):
        rows = [request(), request("missing", raw="", status="infra_error", failure_type="network_error")]
        total = summarize_usage(rows)["_total"]
        self.assertIsNone(total["total_tokens"])
        self.assertEqual(total["known_tokens"], 120)
        self.assertEqual(total["unknown_calls"], 1)
        self.assertEqual(total["successful_calls"], 1)
        self.assertEqual(total["failed_calls"], 1)
        self.assertEqual(total["failure_types"], {"network_error": 1})

    def test_legacy_counters_remain_compatible_but_request_coverage_partial(self):
        tracker = TokenTracker()
        tracker.record("analyst", 10, 3)
        tracker.record_request(request())
        summary = tracker.summary()
        self.assertEqual(summary["_total"]["calls"], 2)
        self.assertEqual(summary["_total"]["prompt_tokens"], 110)
        self.assertEqual(summary["_total"]["completion_tokens"], 23)
        self.assertEqual(summary["_total"]["legacy_calls"], 1)
        self.assertEqual(summary["_total"]["request_coverage"], "partial_legacy")
        self.assertTrue(summary["_total"]["usage_complete"])
        self.assertFalse(summary["_total"]["cache_usage_complete"])

    def test_records_are_defensive_copies_and_reset_removes_in_memory_only(self):
        tracker = TokenTracker()
        tracker.record_request(request())
        rows = tracker.records()
        rows[0]["usage"]["input_tokens"] = 999
        self.assertEqual(tracker.summary()["_total"]["total_tokens"], 120)
        tracker.reset()
        self.assertEqual(tracker.summary()["_total"]["calls"], 0)

    def test_api_shared_tracker_read_once_and_disk_memory_request_dedup(self):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            ledger = UsageLedger(temporary)
            rid = ledger.start("target", "codex_exec", "offline")
            row = ledger.finish(rid, terminal(), "completed")
            shared = TokenTracker()
            shared.record_request(row)
            shared.records = Mock(wraps=shared.records)
            shared.legacy_summary = Mock(wraps=shared.legacy_summary)
            shared.reset = Mock(wraps=shared.reset)
            azure = Mock()
            azure.summary.return_value = {"azure-stage": {"calls": 1, "prompt_tokens": 2, "completion_tokens": 1}}
            # An actual legacy object intentionally does not expose records().
            legacy = SimpleNamespace(summary=azure.summary, reset=azure.reset)
            stack.enter_context(patch.dict(os.environ, {"SKILLOPT_USAGE_ROOT": temporary}))
            for name in ("_codex", "_claude", "_qwen", "_minimax"):
                stack.enter_context(patch.object(model_api, name, SimpleNamespace(tracker=shared)))
            stack.enter_context(patch.object(model_api, "_openai", SimpleNamespace(tracker=legacy)))
            summary = model_api.get_token_summary()
            self.assertEqual(summary["_total"]["calls"], 2)
            self.assertEqual(summary["_total"]["total_tokens"], 123)
            shared.records.assert_called_once_with()
            shared.legacy_summary.assert_called_once_with()
            azure.summary.assert_called_once_with()
            model_api.reset_token_tracker()
            shared.reset.assert_called_once_with()
            azure.reset.assert_called_once_with()
            self.assertEqual(len(ledger.records()), 1)
            self.assertEqual(model_api.get_token_summary()["target"]["total_tokens"], 120)

    def test_active_ledger_excludes_other_roots_and_unpersisted_ram(self):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            shared = TokenTracker()
            ledgers = [UsageLedger(Path(temporary) / name) for name in ("a", "b")]
            for index, ledger in enumerate(ledgers):
                rid = ledger.start("target", "codex_exec", "offline")
                shared.record_request(ledger.finish(rid, terminal(100 + index), "completed"))
            shared.record_request(request("unpersisted"))
            for name in ("_openai", "_codex", "_claude", "_qwen", "_minimax"):
                stack.enter_context(patch.object(model_api, name, SimpleNamespace(tracker=shared)))
            stack.enter_context(patch.dict(os.environ, {}, clear=False))
            for index, ledger in enumerate(ledgers):
                configure_usage_accounting(ledger.root)
                total = model_api.get_token_summary()["_total"]
                self.assertEqual(total["calls"], 1)
                self.assertEqual(total["total_tokens"], 120 + index)
            configure_usage_accounting(None)
            self.assertEqual(model_api.get_token_summary()["_total"]["calls"], 3)
            self.assertEqual(model_api.get_token_summary()["_total"]["total_tokens"], 361)


if __name__ == "__main__":
    unittest.main()
