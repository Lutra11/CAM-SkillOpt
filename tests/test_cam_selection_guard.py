"""Pure offline CAM selection evidence and state-transition regression tests."""
from dataclasses import asdict
import hashlib
import unittest
from unittest.mock import patch

from skillopt.cam.bootstrap_gate import paired_bootstrap_gate
from skillopt.cam.selection_guard import SelectionEvidenceError, decide_cam_update, indexed_scores


def rows(scores, *, ids=None, soft=None):
    ids = ids or [str(index) for index in range(len(scores))]
    return [{"id": item_id, "hard": hard, "soft": soft[index] if soft is not None else hard,
             "split": "valid_seen", "status": "completed"}
            for index, (item_id, hard) in enumerate(zip(ids, scores))]


class IndexedSelectionScoresTests(unittest.TestCase):
    def test_reorders_by_normalized_id_and_preserves_scores(self):
        self.assertEqual(indexed_scores(rows([0.8, 0.3], ids=[2, "1"])), {"1": 0.3, "2": 0.8})

    def test_missing_empty_duplicate_or_invalid_ids_rejected(self):
        cases = [[], [{"hard": 0}], [{"id": " ", "hard": 0}], [{"id": None, "hard": 0}],
                 [{"id": True, "hard": 0}], rows([0, 1], ids=[1, "1"]),
                 rows([0, 1], ids=["A", " A "])]
        for evidence in cases:
            with self.subTest(evidence=evidence), self.assertRaises(SelectionEvidenceError):
                indexed_scores(evidence)

    def test_missing_none_nan_infinite_out_of_range_and_boolean_scores_rejected(self):
        for invalid in (None, float("nan"), float("inf"), -0.1, 1.1, True, "0.5"):
            with self.subTest(invalid=invalid), self.assertRaises(SelectionEvidenceError):
                indexed_scores([{"id": "A", "hard": invalid}])
        with self.assertRaises(SelectionEvidenceError):
            indexed_scores([{"id": "A", "soft": 0.5}])

    def test_soft_and_mixed_use_selected_fields_with_soft_weight(self):
        evidence = rows([0, 1], soft=[0.8, 0.2])
        self.assertEqual(indexed_scores(evidence, metric="soft"), {"0": 0.8, "1": 0.2})
        mixed = indexed_scores(evidence, metric="mixed", mixed_weight=0.25)
        self.assertAlmostEqual(mixed["0"], 0.2)
        self.assertAlmostEqual(mixed["1"], 0.8)
        self.assertEqual(indexed_scores([{"id": "A", "soft": 0.5}], metric="soft"), {"A": 0.5})
        with self.assertRaises(SelectionEvidenceError):
            indexed_scores([{"id": "A", "hard": 0}], metric="mixed")

    def test_mixed_weight_is_validated_not_clamped(self):
        for weight in (-0.1, 1.1, float("nan"), None, True):
            with self.subTest(weight=weight), self.assertRaises(SelectionEvidenceError):
                indexed_scores(rows([0]), metric="mixed", mixed_weight=weight)

    def test_training_test_conflicting_or_invalid_split_declarations_rejected(self):
        for field in ("split", "dataset_split", "source_split"):
            for split in ("train", "test", "valid_unseen", "", None):
                with self.subTest(field=field, split=split), self.assertRaises(SelectionEvidenceError):
                    indexed_scores([{**rows([0])[0], field: split}])
        self.assertEqual(indexed_scores([{"id": "A", "hard": 0}]), {"A": 0.0})

    def test_historical_infrastructure_zero_cannot_enter_gate(self):
        cases = [dict(status="invalid_auth"), dict(status="infra_error"), dict(status="running"),
                 dict(infra_error={"failure_type": "auth_error"}), dict(failure_type="network_error"),
                 dict(error="403 Forbidden"), dict(fail_reason="401 invalid_refresh_token")]
        for extra in cases:
            with self.subTest(extra=extra), self.assertRaises(SelectionEvidenceError):
                indexed_scores([{**rows([0])[0], **extra}])

    def test_scored_code_failure_remains_valid_zero(self):
        evidence = [{**rows([0])[0], "failure_type": "code_syntax_error", "fail_reason": "SyntaxError: invalid syntax"}]
        self.assertEqual(indexed_scores(evidence), {"0": 0.0})


