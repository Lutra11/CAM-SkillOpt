"""V2 regressions: real config conversion, checkpoint continuation and uncertainty semantics."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from skillopt.cam.drift_guard import DriftGuard
from skillopt.cam.memory_context import prepare_memory_context
from skillopt.cam.rejected_memory import PersistentRejectedEditMemory
from skillopt.config import flatten_config, load_config
from scripts.run_cell_robust import llm_ok_rows, repair_stale_cam_selection
from tests.test_cam_gate_trainer import CAMGateTrainerTests as TrainerFixture, SyntheticGateAdapter, INITIAL_SKILL


class DriftGuardTests(unittest.TestCase):
    def test_initial_best_is_protected_and_state_survives_resume(self):
        guard = DriftGuard()
        self.assertFalse(guard.observe(0.3, 0.5, 1)["rolled_back"])
        guard = DriftGuard(state=guard.state)
        self.assertTrue(guard.observe(0.4, 0.5, 2)["rolled_back"])
        self.assertEqual(guard.state["rollbacks"], 1)

    def test_auxiliary_updates_do_not_double_count_same_step(self):
        guard = DriftGuard()
        guard.observe(0.3, 0.5, 1)
        self.assertFalse(guard.observe(0.2, 0.5, 1)["rolled_back"])
        self.assertEqual(guard.state["streak"], 1)
        self.assertTrue(guard.observe(0.2, 0.5, 2)["rolled_back"])

    def test_auxiliary_update_can_change_the_step_observation(self):
        guard = DriftGuard()
        guard.observe(0.5, 0.5, 1)
        guard.observe(0.3, 0.5, 1)
        self.assertEqual(guard.state["streak"], 1)
        guard.observe(0.5, 0.5, 1)
        self.assertEqual(guard.state["streak"], 0)

    def test_zero_disables_and_tolerance_is_respected(self):
        guard = DriftGuard(patience=0)
        for step in range(5):
            self.assertFalse(guard.observe(0.0, 1.0, step)["rolled_back"])
        self.assertFalse(DriftGuard(patience=1, tolerance=0.1).observe(0.45, 0.5, 1)["rolled_back"])
        for kwargs in ({"patience": -1}, {"patience": True}, {"tolerance": float("nan")}):
            with self.assertRaises(ValueError):
                DriftGuard(**kwargs)


class V2ConfigurationTests(unittest.TestCase):
    def test_all_v2_overrides_reach_the_flat_trainer_config(self):
        cfg = flatten_config(load_config("configs/experiments/zgci_cam_matrix.yaml", overrides=[
            "cam.soft_accept=false", "cam.drift_patience=0", "cam.drift_tolerance=0.1",
            "cam.memory_inconclusive=false"]))
        self.assertIs(cfg["cam_soft_accept"], False)
        self.assertEqual(cfg["cam_drift_patience"], 0)
        self.assertEqual(cfg["cam_drift_tolerance"], 0.1)
        self.assertIs(cfg["cam_memory_inconclusive"], False)

    def test_preflight_rejects_an_unmapped_override(self):
        run = subprocess.run([sys.executable, "-X", "utf8", "scripts/verify_config.py", "--config",
                              "configs/experiments/zgci_cam_matrix.yaml", "--cfg-options",
                              "cam.not_a_real_option=false"], capture_output=True, text=True, encoding="utf-8")
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("DROPPED", run.stdout)

    def test_lmb_without_llm_field_is_unknown_not_zero_calls(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            (path / "results.jsonl").write_text('{"id":"a","hard":0,"agent_ok":true}\n', encoding="utf-8")
            self.assertEqual(llm_ok_rows(path), (None, 1))

    def test_certificate_failure_never_deletes_a_step(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            step = path / "steps" / "step_0001"
            step.mkdir(parents=True)
            (path / "summary.json").write_text(json.dumps({"status": "invalid_selection_evidence",
                "message": "CAM checkpoint current/best has no recorded acceptance"}), encoding="utf-8")
            self.assertFalse(repair_stale_cam_selection(path))
            self.assertTrue(step.exists())


class CAMV2TrainerTests(unittest.TestCase):
    setUp = TrainerFixture.setUp
    config = TrainerFixture.config
    train = TrainerFixture.train

    def test_soft_accepted_current_resumes_but_does_not_authorize_best(self):
        output = self.root / "soft_resume"
        cfg = self.config(output, cam_soft_accept=True, eval_test=False)
        self.train(cfg, SyntheticGateAdapter(baseline=[0, 0, 0, 1], candidate=[1, 1, 0, 1]))
        state = json.loads((output / "runtime_state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["best_step"], 0)
        self.assertEqual(state["cam_drift_guard"], {
            "streak": 0, "rollbacks": 0, "last_step": 1, "step_base_streak": 0,
        })
        self.assertEqual((output / "best_skill.md").read_text(encoding="utf-8"), INITIAL_SKILL)
        resumed = SyntheticGateAdapter(baseline=[0, 0, 0, 1], candidate=[1, 1, 0, 1])
        summary, requests = self.train(cfg, resumed)
        self.assertEqual(requests, [])
        self.assertEqual(resumed.events, [])
        self.assertEqual(summary["total_soft_accepts"], 1)
        self.assertEqual(summary["total_accepts"], 0)
        resumed_state = json.loads((output / "runtime_state.json").read_text(encoding="utf-8"))
        self.assertEqual(resumed_state["cam_drift_guard"], state["cam_drift_guard"])

    def test_inconclusive_memory_preserves_positive_evidence_in_prompt(self):
        path = self.root / "memory.json"
        memory = PersistentRejectedEditMemory(path)
        memory.add_rejected_step(failure_patterns=["lookup formula"], rejected_edits=["use exact lookup"],
            score_change=0.05, benchmark="synthetic", task_type="overall", epoch=1, step=1, kind="inconclusive")
        context, audit = prepare_memory_context(memory,
            [{"id": "train-a", "hard": 0, "fail_reason": "lookup formula", "split": "train"}],
            benchmark="synthetic", epoch=1, step=2, top_k=3)
        self.assertEqual(audit["hits"][0]["kind"], "inconclusive")
        self.assertIn("Do not treat it as a failed edit", context)
        evidence = json.loads(context.split("\n", 1)[1])
        self.assertEqual(evidence[0]["kind"], "inconclusive")
        self.assertEqual(evidence[0]["observed_selection_score_change"], 0.05)

    def test_v2_protocol_change_is_rejected_before_resume(self):
        output = self.root / "changed_protocol"
        cfg = self.config(output, cam_soft_accept=True, eval_test=False)
        self.train(cfg, SyntheticGateAdapter(baseline=[0]*4, candidate=[1]*4))
        with self.assertRaises(ValueError):
            self.train({**cfg, "cam_drift_patience": 3}, SyntheticGateAdapter(baseline=[0]*4, candidate=[1]*4))


if __name__ == "__main__":
    unittest.main()
