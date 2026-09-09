"""Offline infrastructure regression tests. No credentials or API calls used."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from skillopt.model import codex_backend, codex_harness
from skillopt.model.infra_errors import (
    InfraError,
    classify_infra_error,
    codex_transport_args,
    detect_infra_error,
    run_cli_failfast,
    sanitize_details,
)


class InfraClassificationTests(unittest.TestCase):
    def test_default_transport_leaves_cli_configuration_unchanged(self):
        with patch.dict(os.environ, {"SKILLOPT_CODEX_TRANSPORT": "default"}):
            self.assertEqual(codex_transport_args(), [])

    def test_http_transport_uses_fixed_official_authenticated_endpoint(self):
        with patch.dict(os.environ, {"SKILLOPT_CODEX_TRANSPORT": "http", "OPENAI_BASE_URL": "https://invalid.example"}):
            args = codex_transport_args()
        overrides = dict(value.split("=", 1) for value in args[1::2])
        self.assertEqual(args[::2], ["-c"] * len(overrides))
        self.assertEqual(overrides["model_providers.cam_openai_http.base_url"], '"https://chatgpt.com/backend-api/codex"')
        self.assertEqual(overrides["model_providers.cam_openai_http.requires_openai_auth"], "true")
        self.assertEqual(overrides["model_providers.cam_openai_http.supports_websockets"], "false")
        self.assertEqual(overrides["model_providers.cam_openai_http.wire_api"], '"responses"')
        self.assertEqual(overrides["model_providers.cam_openai_http.request_max_retries"], "0")
        self.assertEqual(overrides["model_providers.cam_openai_http.stream_max_retries"], "0")
        self.assertNotIn("model", overrides)
        self.assertNotIn("model_reasoning_effort", overrides)
        self.assertNotIn("approval_policy", overrides)
        self.assertNotIn("invalid.example", " ".join(args))

    def test_arbitrary_transport_is_rejected(self):
        with patch.dict(os.environ, {"SKILLOPT_CODEX_TRANSPORT": "https://invalid.example"}):
            with self.assertRaises(ValueError):
                codex_transport_args()

    def test_failure_categories(self):
        for text, expected in [
            ("HTTP 401 Unauthorized invalid_refresh_token", "auth_error"),
            ("refresh_token_reused: Please sign in again", "auth_error"),
            ("AuthenticationError: expired", "auth_error"),
            ("The model gpt-test is not supported", "model_unavailable"),
            ("model_not_found", "model_unavailable"),
            ("error sending request: connection reset", "network_error"),
            ("stream disconnected before completion", "network_error"),
            ("request timed out", "llm_timeout"),
            ("429 rate limit exceeded", "provider_error"),
        ]:
            with self.subTest(text=text):
                self.assertEqual(detect_infra_error(text).failure_type, expected)

    def test_known_exceptions_and_model_mistake(self):
        self.assertEqual(classify_infra_error(TimeoutError()).failure_type, "llm_timeout")
        self.assertIsNone(classify_infra_error(ValueError("bad spreadsheet code")))
        self.assertIsNone(detect_infra_error("loaded authentication information"))

    def test_redaction(self):
        result = sanitize_details({
            "access_token": "private-value",
            "trace": '401 invalid_refresh_token refresh_token="very-secret" Authorization: Bearer abc.def.xyz api_key=sk-test-secretvalue',
        })
        dumped = json.dumps(result)
        for secret in ("private-value", "very-secret", "abc.def.xyz", "sk-test-secretvalue"):
            self.assertNotIn(secret, dumped)
        self.assertIn("invalid_refresh_token", dumped)


class StreamingProcessTests(unittest.TestCase):
    def test_401_stops_first_request_without_waiting_for_reconnect(self):
        with tempfile.TemporaryDirectory() as folder:
            counter = Path(folder) / "request_count.txt"
            code = (
                "import json,pathlib,sys,time\n"
                "sys.stdin.read()\n"
                f"counter=pathlib.Path({str(counter)!r})\n"
                "counter.write_text('1')\n"
                "print(json.dumps({'type':'error','message':'401 invalid_refresh_token refresh_token=secret123'}),flush=True)\n"
                "time.sleep(30)\n"
                "counter.write_text('2')\n"
            )
            started = time.monotonic()
            with self.assertRaises(InfraError) as raised:
                run_cli_failfast(
                    [sys.executable, "-u", "-c", code], prompt="offline simulation",
                    timeout=40, stage="target", model="offline-model", evidence_dir=folder,
                )
            self.assertEqual(raised.exception.failure_type, "auth_error")
            self.assertLess(time.monotonic() - started, 8)
            self.assertEqual(counter.read_text(), "1")
            summary = json.loads((Path(folder) / "infra_error.json").read_text())
            self.assertEqual(summary["status"], "infra_error")
            self.assertEqual(summary["failure_type"], "auth_error")
            self.assertNotIn("hard", summary)
            self.assertNotIn("secret123", (Path(folder) / "infra_raw_trace.txt").read_text())

    def test_successful_answer_mentioning_401_is_not_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            event = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "401 means Unauthorized"}})
            code = f"import sys; sys.stdin.read(); print({event!r})"
            result = run_cli_failfast(
                [sys.executable, "-u", "-c", code], prompt="explain HTTP", timeout=5,
                stage="target", model="offline-model", evidence_dir=folder,
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("401", result.stdout)
            self.assertFalse((Path(folder) / "infra_error.json").exists())

    def test_timeout_is_typed_and_persisted(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(InfraError) as raised:
                run_cli_failfast(
                    [sys.executable, "-u", "-c", "import time; time.sleep(30)"],
                    prompt="", timeout=0.2, stage="analyst", model="offline", evidence_dir=folder,
                )
            self.assertEqual(raised.exception.failure_type, "llm_timeout")
            self.assertTrue((Path(folder) / "infra_error.json").exists())

    def test_unspecified_optimizer_timeout_uses_finite_configured_default(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {"SKILLOPT_CODEX_TIMEOUT_SECONDS": "0.1"}):
                with self.assertRaises(InfraError) as raised:
                    run_cli_failfast(
                        [sys.executable, "-u", "-c", "import time; time.sleep(30)"],
                        prompt="", timeout=None, stage="analyst", model="offline", evidence_dir=folder,
                    )
            self.assertEqual(raised.exception.failure_type, "llm_timeout")
            self.assertEqual(raised.exception.details["timeout_seconds"], 0.1)

    def test_invalid_infinite_timeout_rejected_before_launch(self):
        with patch.dict(os.environ, {"SKILLOPT_CODEX_TIMEOUT_SECONDS": "inf"}):
            with patch("skillopt.model.infra_errors.subprocess.Popen") as start:
                with self.assertRaises(ValueError):
                    run_cli_failfast(["offline"], prompt="", timeout=None, stage="analyst", model="offline")
            start.assert_not_called()

    def test_background_websocket_failure_can_fall_back_and_succeed(self):
        with tempfile.TemporaryDirectory() as folder:
            diagnostic = "2026-09-09T01:00:00Z ERROR codex_api::endpoint::responses_websocket: failed to connect (10054)"
            warning = "2026-09-09T01:00:00Z WARN codex_core::session_startup_prewarm: websocket prewarm failed"
            event = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "OK"}})
            code = f"import sys; sys.stdin.read(); print({diagnostic!r},file=sys.stderr,flush=True); print({warning!r},file=sys.stderr,flush=True); print({event!r},flush=True)"
            result = run_cli_failfast(
                [sys.executable, "-u", "-c", code], prompt="test", timeout=5,
                stage="target", model="offline", evidence_dir=folder,
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("prewarm", result.stderr)
            self.assertIn("OK", result.stdout)
            self.assertFalse((Path(folder) / "infra_error.json").exists())

    def test_real_network_turn_error_is_not_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            event = json.dumps({"type": "turn.failed", "error": {"message": "stream disconnected before completion"}})
            code = f"import sys,time; sys.stdin.read(); print({event!r},flush=True); time.sleep(30)"
            started = time.monotonic()
            with self.assertRaises(InfraError) as raised:
                run_cli_failfast(
                    [sys.executable, "-u", "-c", code], prompt="test", timeout=40,
                    stage="target", model="offline", evidence_dir=folder,
                )
            self.assertEqual(raised.exception.failure_type, "network_error")
            self.assertLess(time.monotonic() - started, 8)

    def test_unknown_nonzero_exit_is_provider_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(InfraError) as raised:
                run_cli_failfast(
                    [sys.executable, "-u", "-c", "import sys; sys.exit(2)"],
                    prompt="", timeout=5, stage="optimizer", model="offline", evidence_dir=folder,
                )
            self.assertEqual(raised.exception.failure_type, "provider_error")


class BackendPropagationTests(unittest.TestCase):
    def test_target_optimizer_share_http_flags_without_changing_model_reasoning(self):
        event = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "OK"}})
        completed = subprocess.CompletedProcess([], 0, event, "")
        with tempfile.TemporaryDirectory() as folder:
            work_dir = Path(folder) / "workspace"
            work_dir.mkdir()
            cfg = {"path": "offline-codex", "sandbox": "read-only", "full_auto": False, "reasoning_effort": "none"}
            with patch.dict(os.environ, {"SKILLOPT_CODEX_TRANSPORT": "http", "SKILLOPT_INFRA_ARTIFACT_DIR": folder}):
                expected = codex_transport_args()
                with patch.object(codex_backend, "CODEX_PROFILE", ""), patch.object(codex_backend, "REASONING_EFFORT", "medium"):
                    with patch.object(codex_backend, "run_cli_failfast", return_value=completed) as optimizer:
                        codex_backend._run_codex_exec(model="gpt-5.6-sol", prompt="test", attachments=[], output_schema=None, timeout=10)
                with patch.object(codex_harness, "get_codex_exec_config", return_value=cfg):
                    with patch.object(codex_harness, "run_cli_failfast", return_value=completed) as target:
                        codex_harness._run_codex_cli_exec(work_dir=str(work_dir), prompt="test", model="gpt-5.6-terra", timeout=10)
            optimizer_cmd = optimizer.call_args.args[0]
            target_cmd = target.call_args.args[0]
            # Codex has separate global/subcommand -c vectors. Mixing them can
            # discard global transport overrides when reasoning is supplied to
            # exec, so every override must live in the exec vector.
            self.assertEqual(optimizer_cmd[1], "exec")
            self.assertEqual(target_cmd[1], "exec")
            self.assertEqual(optimizer_cmd[2:2 + len(expected)], expected)
            self.assertEqual(target_cmd[2:2 + len(expected)], expected)
            self.assertNotIn("-c", optimizer_cmd[:optimizer_cmd.index("exec")])
            self.assertNotIn("-c", target_cmd[:target_cmd.index("exec")])
            self.assertEqual(optimizer_cmd[optimizer_cmd.index("--model") + 1], "gpt-5.6-sol")
            self.assertEqual(target_cmd[target_cmd.index("-m") + 1], "gpt-5.6-terra")
            self.assertIn('model_reasoning_effort="medium"', optimizer_cmd)
            self.assertIn('model_reasoning_effort="none"', target_cmd)

    def test_optimizer_does_not_retry_auth(self):
        failure = InfraError("auth_error", "401 invalid_refresh_token")
        with patch.object(codex_backend, "_run_codex_exec", side_effect=failure) as call:
            with patch.object(codex_backend.time, "sleep") as sleep:
                with self.assertRaises(InfraError):
                    codex_backend.chat_with_model("offline", "system", "user", retries=5, stage="analyst")
        self.assertEqual(call.call_count, 1)
        sleep.assert_not_called()

    def test_sdk_auth_does_not_fallback_or_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            work_dir = Path(folder) / "workspace"
            work_dir.mkdir()
            with patch.object(codex_harness, "get_codex_exec_config", return_value={"use_sdk": "auto", "empty_response_retries": 4}):
                with patch.object(codex_harness, "_run_codex_sdk_exec", side_effect=RuntimeError("401 invalid_refresh_token")) as sdk:
                    with patch.object(codex_harness, "_run_codex_cli_exec") as cli:
                        with self.assertRaises(InfraError) as raised:
                            codex_harness.run_codex_exec(work_dir=str(work_dir), prompt="test", model="offline", timeout=10)
            self.assertEqual(raised.exception.failure_type, "auth_error")
            self.assertEqual(sdk.call_count, 1)
            cli.assert_not_called()
            self.assertTrue((Path(folder) / "codex_raw.txt").exists())
            self.assertTrue((Path(folder) / "infra_error.json").exists())

    def test_empty_profile_and_success_artifacts(self):
        event = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "OK"}})
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {"SKILLOPT_INFRA_ARTIFACT_DIR": folder}):
                with patch.object(codex_backend, "CODEX_PROFILE", ""):
                    with patch.object(codex_backend, "run_cli_failfast", return_value=subprocess.CompletedProcess([], 0, event, "")) as run:
                        response, _usage = codex_backend._run_codex_exec(model="offline", prompt="test", attachments=[], output_schema=None, timeout=10)
            self.assertEqual(response, "OK")
            self.assertNotIn("--profile", run.call_args.args[0])
            files = list(Path(folder).glob("*/conversation.json"))
            self.assertEqual(len(files), 1)
            conversation = json.loads(files[0].read_text())
            self.assertEqual(conversation["status"], "ok")
            self.assertEqual(conversation["messages"][-1]["content"], "OK")
            self.assertTrue(files[0].with_name("raw_trace.txt").exists())

    def test_target_stale_last_message_cannot_survive_empty_new_response(self):
        with tempfile.TemporaryDirectory() as folder:
            work_dir = Path(folder) / "workspace"
            work_dir.mkdir()
            stale = work_dir / "codex_last_message.txt"
            stale.write_text("stale answer")
            config = {"path": "offline", "sandbox": "read-only", "full_auto": False}
            with patch.object(codex_harness, "get_codex_exec_config", return_value=config):
                with patch.object(codex_harness, "run_cli_failfast", return_value=subprocess.CompletedProcess([], 0, "", "")):
                    with self.assertRaises(InfraError) as raised:
                        codex_harness._run_codex_cli_exec(work_dir=str(work_dir), prompt="test", model="offline", timeout=5)
            self.assertEqual(raised.exception.failure_type, "provider_error")
            self.assertFalse(stale.exists())

    def test_json_trace_keeps_agent_and_execution_steps(self):
        events = [
            {"type": "item.completed", "item": {"type": "command_execution", "command": "python task.py", "exit_code": 0, "status": "completed"}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "Done"}},
        ]
        raw = "HEADER\n" + "\n".join(json.dumps(item) for item in events)
        parsed = codex_harness.parse_codex_raw(raw)
        self.assertEqual([item["type"] for item in parsed["steps"]], ["exec", "codex"])
        self.assertIn("python task.py", codex_harness.format_codex_trace_steps(raw))
        self.assertNotIn("Done", codex_harness.extract_codex_trace_prefix(raw, after_step=1))


if __name__ == "__main__":
    unittest.main()
