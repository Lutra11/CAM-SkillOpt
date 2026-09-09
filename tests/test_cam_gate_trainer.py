"""Offline integrated CAM gate regression tests with synthetic, paired scores.

Only model responses/configuration are mocked. The Trainer, EnvAdapter reflect,
patch application, slow-update construction and statistical gate remain real.
"""
from __future__ import annotations

from contextlib import ExitStack, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from skillopt.engine import trainer as trainer_module
from skillopt.envs.base import EnvAdapter
from skillopt.gradient import reflect as reflect_module
from skillopt.optimizer import slow_update as slow_module


INITIAL_SKILL = "# Synthetic Gate Skill\n\n- Preserve initial lookup rules.\n"
PATCH_MARKER = "SYNTHETIC_PATCH_RULE"
SLOW_MARKER = "SYNTHETIC_SLOW_RULE"
APPENDIX_NOTE = "SYNTHETIC_EXECUTION_LAPSE_NOTE"


class SyntheticGateAdapter(EnvAdapter):
    def __init__(self, *, baseline, candidate, placeholder=None,
                 reverse_candidate_rows=False, mismatch_candidate_ids=False,
                 baseline_soft=None, candidate_soft=None, drop_last_candidate_row=False):
        self.baseline = list(baseline)
        self.candidate = list(candidate)
        self.placeholder = list(placeholder) if placeholder is not None else list(baseline)
        self.baseline_soft = list(baseline_soft) if baseline_soft is not None else list(baseline)
        self.candidate_soft = list(candidate_soft) if candidate_soft is not None else list(candidate)
        self.analyst_workers = 1
        self.failure_only = False
        self.minibatch_size = 4
        self.edit_budget = 2
        self.events = []
        self.reverse_candidate_rows = reverse_candidate_rows
        self.mismatch_candidate_ids = mismatch_candidate_ids
        self.drop_last_candidate_row = drop_last_candidate_row

    def build_train_env(self, batch_size, seed, **kwargs):
        return [{"id": f"synthetic-train-{i}", "index": i, "split": "train"}
                for i in range(batch_size)]

    def build_eval_env(self, env_num, split, seed, **kwargs):
        return [{"id": f"synthetic-{split}-{i}", "index": i, "split": split}
                for i in range(env_num)]

    def get_task_types(self):
        return ["synthetic_lookup"]

    def rollout(self, env_manager, skill_content, out_dir, **kwargs):
        path = Path(out_dir)
        path.mkdir(parents=True, exist_ok=True)
        is_candidate = any(marker in skill_content for marker in (PATCH_MARKER, SLOW_MARKER, APPENDIX_NOTE))
        scores = (self.candidate if is_candidate else
                  self.placeholder if "SLOW_UPDATE_START" in skill_content else self.baseline)
        soft_scores = self.candidate_soft if is_candidate else self.baseline_soft
        rows = []
        for item in env_manager:
            score = 0 if item["split"] == "train" else scores[item["index"] % len(scores)]
            soft = 0.0 if item["split"] == "train" else float(soft_scores[item["index"] % len(soft_scores)])
            row = {**item, "hard": score, "soft": soft, "ok": bool(score),
                   "status": "completed", "phase": "exec", "llm_ok": True,
                   "code_ok": True, "exec_ok": True, "task_type": "synthetic_lookup",
                   "task_description": "Synthetic lookup formula test fixture.",
                   "fail_reason": "lookup formula" if not score else "",
                   "failure_type": "score_mismatch" if not score else "none"}
            rows.append(row)
            prediction = path / "predictions" / item["id"]
            prediction.mkdir(parents=True, exist_ok=True)
            (prediction / "conversation.json").write_text(json.dumps([
                {"role": "assistant", "content": "Synthetic lookup formula trajectory."}
            ]), encoding="utf-8")
        if env_manager and env_manager[0]["split"] == "valid_seen" and PATCH_MARKER in skill_content:
            if self.mismatch_candidate_ids:
                rows = [{**row, "id": row["id"] + "-WRONG_SPLIT_ID"} for row in rows]
            if self.reverse_candidate_rows:
                rows = list(reversed(rows))
            if self.drop_last_candidate_row:
                rows = rows[:-1]
        (path / "results.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        self.events.append({"path": str(path), "skill": skill_content,
                            "split": env_manager[0]["split"] if env_manager else "",
                            "ids": [r["id"] for r in rows], "scores": [r["hard"] for r in rows]})
        return rows


