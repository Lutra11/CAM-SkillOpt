"""Opt-in noninferiority and task-type CAM selection safeguards."""
import unittest
from unittest.mock import patch

from skillopt.cam.bootstrap_gate import CAMGateDecision
from skillopt.cam.selection_guard import SelectionEvidenceError, decide_cam_update


def rows(scores, *, ids=None, kinds=None):
    ids = ids or [str(i) for i in range(len(scores))]
    kinds = kinds or [None] * len(scores)
    return [{"id": item_id, "hard": score, "soft": score, "split": "valid_seen",
             "status": "completed", **({"task_type": kind} if kind is not None else {})}
            for item_id, score, kind in zip(ids, scores, kinds)]


class CAMV3GateTests(unittest.TestCase):
    def decide(self, current, candidate, best=None, *, cfg=None, current_rows=None,
               candidate_rows=None, best_rows=None):
        best = current if best is None else best
        return decide_cam_update(
            candidate_skill="candidate", current_skill="current",
            best_skill="current" if best == current else "best",
            current_results=current_rows or rows(current),
            candidate_results=candidate_rows or rows(candidate),
            best_results=best_rows or rows(best), best_step=0, global_step=2,
            cfg={"cam_bootstrap_samples": 50, **(cfg or {})},
        )

    def test_absent_policy_keeps_legacy_v2_soft_accept(self):
        with patch("skillopt.cam.selection_guard.paired_bootstrap_gate",
                   return_value=CAMGateDecision("re_evaluate", 0, -0.2, 0.2, .95, 0, 4, 10, .5)):
            gate, audit = self.decide([.2, .8, .2, .8], [.8, .2, .8, .2])
        self.assertEqual(gate.action, "soft_accept")
        self.assertTrue(audit["soft_accept"])
        self.assertEqual(gate.best_skill, "current")

    def test_noninferiority_lcb_boundary_soft_accepts_without_best_promotion(self):
        decision = CAMGateDecision("re_evaluate", 0, -0.05, 0.05, .95, 0, 4, 10, .5)
        with patch("skillopt.cam.selection_guard.paired_bootstrap_gate", return_value=decision):
            gate, audit = self.decide([.2, .8, .2, .8], [.8, .2, .8, .2],
                                      cfg={"cam_soft_accept_policy": "noninferiority",
                                           "cam_noninferiority_margin": .05})
        self.assertEqual(gate.action, "soft_accept")
        self.assertEqual((gate.current_skill, gate.best_skill, gate.best_step), ("candidate", "current", 0))
        self.assertTrue(audit["noninferiority_passed"])
        self.assertFalse(audit["promotion_authorized"])

    def test_noninferiority_failure_holds_states_with_audited_reason(self):
        decision = CAMGateDecision("re_evaluate", -.1, -.1, .02, .95, 0, 4, 10, .5)
        with patch("skillopt.cam.selection_guard.paired_bootstrap_gate", return_value=decision):
            gate, audit = self.decide([.2, .8, .2, .8], [.8, .2, .8, .2],
                                      cfg={"cam_soft_accept_policy": "noninferiority",
                                           "cam_noninferiority_margin": .05})
        self.assertEqual(gate.action, "cam_re_evaluate")
        self.assertEqual((gate.current_skill, gate.best_skill), ("current", "current"))
        self.assertEqual(audit["reason"], "noninferiority_lcb_below_negative_margin")
        self.assertFalse(audit["promotion_authorized"])

    def test_v3_config_margin_requires_finite_unit_interval(self):
        for margin in (-.01, 1.01, float("nan"), float("inf"), True):
            with self.subTest(margin=margin), self.assertRaises(SelectionEvidenceError):
                self.decide([0, 1], [1, 0], cfg={"cam_soft_accept_policy": "noninferiority",
                                                  "cam_noninferiority_margin": margin})

    def test_group_guard_blocks_aggregate_accept_on_descriptive_regression(self):
        ids = [f"c{i}" for i in range(5)] + [f"s{i}" for i in range(5)]
        kinds = ["cell_level"] * 5 + ["sheet_level"] * 5
        current = [.2] * 10
        candidate = [.9] * 5 + [.0] * 5
        before, after = rows(current, ids=ids, kinds=kinds), rows(candidate, ids=ids, kinds=kinds)
        gate, audit = self.decide(current, candidate, current_rows=before, candidate_rows=after,
                                  best_rows=before,
                                  cfg={"cam_group_guard": True, "cam_group_max_regression": .1,
                                       "cam_group_min_pairs": 5})
        self.assertEqual(gate.action, "cam_re_evaluate")
        self.assertEqual((gate.current_skill, gate.best_skill), ("current", "current"))
        self.assertEqual(audit["reason"], "group_guard:sheet_level_current_mean_regression_exceeds_tolerance")
        self.assertAlmostEqual(audit["per_task_type"]["groups"]["cell_level"]["candidate_minus_current_mean"], .7)
        self.assertAlmostEqual(audit["per_task_type"]["groups"]["sheet_level"]["candidate_minus_current_mean"], -.2)
        self.assertEqual(audit["group_guard"]["interpretation"],
                         "descriptive_mean_tolerance_not_a_confidence_guarantee")

    def test_group_guard_fails_closed_for_missing_and_small_group_metadata(self):
        gate, audit = self.decide([0] * 4, [1] * 4,
                                  cfg={"cam_group_guard": True, "cam_group_min_pairs": 2})
        self.assertEqual(gate.action, "cam_re_evaluate")
        self.assertIn(audit["group_guard"]["reason"], {
            "missing_or_inconsistent_task_type_metadata", "task_type_groups_incomplete",
            "cell_level_insufficient_pairs", "sheet_level_insufficient_pairs"})

    def test_inconsistent_pair_metadata_fails_closed_when_guard_enabled(self):
        before = rows([0] * 4, kinds=["cell_level"] * 4)
        after = rows([1] * 4, kinds=["sheet_level"] * 4)
        gate, audit = self.decide([0] * 4, [1] * 4, current_rows=before,
                                  candidate_rows=after, best_rows=before,
                                  cfg={"cam_group_guard": True, "cam_group_min_pairs": 1})
        self.assertEqual(gate.action, "cam_re_evaluate")
        self.assertEqual(audit["group_guard"]["reason"], "missing_or_inconsistent_task_type_metadata")

    def test_id_task_type_mapping_supplies_canonical_groups_for_audit(self):
        ids = [f"c{i}" for i in range(5)] + [f"s{i}" for i in range(5)]
        mapped_rows = rows([.2] * 10, ids=ids)
        mapping = {item_id: ("cell_level" if item_id.startswith("c") else "sheet_level")
                   for item_id in ids}
        gate, audit = self.decide([.2] * 10, [.3] * 10, current_rows=mapped_rows,
                                  candidate_rows=rows([.3] * 10, ids=ids), best_rows=mapped_rows,
                                  cfg={"cam_group_guard": True, "cam_group_min_pairs": 5,
                                       "cam_task_type_by_id": mapping})
        self.assertEqual(gate.action, "accept_new_best")
        self.assertEqual(audit["per_task_type"]["status"], "complete")
        self.assertEqual(set(audit["per_task_type"]["groups"]), {"cell_level", "sheet_level"})

    def test_instruction_type_human_names_map_to_canonical_group_types(self):
        ids = [f"c{i}" for i in range(5)] + [f"s{i}" for i in range(5)]
        aliases = ["cell-level manipulation"] * 5 + ["sheet-level manipulation"] * 5

        def evidence(scores):
            return [{**row, "instruction_type": alias}
                    for row, alias in zip(rows(scores, ids=ids), aliases)]

        current, candidate, best = evidence([.2] * 10), evidence([.3] * 10), evidence([.2] * 10)
        gate, audit = self.decide([.2] * 10, [.3] * 10,
                                  current_rows=current, candidate_rows=candidate, best_rows=best,
                                  cfg={"cam_group_guard": True, "cam_group_min_pairs": 5})
        self.assertEqual(gate.action, "accept_new_best")
        self.assertEqual(audit["per_task_type"]["status"], "complete")
        groups = audit["per_task_type"]["groups"]
        self.assertEqual(groups["cell_level"]["n_pairs"], 5)
        self.assertEqual(groups["sheet_level"]["n_pairs"], 5)

    def test_best_promotion_checks_candidate_against_best_by_group(self):
        ids = [f"c{i}" for i in range(5)] + [f"s{i}" for i in range(5)]
        kinds = ["cell_level"] * 5 + ["sheet_level"] * 5
        current = [.1] * 10
        best = [.8] * 5 + [.3] * 5
        candidate = [.7] * 10
        before, after, best_evidence = (rows(values, ids=ids, kinds=kinds)
                                        for values in (current, candidate, best))
        accepted = CAMGateDecision("accept", .5, .2, .8, .95, 0, 10, 10, 1)
        with patch("skillopt.cam.selection_guard.paired_bootstrap_gate", return_value=accepted):
            gate, audit = self.decide(current, candidate, best, current_rows=before,
                                      candidate_rows=after, best_rows=best_evidence,
                                      cfg={"cam_group_guard": True, "cam_group_max_regression": .05,
                                           "cam_group_min_pairs": 5})
        self.assertEqual(gate.action, "accept")
        self.assertEqual(gate.current_skill, "candidate")
        self.assertEqual(gate.best_skill, "best")
        self.assertFalse(audit["promotion_authorized"])
        self.assertEqual(audit["best_comparison"]["group_guard_reason"],
                         "cell_level_best_mean_regression_exceeds_tolerance")


if __name__ == "__main__":
    unittest.main()
