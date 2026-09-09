"""Offline request-boundary tests. Model responses are synthetic, never live."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skillopt.cam.rejected_memory import PersistentRejectedEditMemory, RejectedEditMemoryItem
from skillopt.gradient import reflect
from skillopt.model.infra_errors import InfraError


class MemoryReflectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.predictions = self.root / "predictions"
        trajectory = self.predictions / "synthetic_train"
        trajectory.mkdir(parents=True)
        (trajectory / "conversation.json").write_text(json.dumps([
            {"role": "assistant", "content": "SYNTHETIC: overwrote a formula; execution completed."}
        ]), encoding="utf-8")
        self.rows = [{"id": "synthetic_train", "hard": 0, "soft": 0,
                      "fail_reason": "unsafe formula overwrite", "task_description": "preserve formula",
                      "task_type": "synthetic", "split": "train"}]
        self.memory = PersistentRejectedEditMemory(self.root / "memory.json")
        self.memory.add(RejectedEditMemoryItem(
            memory_id="synthetic:e1:s1:r0", failure_pattern="unsafe formula overwrite",
            rejected_edit="NEGATIVE_EDIT_SENTINEL: overwrite all formula cells",
            score_change=-0.25, benchmark="synthetic", task_type="synthetic", epoch=1, step=1))
        # Verify the request boundary uses records loaded from disk, not an in-memory fixture shortcut.
        self.memory = PersistentRejectedEditMemory(self.root / "memory.json")
        self.reply = json.dumps({"patch": {"edits": [{"op": "append", "content": "Preserve formulas."}]}})

    def run_reflection(self, name="reflect", *, memory="default", rows=None, **kwargs):
        return reflect.run_minibatch_reflect(
            results=self.rows if rows is None else rows, skill_content="# Synthetic Skill",
            prediction_dir=str(self.predictions), patches_dir=str(self.root / name),
            workers=1, failure_only=False, minibatch_size=1,
            persistent_memory=self.memory if memory == "default" else memory,
            memory_scope={"benchmark": "synthetic", "epoch": 2, "step": 2, "top_k": 3,
                          "source_split": "train"},
            skill_aware_reflection=False, **kwargs)

    def read(self, name):
        return json.loads((self.root / name).read_text(encoding="utf-8"))

    def test_hit_enters_actual_optimizer_prompt_with_matching_artifact_hash(self):
        with patch.object(reflect, "chat_optimizer", return_value=(self.reply, {})) as model:
            patches = self.run_reflection()
        self.assertEqual(len(patches), 1)
        self.assertEqual(model.call_count, 1)
        prompt = model.call_args.kwargs["user"]
        self.assertIn("## Persistent Rejected-Edit Memory", prompt)
        self.assertIn("NEGATIVE_EDIT_SENTINEL", prompt)
        self.assertIn("synthetic:e1:s1:r0", prompt)
        self.assertNotIn("persistent_memory_context", model.call_args.kwargs)
        prefix = "reflect/minibatch_fail_000/"
        audit = self.read(prefix + "memory_retrieval.json")
        meta = self.read(prefix + "request_meta.json")
        conversation = self.read(prefix + "conversation.json")
        self.assertEqual(conversation[1]["content"], prompt)
        self.assertEqual(meta["context_sha256"], audit["context_sha256"])
        self.assertTrue(meta["context_injected"])
        self.assertEqual(meta["status"], "completed")
        self.assertEqual(audit["hit_ids"], ["synthetic:e1:s1:r0"])
        self.assertEqual(self.read("reflect/stage_stats.json")["persistent_memory"], {
            "retrieval_calls": 1, "hit_count": 1, "injected_groups": 1,
            "injected_items": 1, "completed_injected_groups": 1})

    def test_cold_start_and_disabled_keep_prompt_identical_and_local_buffer(self):
        cold = PersistentRejectedEditMemory(self.root / "missing_memory.json")
        prompts = []
        for name, memory in (("disabled", None), ("cold", cold)):
            with patch.object(reflect, "chat_optimizer", return_value=(self.reply, {})) as model:
                self.run_reflection(name, memory=memory, step_buffer_context="EPOCH_LOCAL_BUFFER_SENTINEL")
            prompts.append(model.call_args.kwargs["user"])
        self.assertEqual(prompts[0], prompts[1])
        self.assertIn("EPOCH_LOCAL_BUFFER_SENTINEL", prompts[0])
        self.assertNotIn("Persistent Rejected-Edit Memory", prompts[0])
        self.assertFalse(cold.path.exists())
        cold_stats = self.read("cold/stage_stats.json")["persistent_memory"]
        disabled_stats = self.read("disabled/stage_stats.json")["persistent_memory"]
        self.assertEqual(cold_stats["retrieval_calls"], 1)
        self.assertEqual(cold_stats["hit_count"], 0)
        self.assertEqual(disabled_stats["retrieval_calls"], 0)

    def test_cached_patch_does_not_recount_retrieval_or_injection(self):
        with patch.object(reflect, "chat_optimizer", return_value=(self.reply, {})) as model:
            self.run_reflection()
            self.run_reflection()
        self.assertEqual(model.call_count, 1)
        stats = self.read("reflect/stage_stats.json")
        self.assertEqual(stats["cached_groups"], 1)
        self.assertEqual(stats["analyst_calls"], 0)
        self.assertTrue(all(value == 0 for value in stats["persistent_memory"].values()))
        self.assertEqual(stats["cumulative_persistent_memory"]["injected_groups"], 1)
        self.assertEqual(stats["cumulative_persistent_memory"]["retrieval_calls"], 1)
        self.assertEqual(stats["memory_coverage"], "complete")

    def test_uninstrumented_cache_marks_cumulative_coverage_partial(self):
        folder = self.root / "reflect"
        folder.mkdir()
        (folder / "minibatch_fail_000.json").write_text(self.reply, encoding="utf-8")
        with patch.object(reflect, "chat_optimizer") as model:
            self.run_reflection()
        model.assert_not_called()
        stats = self.read("reflect/stage_stats.json")
        self.assertEqual(stats["memory_coverage"], "partial")
        self.assertEqual(stats["cached_groups"], 1)

    def test_missing_trajectory_cannot_reuse_stale_injection_metadata(self):
        artifact = self.root / "reflect" / "minibatch_fail_000"
        artifact.mkdir(parents=True)
        (artifact / "request_meta.json").write_text(json.dumps({
            "optimizer_called": True, "context_injected": True, "status": "completed"}), encoding="utf-8")
        rows = [dict(self.rows[0], id="missing_trajectory")]
        with patch.object(reflect, "chat_optimizer", side_effect=AssertionError("must not call")) as model:
            self.run_reflection(rows=rows)
        model.assert_not_called()
        stats = self.read("reflect/stage_stats.json")["persistent_memory"]
        self.assertEqual(stats["hit_count"], 1)
        self.assertEqual(stats["injected_groups"], 0)
        self.assertEqual(stats["completed_injected_groups"], 0)
        self.assertEqual(self.read("reflect/minibatch_fail_000/request_meta.json")["status"], "not_requested")

    def test_failed_request_is_attempted_injection_not_completed_injection(self):
        failure = InfraError("auth_error", "SYNTHETIC 401", stage="reflect")
        with patch.object(reflect, "chat_optimizer", side_effect=failure), self.assertRaises(InfraError):
            self.run_reflection()
        stats = self.read("reflect/stage_stats.json")
        self.assertEqual(stats["status"], "infra_error")
        self.assertEqual(stats["persistent_memory"]["injected_groups"], 1)
        self.assertEqual(stats["persistent_memory"]["completed_injected_groups"], 0)
        conv = self.read("reflect/minibatch_fail_000/conversation.json")
        self.assertEqual([m["role"] for m in conv], ["system", "user"])

    def test_success_only_does_not_retrieve_failure_memory(self):
        with patch.object(reflect, "chat_optimizer", return_value=(self.reply, {})) as model:
            self.run_reflection(rows=[dict(self.rows[0], hard=1, soft=1)])
        model.assert_called_once()
        self.assertNotIn("NEGATIVE_EDIT_SENTINEL", model.call_args.kwargs["user"])
        self.assertEqual(self.read("reflect/stage_stats.json")["persistent_memory"]["retrieval_calls"], 0)

    def test_empty_model_response_does_not_count_as_completed_injection(self):
        with patch.object(reflect, "chat_optimizer", return_value=("", {})):
            self.run_reflection()
        counts = self.read("reflect/stage_stats.json")["persistent_memory"]
        self.assertEqual(counts["injected_groups"], 1)
        self.assertEqual(counts["completed_injected_groups"], 0)
        rows = (self.root / "reflect/results.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(rows[-1])["failure_type"], "empty_response")


if __name__ == "__main__":
    unittest.main()
