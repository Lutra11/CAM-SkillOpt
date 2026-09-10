"""Offline regressions for the non-JSON CLI reconnect format seen on 2026-09-10."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from skillopt.model import get_token_summary, reset_token_tracker
from skillopt.model.infra_errors import (
    InfraError, _error_event_text, _reconnect_notice_text, run_cli_failfast,
)
from skillopt.model.usage_accounting import UsageLedger


NOTICE = "ERROR: Reconnecting... waiting for network"


class StderrReconnectFormatTests(unittest.TestCase):
    def test_observed_stderr_format_is_recognized_without_losing_prefix(self):
        self.assertEqual(_error_event_text(NOTICE, "stderr"), NOTICE)
        self.assertEqual(_reconnect_notice_text(NOTICE, "stderr"), NOTICE)

    def test_case_and_leading_whitespace(self):
        line = "  error:   reconnecting... waiting for NETWORK  "
        self.assertTrue(_error_event_text(line, "stderr"))
        self.assertEqual(_reconnect_notice_text(line, "stderr"), line.strip())

    def test_original_bare_stderr_format_still_recognized(self):
        self.assertTrue(_reconnect_notice_text("Reconnecting... network", "stderr"))

    def test_non_json_stdout_is_not_an_error_channel(self):
        self.assertEqual(_error_event_text(NOTICE, "stdout"), "")
        self.assertEqual(_reconnect_notice_text(NOTICE, "stdout"), "")

    def test_json_error_notice_still_recognized(self):
        line = json.dumps({"type": "error", "message": "Reconnecting... network"})
        self.assertTrue(_reconnect_notice_text(line, "stdout"))

    def test_terminal_json_turn_cannot_be_a_notice(self):
        line = json.dumps({"type": "turn.failed", "message": NOTICE})
        self.assertTrue(_error_event_text(line, "stdout"))
        self.assertEqual(_reconnect_notice_text(line, "stdout"), "")

    def test_json_answer_quoting_notice_is_not_an_error(self):
        line = json.dumps({"type": "item.completed", "item": {
            "type": "agent_message", "text": NOTICE}})
        self.assertEqual(_error_event_text(line, "stdout"), "")
        self.assertEqual(_reconnect_notice_text(line, "stdout"), "")

    def test_normal_errors_fatal_and_background_logs_are_not_recovery(self):
        for line in (
            "ERROR: Connection failed: error sending request",
            "ERROR: waiting for network", "ERROR: not Reconnecting network",
            "ERROR: ReconnectingElsewhere network", "ERROR: Reconnecting configuration",
            "FATAL: Reconnecting... connection reset",
            "2026-09-10T02:15:00Z ERROR responses_retry: Reconnecting... network",
        ):
            with self.subTest(line=line):
                self.assertEqual(_reconnect_notice_text(line, "stderr"), "")


class StderrReconnectProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ledger = UsageLedger(self.root / "usage")
        self.environment = patch.dict(os.environ, {
            "SKILLOPT_USAGE_ROOT": str(self.root / "usage"),
            "SKILLOPT_INFRA_ARTIFACT_DIR": str(self.root / "models"),
        })
        self.environment.start()
        reset_token_tracker()

    def tearDown(self):
        reset_token_tracker()
        self.environment.stop()
        self.temp.cleanup()

    def request(self, lines, *, success=False, timeout=10, channel="stderr"):
        code = "import sys,time\nsys.stdin.read()\n"
        for line in lines:
            code += f"print({line!r}, file=sys.{channel}, flush=True)\n"
        if success:
            event = json.dumps({"type": "turn.completed", "usage": {
                "input_tokens": 11, "cached_input_tokens": 5, "output_tokens": 3}})
            code += f"print({event!r}, flush=True)\n"
        else:
            code += "time.sleep(30)\n"
        return run_cli_failfast([sys.executable, "-u", "-c", code],
            prompt="offline fixture", timeout=timeout, stage="target_1", model="offline",
            evidence_dir=self.root / "evidence")

    def warnings(self):
        return json.loads((self.root / "evidence" / "transport_warnings.json").read_text())

    def assert_one_record(self, status, recovered):
        records = self.ledger.records()
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["status"], status)
        self.assertEqual(record["recovered_warning_count"], recovered)
        self.assertEqual(get_token_summary()["_total"]["calls"], 1)
        return record

    def test_one_notice_recovers_counted_once_with_exact_usage(self):
        result = self.request([NOTICE], success=True)
        self.assertEqual(result.returncode, 0)
        warnings = self.warnings()
        self.assertEqual(warnings["notice_count"], 1)
        self.assertTrue(warnings["recovered"])
        self.assertEqual(warnings["notices"][0]["message"], NOTICE)
        record = self.assert_one_record("completed", 1)
        self.assertEqual(record["total_tokens"], 14)
        self.assertEqual(record["cached_input_tokens"], 5)
        self.assertIn(NOTICE, Path(record["raw_trace"]).read_text())
        self.assertFalse((self.root / "evidence" / "infra_error.json").exists())

    def test_two_notices_can_recover_with_one_call(self):
        self.request([NOTICE, NOTICE], success=True)
        self.assertEqual(self.warnings()["notice_count"], 2)
        self.assertTrue(self.warnings()["recovered"])
        self.assert_one_record("completed", 2)

    def test_third_notice_fast_stops_and_persists_unknown_usage(self):
        started = time.monotonic()
        with self.assertRaises(InfraError) as raised:
            self.request([NOTICE] * 3)
        self.assertLess(time.monotonic() - started, 8)
        self.assertEqual(raised.exception.failure_type, "network_error")
        self.assertEqual(raised.exception.details["transport_notice_count"], 3)
        self.assertEqual(self.warnings()["notice_count"], 3)
        self.assertFalse(self.warnings()["recovered"])
        record = self.assert_one_record("infra_error", 0)
        self.assertIsNone(record["total_tokens"])
        self.assertFalse(record["usage_complete"])
        self.assertEqual(Path(record["raw_trace"]).read_text().count(NOTICE), 3)
        self.assertNotIn("hard", raised.exception.to_dict())

    def test_original_deadline_still_applies_during_recovery(self):
        started = time.monotonic()
        with self.assertRaises(InfraError) as raised:
            self.request([NOTICE], timeout=0.5)
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(raised.exception.failure_type, "network_error")
        self.assertEqual(raised.exception.details["timeout_seconds"], 0.5)
        self.assertEqual(raised.exception.details["transport_notice_count"], 1)
        self.assert_one_record("infra_error", 0)

    def test_prefixed_401_is_immediately_fatal_not_recovered(self):
        started = time.monotonic()
        with self.assertRaises(InfraError) as raised:
            self.request([NOTICE + " HTTP 401 invalid_refresh_token"])
        self.assertLess(time.monotonic() - started, 8)
        self.assertEqual(raised.exception.failure_type, "auth_error")
        self.assertEqual(self.warnings()["notice_count"], 0)
        self.assert_one_record("infra_error", 0)

    def test_prefixed_unavailable_model_is_immediately_fatal(self):
        started = time.monotonic()
        with self.assertRaises(InfraError) as raised:
            self.request([NOTICE + " model_not_found"])
        self.assertLess(time.monotonic() - started, 8)
        self.assertEqual(raised.exception.failure_type, "model_unavailable")
        self.assertEqual(self.warnings()["notice_count"], 0)
        self.assert_one_record("infra_error", 0)

    def test_terminal_turn_failed_never_gets_recovery_allowance(self):
        event = json.dumps({"type": "turn.failed", "error": {
            "message": NOTICE + " connection reset"}})
        started = time.monotonic()
        with self.assertRaises(InfraError) as raised:
            self.request([event], channel="stdout")
        self.assertLess(time.monotonic() - started, 8)
        self.assertEqual(raised.exception.failure_type, "network_error")
        self.assertEqual(self.warnings()["notice_count"], 0)
        self.assert_one_record("infra_error", 0)

    def test_normal_terminal_error_is_not_swallowed(self):
        started = time.monotonic()
        with self.assertRaises(InfraError) as raised:
            self.request(["ERROR: Connection failed: error sending request"])
        self.assertLess(time.monotonic() - started, 8)
        self.assertEqual(raised.exception.failure_type, "network_error")
        self.assertEqual(self.warnings()["notice_count"], 0)
        self.assert_one_record("infra_error", 0)


if __name__ == "__main__":
    unittest.main()
