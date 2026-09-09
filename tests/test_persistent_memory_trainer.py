"""Offline two-epoch integration through the real Trainer/reflect/CAM gate.

Synthetic target outcomes and deterministic optimizer answers exercise wiring,
not research performance. No benchmark data or external model requests are used.
"""
from __future__ import annotations

from contextlib import ExitStack, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from skillopt.cam.rejected_memory import PersistentRejectedEditMemory
from skillopt.engine import trainer as trainer_module
from skillopt.envs.base import EnvAdapter
from skillopt.gradient import reflect as reflect_module


class SyntheticMemoryAdapter(EnvAdapter):
    """Keep real reflection while supplying deterministic local task outcomes."""

    def __init__(self):
        self.analyst_workers = 1
        self.failure_only = False
        self.minibatch_size = 2
        self.edit_budget = 2
        self.reflection_scopes = []
        self.memory_objects = []
        self.rollout_events = []

    def build_train_env(self, batch_size, seed, **kwargs):
        return [{"id": f"synthetic-train-{index}", "split": "train"}
                for index in range(batch_size)]

    def build_eval_env(self, env_num, split, seed, **kwargs):
        return [{"id": f"synthetic-{split}-{index}", "split": split}
                for index in range(env_num)]

    def get_task_types(self):
        return ["synthetic_lookup"]

    def rollout(self, env_manager, skill_content, out_dir, **kwargs):
        output = Path(out_dir)
        output.mkdir(parents=True, exist_ok=True)
        rows = []
        for item in env_manager:
            # Both epochs share a training failure pattern. Any proposed bad
            # rule reduces every selection item from 1 to 0, so the real
            # paired bootstrap gate deterministically rejects the update.
            hard = 0 if item["split"] == "train" or "BAD_LOOKUP_RULE" in skill_content else 1
            row = {**item, "hard": hard, "soft": float(hard), "ok": bool(hard),
                   "status": "completed", "phase": "exec", "llm_ok": True,
                   "code_ok": True, "exec_ok": True, "task_type": "synthetic_lookup",
                   "task_description": "Repair lookup formula in a synthetic training table.",
                   "fail_reason": "lookup formula" if not hard else "",
                   "failure_type": "score_mismatch" if not hard else "none"}
            rows.append(row)
            prediction = output / "predictions" / item["id"]
            prediction.mkdir(parents=True, exist_ok=True)
            conversation = [{"role": "assistant", "content": "Synthetic target trace: lookup formula remains wrong."}]
            (prediction / "conversation.json").write_text(json.dumps(conversation), encoding="utf-8")
        (output / "results.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        self.rollout_events.append({"out_dir": str(output), "skill": skill_content,
                                    "ids": [row["id"] for row in rows],
                                    "scores": [row["hard"] for row in rows]})
        return rows

    def reflect(self, results, skill_content, out_dir, **kwargs):
        self.reflection_scopes.append(dict(kwargs.get("memory_scope") or {}))
        self.memory_objects.append(kwargs.get("persistent_memory"))
        # This explicitly exercises the production EnvAdapter pass-through,
        # minibatch dispatcher, memory query and analyst prompt construction.
        return super().reflect(results, skill_content, out_dir, **kwargs)


class PersistentMemoryTrainerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def _run_two_epochs(self, *, enabled):
        output = self.root / ("memory_enabled" if enabled else "memory_disabled")
        initial = self.root / "initial_skill.md"
        initial.write_text("# Synthetic Skill\n\n- Preserve lookup formula references.\n", encoding="utf-8")
        cfg = {
            "env": "synthetic_lookup", "out_root": str(output), "skill_init": str(initial),
            "optimizer_model": "synthetic-local-optimizer", "target_model": "synthetic-local-target",
            "optimizer_backend": "openai_chat", "target_backend": "openai_chat",
            "num_epochs": 2, "train_size": 2, "batch_size": 2, "accumulation": 1,
            "seed": 42, "merge_batch_size": 2, "minibatch_size": 2,
            "edit_budget": 2, "min_edit_budget": 1, "analyst_workers": 1,
            "max_analyst_rounds": 1, "skill_update_mode": "patch", "use_gate": True,
            "sel_env_num": 2, "test_env_num": 2, "eval_test": False,
            "use_slow_update": False, "use_meta_skill": False,
            "cam_enabled": True, "cam_bootstrap_gate": True,
            "cam_confidence_level": 0.95, "cam_meaningful_improvement": 0.0,
            "cam_bootstrap_samples": 100, "cam_seed": 42,
            "cam_adaptive_budget": False, "cam_memory_enabled": enabled,
            "cam_memory_top_k": 3,
        }
        adapter = SyntheticMemoryAdapter()
        requests = []

        def synthetic_optimizer(**kwargs):
            requests.append(dict(kwargs))
            update = {"failure_summary": [{"failure_type": "lookup formula", "description": "lookup formula"}],
                      "patch": {"reasoning": "Synthetic deliberately harmful candidate for gate testing.",
                                "edits": [{"op": "append", "content": f"- BAD_LOOKUP_RULE_EPOCH_{len(requests)}"}]}}
            return json.dumps(update), None

        # Suppress credential/configuration side effects, but preserve the real
        # trainer, gate, patch application, reflection and memory implementations.
        configuration_calls = (
            "configure_azure_openai", "configure_codex_exec", "configure_claude_code_exec",
            "configure_qwen_chat", "configure_minimax_chat", "set_optimizer_backend",
            "set_target_backend", "set_optimizer_deployment", "set_target_deployment",
            "set_reasoning_effort",
        )
        with ExitStack() as stack:
            for name in configuration_calls:
                stack.enter_context(patch.object(trainer_module, name))
            stack.enter_context(patch.object(reflect_module, "chat_optimizer", side_effect=synthetic_optimizer))
            stack.enter_context(patch("socket.socket.connect", side_effect=AssertionError("Unexpected external network request")))
            stack.enter_context(patch("subprocess.Popen", side_effect=AssertionError("Unexpected model subprocess")))
            if not enabled:
                stack.enter_context(patch.object(trainer_module, "PersistentRejectedEditMemory",
                                                side_effect=AssertionError("NoMemory constructed persistent storage")))
                for name in ("load", "save", "retrieve", "add", "add_rejected_step"):
                    stack.enter_context(patch.object(PersistentRejectedEditMemory, name,
                                                    side_effect=AssertionError(f"NoMemory called {name}")))
            captured = io.StringIO()
            with redirect_stdout(captured):
                summary = trainer_module.ReflACTTrainer(cfg, adapter).train()
        history = json.loads((output / "history.json").read_text(encoding="utf-8"))
        return output, adapter, requests, history, summary

    def test_epoch_one_rejection_is_retrieved_in_epoch_two_actual_prompt(self):
        output, adapter, requests, history, summary = self._run_two_epochs(enabled=True)
        self.assertEqual(len(requests), 2)
        self.assertEqual([(s["epoch"], s["step"]) for s in adapter.reflection_scopes], [(1, 1), (2, 2)])
        self.assertTrue(all(s["benchmark"] == "synthetic_lookup" and s["source_split"] == "train"
                            and s["top_k"] == 3 for s in adapter.reflection_scopes))
        self.assertIs(adapter.memory_objects[0], adapter.memory_objects[1])
        self.assertIsInstance(adapter.memory_objects[0], PersistentRejectedEditMemory)
        self.assertEqual([h["action"] for h in history], ["reject", "reject"])
        self.assertTrue(all(h["cam_gate_used"] and h["cam_gate"]["action"] == "reject" for h in history))
        self.assertEqual(summary["total_rejects"], 2)
        self.assertEqual(summary["total_skips"], 0)

        first_dir = output / "steps" / "step_0001" / "patches" / "minibatch_fail_000"
        second_dir = output / "steps" / "step_0002" / "patches" / "minibatch_fail_000"
        first_audit = json.loads((first_dir / "memory_retrieval.json").read_text(encoding="utf-8"))
        second_audit = json.loads((second_dir / "memory_retrieval.json").read_text(encoding="utf-8"))
        self.assertEqual(first_audit["status"], "empty_history")
        self.assertEqual(first_audit["hit_count"], 0)
        self.assertEqual(second_audit["status"], "hit")
        self.assertEqual(second_audit["hit_ids"], ["synthetic_lookup:e1:s1:r0"])
        self.assertTrue(all(h["epoch"] == 1 and h["step"] == 1 for h in second_audit["hits"]))
        self.assertNotIn("## Persistent Rejected-Edit Memory", requests[0]["user"])
        self.assertIn("## Persistent Rejected-Edit Memory", requests[1]["user"])
        self.assertIn("BAD_LOOKUP_RULE_EPOCH_1", requests[1]["user"])
        self.assertNotIn("## Previous Steps in This Epoch", requests[1]["user"])
        # The retrieval parameter is consumed before the real model boundary.
        self.assertNotIn("persistent_memory_context", requests[1])
        conversation = json.loads((second_dir / "conversation.json").read_text(encoding="utf-8"))
        recorded_user = next(m["content"] for m in conversation if m["role"] == "user")
        self.assertEqual(recorded_user, requests[1]["user"])
        meta = json.loads((second_dir / "request_meta.json").read_text(encoding="utf-8"))
        self.assertTrue(meta["context_injected"])
        self.assertEqual(meta["status"], "completed")
        self.assertEqual(meta["context_sha256"], second_audit["context_sha256"])
        self.assertEqual(meta["context_chars"], second_audit["context_chars"])

        memory = json.loads((output / "cam_rejected_memory.json").read_text(encoding="utf-8"))
        self.assertEqual([(item["epoch"], item["step"]) for item in memory["items"]], [(1, 1), (2, 2)])
        expected = {"retrieval_calls": 2, "hit_count": 1, "injected_groups": 1,
                    "injected_items": 1, "completed_injected_groups": 1,
                    "write_calls": 2, "items_added": 2}
        for key, value in expected.items():
            self.assertEqual(summary["persistent_memory"][key], value, key)
        self.assertEqual(summary["persistent_memory"]["coverage"], "complete")
        self.assertEqual(summary["persistent_memory"]["stored_item_count"], 2)
        self.assertEqual(history[0]["persistent_memory"]["items_added"], 1)
        self.assertEqual(history[1]["persistent_memory"]["hit_count"], 1)

    def test_memory_disabled_two_epochs_never_reads_writes_or_injects(self):
        output, adapter, requests, history, summary = self._run_two_epochs(enabled=False)
        self.assertEqual(len(requests), 2)
        self.assertEqual(adapter.memory_objects, [None, None])
        self.assertEqual([h["action"] for h in history], ["reject", "reject"])
        self.assertTrue(all(h["cam_gate_used"] for h in history))
        self.assertFalse((output / "cam_rejected_memory.json").exists())
        self.assertFalse(summary["persistent_memory"]["enabled"])
        self.assertEqual(summary["persistent_memory"]["coverage"], "complete")
        for key in ("retrieval_calls", "hit_count", "injected_groups", "injected_items",
                    "completed_injected_groups", "write_calls", "items_added", "stored_item_count"):
            self.assertEqual(summary["persistent_memory"][key], 0, key)
        for step, request in enumerate(requests, 1):
            self.assertNotIn("## Persistent Rejected-Edit Memory", request["user"])
            artifact = output / "steps" / f"step_{step:04d}" / "patches" / "minibatch_fail_000"
            audit = json.loads((artifact / "memory_retrieval.json").read_text(encoding="utf-8"))
            meta = json.loads((artifact / "request_meta.json").read_text(encoding="utf-8"))
            self.assertEqual(audit["status"], "disabled")
            self.assertEqual(audit["retrieval_calls"], 0)
            self.assertFalse(meta["context_injected"])


if __name__ == "__main__":
    unittest.main()
