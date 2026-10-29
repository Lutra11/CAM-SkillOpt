"""Meaningful offline checks for diagnostic scope and recurrence denominators."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import diagnose_cam_v3_mechanisms as diagnostic


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.study = self.root / "old_study"
        self.folder = self.root / "old_cell"
        self.folder.mkdir()
        (self.folder / "best_skill.md").write_text("old best", encoding="utf-8")
        current = self.folder / "current.md"
        current.write_text("new soft candidate", encoding="utf-8")
        put(self.folder / "runtime_state.json", {
            "current_score": .675, "best_score": .55, "best_origin": "step_0001",
            "current_origin": "step_0003", "current_skill_path": str(current),
        })
        history, events, batches = [], [], []
        for step, action in enumerate(("accept_new_best", "cam_re_evaluate", "soft_accept", "reject"), 1):
            kind = "Cell" if step % 2 else "Sheet"
            batches.append({"global_step": step, "task_type": kind})
            history.append({"step": step, "action": action, "selection_hard": .675,
                            "best_score": .55, "persistent_memory": {
                                "retrieval_calls": 2, "injected_items": 2 if step == 3 else 0,
                                "items_added": 1,
                            }})
            events.append({"origin": f"step_{step:04d}", "lower_confidence_bound": -.025,
                           "upper_confidence_bound": .275, "promotion_authorized": step == 1})
            put(self.folder / "steps" / f"step_{step:04d}" / "evidence_budget.json", {
                "budget": 2, "reason": "provenance_unknown_minimum_fallback",
                "unknown_provenance": True, "evidence_task_ids": [],
            })
        put(self.folder / "history.json", history)
        put(self.folder / "cam_gate_events.json", events)
        put(self.folder / "slow_update/epoch_03/slow_result.json", {"action": "no_content", "time_s": 3})
        put(self.folder / "meta_skill/epoch_03/meta_skill_result.json", {"action": "write_meta_skill"})
        put(self.study / "freeze.json", {
            "cells": [{"cell_id": "cam", "method": "cam_v3", "seed": 43, "out_root": "old_cell"}],
            "task_orders": {"cam": {"batches": batches}},
        })
        # Poison data would fail JSON parsing if the diagnostic accessed held-out predictions.
        forbidden = self.folder / "test_eval/results.jsonl"
        forbidden.parent.mkdir()
        forbidden.write_text("NOT VALID JSON AND MUST NEVER BE READ", encoding="utf-8")

    def test_recurrence_excludes_warmup_and_does_not_claim_no_memory(self):
        with patch("socket.socket", side_effect=AssertionError("No network")):
            result = diagnostic.diagnose(self.root, self.study)
        self.assertEqual(result["totals"]["training_steps"], 4)
        self.assertEqual(result["totals"]["recurrence_opportunities"], 2)
        self.assertEqual(result["totals"]["recurrence_steps_with_memory"], 1)
        self.assertEqual(result["totals"]["memory_injected_items"], 2)
        self.assertEqual(result["totals"]["budget_unknown_provenance_steps"], 4)
        self.assertEqual(result["totals"]["legacy_auxiliary_no_content"], 1)
        self.assertFalse(result["cam_cells"][0]["same_current_and_best"])
        self.assertFalse(result["heldout_predictions_read"])
        self.assertEqual(result["model_api_calls"], 0)
        self.assertNotIn("test_hard", json.dumps(result))

    def test_forbidden_inputs_and_path_escape_are_rejected(self):
        for relative in ("test_eval/results.jsonl", "test/items.json", "test_eval_final/summary.json"):
            with self.subTest(path=relative), self.assertRaises(ValueError):
                diagnostic.read_training_json(self.root / relative)
        with self.assertRaises(ValueError):
            diagnostic.resolve_inside(self.root, "../outside.json")

    def test_preservation_guard_stops_changed_old_evidence(self):
        old = self.root / "old_report.md"
        old.write_text("original", encoding="utf-8")
        registration = self.root / "registration.json"
        put(registration, {"preserved_old_evidence": [
            {"path": "old_report.md", "sha256": hashlib.sha256(old.read_bytes()).hexdigest()}]})
        self.assertTrue(diagnostic.preserve_checks(self.root, registration)[0]["unchanged"])
        old.write_text("modified", encoding="utf-8")
        with self.assertRaises(ValueError):
            diagnostic.preserve_checks(self.root, registration)

    def test_online_flag_rejected_before_old_evidence_access(self):
        with patch("sys.argv", ["diagnose", "--run"]), \
             patch.object(diagnostic, "preserve_checks", side_effect=AssertionError("Must not read")), \
             contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            diagnostic.main()
        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