class CAMSelectionGuardTests(unittest.TestCase):
    def decide(self, current, candidate, best=None, **overrides):
        arguments = dict(candidate_skill="candidate", current_skill="current", best_skill="current" if best is None else "best",
                         current_results=rows(current), candidate_results=rows(candidate),
                         best_results=rows(current if best is None else best), best_step=0, global_step=2,
                         cfg={"cam_bootstrap_samples": 500, "cam_seed": 42})
        arguments.update(overrides)
        return decide_cam_update(**arguments)

    def test_pairing_is_by_id_not_candidate_list_position(self):
        current = rows([0.1, 0.8], ids=["A", "B"])
        candidate = rows([1.0, 0.3], ids=["B", "A"])
        with patch("skillopt.cam.selection_guard.paired_bootstrap_gate", wraps=paired_bootstrap_gate) as compare:
            gate, audit = self.decide([0.1, 0.8], [0.3, 1.0], current_results=current, candidate_results=candidate, best_results=current)
        self.assertEqual(gate.action, "accept_new_best")
        self.assertEqual(compare.call_args.args, ([0.1, 0.8], [0.3, 1.0]))
        self.assertEqual(audit["pair_ids"], ["A", "B"])
        self.assertAlmostEqual(gate.current_score, 0.65)

    def test_mismatched_current_candidate_or_best_ids_rejected(self):
        for key in ("candidate_results", "best_results"):
            with self.subTest(key=key), self.assertRaises(SelectionEvidenceError):
                self.decide([0, 0], [1, 1], **{key: rows([0, 0], ids=["0", "different"])})

    def test_accepted_initial_best_reuses_current_gate_once(self):
        with patch("skillopt.cam.selection_guard.paired_bootstrap_gate", wraps=paired_bootstrap_gate) as compare:
            gate, audit = self.decide([0.1] * 4, [0.7] * 4)
        self.assertEqual(compare.call_count, 1)
        self.assertEqual(gate.action, "accept_new_best")
        self.assertEqual(gate.current_skill, "candidate")
        self.assertEqual(gate.best_skill, "candidate")
        self.assertEqual(gate.best_step, 2)
        self.assertTrue(audit["promotion_authorized"])
        self.assertEqual(audit["best_comparison"]["status"], "reused_current_comparison")

    def test_reject_preserves_both_states(self):
        gate, audit = self.decide([0.8] * 4, [0.1] * 4, best=[1] * 4)
        self.assertEqual(gate.action, "reject")
        self.assertEqual((gate.current_skill, gate.best_skill, gate.best_step), ("current", "best", 0))
        self.assertEqual((gate.current_score, gate.best_score), (0.8, 1.0))
        self.assertFalse(audit["promotion_authorized"])

    def test_uncertain_keeps_states_and_never_retries_same_pairs(self):
        with patch("skillopt.cam.selection_guard.paired_bootstrap_gate", wraps=paired_bootstrap_gate) as compare:
            gate, audit = self.decide([0, 1, 0, 1], [1, 0, 1, 0])
        self.assertEqual(compare.call_count, 1)
        self.assertEqual(gate.action, "cam_re_evaluate")
        self.assertEqual((gate.current_skill, gate.best_skill), ("current", "current"))
        self.assertTrue(audit["pending_additional_paired_evidence"])
        self.assertFalse(audit["automatic_retry"])

    def test_independent_best_uncertainty_blocks_promotion_but_not_accepted_current(self):
        with patch("skillopt.cam.selection_guard.paired_bootstrap_gate", wraps=paired_bootstrap_gate) as compare:
            gate, audit = self.decide([0] * 4, [0.6] * 4, best=[0, 1, 0, 1])
        self.assertEqual(compare.call_count, 2)
        self.assertEqual(gate.action, "accept")
        self.assertEqual(gate.current_skill, "candidate")
        self.assertEqual(gate.best_skill, "best")
        self.assertEqual(gate.best_score, 0.5)
        self.assertEqual(audit["best_comparison"]["decision"]["action"], "re_evaluate")
        self.assertFalse(audit["promotion_authorized"])
        self.assertTrue(audit["pending_additional_paired_evidence"])

    def test_independent_best_accept_authorizes_promotion(self):
        gate, audit = self.decide([0] * 4, [0.8] * 4, best=[0.4] * 4)
        self.assertEqual(gate.action, "accept_new_best")
        self.assertTrue(audit["best_comparison"]["performed"])
        self.assertEqual(audit["best_comparison"]["decision"]["action"], "accept")
        self.assertTrue(audit["promotion_authorized"])

    def test_independent_best_reject_cannot_promote_higher_aggregate_candidate(self):
        # A single deterministic draw deliberately exercises the rejection
        # branch; this is a control-flow test, not a recommended CI budget.
        gate, audit = self.decide([0] * 4, [0.6] * 4, best=[0.7, 0.7, 0.7, 0],
                                  cfg={"cam_bootstrap_samples": 1, "cam_seed": 42})
        self.assertGreater(audit["candidate_score"], audit["best_score"])
        self.assertEqual(audit["best_comparison"]["decision"]["action"], "reject")
        self.assertEqual(gate.action, "accept")
        self.assertEqual(gate.best_skill, "best")
        self.assertFalse(audit["promotion_authorized"])

    def test_candidate_not_better_than_best_skips_unnecessary_best_comparison(self):
        with patch("skillopt.cam.selection_guard.paired_bootstrap_gate", wraps=paired_bootstrap_gate) as compare:
            gate, audit = self.decide([0] * 4, [0.5] * 4, best=[1] * 4)
        self.assertEqual(compare.call_count, 1)
        self.assertEqual(gate.action, "accept")
        self.assertEqual(gate.best_skill, "best")
        self.assertFalse(audit["promotion_authorized"])

    def test_meaningful_improvement_threshold_and_confidence_cfg_reach_real_gate(self):
        cfg = {"cam_meaningful_improvement": 0.2, "cam_confidence_level": 0.9,
               "cam_bootstrap_samples": 200, "cam_seed": 9}
        gate, audit = self.decide([0.4] * 4, [0.5] * 4, cfg=cfg)
        self.assertEqual(gate.action, "cam_re_evaluate")
        self.assertEqual(audit["meaningful_improvement"], 0.2)
        self.assertEqual(audit["confidence_level"], 0.9)
        self.assertEqual(audit["bootstrap_samples"], 200)

    def test_soft_and_mixed_decisions_derive_returned_scores(self):
        before, after = rows([0] * 4, soft=[0.2] * 4), rows([0] * 4, soft=[0.8] * 4)
        for metric, expected in (("soft", 0.8), ("mixed", 0.2)):
            with self.subTest(metric=metric):
                gate, _audit = self.decide([0] * 4, [0] * 4, current_results=before, candidate_results=after,
                                          best_results=before, metric=metric, mixed_weight=0.25)
                self.assertEqual(gate.action, "accept_new_best")
                self.assertAlmostEqual(gate.best_score, expected)

    def test_audit_preserves_cam_fields_and_fingerprints_without_skill_text(self):
        gate, audit = self.decide([0] * 4, [1] * 4)
        original = asdict(paired_bootstrap_gate([0] * 4, [1] * 4, bootstrap_samples=500, seed=42))
        self.assertEqual({key: audit[key] for key in original}, original)
        expected_hash = hashlib.sha256(b"candidate").hexdigest()
        self.assertEqual(audit["candidate_skill_sha256"], expected_hash)
        self.assertEqual(audit["candidate_hash"], expected_hash[:16])
        self.assertEqual(audit["result_action"], gate.action)

    def test_same_skill_with_inconsistent_recorded_scores_is_invalid(self):
        with self.assertRaises(SelectionEvidenceError):
            self.decide([0] * 4, [1] * 4, best_results=rows([0.5] * 4))

    def test_invalid_bootstrap_configuration_is_not_silently_corrected(self):
        for config in ({"cam_meaningful_improvement": -0.1}, {"cam_confidence_level": float("nan")},
                       {"cam_bootstrap_samples": 0}, {"cam_bootstrap_samples": True}, {"cam_seed": 1.5}):
            with self.subTest(cfg=config), self.assertRaises(SelectionEvidenceError):
                self.decide([0] * 4, [1] * 4, cfg=config)


if __name__ == "__main__":
    unittest.main()
