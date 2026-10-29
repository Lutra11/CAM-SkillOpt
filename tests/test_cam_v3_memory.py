"""Evidence-aware v3 memory tests using only synthetic training evidence."""
import json
import tempfile
import unittest
from pathlib import Path

from skillopt.cam.memory_context import prepare_memory_context
from skillopt.cam.rejected_memory import PersistentRejectedEditMemory


class CAMV3MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "memory.json"
        self.memory = PersistentRejectedEditMemory(self.path, policy="evidence_v3")

    def add(self, *, task_type="formula", step=1, edit="preserve lookup formula",
            change=0.0, kind="inconclusive", task_ids=("valid-a", "valid-b")):
        return self.memory.add_rejected_step(
            failure_patterns=["lookup formula mismatch"], rejected_edits=[edit, "keep formula reference"],
            score_change=change, benchmark="synthetic", task_type=task_type,
            epoch=1, step=step, kind=kind,
            skill_before_hash="before-hash", skill_after_hash="after-hash",
            supporting_task_ids=task_ids,
        )

    def retrieve(self, *, task_type="formula", step=10, **kwargs):
        return self.memory.retrieve("lookup formula mismatch", benchmark="synthetic",
                                    before_step=step, through_epoch=1,
                                    task_type=task_type, **kwargs)

    def test_legacy_policy_keeps_per_edit_calls_and_v1_schema(self):
        legacy = PersistentRejectedEditMemory(self.path)
        items = legacy.add_rejected_step(
            failure_patterns=["lookup formula mismatch"], rejected_edits=["edit A", "edit B"],
            score_change=-0.2, benchmark="synthetic", task_type="formula", epoch=1, step=1,
        )
        self.assertEqual(len(items), 2)
        self.assertEqual({item.attribution_scope for item in items}, {"per_edit"})
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["schema_version"], 1)
        self.assertEqual(len(PersistentRejectedEditMemory(self.path).items), 2)

    def test_pre_v3_json_rows_load_and_legacy_ignores_v3_tuning(self):
        self.path.write_text(json.dumps({"schema_version": 1, "items": [{
            "memory_id": "old-row", "failure_pattern": "lookup formula mismatch",
            "rejected_edit": "old edit", "score_change": -0.2,
            "benchmark": "synthetic", "task_type": "formula", "epoch": 1, "step": 1,
        }]}), encoding="utf-8")
        legacy = PersistentRejectedEditMemory(self.path, min_similarity=0.99, half_life_steps=1)
        self.assertEqual(legacy.items[0].kind, "rejected")
        self.assertEqual(legacy.items[0].attribution_scope, "per_edit")
        self.assertEqual(legacy.min_similarity, 0.0)
        self.assertIsNone(legacy.age_half_life_steps)
        self.assertEqual(len(legacy.retrieve("lookup formula mismatch")), 1)

    def test_v3_collapses_edits_into_one_update_bundle_with_evidence(self):
        items = self.add(change=0.1, kind="accepted")
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.attribution_scope, "update_bundle")
        self.assertIn("preserve lookup formula\nkeep formula reference", item.rejected_edit)
        self.assertEqual(item.skill_before_hash, "before-hash")
        self.assertEqual(item.skill_after_hash, "after-hash")
        self.assertEqual(item.supporting_task_ids, ("valid-a", "valid-b"))
        self.assertEqual(item.support_count, 2)
        self.assertEqual(item.kind, "accepted")
        loaded = PersistentRejectedEditMemory(self.path, policy="evidence_v3")
        self.assertEqual(loaded.items[0].supporting_task_ids, item.supporting_task_ids)

    def test_v3_scope_excludes_other_task_types_and_allows_overall_scope(self):
        formula = self.add(task_type="formula", step=1)[0]
        self.add(task_type="code", step=2)
        self.add(task_type="overall", step=3)
        exact = self.retrieve(task_type="formula")
        self.assertEqual({result.item.memory_id for result in exact}, {formula.memory_id,
            "synthetic:e1:s3:bundle"})
        broad = self.retrieve(task_type="overall")
        self.assertEqual(len(broad), 3)

    def test_zero_delta_inconclusive_is_retrievable_and_keeps_signed_semantics(self):
        item = self.add(change=0.0, kind="inconclusive")[0]
        results = self.retrieve()
        self.assertEqual([result.item.memory_id for result in results], [item.memory_id])
        self.assertGreater(results[0].retrieval_score, 0)
        self.assertEqual(results[0].item.score_change, 0.0)

    def test_context_infers_train_scope_and_exposes_bundle_evidence(self):
        item = self.add(change=-0.1)[0]
        context, audit = prepare_memory_context(self.memory,
            [{"id": "train-a", "hard": 0, "task_type": "formula", "fail_reason": "lookup formula mismatch", "split": "train"},
             {"id": "train-b", "hard": 0, "task_type": "code", "fail_reason": "lookup formula mismatch", "split": "train"},
             {"id": "success", "hard": 1, "task_type": "secret", "fail_reason": "other", "split": "train"}],
            benchmark="synthetic", epoch=1, step=2, top_k=3)
        self.assertEqual(audit["task_scope"], "overall")
        self.assertEqual(audit["hit_ids"], [item.memory_id])
        evidence = json.loads(context.split("\n", 1)[1])[0]
        self.assertEqual(evidence["attribution_scope"], "update_bundle")
        self.assertEqual(evidence["task_type"], "formula")
        self.assertEqual(evidence["supporting_task_ids"], ["valid-a", "valid-b"])
        self.assertIn("does not identify a causal edit", context)
        self.assertIn("paired validation tasks", context)
        self.assertIn("heuristic, not a calibrated confidence", context)

    def test_source_split_metadata_is_checked_even_when_api_scope_says_train(self):
        for declared_source in ("test", "valid_unseen"):
            row = {"id": "leak-sentinel", "hard": 0, "task_type": "formula",
                   "fail_reason": "lookup formula mismatch", "source_split": declared_source}
            with self.subTest(source_split=declared_source):
                from skillopt.cam.memory_context import build_training_failure_query
                with self.assertRaises(ValueError):
                    build_training_failure_query([row], source_split="train")
                with self.assertRaises(ValueError):
                    prepare_memory_context(self.memory, [row], benchmark="synthetic",
                                           epoch=1, step=2, source_split="train")

    def test_invalid_source_split_is_rejected_before_retrieval(self):
        from unittest.mock import patch
        row = {"id": "leak-sentinel", "hard": 0, "task_type": "formula",
               "fail_reason": "lookup formula mismatch", "source_split": "valid_unseen"}
        with patch.object(self.memory, "retrieve", wraps=self.memory.retrieve) as retrieve:
            with self.assertRaises(ValueError):
                prepare_memory_context(self.memory, [row], benchmark="synthetic",
                                       epoch=1, step=2, source_split="train")
        retrieve.assert_not_called()

    def test_future_records_are_excluded(self):
        past = self.add(step=1)[0]
        future = self.add(step=4, edit="future lookup formula")[0]
        self.assertEqual([result.item.memory_id for result in self.retrieve(step=3)], [past.memory_id])
        self.assertNotEqual(past.memory_id, future.memory_id)

    def test_duplicate_bundles_are_deduplicated_and_age_decay_applies(self):
        old = self.add(step=1)[0]
        # Import a duplicate under a distinct stable ID to exercise retrieval de-duplication.
        from dataclasses import replace
        duplicate = replace(old, memory_id="duplicate-id", step=2)
        self.memory.add(duplicate)
        recent = self.add(step=9, edit="new formula bundle")[0]
        self.memory = PersistentRejectedEditMemory(self.path, policy="evidence_v3", age_half_life_steps=8)
        results = self.retrieve(step=10)
        self.assertEqual(len([result for result in results if result.item.rejected_edit == old.rejected_edit]), 1)
        scored = {result.item.memory_id: result.retrieval_score for result in results}
        self.assertIn("duplicate-id", scored)
        self.assertNotIn(old.memory_id, scored)
        self.assertGreater(scored[recent.memory_id], scored["duplicate-id"])


if __name__ == "__main__":
    unittest.main()
