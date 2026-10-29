"""Offline integration coverage for CAM v3 trainer/configuration behavior."""
import json
import unittest
from unittest.mock import patch

from skillopt.config import flatten_config
from skillopt.engine import trainer as trainer_module
from tests.test_cam_gate_trainer import (
    CAMGateTrainerTests as TrainerFixture,
    SLOW_MARKER,
    SyntheticGateAdapter,
)


class TypedSelectionAdapter(SyntheticGateAdapter):
    """Give paired validation rows six examples from each supported CAM group."""

    def get_task_types(self):
        return ["cell_level", "sheet_level"]

    def rollout(self, env_manager, skill_content, out_dir, **kwargs):
        results = super().rollout(env_manager, skill_content, out_dir, **kwargs)
        if env_manager and env_manager[0].get("split") == "valid_seen":
            for row in results:
                row["task_type"] = "cell_level" if row["index"] < 6 else "sheet_level"
        return results


class CAMV3TrainerTests(unittest.TestCase):
    # Reuse only fixture helpers; avoid inheriting and rediscovering the v2 suite.
    setUp = TrainerFixture.setUp
    config = TrainerFixture.config
    train = TrainerFixture.train

    def test_structured_v3_cam_options_flatten_to_trainer_keys(self):
        flat = flatten_config({
            "evaluation": {"final_selection": True},
            "cam": {
                "soft_accept_policy": "noninferiority",
                "noninferiority_margin": .05,
                "group_guard": False,
                "group_max_regression": .1,
                "group_min_pairs": 5,
                "budget_policy": "evidence_v3",
                "budget_min_support_tasks": 2,
                "memory_policy": "evidence_v3",
            },
        })
        self.assertEqual(flat["cam_soft_accept_policy"], "noninferiority")
        self.assertEqual(flat["cam_noninferiority_margin"], .05)
        self.assertIs(flat["cam_group_guard"], False)
        self.assertEqual(flat["cam_group_max_regression"], .1)
        self.assertEqual(flat["cam_group_min_pairs"], 5)
        self.assertEqual(flat["cam_budget_policy"], "evidence_v3")
        self.assertEqual(flat["cam_budget_min_support_tasks"], 2)
        self.assertEqual(flat["cam_memory_policy"], "evidence_v3")
        self.assertIs(flat["final_selection"], True)

    def test_baseline_final_slow_update_is_validated_without_test_rollout(self):
        output = self.root / "baseline_final_selection"
        adapter = SyntheticGateAdapter(baseline=[0, 0, 0, 1], candidate=[1, 1, 1, 1])
        summary, _ = self.train(
            self.config(output, cam_enabled=False, num_epochs=2, use_slow_update=True,
                        eval_test=False, final_selection=True),
            adapter, produce_patch=False,
        )
        final_validations = [event for event in adapter.events
                             if event["split"] == "valid_seen" and SLOW_MARKER in event["skill"]]
        self.assertEqual(len(final_validations), 1)
        self.assertFalse(any(event["split"] == "valid_unseen" for event in adapter.events))
        self.assertEqual(summary["final_selection_hard"], 1.0)
        self.assertIn(SLOW_MARKER, (output / "best_skill.md").read_text(encoding="utf-8"))

    def test_strict_cam_final_validation_runs_with_test_disabled(self):
        output = self.root / "strict_final_selection"
        adapter = SyntheticGateAdapter(baseline=[0, 0, 0, 1], candidate=[1, 1, 1, 1])
        summary, _ = self.train(
            self.config(output, cam_enabled=True, cam_bootstrap_gate=True,
                        cam_soft_accept_policy="noninferiority", cam_noninferiority_margin=.05,
                        cam_group_guard=False, num_epochs=2, use_slow_update=True,
                        eval_test=False, final_selection=True),
            adapter, produce_patch=False,
        )
        final_gate = json.loads((output / "final_cam_gate.json").read_text(encoding="utf-8"))
        self.assertIn(final_gate["action"], {"reuse_accepted_best", "accept", "accept_new_best"})
        self.assertEqual(summary["final_selection_hard"], 1.0)
        self.assertFalse(any(event["split"] == "valid_unseen" for event in adapter.events))

    def test_evidence_budget_uses_minimum_fallback_without_task_provenance(self):
        output = self.root / "budget_unknown_provenance"
        adapter = SyntheticGateAdapter(baseline=[0, 0, 0, 0], candidate=[1, 1, 1, 1])
        self.train(self.config(output, cam_enabled=True, cam_adaptive_budget=True,
                               cam_budget_policy="evidence_v3", cam_min_budget=2,
                               cam_max_budget=4, cam_budget_min_support_tasks=2,
                               cam_group_guard=False, eval_test=False), adapter)
        record = json.loads((output / "steps" / "step_0001" / "evidence_budget.json").read_text(encoding="utf-8"))
        self.assertEqual(record["reason"], "provenance_unknown_minimum_fallback")
        self.assertTrue(record["unknown_provenance"])
        self.assertGreater(record["budget"], 0)
        self.assertEqual(record["eligible_candidate_count"], 1)

    def test_zero_evidence_budget_skips_ranker(self):
        output = self.root / "budget_zero"
        adapter = SyntheticGateAdapter(baseline=[0, 0, 0, 0], candidate=[1, 1, 1, 1])
        conflicts = {"reasoning": "Two incompatible writes to one locator.", "edits": [
            {"op": "replace", "path": "rules.md", "target": "lookup", "value": "version A"},
            {"op": "replace", "path": "rules.md", "target": "lookup", "value": "version B"},
        ]}
        with patch.object(trainer_module, "merge_patches", return_value=conflicts), \
                patch.object(trainer_module, "rank_and_select",
                             side_effect=AssertionError("ranker called for zero budget")) as ranker:
            self.train(self.config(output, cam_enabled=True, cam_adaptive_budget=True,
                                   cam_budget_policy="evidence_v3", cam_min_budget=2,
                                   cam_max_budget=4, cam_budget_min_support_tasks=2,
                                   cam_group_guard=False, eval_test=False), adapter)
        record = json.loads((output / "steps" / "step_0001" / "evidence_budget.json").read_text(encoding="utf-8"))
        self.assertEqual(record["budget"], 0)
        self.assertEqual(record["conflicting_count"], 2)
        self.assertEqual(record["eligible_candidate_count"], 0)
        ranker.assert_not_called()

    def test_memory_v3_persists_one_scoped_update_bundle_with_hashes_and_changed_ids(self):
        output = self.root / "memory_bundle"
        # The candidate differs on two fixed selection IDs and remains uncertain;
        # v3 treats its paired delta as evidence about the update bundle.
        adapter = SyntheticGateAdapter(baseline=[0, 0, 0, 1], candidate=[1, 1, 0, 1])
        self.train(self.config(output, cam_enabled=True, cam_bootstrap_gate=True,
                               cam_soft_accept_policy="noninferiority", cam_noninferiority_margin=.05,
                               cam_group_guard=False, cam_memory_enabled=True,
                               cam_memory_policy="evidence_v3", eval_test=False), adapter)
        payload = json.loads((output / "cam_rejected_memory.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], 3)
        self.assertEqual(len(payload["items"]), 1)
        item = payload["items"][0]
        self.assertEqual(item["attribution_scope"], "update_bundle")
        self.assertEqual(item["task_type"], "synthetic_lookup")
        self.assertEqual(item["failure_pattern"], "synthetic_lookup: score_mismatch: lookup formula")
        digest = json.loads((output / "steps" / "step_0001" / "trajectory_digest.json").read_text(encoding="utf-8"))
        self.assertEqual(digest["failure_patterns"], [{
            "pattern": "synthetic_lookup: score_mismatch: lookup formula",
            "count": 4,
            "task_ids": [f"synthetic-train-{index}" for index in range(4)],
            "source": "observed_training_metadata",
        }])
        self.assertEqual(item["supporting_task_ids"], ["synthetic-valid_seen-0", "synthetic-valid_seen-1"])
        self.assertEqual(item["support_count"], 2)
        self.assertTrue(item["skill_before_hash"])
        self.assertTrue(item["skill_after_hash"])
        self.assertNotIn("Synthetic candidate", item["failure_pattern"])

    def test_strict_group_guard_uses_task_types_preserved_in_selection_cache(self):
        output = self.root / "strict_typed_selection"
        adapter = TypedSelectionAdapter(baseline=[0] * 12, candidate=[1] * 12)
        self.train(self.config(output, cam_enabled=True, cam_bootstrap_gate=True,
                               cam_group_guard=True, cam_group_min_pairs=5,
                               cam_soft_accept_policy="noninferiority", cam_noninferiority_margin=.05,
                               sel_env_num=12, eval_test=False, final_selection=True), adapter)
        history = json.loads((output / "history.json").read_text(encoding="utf-8"))
        audit = history[0]["cam_gate"]
        groups = audit["per_task_type"]["groups"]
        self.assertEqual(audit["per_task_type"]["status"], "complete")
        self.assertEqual({name: groups[name]["n_pairs"] for name in groups},
                         {"cell_level": 6, "sheet_level": 6})
        self.assertEqual(audit["group_guard"]["status"], "passed")

    def test_invalid_cam_policy_is_rejected_before_any_rollout(self):
        output = self.root / "invalid_v3_policy"
        adapter = SyntheticGateAdapter(baseline=[0] * 4, candidate=[1] * 4)
        with self.assertRaisesRegex(ValueError, "cam_soft_accept_policy"):
            self.train(self.config(output, cam_enabled=True, cam_bootstrap_gate=True,
                                   cam_soft_accept_policy="not-a-policy", cam_group_guard=False), adapter)
        self.assertEqual(adapter.events, [])

    def test_evidence_v3_memory_requires_paired_gate_before_any_rollout(self):
        output = self.root / "memory_v3_without_paired_gate"
        adapter = SyntheticGateAdapter(baseline=[0] * 4, candidate=[1] * 4)
        with self.assertRaisesRegex(ValueError, "Evidence-v3 memory requires aligned strict CAM validation evidence"):
            self.train(self.config(output, cam_enabled=True, cam_bootstrap_gate=False,
                                   cam_memory_enabled=True, cam_memory_policy="evidence_v3",
                                   cam_group_guard=False), adapter)
        self.assertEqual(adapter.events, [])

    def test_resume_rejects_changed_v3_policy_before_rollout(self):
        output = self.root / "v3_config_resume"
        initial = self.config(output, cam_enabled=True, cam_bootstrap_gate=True,
                              cam_soft_accept_policy="noninferiority", cam_noninferiority_margin=.05,
                              cam_group_guard=False, eval_test=False)
        self.train(initial, SyntheticGateAdapter(baseline=[0] * 4, candidate=[1] * 4))
        resumed = SyntheticGateAdapter(baseline=[0] * 4, candidate=[1] * 4)
        with self.assertRaises((ValueError, RuntimeError)):
            self.train({**initial, "cam_noninferiority_margin": .1}, resumed)
        self.assertEqual(resumed.events, [])


if __name__ == "__main__":
    unittest.main()
