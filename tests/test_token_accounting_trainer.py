"""Offline Trainer accounting tests: no external model or child process runs."""
from __future__ import annotations

from contextlib import ExitStack, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from skillopt.engine import trainer as trainer_module
from skillopt.envs.base import EnvAdapter
from skillopt.model.common import tracker
from skillopt.model.infra_errors import InfraError
from skillopt.model.usage_accounting import UsageLedger


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class SyntheticUsageAdapter(EnvAdapter):
    """Independent ledger instances imitate worker-local accounting writers."""

    def __init__(self, *, patches=False, fail_train=False, legacy=False):
        self.patches = patches
        self.fail_train = fail_train
        self.legacy = legacy
        self.calls = []
        self.roots = []

    def _request(self, stage, *, failed=False):
        self.roots.append(os.environ.get("SKILLOPT_USAGE_ROOT"))
        if self.legacy:
            tracker.record(stage, 7, 3)
            self.calls.append({"stage": stage, "request_id": None})
            return
        # No parent-process tracker recording: Trainer must discover the
        # authoritative disk record even when a worker has isolated memory.
        ledger = UsageLedger(os.environ["SKILLOPT_USAGE_ROOT"])
        request_id = ledger.start(stage, "codex_cli", "synthetic-offline-model")
        raw = "simulated 401" if failed else json.dumps({
            "type": "turn.completed", "turn_id": request_id,
            "usage": {"input_tokens": 7, "cached_input_tokens": 2, "output_tokens": 3},
        })
        record = ledger.finish(request_id, raw, "infra_error" if failed else "completed",
                               failure_type="auth_error" if failed else None)
        self.calls.append(record)
        if failed:
            raise InfraError("auth_error", "Simulated 401; no external request", stage=stage)

    def build_train_env(self, batch_size, seed, **kwargs):
        return [{"id": f"train-{i}", "split": "train"} for i in range(batch_size)]

    def build_eval_env(self, env_num, split, seed, **kwargs):
        return [{"id": f"{split}-{i}", "split": split} for i in range(env_num)]

    def get_task_types(self):
        return ["synthetic_accounting"]

    def rollout(self, env_manager, skill_content, out_dir, **kwargs):
        path = Path(out_dir)
        path.mkdir(parents=True, exist_ok=True)
        failed = self.fail_train and env_manager[0]["split"] == "train"
        self._request("target", failed=failed)
        score = int("ACCOUNTING_PATCH" in skill_content)
        rows = [{**item, "status": "completed", "hard": score, "soft": float(score),
                 "task_type": "synthetic_accounting"} for item in env_manager]
        (path / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        return rows

    def reflect(self, results, skill_content, out_dir, **kwargs):
        self._request("analyst")
        return ([{"source_type": "failure", "batch_size": len(results),
                  "patch": {"edits": [{"op": "append", "content": "- ACCOUNTING_PATCH"}]}}]
                if self.patches else [])


class TokenAccountingTrainerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.initial = self.root / "initial.md"
        self.initial.write_text("# Local accounting fixture\n\n- Initial rule.\n", encoding="utf-8")
        self.addCleanup(trainer_module.reset_token_tracker)

    def config(self, name, **overrides):
        cfg = {
            "env": "synthetic_accounting", "out_root": str(self.root / name),
            "skill_init": str(self.initial), "optimizer_model": "synthetic-optimizer",
            "target_model": "synthetic-target", "optimizer_backend": "codex_cli",
            "target_backend": "codex_exec", "codex_exec_use_sdk": "cli",
            "num_epochs": 1, "train_size": 4,
            "batch_size": 4, "accumulation": 1, "seed": 42, "merge_batch_size": 2,
            "edit_budget": 2, "min_edit_budget": 1, "analyst_workers": 1,
            "skill_update_mode": "patch", "use_gate": True, "sel_env_num": 4,
            "test_env_num": 4, "eval_test": False, "use_slow_update": False,
            "use_meta_skill": False, "cam_enabled": True, "cam_bootstrap_gate": True,
            "cam_confidence_level": 0.95, "cam_meaningful_improvement": 0.0,
            "cam_bootstrap_samples": 100, "cam_seed": 42,
            "cam_adaptive_budget": False, "cam_memory_enabled": False,
        }
        cfg.update(overrides)
        return cfg

    def train(self, cfg, adapter):
        setup_names = (
            "configure_azure_openai", "configure_codex_exec", "configure_claude_code_exec",
            "configure_qwen_chat", "configure_minimax_chat", "set_optimizer_backend",
            "set_target_backend", "set_optimizer_deployment", "set_target_deployment",
            "set_reasoning_effort",
        )
        with ExitStack() as stack:
            for name in setup_names:
                stack.enter_context(patch.object(trainer_module, name))
            stack.enter_context(patch("socket.socket.connect", side_effect=AssertionError("No network permitted")))
            stack.enter_context(patch("subprocess.Popen", side_effect=AssertionError("No subprocess permitted")))
            with redirect_stdout(io.StringIO()):
                return trainer_module.ReflACTTrainer(cfg, adapter).train()

    def read(self, cfg, name):
        return json.loads((Path(cfg["out_root"]) / name).read_text(encoding="utf-8"))

    def assert_provenance(self, cfg, summary):
        meta = summary["token_accounting"]
        records = sorted(UsageLedger(meta["ledger_root"]).records(), key=lambda r: r["request_id"])
        self.assertEqual(meta["request_ids"], [r["request_id"] for r in records])
        self.assertEqual(meta["record_count"], len(records))
        self.assertEqual(meta["records_sha256"], canonical_hash(records))
        self.assertEqual(meta["summary_sha256"], canonical_hash(summary["token_summary"]))
        self.assertEqual(meta["counting_scope"], "codex_cli_invocations")
        self.assertIsNone(meta["http_subrequest_count"])
        self.assertEqual(meta["ledger_root"], str((Path(cfg["out_root"]) / "token_accounting").resolve()))

    def test_normal_step_uses_only_new_request_ids_and_full_run_includes_evaluations(self):
        cfg = self.config("normal", eval_test=True)
        adapter = SyntheticUsageAdapter(patches=True)
        summary = self.train(cfg, adapter)
        self.assert_provenance(cfg, summary)
        step = self.read(cfg, "steps/step_0001/step_usage.json")
        history = self.read(cfg, "history.json")[0]
        self.assertEqual(history["action"], "accept_new_best")
        self.assertEqual(step["record_count"], 3)  # train + analyst + candidate selection
        self.assertEqual(step["ledger_summary"]["_total"]["total_tokens"], 30)
        self.assertEqual(history["tokens"]["target"]["calls"], 2)
        self.assertEqual(history["tokens"]["analyst"]["calls"], 1)
        self.assertEqual(step["selection_method"], "request_id_set_difference")
        self.assertTrue(set(step["request_ids"]).isdisjoint(step["before_request_ids"]))
        self.assertGreater(summary["token_accounting"]["record_count"], step["record_count"])
        self.assertEqual(summary["token_summary"]["_total"]["calls"], len(adapter.calls))
        self.assertEqual(summary["token_summary"]["_total"]["total_tokens"], len(adapter.calls) * 10)
        self.assertEqual(summary["token_accounting"]["coverage"], "complete")
        self.assertTrue(summary["token_accounting"]["usage_complete"])

    def test_skipped_step_still_persists_actual_target_and_analyst_accounting(self):
        cfg = self.config("skip")
        summary = self.train(cfg, SyntheticUsageAdapter())
        self.assert_provenance(cfg, summary)
        history = self.read(cfg, "history.json")[0]
        step = self.read(cfg, "steps/step_0001/step_usage.json")
        self.assertEqual(history["action"], "skip_no_patches")
        self.assertEqual(step["record_count"], 2)
        self.assertEqual(step["status"], "completed")
        self.assertEqual(history["tokens"]["analyst"]["total_tokens"], 10)

    def test_old_unknown_usage_does_not_poison_known_step_or_crash_summary_printing(self):
        cfg = self.config("unknown")
        ledger = UsageLedger(Path(cfg["out_root"]) / "token_accounting")
        request_id = ledger.start("target", "codex_cli", "earlier-offline-model")
        ledger.finish(request_id, "No usage survived the prior failed request.", "infra_error", "auth_error")
        summary = self.train(cfg, SyntheticUsageAdapter())
        self.assert_provenance(cfg, summary)
        self.assertIsNone(summary["token_summary"]["_total"]["total_tokens"])
        self.assertEqual(summary["token_summary"]["_total"]["unknown_calls"], 1)
        self.assertFalse(summary["token_accounting"]["usage_complete"])
        step = self.read(cfg, "steps/step_0001/step_usage.json")
        self.assertNotIn(request_id, step["request_ids"])
        self.assertEqual(step["ledger_summary"]["_total"]["total_tokens"], 20)
        self.assertTrue(step["usage_complete"])

    def test_resume_preserves_disk_records_and_separate_runs_restore_parent_scope(self):
        parent_root = str(self.root / "outer_scope")
        parent = UsageLedger(parent_root)
        parent_id = parent.start("outer", "codex_cli", "parent-offline-model")
        parent.finish(parent_id, "", "completed")
        cfg = self.config("first")
        with patch.dict(os.environ, {"SKILLOPT_USAGE_ROOT": parent_root}):
            tracker.record("contamination", 999, 999)
            first = self.train(cfg, SyntheticUsageAdapter())
            self.assertEqual(os.environ["SKILLOPT_USAGE_ROOT"], parent_root)
            self.assertNotIn("contamination", first["token_summary"])
            self.assertNotIn(parent_id, first["token_accounting"]["request_ids"])
            resumed_adapter = SyntheticUsageAdapter()
            resumed = self.train(cfg, resumed_adapter)
            self.assertEqual(resumed_adapter.calls, [])
            self.assertEqual(first["token_accounting"]["request_ids"], resumed["token_accounting"]["request_ids"])
            self.assertEqual(resumed["token_summary"]["_total"]["calls"], 3)
            second = self.train(self.config("second"), SyntheticUsageAdapter())
            self.assertEqual(second["token_summary"]["_total"]["calls"], 3)
            self.assertTrue(set(first["token_accounting"]["request_ids"]).isdisjoint(second["token_accounting"]["request_ids"]))
            self.assertEqual(os.environ["SKILLOPT_USAGE_ROOT"], parent_root)
        self.assertEqual(len(parent.records()), 1)

    def test_infra_abort_writes_nullable_summary_and_interrupted_step_evidence(self):
        cfg = self.config("auth_abort")
        adapter = SyntheticUsageAdapter(fail_train=True)
        previous = os.environ.get("SKILLOPT_USAGE_ROOT")
        with self.assertRaises(InfraError):
            self.train(cfg, adapter)
        self.assertEqual(os.environ.get("SKILLOPT_USAGE_ROOT"), previous)
        summary = self.read(cfg, "summary.json")
        self.assert_provenance(cfg, summary)
        self.assertEqual(summary["validity"], "invalid_auth")
        self.assertIsNone(summary["test_hard"])
        self.assertEqual(summary["token_summary"]["_total"]["calls"], 2)
        self.assertEqual(summary["token_summary"]["_total"]["failed_calls"], 1)
        self.assertIsNone(summary["token_summary"]["_total"]["total_tokens"])
        step = self.read(cfg, "steps/step_0001/step_usage.json")
        self.assertEqual(step["status"], "infra_error")
        self.assertEqual(step["failure_type"], "auth_error")
        self.assertEqual(step["record_count"], 1)
        self.assertIsNone(step["ledger_summary"]["_total"]["total_tokens"])
        self.assertFalse((Path(cfg["out_root"]) / "history.json").exists())
        retained = Path(step["attempt_path"]).read_bytes()
        resumed = self.train(cfg, SyntheticUsageAdapter())
        attempts = list((Path(cfg["out_root"]) / "steps/step_0001/usage_attempts").glob("*.json"))
        self.assertEqual(len(attempts), 2)
        self.assertEqual(Path(step["attempt_path"]).read_bytes(), retained)
        self.assertTrue(set(step["request_ids"]).issubset(resumed["token_accounting"]["request_ids"]))

    def test_legacy_backend_is_explicitly_not_request_level_complete(self):
        cfg = self.config("legacy", optimizer_backend="openai_chat", target_backend="openai_chat")
        summary = self.train(cfg, SyntheticUsageAdapter(legacy=True))
        self.assert_provenance(cfg, summary)
        meta = summary["token_accounting"]
        self.assertEqual(meta["record_count"], 0)
        self.assertEqual(meta["coverage"], "partial")
        self.assertFalse(meta["usage_complete"])
        self.assertEqual(meta["untracked_summary_calls"], 3)
        self.assertEqual(summary["token_summary"]["_total"]["calls"], 3)
        self.assertEqual(self.read(cfg, "history.json")[0]["tokens"], {})
        self.assertEqual(meta["uncovered_backends"], {"optimizer": "openai_chat", "target": "openai_chat"})

    def test_sdk_or_auto_target_cannot_be_certified_by_optimizer_cli_records(self):
        for mode in ("sdk", "auto", None):
            with self.subTest(mode=mode):
                cfg = self.config(f"sdk_{mode}", codex_exec_use_sdk=mode)
                summary = self.train(cfg, SyntheticUsageAdapter())
                meta = summary["token_accounting"]
                self.assertEqual(meta["coverage"], "partial")
                self.assertFalse(meta["usage_complete"])
                self.assertIn("target", meta["uncovered_exec_modes"])

    def test_accounting_and_configuration_share_non_codex_backend_defaults(self):
        for backend, expected in (("claude", ("claude_chat", "claude_chat")),
                                  ("qwen", ("openai_chat", "qwen_chat"))):
            cfg = self.config(backend, model_backend=backend, optimizer_backend=None, target_backend=None)
            summary = self.train(cfg, SyntheticUsageAdapter(legacy=True))
            resolved = summary["token_accounting"]["configured_backends"]
            self.assertEqual((resolved["optimizer"], resolved["target"]), expected)
            self.assertEqual(resolved["optimizer"], cfg["optimizer_backend"])
            self.assertEqual(resolved["target"], cfg["target_backend"])

    def test_legacy_history_cannot_be_certified_by_later_cli_configuration(self):
        cfg = self.config("changed_backend", cam_enabled=False,
                          optimizer_backend="openai_chat", target_backend="openai_chat")
        self.train(cfg, SyntheticUsageAdapter(legacy=True))
        resumed_cfg = {**cfg, "optimizer_backend": "codex_cli", "target_backend": "codex_exec"}
        summary = self.train(resumed_cfg, SyntheticUsageAdapter())
        self.assertEqual(summary["token_accounting"]["historical_coverage"], "partial")
        self.assertEqual(summary["token_accounting"]["coverage"], "partial")
        self.assertFalse(summary["token_accounting"]["usage_complete"])


if __name__ == "__main__":
    unittest.main()
