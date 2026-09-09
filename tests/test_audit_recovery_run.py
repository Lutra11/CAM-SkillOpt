"""Synthetic offline audits; never read/modify experiment data or call models."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


HELPER_PATH = Path(__file__).resolve().parents[1] / "scripts/audit_recovery_run.py"
SPEC = importlib.util.spec_from_file_location("recovery_audit_under_test", HELPER_PATH)
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)
SENTINELS = ("PROMPT_SENTINEL", "WORKBOOK_SENTINEL", "sk-SYNTHETICnotrealSECRET")


class AuditRecoveryRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cam_audit_synthetic_")
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name) / "synthetic_p0"
        self.run.mkdir()
        self.source = patch.object(helper, "source_evidence", return_value={
            "current_source_sha256": "a" * 64,
            "rejected_memory_retrieve_call": {"file": "skillopt/engine/trainer.py", "lines": []},
        })
        self.source.start()
        self.addCleanup(self.source.stop)

    def write(self, relative, value):
        path = self.run / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def text(self, relative, value):
        path = self.run / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")

    def raw(self):
        return json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": " ".join(SENTINELS)}}) + "\n" + json.dumps({
            "type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 10, "cached_input_tokens": 20},
        }) + "\n"

    def fixture(self, reuse=False):
        cfg = {"train_size": 4, "batch_size": 4, "sel_env_num": 4, "test_env_num": 4,
            "limit": 4, "num_epochs": 1, "steps_per_epoch": 1, "accumulation": 1,
            "workers": 1, "analyst_workers": 1, "seed": 42, "split_seed": 42,
            "exec_timeout": 420, "edit_budget": 4}
        self.write("config.json", cfg)
        self.write("manifest.json", {"source_sha256": "a" * 64})
        self.write("recovery_summary.json", {"status": "completed_pending_audit", "stage": "p0"})
        summary = {key: 0.5 for key in helper.SUMMARY_METRICS}
        summary.update(total_steps=1, total_wall_time_s=100)
        if reuse:
            summary["final_selection_soft"] = None
        self.write("summary.json", summary)
        self.rows = [{"id": str(i), "status": "completed", "phase": "exec", "llm_ok": True,
            "code_ok": True, "exec_ok": True, "hard": i % 2, "soft": i % 2,
            "ok": bool(i % 2), "task_description": SENTINELS[0], "spreadsheet_preview": SENTINELS[1],
            "target_user_prompt": SENTINELS[2]} for i in range(4)]
        stages = ["selection_eval_baseline", "steps/step_0001/rollout", "steps/step_0001/selection_eval", "test_eval_baseline", "test_eval"]
        if not reuse:
            stages += ["final_selection_eval", "test_eval_final"]
        for stage in stages:
            self.text(stage + "/results.jsonl", "\n".join(json.dumps(row) for row in self.rows) + "\n")
            for row in self.rows:
                task = stage + "/predictions/" + row["id"]
                self.write(task + "/conversation.json", [{"role": "assistant", "content": " ".join(SENTINELS)}])
                for filename in ("codex_raw.txt", "raw_trace.txt", "raw.txt"):
                    self.text(task + "/" + filename, self.raw())
        if reuse:
            self.write("test_eval_final/summary.json", {"overall": {"total": 4, "hard_acc": 0.5}})
        self.write("steps/step_0001/step_record.json", {"step": 1, "n_patches": 1, "selection_hard": 0.5,
            "action": "accept", "cam_gate_used": True, "cam_gate": {"action": "accept"},
            "edit_budget": 3, "lr_control_mode": "cam_adaptive",
            "cam_failure_confidence": {"confidence": 0.2, "counts": {SENTINELS[0]: 4}}})
        self.text("skills/skill_v0000.md", "initial")
        self.text("skills/skill_v0001.md", "updated")
        self.text("best_skill.md", "updated")
        self.text("steps/step_0001/candidate_skill.md", "updated")
        self.write("runtime_state.json", {"current_skill_path": "skills/skill_v0001.md"})
        self.write("steps/step_0001/patches/minibatch_fail_000.json", {"patch": {"edits": [{"op": "append", "content": " ".join(SENTINELS)}]}})
        self.write("steps/step_0001/patches/minibatch_fail_000/request_meta.json", {"optimizer_called": True, "status": "completed"})
        self.write("model_calls/request1/conversation.json", {"stage": "analyst", "status": "ok", "messages": [{"role": "assistant", "content": " ".join(SENTINELS)}]})
        self.text("model_calls/request1/raw_trace.txt", self.raw())
        self.write("cam_rejected_memory.json", {"items": [{"failure_pattern": SENTINELS[0], "rejected_edit": SENTINELS[1]}]})
        return len(stages)

    def audit(self):
        return helper.audit(self.run, self.run / "unused_source")

    def test_complete_direct_artifacts_pass_and_sensitive_fields_stay_local(self):
        stages = self.fixture()
        result = self.audit()
        self.assertTrue(result["completeness"]["core_probe_evidence_pass"])
        self.assertEqual(result["task_stage_counts"]["n_scored"], stages * 4)
        self.assertEqual(result["cost"]["target_canonical_raw_usage"]["input_plus_output_tokens"], stages * 4 * 110)
        export = json.dumps(result) + helper.markdown(result)
        for sentinel in SENTINELS:
            self.assertNotIn(sentinel, export)

    def test_unvalidated_config_string_is_not_exported(self):
        self.fixture()
        cfg = json.loads((self.run / "config.json").read_text())
        cfg["sel_env_num"] = SENTINELS[2]
        self.write("config.json", cfg)
        result = self.audit()
        self.assertNotIn(SENTINELS[2], json.dumps(result) + helper.markdown(result))
        self.assertFalse(result["completeness"]["core_probe_evidence_pass"])

    def test_missing_train_results_and_step_cannot_pass(self):
        self.fixture()
        (self.run / "steps/step_0001/rollout/results.jsonl").unlink()
        (self.run / "steps/step_0001/step_record.json").unlink()
        result = self.audit()
        self.assertFalse(result["completeness"]["core_checks"]["complete_single_training_step"])
        self.assertFalse(result["completeness"]["core_probe_evidence_pass"])

    def test_empty_transcript_cannot_pass(self):
        self.fixture()
        self.write("test_eval/predictions/0/conversation.json", [{"role": "assistant", "content": "   "}])
        self.assertFalse(self.audit()["completeness"]["core_probe_evidence_pass"])

    def test_empty_raw_trace_cannot_pass(self):
        self.fixture()
        self.text("test_eval/predictions/0/codex_raw.txt", "")
        self.assertFalse(self.audit()["completeness"]["core_probe_evidence_pass"])

    def test_running_optimizer_is_not_completed_call(self):
        self.fixture()
        self.write("model_calls/request1/conversation.json", {"stage": "analyst", "status": "running"})
        result = self.audit()
        self.assertEqual(result["reflection"]["completed_analyst_request_artifacts"], 0)
        self.assertFalse(result["completeness"]["core_probe_evidence_pass"])

    def test_source_mismatch_cannot_pass(self):
        self.fixture()
        self.write("manifest.json", {"source_sha256": "b" * 64})
        self.assertFalse(self.audit()["completeness"]["core_probe_evidence_pass"])

    def test_missing_manifest_is_unknown_and_cannot_pass(self):
        self.fixture()
        (self.run / "manifest.json").unlink()
        result = self.audit()
        self.assertIsNone(result["current_source_matches_manifest"])
        self.assertFalse(result["completeness"]["core_probe_evidence_pass"])

    def test_missing_final_metrics_cannot_pass(self):
        self.fixture()
        self.write("summary.json", {"total_steps": 1})
        self.assertFalse(self.audit()["completeness"]["core_probe_evidence_pass"])

    def test_verified_same_skill_evaluation_reuse_passes(self):
        stages = self.fixture(reuse=True)
        result = self.audit()
        evidence = result["completeness"]["evaluation_provenance"]
        self.assertEqual(evidence["final_selection"]["kind"], "same_skill_reuse")
        self.assertIsNone(result["summary_metrics"]["final_selection_soft"])
        self.assertEqual(evidence["final_selection"]["derived_soft"], 0.5)
        self.assertTrue(evidence["final_selection"]["derived_from_verified_source"])
        self.assertEqual(evidence["final_test"]["kind"], "same_skill_reuse")
        self.assertTrue(result["completeness"]["core_probe_evidence_pass"])
        self.assertEqual(result["task_stage_counts"]["n_scored"], stages * 4)

    def test_reused_selection_contradictory_soft_is_not_ignored(self):
        self.fixture(reuse=True)
        summary = json.loads((self.run / "summary.json").read_text())
        summary["final_selection_soft"] = 0.75
        self.write("summary.json", summary)
        self.assertFalse(self.audit()["completeness"]["core_probe_evidence_pass"])

    def test_paired_diagnostic_zero_difference_is_degenerate_and_not_formal(self):
        self.fixture()
        diagnostic = self.audit()["paired_baseline_best_test_diagnostic"]
        self.assertEqual(diagnostic["status"], "verified_exploratory_diagnostic")
        self.assertEqual(diagnostic["n_pairs"], 4)
        self.assertEqual(diagnostic["resamples"], 256)
        for metric in ("hard", "soft"):
            self.assertEqual(diagnostic["metrics"][metric]["mean_difference_best_minus_baseline"], 0)
            self.assertEqual(diagnostic["metrics"][metric]["lower"], 0)
            self.assertEqual(diagnostic["metrics"][metric]["upper"], 0)
        self.assertIn("not_formal", diagnostic["scope"])

    def test_paired_diagnostic_matches_ids_and_enumerates_nonzero_differences(self):
        self.fixture()
        result = self.audit()
        tasks = [dict(row) for row in result["per_task_metrics"]]
        for row in tasks:
            if row["stage"] == "test_eval":
                row.update(hard=1, soft=1)
        diagnostic = helper.paired_test_diagnostic(list(reversed(tasks)), result["stages"], True)
        self.assertEqual(diagnostic["metrics"]["hard"]["mean_difference_best_minus_baseline"], 0.5)
        self.assertEqual(diagnostic["metrics"]["hard"]["lower"], 0)
        self.assertEqual(diagnostic["metrics"]["hard"]["upper"], 1)
        for pair in diagnostic["pairs"]:
            self.assertEqual(pair["baseline_hard"], int(pair["task_id"]) % 2)

    def test_paired_diagnostic_rejects_missing_mismatched_duplicate_or_invalid_evidence(self):
        self.fixture()
        result = self.audit()
        original = result["per_task_metrics"]
        for kind in ("missing", "mismatched", "duplicate", "invalid_core"):
            with self.subTest(kind=kind):
                tasks = [dict(row) for row in original]
                index = next(i for i, row in enumerate(tasks) if row["stage"] == "test_eval")
                if kind == "missing":
                    tasks.pop(index)
                elif kind == "mismatched":
                    tasks[index]["task_id"] = "unpaired"
                elif kind == "duplicate":
                    tasks.append(dict(tasks[index]))
                diagnostic = helper.paired_test_diagnostic(tasks, result["stages"], kind != "invalid_core")
                self.assertEqual(diagnostic["status"], "unavailable_or_unverified")
                self.assertEqual(diagnostic["metrics"], {})
                self.assertIsNone(diagnostic["n_pairs"])

    def test_timing_and_origin_are_allowlisted_and_candidate_acceptance_is_explicit(self):
        stages = self.fixture()
        summary = json.loads((self.run / "summary.json").read_text())
        summary.update(best_origin="slow_update_placeholder_epoch_01", current_origin=SENTINELS[2])
        self.write("summary.json", summary)
        self.write("recovery_summary.json", {"status": "completed_pending_audit", "wall_seconds": 123.456})
        record = json.loads((self.run / "steps/step_0001/step_record.json").read_text())
        record.update(action="cam_re_evaluate")
        self.write("steps/step_0001/step_record.json", record)
        result = self.audit()
        self.assertEqual(result["skill_origins_as_recorded"]["best_origin"], "slow_update_placeholder_epoch_01")
        self.assertEqual(result["skill_origins_as_recorded"]["current_origin"], "unknown")
        self.assertEqual(result["cost"]["wall_time"]["wrapper_wall_seconds"], 123.456)
        self.assertEqual(result["cost"]["wall_time"]["trainer_wall_seconds"], 100)
        self.assertFalse(result["mechanisms"]["steps"][0]["candidate_accepted_by_recorded_action"])
        self.assertEqual(result["cost"]["combined_canonical_raw_usage"]["input_plus_output_tokens"], (stages * 4 + 1) * 110)
        self.assertNotIn(SENTINELS[2], json.dumps(result) + helper.markdown(result))

    def test_matching_metrics_without_skill_hash_proof_do_not_pass(self):
        self.fixture(reuse=True)
        self.text("skills/skill_v0001.md", "different final skill")
        result = self.audit()
        self.assertFalse(result["completeness"]["evaluation_provenance"]["final_test"]["verified"])
        self.assertFalse(result["completeness"]["core_probe_evidence_pass"])

    def test_final_test_reuse_requires_summary_marker(self):
        self.fixture(reuse=True)
        (self.run / "test_eval_final/summary.json").unlink()
        self.assertFalse(self.audit()["completeness"]["core_probe_evidence_pass"])

    def test_partial_usage_never_claims_complete_or_assumes_missing_input_zero(self):
        path = self.run / "usage.txt"
        for usage in ({"output_tokens": 10}, {"input_tokens": 100}, [], {"input_tokens": -1, "output_tokens": 10}):
            with self.subTest(usage=usage):
                path.write_text(json.dumps({"type": "turn.completed", "usage": usage}), encoding="utf-8")
                parsed = helper.usage_from_raw(path)
                self.assertFalse(parsed["usage_complete"])
                self.assertIsNone(parsed["input_plus_output_tokens"])
                self.assertEqual(helper.sum_usage([parsed])["coverage"], "partial_or_unavailable")

    def test_duplicate_rows_cannot_pass_and_raw_tokens_are_not_doubled(self):
        stages = self.fixture()
        self.text("selection_eval_baseline/results.jsonl", "\n".join(json.dumps(row) for row in self.rows + [self.rows[0]]) + "\n")
        result = self.audit()
        self.assertEqual(result["cost"]["target_canonical_raw_usage"]["input_plus_output_tokens"], stages * 4 * 110)
        self.assertFalse(result["completeness"]["core_probe_evidence_pass"])

    def test_running_task_or_out_of_range_scores_cannot_pass(self):
        self.fixture()
        changed = [dict(row) for row in self.rows]
        changed[0].update(status="running", hard=9)
        self.text("test_eval/results.jsonl", "\n".join(json.dumps(row) for row in changed) + "\n")
        result = self.audit()
        row = next(row for row in result["per_task_metrics"] if row["stage"] == "test_eval" and row["task_id"] == "0")
        self.assertFalse(row["scored"])
        self.assertIsNone(row["hard"])
        self.assertFalse(result["completeness"]["core_probe_evidence_pass"])

    def test_infrastructure_event_overrides_stale_complete_summary(self):
        self.fixture()
        self.text("test_eval/predictions/0/codex_raw.txt", json.dumps({"type": "error", "message": "401 invalid_refresh_token " + SENTINELS[2]}))
        result = self.audit()
        self.assertEqual(result["observation_status"], "invalid_infrastructure")
        self.assertIsNone(result["summary_metrics"]["test_hard"])
        self.assertEqual(result["summary_metrics"]["total_wall_time_s"], 100)
        self.assertFalse(result["completeness"]["core_probe_evidence_pass"])
        self.assertNotIn(SENTINELS[2], json.dumps(result))

    def test_parse_failure_blocks_acceptance(self):
        self.fixture()
        with (self.run / "test_eval/results.jsonl").open("a", encoding="utf-8") as handle:
            handle.write("{partial")
        self.assertFalse(self.audit()["completeness"]["core_probe_evidence_pass"])

    def network_notice(self):
        return json.dumps({"type": "error", "message": "Reconnecting... 1/5 (waiting for network: stream disconnected)"}) + "\n"

    def transport_sidecar(self, **overrides):
        record = {"notice_count": 1, "max_recovery_notices": 2, "recovered": True,
                  "status": "completed", "returncode": 0, "notices": [{"message": SENTINELS[2]}]}
        record.update(overrides)
        return record

    def test_one_or_two_explicit_notices_with_proven_recovery_can_pass(self):
        self.fixture()
        for count in (1, 2):
            with self.subTest(notices=count):
                self.text("test_eval/predictions/0/codex_raw.txt", self.network_notice() * count + self.raw())
                self.write("test_eval/predictions/0/transport_warnings.json", self.transport_sidecar(notice_count=count))
                result = self.audit()
                self.assertTrue(result["completeness"]["core_probe_evidence_pass"])
                self.assertEqual(result["transport"]["recovered_network_notice_count"], count)
                self.assertEqual(result["transport"]["recovered_network_affected_requests"], 1)
                self.assertEqual(result["transport"]["terminal_infrastructure_events"], 0)
                self.assertNotIn(SENTINELS[2], json.dumps(result))

    def test_notice_without_sidecar_is_pending_not_silently_recovered(self):
        self.fixture()
        self.text("test_eval/predictions/0/codex_raw.txt", self.network_notice() + self.raw())
        result = self.audit()
        self.assertFalse(result["completeness"]["core_probe_evidence_pass"])
        self.assertEqual(result["observation_status"], "completed_pending_validation")
        self.assertEqual(result["transport"]["recovered_network_notice_count"], 0)
        self.assertEqual(result["transport"]["unverified_transport_requests"], 1)

    def test_inconsistent_sidecar_or_more_than_two_notices_cannot_pass(self):
        self.fixture()
        failures = [self.transport_sidecar(notice_count=2), self.transport_sidecar(max_recovery_notices=3),
                    self.transport_sidecar(recovered=False), self.transport_sidecar(status="failed", returncode=1),
                    self.transport_sidecar(returncode=1), self.transport_sidecar(notice_count=3)]
        for index, sidecar in enumerate(failures):
            with self.subTest(case=index):
                count = 3 if index == len(failures) - 1 else 1
                self.text("test_eval/predictions/0/codex_raw.txt", self.network_notice() * count + self.raw())
                self.write("test_eval/predictions/0/transport_warnings.json", sidecar)
                result = self.audit()
                self.assertFalse(result["completeness"]["core_probe_evidence_pass"])
                self.assertEqual(result["transport"]["recovered_network_notice_count"], 0)

    def test_terminal_failures_override_recovered_sidecar(self):
        self.fixture()
        terminals = [json.dumps({"type": "turn.failed", "error": {"message": "Reconnecting network error"}}),
                     json.dumps({"type": "error", "message": "Reconnecting 401 invalid_refresh_token network"}),
                     "[stderr]\nERROR: stream disconnected", "[stderr]\nFATAL: model unavailable",
                     "[stderr]\n401 Unauthorized invalid_refresh_token"]
        for terminal in terminals:
            with self.subTest(terminal=terminal):
                self.text("test_eval/predictions/0/codex_raw.txt", self.network_notice() + self.raw() + terminal + "\n")
                self.write("test_eval/predictions/0/transport_warnings.json", self.transport_sidecar())
                result = self.audit()
                self.assertEqual(result["observation_status"], "invalid_infrastructure")
                self.assertFalse(result["completeness"]["core_probe_evidence_pass"])
                self.assertEqual(result["transport"]["recovered_network_notice_count"], 0)

    def test_stderr_notice_and_optimizer_recovery_are_counted_separately(self):
        self.fixture()
        self.text("model_calls/request1/raw_trace.txt", self.raw() + "[stderr]\nReconnecting... waiting for network\n")
        self.write("model_calls/request1/transport_warnings.json", self.transport_sidecar())
        result = self.audit()
        self.assertTrue(result["completeness"]["core_probe_evidence_pass"])
        self.assertEqual(result["cost"]["optimizer_canonical_raw_usage"]["recovered_network_notice_count"], 1)
        self.assertEqual(result["cost"]["target_canonical_raw_usage"]["recovered_network_notice_count"], 0)

    def test_no_completed_turn_does_not_prove_recovery(self):
        self.fixture()
        self.text("test_eval/predictions/0/codex_raw.txt", self.network_notice())
        self.write("test_eval/predictions/0/transport_warnings.json", self.transport_sidecar())
        self.assertFalse(self.audit()["completeness"]["core_probe_evidence_pass"])

    def test_notice_in_assistant_content_is_not_a_transport_event(self):
        self.fixture()
        self.text("test_eval/predictions/0/codex_raw.txt", json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "Reconnecting... waiting for network 401"}}) + "\n" + self.raw())
        result = self.audit()
        self.assertTrue(result["completeness"]["core_probe_evidence_pass"])
        self.assertEqual(result["transport"]["observed_reconnect_notice_count"], 0)


if __name__ == "__main__":
    unittest.main()
