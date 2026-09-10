"""Offline request-boundary checks using tiny Python children, never models."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from skillopt.model import codex_backend, codex_harness, get_token_summary, reset_token_tracker
from skillopt.model.infra_errors import InfraError, run_cli_failfast
from skillopt.model.usage_accounting import UsageLedger


def events(text="OK", input_tokens=11, output_tokens=3):
    return "\n".join(json.dumps(event) for event in [
        {"type": "thread.started", "thread_id": "offline-thread"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": text}},
        {"type": "turn.completed", "usage": {"input_tokens": input_tokens,
            "cached_input_tokens": 5, "output_tokens": output_tokens}},
    ])


class CLIAccountingTests(unittest.TestCase):
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

    def request(self, raw, stage="target", timeout=5, suffix=""):
        code = f"import sys; sys.stdin.read(); print({raw!r},flush=True)\n" + suffix
        return run_cli_failfast([sys.executable, "-u", "-c", code], prompt="offline",
            timeout=timeout, stage=stage, model="offline", evidence_dir=self.root / "evidence")

    def test_success_is_once_with_real_tokens_and_raw_link(self):
        result = self.request(events())
        records = self.ledger.records()
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["request_id"], result.usage_record["request_id"])
        self.assertEqual(record["total_tokens"], 14)
        self.assertEqual(record["cached_input_tokens"], 5)
        self.assertTrue(Path(record["raw_trace"]).exists())
        self.assertEqual(get_token_summary()["_total"]["calls"], 1)
        self.assertEqual(get_token_summary()["_total"]["total_tokens"], 14)

    def test_missing_usage_stays_unknown(self):
        self.request(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "OK"}}))
        record = self.ledger.records()[0]
        self.assertIsNone(record["input_tokens"])
        self.assertIsNone(record["total_tokens"])
        self.assertFalse(record["usage_complete"])
        self.assertIsNone(get_token_summary()["_total"]["total_tokens"])

    def test_401_has_one_unknown_failure_and_sanitized_evidence(self):
        with self.assertRaises(InfraError) as raised:
            self.request(json.dumps({"type": "error", "message": "401 invalid_refresh_token refresh_token=secret123"}),
                         suffix="import time; time.sleep(30)")
        records = self.ledger.records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["status"], "infra_error")
        self.assertEqual(records[0]["failure_type"], "auth_error")
        self.assertIsNone(records[0]["total_tokens"])
        self.assertEqual(raised.exception.details["request_id"], records[0]["request_id"])
        self.assertNotIn("secret123", Path(records[0]["raw_trace"]).read_text())

    def test_timeout_keeps_unknown_usage(self):
        with self.assertRaises(InfraError):
            self.request("", timeout=0.2, suffix="import time; time.sleep(30)")
        record = self.ledger.records()[0]
        self.assertEqual(record["failure_type"], "llm_timeout")
        self.assertIsNone(record["output_tokens"])

    def test_reconnect_warning_not_extra_call_or_infra_failure(self):
        notice = json.dumps({"type": "error", "message": "Reconnecting... network connection reset"})
        self.request(notice + "\n" + events())
        record = self.ledger.records()[0]
        self.assertEqual(record["recovered_warning_count"], 1)
        self.assertEqual(record["status"], "completed")
        self.assertEqual(get_token_summary()["_total"]["calls"], 1)

    def test_repeated_same_task_is_two_actual_invocations(self):
        self.request(events())
        self.request(events())
        rows = self.ledger.records()
        self.assertEqual(len({r["request_id"] for r in rows}), 2)
        self.assertEqual(get_token_summary()["_total"]["total_tokens"], 28)

    def test_invalid_config_does_not_create_request(self):
        with self.assertRaises(ValueError):
            self.request(events(), timeout=float("inf"))
        self.assertEqual(self.ledger.records(), [])

    def test_empty_provider_response_is_failed_but_usage_not_discarded(self):
        empty = json.dumps({"type": "turn.completed", "usage": {
            "input_tokens": 11, "cached_input_tokens": 5, "output_tokens": 0}})
        code = f"import sys; sys.stdin.read(); print({empty!r})"
        with self.assertRaises(InfraError):
            run_cli_failfast([sys.executable, "-u", "-c", code,
                "--output-last-message", str(self.root / "empty.txt")], prompt="test",
                timeout=5, stage="target", model="offline", evidence_dir=self.root / "evidence")
        row = self.ledger.records()[0]
        self.assertEqual(row["failure_type"], "provider_error")
        self.assertEqual(row["status"], "infra_error")
        self.assertEqual(row["total_tokens"], 11)

    def test_spawn_failure_is_unknown_infra_not_zero_success(self):
        with self.assertRaises(InfraError):
            run_cli_failfast([str(self.root / "missing.exe")], prompt="test", timeout=3,
                stage="analyst", model="offline", evidence_dir=self.root / "evidence")
        row = self.ledger.records()[0]
        self.assertEqual(row["status"], "infra_error")
        self.assertIsNone(row["total_tokens"])

    def test_optimizer_outer_parser_does_not_double_count(self):
        def fake_cli(*args, **kwargs):
            return self.request(events(), stage="analyst")
        with patch.object(codex_backend, "run_cli_failfast", side_effect=fake_cli):
            response, usage = codex_backend.chat_with_model("offline", "system", "user", retries=1, stage="analyst")
        self.assertEqual(response, "OK")
        self.assertEqual(usage["total_tokens"], 14)
        self.assertEqual(get_token_summary()["_total"]["calls"], 1)
        conversations = list((self.root / "models").glob("*/conversation.json"))
        self.assertEqual(json.loads(conversations[0].read_text())["request_id"], usage["request_id"])

    def test_optimizer_validation_retry_keeps_each_invocation_once(self):
        texts = iter(["malformed", json.dumps({"content": "OK", "tool_calls": []})])
        def fake_cli(*args, **kwargs):
            return self.request(events(next(texts)), stage="analyst")
        with patch.object(codex_backend, "run_cli_failfast", side_effect=fake_cli), patch.object(codex_backend.time, "sleep"):
            response, _ = codex_backend.chat_messages_with_model("offline", [{"role": "user", "content": "test"}],
                                                                retries=2, stage="analyst", return_message=True)
        self.assertEqual(response.content, "OK")
        self.assertEqual(get_token_summary()["_total"]["calls"], 2)
        self.assertEqual(get_token_summary()["_total"]["total_tokens"], 28)

    def test_target_harness_links_request_without_zero_or_double_counter(self):
        work = self.root / "prediction" / "workspace"
        work.mkdir(parents=True)
        def fake_cli(*args, **kwargs):
            return self.request(events())
        config = {"path": "offline", "sandbox": "read-only", "full_auto": False}
        with patch.object(codex_harness, "get_codex_exec_config", return_value=config), \
                patch.object(codex_harness, "run_cli_failfast", side_effect=fake_cli):
            response, raw = codex_harness._run_codex_cli_exec(work_dir=str(work), prompt="test", model="offline", timeout=5)
        self.assertEqual(response, "OK")
        self.assertIn("ACCOUNTING_REQUEST_ID:", raw)
        self.assertEqual(len(list(work.parent.glob("usage_*.json"))), 1)
        self.assertEqual(get_token_summary()["_total"]["calls"], 1)
        self.assertEqual(get_token_summary()["_total"]["total_tokens"], 14)


if __name__ == "__main__":
    unittest.main()
