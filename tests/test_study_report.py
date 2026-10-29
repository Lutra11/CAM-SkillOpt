import json
from pathlib import Path
import tempfile
import unittest

from scripts.report_study import build_report, holm_adjust, holm_adjust_family, paired_effect


class StudyReportStatsTests(unittest.TestCase):
    def test_paired_effect_uses_matching_items_and_b_minus_a(self):
        a = {"x": {"hard": 1}, "y": {"hard": 0}, "z": {"hard": 0}, "w": {"hard": 1}}
        b = {"x": {"hard": 0}, "y": {"hard": 1}, "z": {"hard": 0}, "w": {"hard": 1}}
        effect = paired_effect(a, b, n_boot=100, seed=7)
        self.assertEqual(effect["n_items"], 4)
        self.assertEqual(effect["delta_b_minus_a"], 0.0)
        self.assertEqual(effect["only_a_correct"], 1)
        self.assertEqual(effect["only_b_correct"], 1)
        self.assertEqual(effect["mcnemar_exact_p_descriptive"], 1.0)
        self.assertLessEqual(effect["task_item_bootstrap_95_ci"][0], 0.0)
        self.assertGreaterEqual(effect["task_item_bootstrap_95_ci"][1], 0.0)

    def test_paired_effect_rejects_mismatched_test_ids(self):
        with self.assertRaisesRegex(ValueError, "identical test item IDs"):
            paired_effect({"a": {"hard": 1}}, {"b": {"hard": 0}}, n_boot=10)

    def test_holm_adjustment_preserves_input_order_and_monotonicity(self):
        self.assertEqual(holm_adjust([0.01, 0.04, 0.03]), [0.03, 0.06, 0.06])
        self.assertEqual(holm_adjust([]), [])

    def test_incomplete_holm_family_does_not_shrink_the_planned_family(self):
        names = ("cam_no_bootstrap", "cam_no_memory", "cam_no_adaptive_budget")
        adjusted = holm_adjust_family({"cam_no_bootstrap": 0.01, "cam_no_memory": 0.04}, names)
        self.assertEqual(adjusted, {name: None for name in names})
        complete = holm_adjust_family(dict(zip(names, [0.01, 0.04, 0.03])), names)
        self.assertEqual(complete, {"cam_no_bootstrap": 0.03, "cam_no_memory": 0.06,
                                    "cam_no_adaptive_budget": 0.06})


class IncompleteStudyReportTests(unittest.TestCase):
    def test_incomplete_cells_are_listed_without_effect_claims(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = build_report(root)
            self.assertEqual(report["status"], "incomplete")
            self.assertEqual(len(report["cells"]), 9)
            self.assertTrue(all(not cell["eligible"] for cell in report["cells"]))
            self.assertEqual(report["analysis"]["primary"]["training_seed_count"], 0)
            self.assertTrue(all(not row["available"] for row in report["analysis"]["primary"]["per_seed"]))
            self.assertTrue((root / "reports/v2r1/paired_results.json").is_file())
            markdown = (root / "reports/v2r1/EXPERIMENT_RESULTS.md").read_text(encoding="utf-8")
            self.assertIn("Study status: **incomplete**", markdown)
            self.assertIn("Across-seed effect unavailable", markdown)
            self.assertIn("three paired training seeds", markdown)

    def test_invalid_or_missing_queue_data_does_not_prevent_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue_path = root / "reports/v2r1/queue_state.json"
            queue_path.parent.mkdir(parents=True)
            queue_path.write_text("{truncated", encoding="utf-8")
            report = build_report(root)
            self.assertEqual(report["status"], "incomplete")
            self.assertTrue(all(cell["queue_status"] is None for cell in report["cells"]))


if __name__ == "__main__":
    unittest.main()
