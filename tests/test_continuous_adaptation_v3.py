"""Offline protocol and freeze tests for the sequential v3 runner."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import run_continuous_adaptation_v3 as runner


class ContinuousAdaptationV3ProtocolTests(unittest.TestCase):
    def test_cells_are_fixed_in_sequential_order(self):
        self.assertEqual(
            [(cell["method"], cell["seed"]) for cell in runner.cells()],
            [("baseline", 42), ("cam_v3", 42), ("cam_v3", 43), ("baseline", 43)],
        )
        self.assertEqual(len({cell["cell_id"] for cell in runner.cells()}), 4)

    def test_cfgs_keep_test_hidden_and_final_selection_enabled_in_both_arms(self):
        for cell in runner.cells():
            with self.subTest(cell=cell["cell_id"]):
                cfg = runner.config_for(cell)
                self.assertFalse(cfg["eval_test"])
                self.assertTrue(cfg["final_selection"])
                self.assertEqual(cfg["num_epochs"], 4)
                self.assertEqual(cfg["steps_per_epoch"], 1)
                self.assertEqual(cfg["train_size"], 20)
                self.assertEqual(cfg["batch_size"], 20)
                self.assertEqual(cfg["cam_enabled"], cell["method"] == "cam_v3")
                self.assertEqual(cfg["test_env_num"], 120)

    def test_seed_paired_training_orders_are_identical_a_b_a_b(self):
        first = runner.build_task_order(42)
        paired = runner.build_task_order(42)
        second_seed = runner.build_task_order(43)
        self.assertEqual(first, paired)
        self.assertEqual(len(first["batches"]), 4)
        self.assertEqual([row["global_step"] for row in first["batches"]], [1, 2, 3, 4])
        self.assertEqual([row["task_type"] for row in first["batches"]],
                         list(runner.TYPE_ORDER) * 2)
        self.assertEqual(first["batches"][0]["task_ids"], first["batches"][2]["task_ids"])
        self.assertEqual(first["batches"][1]["task_ids"], first["batches"][3]["task_ids"])
        self.assertNotEqual(first["batches"][0]["task_ids"], second_seed["batches"][0]["task_ids"])

    def test_fixed_test_subset_is_unique_and_has_frozen_83_37_strata(self):
        subset = runner.build_test_subset()
        self.assertEqual(subset["size"], 120)
        self.assertEqual(len(set(subset["task_ids"])), 120)
        self.assertEqual(subset["counts"], {
            "Cell-Level Manipulation": 83,
            "Sheet-Level Manipulation": 37,
        })
        self.assertEqual(len(subset["ids_sha256"]), 64)


class ContinuousAdaptationV3FreezeTests(unittest.TestCase):
    @staticmethod
    def sample_freeze(revision=1):
        cells = runner.cells()
        return {"study_id": runner.STUDY, "protocol_revision": revision,
                "created_at": "initial", "files": {}, "cells": cells,
                "configs": {cell["cell_id"]: {} for cell in cells},
                "task_orders": {cell["cell_id"]: {"seed": cell["seed"]} for cell in cells},
                "test_subset": {"task_ids": []}}

    def test_prepare_is_idempotent_and_does_not_reset_existing_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact_root = Path(directory)
            queue = {"study_id": runner.STUDY, "status": "paused", "sentinel": 17,
                     "cells": [{"cell_id": cell["cell_id"], "status": "accepted"}
                               for cell in runner.cells()]}
            with patch.object(runner, "ROOT", artifact_root), \
                    patch.object(runner, "ARTIFACTS", artifact_root), \
                    patch.object(runner, "make_freeze", return_value=self.sample_freeze()):
                runner.prepare()
                runner.write_json(artifact_root / "queue_state.json", queue)
                returned = runner.prepare()
                self.assertEqual(returned["created_at"], "initial")
                self.assertEqual(json.loads((artifact_root / "queue_state.json").read_text()), queue)

    def test_prepare_rejects_changed_inputs_without_mutating_freeze_or_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact_root = Path(directory)
            original = self.sample_freeze()
            with patch.object(runner, "ROOT", artifact_root), \
                    patch.object(runner, "ARTIFACTS", artifact_root), \
                    patch.object(runner, "make_freeze", return_value=original):
                runner.prepare()
            freeze_bytes = (artifact_root / "freeze.json").read_bytes()
            queue_bytes = (artifact_root / "queue_state.json").read_bytes()
            changed = self.sample_freeze(revision=2)
            with patch.object(runner, "ROOT", artifact_root), \
                    patch.object(runner, "ARTIFACTS", artifact_root), \
                    patch.object(runner, "make_freeze", return_value=changed):
                with self.assertRaisesRegex(RuntimeError, "immutable"):
                    runner.prepare()
            self.assertEqual((artifact_root / "freeze.json").read_bytes(), freeze_bytes)
            self.assertEqual((artifact_root / "queue_state.json").read_bytes(), queue_bytes)

    def test_prepare_refuses_orphan_queue_instead_of_resetting_or_adopting_it(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact_root = Path(directory)
            artifact_root.mkdir(exist_ok=True)
            queue = artifact_root / "queue_state.json"
            queue.write_text('{"status":"running"}', encoding="utf-8")
            with patch.object(runner, "ARTIFACTS", artifact_root), \
                    patch.object(runner, "make_freeze", return_value=self.sample_freeze()):
                with self.assertRaisesRegex(RuntimeError, "Queue state exists without a freeze"):
                    runner.prepare()
            self.assertEqual(queue.read_text(encoding="utf-8"), '{"status":"running"}')

    def _make_valid_cell_artifacts(self, folder: Path, cell: dict) -> dict:
        order = runner.build_task_order(cell["seed"])
        subset = runner.build_test_subset()
        folder.mkdir(parents=True)
        (folder / "config.json").write_text(json.dumps({
            "train_size": 20, "batch_size": 20, "num_epochs": 4,
            "steps_per_epoch": 1, "eval_test": False, "test_env_num": 120,
            "final_selection": True, "cam_enabled": cell["method"] == "cam_v3",
        }), encoding="utf-8")
        history = [{"step": step, "action": "skip_no_patches", "cam_gate_used": False}
                   for step in range(1, 5)]
        (folder / "history.json").write_text(json.dumps(history), encoding="utf-8")
        (folder / "summary.json").write_text(json.dumps({
            "status": "completed", "total_steps": 4, "total_accepts": 0,
            "total_soft_accepts": 0, "total_rejects": 0, "total_skips": 4,
            "test_subset_ids_sha256": subset["ids_sha256"], "test_subset_size": 120,
            "test_hard": 0.0, "final_selection_hard": 0.0,
        }), encoding="utf-8")
        (folder / "runtime_state.json").write_text(json.dumps({"last_completed_step": 4}),
                                                   encoding="utf-8")
        skills = folder / "skills"
        skills.mkdir()
        for step in range(5):
            (skills / f"skill_v{step:04d}.md").write_text("unchanged skill\n", encoding="utf-8")
        for batch in order["batches"]:
            rollout = folder / "steps" / f"step_{batch['global_step']:04d}" / "rollout"
            rollout.mkdir(parents=True)
            rows = [{"id": task_id, "task_type": batch["task_type"]}
                    for task_id in batch["task_ids"]]
            (rollout / "results.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        test_dir = folder / "test_eval"
        test_dir.mkdir()
        test_source = runner.read_items(runner.ROOT / runner.SPLIT / "test" / "items.json")
        test_by_id = {str(row["id"]): row for row in test_source}
        test_rows = []
        for task_id in subset["task_ids"]:
            item = test_by_id[task_id]
            task_type = ("cell_level" if item["instruction_type"] == runner.TYPE_ORDER[0]
                         else "sheet_level")
            test_rows.append({"id": task_id, "task_type": task_type, "hard": 0,
                              "status": "completed"})
        (test_dir / "results.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in test_rows), encoding="utf-8")
        (test_dir / "summary.json").write_text(json.dumps({
            "test_hard": 0.0, "test_subset_size": 120,
            "task_ids_sha256": subset["ids_sha256"],
        }), encoding="utf-8")
        return {"task_orders": {cell["cell_id"]: order}, "test_subset": subset,
                "configs": {cell["cell_id"]: {}}}

    def test_cell_validator_accepts_certified_noop_skips_and_checks_actual_training_rows(self):
        for cell in (runner.cells()[0], runner.cells()[1]):
            with self.subTest(cell=cell["cell_id"]), tempfile.TemporaryDirectory() as directory:
                folder = Path(directory) / "cell"
                freeze = self._make_valid_cell_artifacts(folder, cell)
                with patch.object(runner, "verify_existing_cell", return_value=None), \
                        patch.object(runner, "ARTIFACTS", Path(directory) / "artifacts"):
                    accepted, reason = runner.validate_complete_cell(cell, freeze, folder)
                    self.assertTrue(accepted, reason)
                    rollout_path = folder / "steps" / "step_0001" / "rollout" / "results.jsonl"
                    rows = [json.loads(line) for line in rollout_path.read_text().splitlines()]
                    rollout_path.write_text("".join(json.dumps(row) + "\n" for row in reversed(rows)),
                                            encoding="utf-8")
                    accepted, reason = runner.validate_complete_cell(cell, freeze, folder)
                    self.assertTrue(accepted, reason)
                    valid_rows = list(reversed(rows))
                    wrong_id_rows = [dict(row) for row in valid_rows]
                    wrong_id_rows[0]["id"] = "wrong-task-id"
                    rollout_path.write_text("".join(json.dumps(row) + "\n" for row in wrong_id_rows),
                                            encoding="utf-8")
                    accepted, reason = runner.validate_complete_cell(cell, freeze, folder)
                    self.assertFalse(accepted)
                    self.assertIn("rollout IDs", reason)
                    duplicate_rows = [dict(row) for row in valid_rows]
                    duplicate_rows[1]["id"] = duplicate_rows[0]["id"]
                    rollout_path.write_text("".join(json.dumps(row) + "\n" for row in duplicate_rows),
                                            encoding="utf-8")
                    accepted, reason = runner.validate_complete_cell(cell, freeze, folder)
                    self.assertFalse(accepted)
                    self.assertIn("rollout IDs", reason)

    def test_auth_proof_must_match_both_frozen_role_tuples_and_contains_no_secrets(self):
        from scripts.probe_cam_v3_auth import ROLES

        configs = {cell["cell_id"]: runner.config_for(cell) for cell in runner.cells()}
        expected = {role: runner._role_config(next(iter(configs.values())), role)
                    for role in ("optimizer", "target")}
        roles = {}
        for role in ("optimizer", "target"):
            source_var = ROLES[role]["sources"][0]
            source = f"project_dotenv:{source_var}"
            roles[role] = {
                "role": role, **expected[role], "status": "ok",
                "working_credential_source": source,
                "attempts": [{"role": role, **expected[role], "status": 200,
                              "credential_source": source}],
            }
        report = {
            "schema_version": 1, "config": runner.CONFIG,
            "config_sha256": hashlib.sha256((runner.ROOT / runner.CONFIG).read_bytes()).hexdigest(),
            "observed_at_utc": runner.now(), "timestamp_source": "probe_execution",
            "roles": roles,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "proof.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            proof = runner.validate_auth_report(path, configs)
            serialized = json.dumps(proof)
            self.assertIn("credential_source", serialized)
            self.assertNotIn("not-a-secret-value", serialized)

            report["roles"]["target"]["attempts"][-1]["status"] = 401
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "HTTP 200"):
                runner.validate_auth_report(path, configs)


if __name__ == "__main__":
    unittest.main()
