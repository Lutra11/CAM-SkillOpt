import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.report_continuous_adaptation_v3 as reporter
from scripts.report_continuous_adaptation_v3 import analyze_study


def make_fixture(root: Path):
    ids = [f"test-{index:03d}" for index in range(120)]
    types = {task_id: ("cell_level" if index < 83 else "sheet_level")
             for index, task_id in enumerate(ids)}
    cells = []
    queue_cells = []
    for seed in (42, 43):
        for method in ("baseline", "cam_v3"):
            cell_id = f"{method}_seed{seed}"
            cells.append({"cell_id": cell_id, "method": method, "seed": seed,
                          "block": f"paired_seed{seed}",
                          "out_root": f"outputs/{cell_id}"})
            queue_cells.append({"cell_id": cell_id, "status": "accepted", "exit_code": 0,
                                "acceptance": "accepted by fixture", "started_at": "start",
                                "finished_at": "finish"})
            folder = root / f"outputs/{cell_id}"
            (folder / "test_eval").mkdir(parents=True)
            passes = {task_id: int(index < 40) for index, task_id in enumerate(ids)}
            if method == "cam_v3" and seed == 42:
                for index in range(40, 45):
                    passes[ids[index]] = 1
            rows = [{"id": task_id, "hard": passes[task_id], "task_type": types[task_id],
                     "status": "completed"} for task_id in ids]
            (folder / "test_eval" / "results.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            score = sum(passes.values()) / 120
            (folder / "summary.json").write_text(json.dumps({
                "test_hard": score, "test_subset_size": 120,
                "test_subset_ids_sha256": hashlib.sha256("\n".join(ids).encode()).hexdigest(),
                "token_summary": {"_total": {"total_tokens": 321, "prompt_tokens": 123,
                                               "completion_tokens": 198}},
                "token_accounting": {"coverage": "partial", "usage_complete": False},
                "total_wall_time_s": 17.5,
            }), encoding="utf-8")
            (folder / "test_eval" / "summary.json").write_text(json.dumps({
                "test_hard": score, "test_subset_size": 120,
                "task_ids_sha256": hashlib.sha256("\n".join(ids).encode()).hexdigest(),
            }), encoding="utf-8")
    freeze = {"study_id": "continuous_adaptation_v3", "cells": cells,
              "test_subset": {"task_ids": ids,
                              "ids_sha256": hashlib.sha256("\n".join(ids).encode()).hexdigest(),
                              "counts": {"cell_level": 83, "sheet_level": 37}}}
    queue = {"status": "complete", "started_at": "started", "finished_at": "finished",
             "cells": queue_cells}
    return freeze, queue, ids


def always_accept(_cell, _freeze, _folder):
    return True, "accepted fixture validator"


class ReportContinuousAdaptationV3Tests(unittest.TestCase):
    def test_missing_pair_has_no_performance_conclusion(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            freeze, queue, _ids = make_fixture(root)
            queue["cells"] = [row for row in queue["cells"] if row["cell_id"] != "cam_v3_seed43"]
            result = analyze_study(freeze, queue, workspace_root=root,
                                   validate_complete_cell=always_accept)
            pair = next(row for row in result["seed_pairs"] if row["seed"] == 43)
            self.assertEqual(result["status"], "awaiting_complete_seed_pairs")
            self.assertEqual(pair["status"], "incomplete_seed_pair")
            self.assertIsNone(pair["performance"])
            self.assertIsNone(result["seed_level_summary"])
            lone_cell = next(row for row in result["cells"] if row["cell_id"] == "baseline_seed43")
            self.assertIsNone(lone_cell["performance"])

    def test_id_mismatch_and_duplicate_rows_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            freeze, queue, ids = make_fixture(root)
            folder = root / "outputs/cam_v3_seed42/test_eval/results.jsonl"
            lines = folder.read_text(encoding="utf-8").splitlines()
            replacement = json.loads(lines[-1])
            replacement["id"] = "not-in-freeze"
            lines[-1] = json.dumps(replacement)
            folder.write_text("\n".join(lines) + "\n", encoding="utf-8")
            result = analyze_study(freeze, queue, workspace_root=root,
                                   validate_complete_cell=always_accept)
            cell = next(row for row in result["cells"] if row["cell_id"] == "cam_v3_seed42")
            self.assertEqual(cell["runner_validation"]["status"], "rejected")
            self.assertIn("task IDs do not match", cell["runner_validation"]["reason"])
            self.assertEqual(next(row for row in result["seed_pairs"] if row["seed"] == 42)["status"],
                             "incomplete_seed_pair")

            # Restore the complete ID set and duplicate one row to exercise duplicate rejection.
            fixture_root = Path(temp) / "duplicate-case"
            fixture_root.mkdir()
            duplicate_freeze, duplicate_queue, _ = make_fixture(fixture_root)
            duplicate_path = fixture_root / "outputs/cam_v3_seed42/test_eval/results.jsonl"
            duplicate_lines = duplicate_path.read_text(encoding="utf-8").splitlines()
            duplicate_lines[-1] = duplicate_lines[0]
            duplicate_path.write_text("\n".join(duplicate_lines) + "\n", encoding="utf-8")
            duplicate_result = analyze_study(duplicate_freeze, duplicate_queue,
                                             workspace_root=fixture_root,
                                             validate_complete_cell=always_accept)
            duplicate_cell = next(row for row in duplicate_result["cells"]
                                  if row["cell_id"] == "cam_v3_seed42")
            self.assertIn("duplicate task ID", duplicate_cell["runner_validation"]["reason"])

    def test_score_disagreement_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            freeze, queue, _ids = make_fixture(root)
            summary_path = root / "outputs/cam_v3_seed42/summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["test_hard"] += 0.1
            summary_path.write_text(json.dumps(summary), encoding="utf-8")
            result = analyze_study(freeze, queue, workspace_root=root,
                                   validate_complete_cell=always_accept)
            cell = next(row for row in result["cells"] if row["cell_id"] == "cam_v3_seed42")
            self.assertIn("score disagreement", cell["runner_validation"]["reason"])
            self.assertIsNone(next(row for row in result["seed_pairs"] if row["seed"] == 42)["performance"])

    def test_wrong_task_type_counts_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            freeze, queue, _ids = make_fixture(root)
            path = root / "outputs/cam_v3_seed42/test_eval/results.jsonl"
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            rows[82]["task_type"] = "sheet_level"
            path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            result = analyze_study(freeze, queue, workspace_root=root,
                                   validate_complete_cell=always_accept)
            cell = next(row for row in result["cells"] if row["cell_id"] == "cam_v3_seed42")
            self.assertIn("83/37 strata", cell["runner_validation"]["reason"])

    def test_failure_status_and_returned_usage_are_preserved_without_score(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            freeze, queue, _ids = make_fixture(root)
            failed = next(row for row in queue["cells"] if row["cell_id"] == "cam_v3_seed42")
            failed.update(status="failed", exit_code=1, acceptance="provider request failed",
                          failure_type="auth_error", wall_time_s=12.0)
            result = analyze_study(freeze, queue, workspace_root=root,
                                   validate_complete_cell=always_accept)
            cell = next(row for row in result["cells"] if row["cell_id"] == "cam_v3_seed42")
            self.assertEqual(cell["queue_status"], "failed")
            self.assertEqual(cell["queue_record"]["failure_type"], "auth_error")
            self.assertEqual(cell["returned_token_totals"]["total_tokens"], 321)
            self.assertEqual(cell["token_coverage"], "partial")
            self.assertEqual(cell["queue_wall_time_s"], 12.0)
            self.assertIsNone(cell["performance"])

    def test_accepted_complete_seed_pair_reports_passes_and_task_id_pairs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            freeze, queue, _ids = make_fixture(root)
            result = analyze_study(freeze, queue, workspace_root=root,
                                   validate_complete_cell=always_accept)
            self.assertEqual(result["status"], "complete_seed_pairs")
            self.assertEqual(result["complete_seed_pair_count"], 2)
            seed42 = next(row for row in result["seed_pairs"] if row["seed"] == 42)
            metrics = seed42["performance"]
            self.assertEqual(metrics["baseline_hard_passes"], 40)
            self.assertEqual(metrics["cam_v3_hard_passes"], 45)
            self.assertEqual(metrics["cam_v3_minus_baseline_passes"], 5)
            self.assertEqual((metrics["paired_task_id_wins"], metrics["paired_task_id_losses"],
                              metrics["paired_task_id_ties"]), (5, 0, 115))
            self.assertEqual(metrics["per_type"]["cell_level"]["cam_v3_passes"], 45)
            self.assertEqual(metrics["per_type"]["sheet_level"]["cam_v3_passes"], 0)
            self.assertEqual(metrics["paired_task_bootstrap"]["draws"], 1000)
            self.assertEqual(result["seed_level_summary"]["n_training_seed_pairs"], 2)
            self.assertIn("no contemporaneous online v2 arm", result["primary_online_contrast"])

    def test_main_reads_canonical_study_queue_without_mutating_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            freeze, queue, _ids = make_fixture(root)
            study_root = root / "outputs" / "studies" / "continuous_adaptation_v3"
            study_root.mkdir(parents=True)
            (study_root / "freeze.json").write_text(json.dumps(freeze), encoding="utf-8")
            queue_path = study_root / "queue_state.json"
            queue_path.write_text(json.dumps(queue), encoding="utf-8")
            original_queue_bytes = queue_path.read_bytes()
            runner_path = root / "run_continuous_adaptation_v3.py"
            runner_path.write_text("# fixture runner\n", encoding="utf-8")
            report_root = root / "reports" / "continuous_adaptation_v3"
            argv = ["report_continuous_adaptation_v3.py", "--study-root", str(study_root),
                    "--runner", str(runner_path)]
            with patch.object(reporter, "ROOT", root), \
                 patch.object(reporter, "REPORT_ROOT", report_root), \
                 patch.object(reporter, "load_runner_validator", return_value=always_accept), \
                 patch("sys.argv", argv):
                self.assertEqual(reporter.main(), 0)
            self.assertEqual(queue_path.read_bytes(), original_queue_bytes)
            written = json.loads((report_root / "results.json").read_text(encoding="utf-8"))
            self.assertEqual(written["status"], "complete_seed_pairs")
            self.assertEqual(written["queue_status"]["status"], "complete")
            self.assertTrue((report_root / "report.md").is_file())


if __name__ == "__main__":
    unittest.main()
