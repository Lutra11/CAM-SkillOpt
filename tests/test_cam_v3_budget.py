from dataclasses import asdict
import unittest

from skillopt.cam.adaptive_budget import (
    compute_adaptive_budget,
    compute_evidence_budget,
    estimate_failure_confidence,
)


def edit(target, content, **extra):
    return {"op": "replace", "target": target, "content": content, **extra}


class EvidenceBudgetTests(unittest.TestCase):
    def test_distinct_task_evidence_controls_candidate_eligibility(self):
        candidates = [
            edit("heading A", "A2", support_task_ids=["task-1"]),
            edit("heading B", "B2", support_task_ids=["task-2", "task-3"]),
        ]
        one = compute_evidence_budget(candidates, total_failures=100)
        self.assertEqual(one.budget, 1)
        self.assertEqual(one.eligible_candidate_count, 1)
        self.assertEqual(one.eligible_candidates[0]["target"], "heading B")

        candidates[0]["support_task_ids"] = ["task-1", "task-4"]
        many = compute_evidence_budget(candidates, total_failures=100)
        self.assertEqual(many.budget, 2)
        self.assertEqual(many.eligible_candidate_count, 2)

    def test_generic_support_counts_do_not_count_as_provenance(self):
        decision = compute_evidence_budget(
            [edit("heading A", "A2", support_count=900)], total_failures=900
        )
        self.assertEqual(decision.budget, 1)
        self.assertTrue(decision.unknown_provenance)
        self.assertEqual(decision.reason, "provenance_unknown_minimum_fallback")
        self.assertEqual(decision.evidence_task_ids, [])

    def test_duplicate_edits_deduplicate_and_union_provenance(self):
        decision = compute_evidence_budget(
            [
                edit("heading A", "A2", support_task_ids=["task-1"]),
                edit("heading A", "A2", task_ids=["task-2"]),
                edit("heading B", "B2", evidence_task_ids=["task-3", "task-4"]),
            ],
            total_failures=4,
        )
        self.assertEqual(decision.duplicate_count, 1)
        self.assertEqual(decision.budget, 2)
        self.assertEqual(len(decision.eligible_edits), 2)
        self.assertEqual(decision.eligible_edits[0]["support_task_ids"], ["task-1", "task-2"])
        self.assertEqual(decision.eligible_indices, [0, 2])
        self.assertEqual(asdict(decision)["budget"], 2)

    def test_conflicting_operations_on_same_exact_locator_are_removed(self):
        decision = compute_evidence_budget(
            [
                edit("exact text", "new text", support_task_ids=["1", "2"]),
                {"op": "delete", "target": "exact text", "support_task_ids": ["1", "2"]},
                edit("other text", "updated", support_task_ids=["3", "4"]),
            ],
            total_failures=3,
        )
        self.assertEqual(decision.conflicting_count, 2)
        self.assertEqual(decision.budget, 1)
        self.assertEqual([e["target"] for e in decision.eligible_candidates], ["other text"])

    def test_unrelated_targets_in_same_path_do_not_conflict(self):
        decision = compute_evidence_budget(
            [
                {"op": "replace", "path": "skill.md", "target": "A", "content": "a", "task_ids": ["1", "2"]},
                {"op": "replace", "path": "skill.md", "target": "B", "content": "b", "task_ids": ["3", "4"]},
            ],
            total_failures=2,
        )
        self.assertEqual(decision.conflicting_count, 0)
        self.assertEqual(decision.budget, 2)

    def test_no_candidates_and_unknown_provenance_fallback(self):
        empty = compute_evidence_budget([], total_failures=0)
        self.assertEqual(empty.budget, 0)
        self.assertEqual(empty.eligible_edits, [])
        fallback = compute_evidence_budget(
            [edit("A", "a"), edit("B", "b"), edit("C", "c")],
            total_failures=50,
            min_budget=2,
            max_budget=4,
        )
        self.assertEqual(fallback.budget, 2)
        self.assertEqual(len(fallback.eligible_edits), 3)
        self.assertTrue(fallback.unknown_provenance)

    def test_min_max_limits_and_harmful_recent_change(self):
        candidates = [
            edit(str(i), str(i + 1), support_task_ids=[f"{i}-a", f"{i}-b"])
            for i in range(6)
        ]
        self.assertEqual(compute_evidence_budget(candidates, total_failures=1000).budget, 4)
        self.assertEqual(
            compute_evidence_budget(candidates[:1], total_failures=1, min_budget=0).budget,
            1,
        )
        self.assertEqual(
            compute_evidence_budget(candidates, total_failures=5, recent_score_changes=[0.1, -0.2]).budget,
            3,
        )

    def test_invalid_inputs_are_rejected_and_legacy_api_is_unchanged(self):
        for kwargs in (
            {"total_failures": -1},
            {"total_failures": 0, "min_budget": -1},
            {"total_failures": 0, "max_budget": 1, "min_budget": 2},
            {"total_failures": 0, "min_support_tasks": 0},
            {"total_failures": 0, "recent_score_changes": [float("nan")]},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                compute_evidence_budget([], **kwargs)

        confidence = estimate_failure_confidence(["a", "a", "b"])
        self.assertEqual(confidence.total_failures, 3)
        self.assertEqual(compute_adaptive_budget(confidence, min_budget=2, max_budget=8), 2)


if __name__ == "__main__":
    unittest.main()
