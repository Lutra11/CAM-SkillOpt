"""Offline regressions for final experiment-cell completeness checks."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from scripts.experiment_integrity import load_rows, phase_rows, validate_cell
from scripts.run_cell_robust import record_attempt_cost
from skillopt.utils.run_lock import run_lock


class ExperimentIntegrityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cell = self.root / "cell"
        self.cell.mkdir()
        self.config = {
            "num_epochs": 1, "train_size": 2, "batch_size": 2,
            "cam_enabled": True, "cam_bootstrap_gate": False,
            "cam_memory_enabled": False, "eval_test": False,
        }
        self.history = [{"step": 1, "action": "soft_accept"}]
        self.summary = {
            "status": "complete", "total_steps": 1, "total_accepts": 0,
            "total_soft_accepts": 1, "total_rejects": 0,
        }
        self.write_json("config.json", self.config)
        self.write_json("history.json", self.history)
        self.write_json("summary.json", self.summary)

    def write_json(self, name, value):
        path = self.cell / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def write_rows(self, relative_path, rows):
        path = self.cell / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        return path

    def test_soft_accept_is_counted_as_an_evaluated_update(self):
        self.assertEqual(validate_cell(self.cell), (True,
            "complete; steps, actions, test IDs and scores verified"))
        self.write_json("summary.json", {**self.summary, "total_soft_accepts": 0})
        complete, reason = validate_cell(self.cell)
        self.assertFalse(complete)
        self.assertIn("total_soft_accepts", reason)

    def test_nonbootstrap_cam_cell_rejects_bootstrap_artifacts(self):
        self.assertTrue(validate_cell(self.cell)[0])
        self.write_json("cam_gate_events.json", [{"action": "accept"}])
        complete, reason = validate_cell(self.cell)
        self.assertFalse(complete)
        self.assertIn("Non-bootstrap arm", reason)

    def test_duplicate_and_missing_result_ids_are_rejected(self):
        results = self.write_rows("test_eval_baseline/results.jsonl", [
            {"id": "same", "hard": 0}, {"id": "same", "hard": 1},
        ])
        with self.assertRaisesRegex(ValueError, "Duplicate ID"):
            load_rows(results)

        self.config.update(eval_test=True, test_env_num=1)
        self.summary.update(baseline_test_hard=0.0, test_hard=0.0, final_test_hard=0.0)
        self.write_json("config.json", self.config)
        self.write_json("summary.json", self.summary)
        self.write_rows("test_eval_baseline/results.jsonl", [{"hard": 0}])
        self.write_rows("test_eval/results.jsonl", [{"id": "test-0", "hard": 0}])
        self.write_rows("test_eval_final/results.jsonl", [{"id": "test-0", "hard": 0}])
        complete, reason = validate_cell(self.cell)
        self.assertFalse(complete)
        self.assertIn("id", reason)

    def test_final_test_reuses_test_rows_only_for_identical_skill_bytes(self):
        self.config.update(eval_test=True, test_env_num=2)
        self.summary.update(baseline_test_hard=0.5, test_hard=1.0, final_test_hard=1.0)
        self.write_json("config.json", self.config)
        self.write_json("summary.json", self.summary)
        rows = [{"id": "test-a", "hard": 1}, {"id": "test-b", "hard": 1}]
        self.write_rows("test_eval_baseline/results.jsonl", [
            {"id": "test-a", "hard": 0}, {"id": "test-b", "hard": 1},
        ])
        test_path = self.write_rows("test_eval/results.jsonl", rows)
        current = self.root / "current.md"
        best = self.root / "best.md"
        current.write_bytes(b"same skill\n")
        best.write_bytes(b"same skill\n")
        self.write_json("runtime_state.json", {
            "current_skill_path": str(current), "best_skill_path": str(best),
        })

        self.assertEqual(phase_rows(self.cell, "test_eval_final"), {
            "test-a": rows[0], "test-b": rows[1],
        })
        self.assertTrue(validate_cell(self.cell)[0])
        best.write_bytes(b"different skill\n")
        complete, reason = validate_cell(self.cell)
        self.assertFalse(complete)
        self.assertIn("Final test rows missing", reason)
        self.assertTrue(test_path.exists())

    def test_test_eval_subsample_is_valid_only_when_phases_share_manifest_ids(self):
        split_dir = self.root / "split"
        items = [{"id": f"official-{index}"} for index in range(5)]
        path = split_dir / "test" / "items.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(items), encoding="utf-8")
        self.config.update(eval_test=True, test_env_num=2, split_dir=str(split_dir))
        self.summary.update(baseline_test_hard=0.5, test_hard=0.5, final_test_hard=0.5)
        self.write_json("config.json", self.config)
        self.write_json("summary.json", self.summary)
        valid_rows = [{"id": "official-1", "hard": 1}, {"id": "official-3", "hard": 0}]
        for phase in ("test_eval_baseline", "test_eval", "test_eval_final"):
            self.write_rows(f"{phase}/results.jsonl", valid_rows)
        self.assertTrue(validate_cell(self.cell)[0])

        self.write_rows("test_eval_final/results.jsonl", [
            {"id": "official-0", "hard": 1}, {"id": "official-3", "hard": 0},
        ])
        complete, reason = validate_cell(self.cell)
        self.assertFalse(complete)
        self.assertIn("test_eval_final", reason)

        self.write_rows("test_eval_final/results.jsonl", [
            {"id": "outside-manifest", "hard": 1}, {"id": "official-3", "hard": 0},
        ])
        complete, reason = validate_cell(self.cell)
        self.assertFalse(complete)
        self.assertIn("test_eval_final", reason)

    def test_attempt_cost_aggregates_returned_usage_and_records_missing_summary(self):
        first_summary = self.write_json("summary.json", {
            "token_summary": {"optimizer": {"input_tokens": 10, "output_tokens": 2}},
        })
        record_attempt_cost(self.cell, started=100.0, elapsed=1.5, exit_code=1,
                            prior_summary_mtime=0)
        first_mtime = first_summary.stat().st_mtime_ns

        self.write_json("summary.json", {
            "token_summary": {"optimizer": {"input_tokens": 7, "output_tokens": 3},
                              "target": {"input_tokens": 4}},
        })
        os.utime(first_summary, ns=(first_mtime + 1_000_000, first_mtime + 1_000_000))
        second_mtime = first_summary.stat().st_mtime_ns
        record_attempt_cost(self.cell, started=102.0, elapsed=2.5, exit_code=0,
                            prior_summary_mtime=first_mtime)

        summary_bytes = first_summary.read_bytes()
        record_attempt_cost(self.cell, started=105.0, elapsed=0.5, exit_code=2,
                            prior_summary_mtime=second_mtime)
        self.assertEqual(first_summary.read_bytes(), summary_bytes)
        attempts = [json.loads(line) for line in (self.cell / "attempts.jsonl").read_text(
            encoding="utf-8").splitlines()]
        cost = json.loads((self.cell / "cost_summary.json").read_text(encoding="utf-8"))
        self.assertEqual([attempt["usage_captured"] for attempt in attempts], [True, True, False])
        self.assertEqual(cost["attempts"], 3)
        self.assertEqual(cost["attempts_with_usage"], 2)
        self.assertEqual(cost["wall_time_s"], 4.5)
        self.assertEqual(cost["token_summary"], {
            "optimizer": {"input_tokens": 17, "output_tokens": 5},
            "target": {"input_tokens": 4},
        })

    def test_run_lock_denies_overlap_and_can_be_reacquired_after_release(self):
        path = self.cell / ".run.lock"
        with run_lock(path):
            with self.assertRaisesRegex(RuntimeError, "Another process owns"):
                with run_lock(path):
                    pass
        with run_lock(path):
            self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
