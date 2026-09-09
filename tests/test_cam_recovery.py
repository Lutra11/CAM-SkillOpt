"""Offline checks for sequential recovery gates (no external model requests)."""
import contextlib
import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import cam_recovery
from skillopt.model.infra_errors import InfraError, persist_infra_error


class AuthGateTests(unittest.TestCase):
    def args(self, folder):
        return SimpleNamespace(codex_bin="offline-codex.exe", auth_home=str(Path(folder) / "auth"), transport="default")

    def test_two_consecutive_successes_each_role_with_exact_model(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            calls = []

            def fake_cli(cmd, **kwargs):
                calls.append((kwargs["stage"], kwargs["model"]))
                Path(cmd[cmd.index("--output-last-message") + 1]).write_text("CAM_AUTH_OK", encoding="utf-8")
                return subprocess.CompletedProcess(cmd, 0, "CAM_AUTH_OK", f"model: {kwargs['model']}\nprovider: openai\n")

            with patch("skillopt.model.infra_errors.run_cli_failfast", side_effect=fake_cli):
                with contextlib.redirect_stdout(io.StringIO()):
                    summary = cam_recovery.auth_check(self.args(folder), out)
            self.assertEqual(summary["status"], "passed")
            self.assertEqual(calls, [("target_1", "gpt-5.6-terra"), ("target_2", "gpt-5.6-terra"),
                                     ("optimizer_1", "gpt-5.6-sol"), ("optimizer_2", "gpt-5.6-sol")])
            records = [json.loads(line) for line in (out / "results.jsonl").read_text().splitlines()]
            self.assertEqual(len(records), 4)
            self.assertTrue(all(record["hard"] is None and record["soft"] is None for record in records))
            self.assertEqual(len(list(out.glob("*/conversation.json"))), 4)

    def test_wrong_model_header_stops_first_request(self):
        with tempfile.TemporaryDirectory() as folder:
            def fake_cli(cmd, **kwargs):
                Path(cmd[cmd.index("--output-last-message") + 1]).write_text("CAM_AUTH_OK", encoding="utf-8")
                return subprocess.CompletedProcess(cmd, 0, "CAM_AUTH_OK", "model: wrong-model\nprovider: openai\n")

            with patch("skillopt.model.infra_errors.run_cli_failfast", side_effect=fake_cli) as cli:
                with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(InfraError):
                    cam_recovery.auth_check(self.args(folder), Path(folder))
            self.assertEqual(cli.call_count, 1)

    def test_http_claim_with_builtin_provider_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            args = self.args(folder)
            args.transport = "http"

            def fake_cli(cmd, **kwargs):
                Path(cmd[cmd.index("--output-last-message") + 1]).write_text("CAM_AUTH_OK", encoding="utf-8")
                return subprocess.CompletedProcess(cmd, 0, "CAM_AUTH_OK", f"model: {kwargs['model']}\nprovider: openai\n")

            with patch("skillopt.model.infra_errors.run_cli_failfast", side_effect=fake_cli) as cli:
                with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(InfraError):
                    cam_recovery.auth_check(args, Path(folder))
            self.assertEqual(cli.call_count, 1)

    def test_401_preserves_request_conversation_without_fake_assistant(self):
        with tempfile.TemporaryDirectory() as folder:
            def fake_cli(cmd, **kwargs):
                raise persist_infra_error(
                    InfraError("auth_error", "401 invalid_refresh_token", stage=kwargs["stage"], model=kwargs["model"]),
                    raw="401 invalid_refresh_token", evidence_dir=kwargs["evidence_dir"],
                )

            with patch("skillopt.model.infra_errors.run_cli_failfast", side_effect=fake_cli) as cli:
                with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(InfraError):
                    cam_recovery.auth_check(self.args(folder), Path(folder))
            self.assertEqual(cli.call_count, 1)
            conversation_path = Path(folder) / "target_1" / "conversation.json"
            self.assertTrue(conversation_path.exists(), "Failed request still needs its attempted conversation artifact")
            conversation = json.loads(conversation_path.read_text())
            messages = conversation.get("messages", []) if isinstance(conversation, dict) else conversation
            self.assertTrue(any(message.get("role") == "user" for message in messages))
            self.assertFalse(any(message.get("role") == "assistant" for message in messages))


class PreviousGateTests(unittest.TestCase):
    def check(self, manifest_changes=None, arg_changes=None, change_config=False):
        with tempfile.TemporaryDirectory() as folder:
            base_config = Path(folder) / "config.json"
            base_config.write_text('{}', encoding="utf-8")
            args = SimpleNamespace(auth_home=str(Path(folder) / "auth"), codex_bin="offline-codex.exe",
                                   target_reasoning="none", base_config=str(base_config))
            for key, value in (arg_changes or {}).items():
                setattr(args, key, value)
            manifest = {"auth_home": str(Path(args.auth_home).resolve()), "codex_bin": args.codex_bin,
                        "source_sha256": "current-code", "target_reasoning": "none", "http_only": False,
                        "base_config_sha256": hashlib.sha256(base_config.read_bytes()).hexdigest()}
            manifest.update(manifest_changes or {})
            if change_config:
                base_config.write_text('{"seed": 43}', encoding="utf-8")
            path = Path(folder) / "summary.json"
            path.write_text(json.dumps({"status": "passed", "stage": "single", "manifest": manifest}))
            with patch.object(cam_recovery, "source_hash", return_value="current-code"):
                return cam_recovery.require_gate(str(path), "single", args)

    def test_same_code_identity_and_settings_pass(self):
        self.assertEqual(self.check()["status"], "passed")

    def test_source_change_rejected(self):
        with self.assertRaises(ValueError):
            self.check({"source_sha256": "old-code"})

    def test_auth_home_change_rejected(self):
        with self.assertRaises(ValueError):
            self.check({"auth_home": "different-auth-home"})

    def test_target_reasoning_change_requires_new_single_gate(self):
        with self.assertRaises(ValueError):
            self.check(arg_changes={"target_reasoning": "low"})

    def test_unsupported_transport_gate_is_rejected(self):
        with self.assertRaises(ValueError):
            self.check(manifest_changes={"http_only": True})

    def test_default_gate_cannot_authorize_http_experiment(self):
        with self.assertRaises(ValueError):
            self.check(arg_changes={"transport": "http"})

    def test_matching_http_transport_gate_passes(self):
        self.assertEqual(self.check(manifest_changes={"transport": "http"},
                                    arg_changes={"transport": "http"})["status"], "passed")

    def test_proxy_route_change_requires_new_gate(self):
        with self.assertRaises(ValueError):
            self.check(arg_changes={"proxy_url": "http://127.0.0.1:7890"})

    def test_matching_proxy_route_gate_passes(self):
        self.assertEqual(self.check(manifest_changes={"proxy_url": "http://127.0.0.1:7890"},
                                    arg_changes={"proxy_url": "http://127.0.0.1:7890"})["status"], "passed")

    def test_base_config_mutation_requires_new_single_gate(self):
        with self.assertRaises(ValueError):
            self.check(change_config=True)

    def test_auth_gate_without_four_model_verified_records_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            args = SimpleNamespace(auth_home=folder, codex_bin="offline-codex.exe")
            path = Path(folder) / "summary.json"
            path.write_text(json.dumps({"status": "passed", "stage": "auth-check", "results": [],
                "manifest": {"auth_home": str(Path(folder).resolve()), "codex_bin": args.codex_bin}}))
            with self.assertRaises(ValueError):
                cam_recovery.require_gate(str(path), "auth-check", args)


class ProxyEnvironmentTests(unittest.TestCase):
    def args(self, folder, proxy):
        return SimpleNamespace(auth_home=str(Path(folder) / "auth"), codex_bin=str(Path(folder) / "codex.exe"),
                               transport="http", proxy_url=proxy)

    def test_loopback_routes_only_change_process_environment(self):
        original_environment = dict(os.environ)
        with tempfile.TemporaryDirectory() as folder:
            for proxy in ("http://127.0.0.1:7890", "http://localhost:7890", "http://[::1]:7890"):
                with self.subTest(proxy=proxy), patch.dict(os.environ, {}, clear=True):
                    with patch("subprocess.run") as run, patch("subprocess.Popen") as start:
                        with patch.object(Path, "write_text") as write, patch.object(Path, "mkdir") as mkdir:
                            cam_recovery.configure_env(self.args(folder, proxy), Path(folder))
                    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
                        self.assertEqual(os.environ[key], proxy)
                    run.assert_not_called()
                    start.assert_not_called()
                    write.assert_not_called()
                    mkdir.assert_not_called()
        self.assertEqual(dict(os.environ), original_environment)

    def test_invalid_explicit_proxy_rejected_before_any_request(self):
        invalid = (
            "http://outside.example:7890", "http://192.0.2.1:7890", "https://127.0.0.1:7890",
            "socks5://127.0.0.1:7890", "http://name:password@127.0.0.1:7890",
            "http://@127.0.0.1:7890", "http://:@127.0.0.1:7890",
            "http://127.0.0.1:7890/path", "http://127.0.0.1:7890?key=value",
            "http://127.0.0.1:7890#fragment", "http://127.0.0.1", "http://127.0.0.1:65536",
        )
        with tempfile.TemporaryDirectory() as folder:
            for proxy in invalid:
                with self.subTest(proxy=proxy), patch.dict(os.environ, {}, clear=True):
                    with patch("skillopt.model.infra_errors.run_cli_failfast") as request:
                        with self.assertRaises(ValueError):
                            cam_recovery.configure_env(self.args(folder, proxy), Path(folder))
                        request.assert_not_called()
                    self.assertNotIn("HTTP_PROXY", os.environ)
                    self.assertNotIn("HTTPS_PROXY", os.environ)

    def test_external_proxy_inherited_from_environment_is_not_silent_route(self):
        with tempfile.TemporaryDirectory() as folder:
            for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
                with self.subTest(variable=key), patch.dict(os.environ, {key: "http://outside.example:7890"}, clear=True):
                    with patch("skillopt.model.infra_errors.run_cli_failfast") as request:
                        with self.assertRaises(ValueError):
                            cam_recovery.configure_env(self.args(folder, ""), Path(folder))
                        request.assert_not_called()

    def test_inherited_loopback_proxy_requires_explicit_route_declaration(self):
        with tempfile.TemporaryDirectory() as folder:
            for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
                with self.subTest(variable=key), patch.dict(os.environ, {key: "http://127.0.0.1:7890"}, clear=True):
                    with patch("skillopt.model.infra_errors.run_cli_failfast") as request:
                        with self.assertRaises(ValueError):
                            cam_recovery.configure_env(self.args(folder, ""), Path(folder))
                        request.assert_not_called()

    def test_explicit_local_route_cannot_be_overridden_by_inherited_all_or_no_proxy(self):
        proxy = "http://127.0.0.1:7890"
        inherited = {"ALL_PROXY": "http://outside.example:7890", "all_proxy": "http://outside.example:7890",
                     "NO_PROXY": "chatgpt.com", "no_proxy": "*"}
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, inherited, clear=True):
            cam_recovery.configure_env(self.args(folder, proxy), Path(folder))
            for key in ("ALL_PROXY", "all_proxy"):
                self.assertIn(os.environ.get(key), (None, "", proxy))
            for key in ("NO_PROXY", "no_proxy"):
                self.assertIn(os.environ.get(key), (None, ""))


if __name__ == "__main__":
    unittest.main()