class CAMGateTrainerTests(unittest.TestCase):
    def test_refused_resume_preserves_completed_summary_config_and_initial_snapshot(self):
        output = self.root / "preserved_resume"
        adapter = SyntheticGateAdapter(baseline=[0, 0, 0, 0], candidate=[1, 1, 1, 1])
        self.train(self.config(output), adapter)
        retained = {name: (output / name).read_bytes()
                    for name in ("summary.json", "config.json", "skills/skill_v0000.md", "history.json")}
        for cfg in (self.config(output, exec_timeout=421), self.config(output, cam_seed=43)):
            with self.assertRaises(ValueError):
                self.train(cfg, SyntheticGateAdapter(baseline=[0] * 4, candidate=[1] * 4))
            for name, content in retained.items():
                self.assertEqual((output / name).read_bytes(), content)
        checkpoint = output / "cam_gate_state.json"
        state = json.loads(checkpoint.read_text(encoding="utf-8"))
        state["selection_cache"] = {}
        checkpoint.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.train(self.config(output), SyntheticGateAdapter(baseline=[0] * 4, candidate=[1] * 4))
        for name, content in retained.items():
            self.assertEqual((output / name).read_bytes(), content)
        self.assertTrue((output / "resume_validation_error.json").exists())

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.initial = self.root / "initial.md"
        self.initial.write_text(INITIAL_SKILL, encoding="utf-8")

    def config(self, output, **overrides):
        cfg = {
            "env": "synthetic_gate", "out_root": str(output), "skill_init": str(self.initial),
            "optimizer_model": "synthetic-local-optimizer", "target_model": "synthetic-local-target",
            "optimizer_backend": "openai_chat", "target_backend": "openai_chat",
            "num_epochs": 1, "train_size": 4, "batch_size": 4, "accumulation": 1,
            "seed": 42, "merge_batch_size": 2, "minibatch_size": 4,
            "edit_budget": 2, "min_edit_budget": 1, "analyst_workers": 1,
            "max_analyst_rounds": 1, "skill_update_mode": "patch", "use_gate": True,
            "sel_env_num": 4, "test_env_num": 4, "eval_test": True,
            "use_slow_update": False, "slow_update_samples": 4,
            "slow_update_gate_with_selection": False, "use_meta_skill": False,
            "cam_enabled": True, "cam_bootstrap_gate": True,
            "cam_confidence_level": 0.95, "cam_meaningful_improvement": 0.0,
            "cam_bootstrap_samples": 1000, "cam_seed": 42,
            "cam_adaptive_budget": False, "cam_memory_enabled": False,
        }
        cfg.update(overrides)
        return cfg

    def train(self, cfg, adapter, *, produce_patch=True, appendix_note=""):
        requests = []
        def analyst(**kwargs):
            requests.append({"kind": "analyst", **kwargs})
            edits = [{"op": "append", "content": f"- {PATCH_MARKER}"}] if produce_patch else []
            result = {"patch": {"reasoning": "Synthetic candidate.", "edits": edits}}
            if appendix_note:
                result["appendix_notes"] = [appendix_note]
            return json.dumps(result), None

        def slow_optimizer(**kwargs):
            requests.append({"kind": "slow_update", **kwargs})
            return json.dumps({"reasoning": "Synthetic longitudinal guidance.",
                               "slow_update_content": SLOW_MARKER}), None

        setup_names = (
            "configure_azure_openai", "configure_codex_exec", "configure_claude_code_exec",
            "configure_qwen_chat", "configure_minimax_chat", "set_optimizer_backend",
            "set_target_backend", "set_optimizer_deployment", "set_target_deployment",
            "set_reasoning_effort",
        )
        with ExitStack() as stack:
            for name in setup_names:
                stack.enter_context(patch.object(trainer_module, name))
            stack.enter_context(patch.object(reflect_module, "chat_optimizer", side_effect=analyst))
            stack.enter_context(patch.object(slow_module, "chat_optimizer", side_effect=slow_optimizer))
            stack.enter_context(patch("socket.socket.connect", side_effect=AssertionError("Unexpected network request")))
            stack.enter_context(patch("subprocess.Popen", side_effect=AssertionError("Unexpected model subprocess")))
            with redirect_stdout(io.StringIO()):
                summary = trainer_module.ReflACTTrainer(cfg, adapter).train()
        return summary, requests

    def test_p0_uncertain_improvement_does_not_promote_placeholder_or_repeat_candidate(self):
        output = self.root / "uncertain"
        adapter = SyntheticGateAdapter(baseline=[0, 0, 0, 1], candidate=[1, 1, 0, 1],
                                       placeholder=[1, 1, 1, 1])
        summary, requests = self.train(self.config(output, use_slow_update=True), adapter)
        history = json.loads((output / "history.json").read_text(encoding="utf-8"))
        self.assertEqual(len(requests), 1)
        self.assertEqual(history[0]["selection_hard"], 0.75)
        self.assertEqual(history[0]["action"], "cam_re_evaluate")
        self.assertEqual(history[0]["cam_gate"]["lower_confidence_bound"], 0.0)
        self.assertEqual(history[0]["cam_gate"]["upper_confidence_bound"], 1.0)
        self.assertEqual(summary["best_selection_hard"], 0.25)
        self.assertEqual(summary["best_step"], 0)
        self.assertEqual(summary["best_origin"], "initial_skill")
        self.assertEqual((output / "best_skill.md").read_text(encoding="utf-8"), INITIAL_SKILL)
        self.assertEqual((output / "skills" / "skill_v0001.md").read_text(encoding="utf-8"), INITIAL_SKILL)
        self.assertEqual(summary["test_hard"], 0.25)
        candidate_evaluations = [e for e in adapter.events if e["split"] == "valid_seen" and PATCH_MARKER in e["skill"]]
        self.assertEqual(len(candidate_evaluations), 1)
        self.assertFalse(any("SLOW_UPDATE_START" in e["skill"] for e in adapter.events))

    def test_significant_patch_is_accepted_by_real_bootstrap_gate(self):
        output = self.root / "accept"
        adapter = SyntheticGateAdapter(baseline=[0, 0, 0, 0], candidate=[1, 1, 1, 1])
        summary, _ = self.train(self.config(output), adapter)
        history = json.loads((output / "history.json").read_text(encoding="utf-8"))
        self.assertEqual(history[0]["action"], "accept_new_best")
        self.assertTrue(history[0]["cam_gate_used"])
        self.assertEqual(history[0]["cam_gate"]["lower_confidence_bound"], 1.0)
        self.assertIn(PATCH_MARKER, (output / "best_skill.md").read_text(encoding="utf-8"))
        self.assertEqual(summary["best_selection_hard"], 1.0)
        self.assertEqual(summary["test_hard"], 1.0)

    def test_epoch_two_slow_update_obeys_cam_even_when_legacy_force_option_is_false(self):
        output = self.root / "slow"
        adapter = SyntheticGateAdapter(baseline=[0, 0, 0, 1], candidate=[1, 1, 0, 1])
        summary, requests = self.train(self.config(output, num_epochs=2, use_slow_update=True,
                                                  slow_update_gate_with_selection=False),
                                       adapter, produce_patch=False)
        self.assertEqual(sum(r["kind"] == "slow_update" for r in requests), 1)
        self.assertEqual(summary["best_selection_hard"], 0.25)
        self.assertEqual((output / "best_skill.md").read_text(encoding="utf-8"), INITIAL_SKILL)
        current = (output / "skills" / "skill_v0002.md").read_text(encoding="utf-8")
        self.assertNotIn(SLOW_MARKER, current)
        slow_result = json.loads((output / "slow_update" / "epoch_02" / "slow_result.json").read_text(encoding="utf-8"))
        self.assertNotEqual(slow_result["action"], "force_accept")
        slow_selections = [e for e in adapter.events if e["split"] == "valid_seen" and SLOW_MARKER in e["skill"]]
        self.assertEqual(len(slow_selections), 1)
        self.assertEqual(sum(slow_selections[0]["scores"]) / 4, 0.75)

    def test_active_cam_cannot_disable_gate(self):
        output = self.root / "invalid_configuration"
        adapter = SyntheticGateAdapter(baseline=[0, 0, 0, 1], candidate=[1, 1, 0, 1])
        with self.assertRaises((ValueError, RuntimeError)):
            self.train(self.config(output, use_gate=False), adapter)
        self.assertEqual(adapter.events, [])

    def test_cam_off_retains_scalar_gate_and_force_accept_behavior(self):
        for use_gate in (True, False):
            with self.subTest(use_gate=use_gate):
                output = self.root / f"cam_off_{use_gate}"
                adapter = SyntheticGateAdapter(baseline=[0, 0, 0, 1], candidate=[1, 1, 0, 1])
                summary, _ = self.train(self.config(output, cam_enabled=False, use_gate=use_gate), adapter)
                history = json.loads((output / "history.json").read_text(encoding="utf-8"))
                self.assertFalse(history[0]["cam_gate_used"])
                self.assertIn(history[0]["action"], ("accept_new_best", "force_accept"))
                self.assertIn(PATCH_MARKER, (output / "best_skill.md").read_text(encoding="utf-8"))
                self.assertEqual(summary["best_selection_hard"], 0.75)

    def test_strict_cam_refuses_legacy_unreviewed_resume(self):
        output = self.root / "legacy_resume"
        prior = SyntheticGateAdapter(baseline=[0, 0, 0, 1], candidate=[1, 1, 0, 1])
        # Generate a real legacy/CAM-off checkpoint, rather than guessing the
        # new protocol schema or mocking the resume validator.
        self.train(self.config(output, cam_enabled=False), prior)
        resumed = SyntheticGateAdapter(baseline=[0, 0, 0, 1], candidate=[1, 1, 0, 1])
        with self.assertRaises((ValueError, RuntimeError)):
            self.train(self.config(output, cam_enabled=True), resumed)
        self.assertEqual(resumed.events, [])

    def test_strict_cam_refuses_changed_skill_in_saved_checkpoint(self):
        output = self.root / "changed_skill_resume"
        prior = SyntheticGateAdapter(baseline=[0, 0, 0, 1], candidate=[1, 1, 0, 1])
        self.train(self.config(output), prior)
        (output / "best_skill.md").write_text(INITIAL_SKILL + "\nUNREVIEWED_TAMPER\n", encoding="utf-8")
        resumed = SyntheticGateAdapter(baseline=[0, 0, 0, 1], candidate=[1, 1, 0, 1])
        with self.assertRaises((ValueError, RuntimeError)):
            self.train(self.config(output), resumed)
        self.assertEqual(resumed.events, [])

    def test_paired_gate_aligns_item_ids_instead_of_completion_order(self):
        output = self.root / "reordered_results"
        adapter = SyntheticGateAdapter(baseline=[1, 0, 0, 0], candidate=[1, 1, 0, 0],
                                       reverse_candidate_rows=True)
        self.train(self.config(output), adapter)
        history = json.loads((output / "history.json").read_text(encoding="utf-8"))
        # Correct paired differences are [0,1,0,0]. Positional matching against
        # the reversed completion order would instead include a spurious -1.
        self.assertEqual(history[0]["cam_gate"]["lower_confidence_bound"], 0.0)
        self.assertEqual(history[0]["cam_gate"]["mean_improvement"], 0.25)
        self.assertEqual(history[0]["action"], "cam_re_evaluate")

    def test_mismatched_candidate_ids_never_fall_back_to_scalar_acceptance(self):
        output = self.root / "mismatched_ids"
        adapter = SyntheticGateAdapter(baseline=[0, 0, 0, 0], candidate=[1, 1, 1, 1],
                                       mismatch_candidate_ids=True)
        with self.assertRaises((ValueError, RuntimeError)):
            self.train(self.config(output), adapter)
        self.assertFalse(any(e["split"] == "valid_unseen" for e in adapter.events))

    def test_certified_zero_score_resume_reuses_evidence_without_rollout(self):
        output = self.root / "zero_score_resume"
        cfg = self.config(output, eval_test=False)
        first = SyntheticGateAdapter(baseline=[0, 0, 0, 0], candidate=[0, 0, 0, 0])
        initial_summary, _ = self.train(cfg, first)
        self.assertEqual(initial_summary["best_selection_hard"], 0.0)
        certificate = json.loads((output / "cam_gate_state.json").read_text(encoding="utf-8"))
        self.assertEqual(certificate["policy"], "paired_selection_all_updates_v1")
        resumed = SyntheticGateAdapter(baseline=[0, 0, 0, 0], candidate=[0, 0, 0, 0])
        summary, requests = self.train(self.config(output, eval_test=False), resumed)
        self.assertEqual(requests, [])
        self.assertEqual(resumed.events, [])
        self.assertEqual(summary["baseline_selection_hard"], 0.0)
        self.assertEqual(summary["best_selection_hard"], 0.0)
        self.assertEqual(summary["final_selection_hard"], 0.0)
        self.assertEqual((output / "best_skill.md").read_text(encoding="utf-8"), INITIAL_SKILL)

    def test_certified_resume_does_not_replay_untrusted_slow_update_text(self):
        output = self.root / "slow_resume"
        cfg = self.config(output, num_epochs=2, use_slow_update=True, eval_test=False)
        first = SyntheticGateAdapter(baseline=[0, 0, 0, 0], candidate=[1, 1, 1, 1])
        self.train(cfg, first, produce_patch=False)
        saved_best = (output / "best_skill.md").read_text(encoding="utf-8")
        self.assertIn(SLOW_MARKER, saved_best)
        slow_path = output / "slow_update" / "epoch_02" / "slow_result.json"
        slow = json.loads(slow_path.read_text(encoding="utf-8"))
        slow.update(action="force_accept", slow_update_content="UNREVIEWED_SLOW_REPLAY_SENTINEL")
        slow_path.write_text(json.dumps(slow), encoding="utf-8")
        resumed = SyntheticGateAdapter(baseline=[0, 0, 0, 0], candidate=[1, 1, 1, 1])
        summary, requests = self.train(self.config(output, num_epochs=2, use_slow_update=True, eval_test=False),
                                       resumed, produce_patch=False)
        self.assertEqual(requests, [])
        self.assertEqual(resumed.events, [])
        self.assertEqual(summary["best_selection_hard"], 1.0)
        self.assertEqual((output / "best_skill.md").read_text(encoding="utf-8"), saved_best)
        self.assertEqual((output / "skills" / "skill_v0002.md").read_text(encoding="utf-8"), saved_best)

    def test_corrupt_current_selection_cache_fails_before_any_resume_rollout(self):
        for corruption in ("missing_id", "nan_score", "missing_current_entry"):
            with self.subTest(corruption=corruption):
                output = self.root / corruption
                first = SyntheticGateAdapter(baseline=[0, 0, 0, 1], candidate=[1, 1, 0, 1])
                self.train(self.config(output, eval_test=False), first)
                path = output / "cam_gate_state.json"
                certificate = json.loads(path.read_text(encoding="utf-8"))
                key = certificate["current_hash"]
                if corruption == "missing_current_entry":
                    del certificate["selection_cache"][key]
                elif corruption == "missing_id":
                    del certificate["selection_cache"][key]["rows"][0]["id"]
                else:
                    certificate["selection_cache"][key]["rows"][0]["hard"] = float("nan")
                path.write_text(json.dumps(certificate), encoding="utf-8")
                resumed = SyntheticGateAdapter(baseline=[0, 0, 0, 1], candidate=[1, 1, 0, 1])
                with self.assertRaises((ValueError, RuntimeError)):
                    self.train(self.config(output, eval_test=False), resumed)
                self.assertEqual(resumed.events, [])

    def test_appendix_only_skip_branch_requires_real_cam_acceptance(self):
        output = self.root / "appendix_only"
        adapter = SyntheticGateAdapter(baseline=[0, 0, 0, 1], candidate=[1, 1, 0, 1])
        summary, _ = self.train(self.config(output, use_skill_aware_reflection=True),
                                adapter, produce_patch=False, appendix_note=APPENDIX_NOTE)
        history = json.loads((output / "history.json").read_text(encoding="utf-8"))
        self.assertEqual(history[0]["action"], "skip_no_patches")
        events = json.loads((output / "cam_gate_events.json").read_text(encoding="utf-8"))
        appendix_events = [event for event in events if event["origin"].startswith("appendix_")]
        self.assertEqual(len(appendix_events), 1)
        self.assertEqual(appendix_events[0]["action"], "cam_re_evaluate")
        self.assertFalse(appendix_events[0]["promotion_authorized"])
        self.assertEqual(appendix_events[0]["lower_confidence_bound"], 0.0)
        self.assertEqual(len(appendix_events[0]["pair_ids"]), 4)
        self.assertEqual(summary["best_selection_hard"], 0.25)
        self.assertEqual((output / "skills" / "skill_v0001.md").read_text(encoding="utf-8"), INITIAL_SKILL)
        self.assertNotIn(APPENDIX_NOTE, (output / "best_skill.md").read_text(encoding="utf-8"))

    def test_soft_and_mixed_gate_metrics_keep_raw_hard_scores_in_summary(self):
        for metric, expected_gate in (("soft", 0.8), ("mixed", 0.525)):
            with self.subTest(metric=metric):
                output = self.root / f"metric_{metric}"
                adapter = SyntheticGateAdapter(baseline=[0, 0, 0, 1], candidate=[0, 0, 0, 1],
                                               baseline_soft=[0.4] * 4, candidate_soft=[0.8] * 4)
                summary, _ = self.train(self.config(output, gate_metric=metric, gate_mixed_weight=0.5), adapter)
                self.assertEqual(summary["total_accepts"], 1)
                self.assertEqual(summary["gate_metric"], metric)
                self.assertAlmostEqual(summary["best_gate_score"], expected_gate)
                self.assertEqual(summary["baseline_selection_hard"], 0.25)
                self.assertEqual(summary["best_selection_hard"], 0.25)
                self.assertEqual(summary["final_selection_hard"], 0.25)
                self.assertEqual(summary["test_hard"], 0.25)
                self.assertEqual(summary["best_selection_soft"], 0.8)
                self.assertEqual(summary["final_selection_soft"], 0.8)

    def test_accepted_checkpoint_resume_restores_zero_scored_initial_baseline(self):
        output = self.root / "accepted_resume_with_zero_baseline"
        first = SyntheticGateAdapter(baseline=[0, 0, 0, 0], candidate=[1, 1, 1, 1])
        original, _ = self.train(self.config(output, eval_test=False), first)
        self.assertEqual(original["baseline_selection_hard"], 0.0)
        self.assertEqual(original["best_selection_hard"], 1.0)
        self.assertEqual(original["total_accepts"], 1)
        resumed = SyntheticGateAdapter(baseline=[0, 0, 0, 0], candidate=[1, 1, 1, 1])
        summary, requests = self.train(self.config(output, eval_test=False), resumed)
        self.assertEqual(requests, [])
        self.assertEqual(resumed.events, [])
        self.assertEqual(summary["baseline_selection_hard"], 0.0)
        self.assertEqual(summary["best_selection_hard"], 1.0)
        self.assertEqual(summary["final_selection_hard"], 1.0)

    def test_existing_candidate_artifacts_require_matching_skill_identity(self):
        # Obtain a genuine certificate with the same model/split/gate signature
        # but initial-skill identity; do not guess or mock the identity schema.
        reference = self.root / "identity_reference"
        reference_adapter = SyntheticGateAdapter(baseline=[0, 0, 0, 0], candidate=[1, 1, 1, 1])
        self.train(self.config(reference, eval_test=False), reference_adapter)
        initial_identity = json.loads((reference / "selection_eval_baseline" /
                                       "selection_identity.json").read_text(encoding="utf-8"))
        fake_rows = [{"id": f"synthetic-valid_seen-{i}", "hard": 1, "soft": 1.0,
                      "status": "completed", "split": "valid_seen"} for i in range(4)]
        for identity_case in ("missing", "another_skill"):
            with self.subTest(identity_case=identity_case):
                output = self.root / f"stale_selection_{identity_case}"
                candidate_dir = output / "steps" / "step_0001" / "selection_eval"
                candidate_dir.mkdir(parents=True)
                results_path = candidate_dir / "results.jsonl"
                results_path.write_text("".join(json.dumps(row) + "\n" for row in fake_rows), encoding="utf-8")
                before = results_path.read_bytes()
                if identity_case == "another_skill":
                    (candidate_dir / "selection_identity.json").write_text(json.dumps(initial_identity), encoding="utf-8")
                adapter = SyntheticGateAdapter(baseline=[0, 0, 0, 0], candidate=[1, 1, 1, 1])
                with self.assertRaisesRegex(ValueError, "identity|another skill"):
                    self.train(self.config(output, eval_test=False), adapter)
                self.assertEqual(results_path.read_bytes(), before)
                self.assertFalse(any(e["split"] == "valid_seen" and PATCH_MARKER in e["skill"]
                                     for e in adapter.events))

    def test_four_requested_candidate_items_cannot_be_scored_from_three_results(self):
        output = self.root / "missing_candidate_item"
        adapter = SyntheticGateAdapter(baseline=[0, 0, 0, 0], candidate=[1, 1, 1, 1],
                                       drop_last_candidate_row=True)
        with self.assertRaisesRegex(ValueError, "count"):
            self.train(self.config(output), adapter)
        candidate_calls = [e for e in adapter.events if e["split"] == "valid_seen" and PATCH_MARKER in e["skill"]]
        self.assertEqual(len(candidate_calls), 1)
        self.assertEqual(len(candidate_calls[0]["ids"]), 3)
        self.assertFalse(any(e["split"] == "valid_unseen" for e in adapter.events))
        summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["status"], "invalid_selection_evidence")
        self.assertIsNone(summary["best_selection_hard"])


if __name__ == "__main__":
    unittest.main()
