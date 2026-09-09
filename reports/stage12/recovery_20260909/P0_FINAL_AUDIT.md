# P0 recovery audit

Run: p0_newcli_retry_01

Status: completed_core_evidence_verified

Scope: exploratory n=4; not a formal CAM ablation result.

| Stage | Recorded / expected | Scored | Hard (completed tasks) | Soft (completed tasks) | Complete |
|---|---:|---:|---:|---:|---|
| final_selection_eval | 4 / 4 | 4 | 0.5 | 0.5 | True |
| selection_eval_baseline | 4 / 4 | 4 | 0.25 | 0.25 | True |
| steps/step_0001/rollout | 4 / 4 | 4 | 0.0 | 0.0 | True |
| steps/step_0001/selection_eval | 4 / 4 | 4 | 0.75 | 0.75 | True |
| test_eval | 4 / 4 | 4 | 0.25 | 0.25 | True |
| test_eval_baseline | 4 / 4 | 4 | 0.25 | 0.25 | True |

Skill origins as recorded: {"best_origin": "slow_update_placeholder_epoch_01", "current_origin": "slow_update_placeholder_epoch_01"}

Step decisions (a scored candidate is not necessarily an accepted skill): [{"step": 1, "patches": 2, "action": "cam_re_evaluate", "candidate_selection_hard": 0.75, "candidate_accepted_by_recorded_action": false, "cam_gate_used": true, "gate": {"mean_improvement": 0.5, "lower_confidence_bound": 0.0, "upper_confidence_bound": 1.0, "n_pairs": 4, "bootstrap_samples": 10000}, "gate_action": "re_evaluate", "adaptive_budget_observed": true, "edit_budget": 8, "failure_confidence": {"confidence": 1.0, "entropy": -0.0, "normalized_entropy": 0.0, "total_failures": 4}, "n_edits_merged": 3, "n_edits_ranked": 3, "wall_seconds": 478.9}]

Paired baseline-versus-best test diagnostic (not generalizable): {"status": "verified_exploratory_diagnostic", "baseline_stage": "test_eval_baseline", "best_stage": "test_eval", "n_pairs": 4, "pairs": [{"task_id": "52532", "baseline_hard": 0, "best_hard": 0, "hard_difference": 0, "baseline_soft": 0.0, "best_soft": 0.0, "soft_difference": 0.0}, {"task_id": "41-47", "baseline_hard": 0, "best_hard": 0, "hard_difference": 0, "baseline_soft": 0.0, "best_soft": 0.0, "soft_difference": 0.0}, {"task_id": "59794", "baseline_hard": 1, "best_hard": 1, "hard_difference": 0, "baseline_soft": 1.0, "best_soft": 1.0, "soft_difference": 0.0}, {"task_id": "42515", "baseline_hard": 0, "best_hard": 0, "hard_difference": 0, "baseline_soft": 0.0, "best_soft": 0.0, "soft_difference": 0.0}], "metrics": {"hard": {"mean_difference_best_minus_baseline": 0.0, "lower": 0.0, "upper": 0.0, "all_observed_differences_zero": true}, "soft": {"mean_difference_best_minus_baseline": 0.0, "lower": 0.0, "upper": 0.0, "all_observed_differences_zero": true}}, "confidence_level": 0.95, "resamples": 256, "method": "exact_enumeration_of_all_ordered_paired_bootstrap_resamples_linear_percentiles", "scope": "diagnostic_only_four_observed_tasks_not_formal_efficacy_evidence", "note": "A degenerate [0,0] interval means the four observed paired differences are all zero; it does not prove equivalence or generalize beyond these tasks."}

Patch yield: 2 payload groups / 2 observed called groups; actual analyst request artifacts=2.

CAM Gate used=1; adaptive budget computations=1.

Budget transitions: [{"step": 1, "previous_or_configured_budget": 4, "chosen_budget": 8, "changed": true, "baseline_kind": "configured_initial_not_previous_observation"}]

Persistent rejected memory: {"file_present": false, "stored_items": 0, "observed_write_events": 0, "reported_items_added": 0, "write_records": [], "retrieval_wiring": "no_trainer_retrieve_call", "retrieval_calls": null, "retrieval_hits": null, "note": "Missing retrieval instrumentation is null, not zero measured hits. Step-buffer context is distinct from persistent rejected-memory retrieval. Initialization is not activation."}

Target canonical raw usage: {"completed_turn_events": 24, "input_tokens": 320404, "cached_input_tokens": 86528, "output_tokens": 7434, "request_artifacts": 24, "canonical_files": 24, "files_with_usage": 24, "usage_events_with_missing_fields": 0, "input_plus_output_tokens": 327838, "coverage": "complete", "recovered_network_notice_count": 4, "recovered_network_affected_requests": 3, "unverified_transport_requests": 0}

Optimizer canonical raw usage: {"completed_turn_events": 3, "input_tokens": 54436, "cached_input_tokens": 0, "output_tokens": 2106, "request_artifacts": 3, "canonical_files": 3, "files_with_usage": 3, "usage_events_with_missing_fields": 0, "input_plus_output_tokens": 56542, "coverage": "complete", "recovered_network_notice_count": 0, "recovered_network_affected_requests": 0, "unverified_transport_requests": 0}

Combined canonical raw usage: {"completed_turn_events": 27, "input_tokens": 374840, "cached_input_tokens": 86528, "output_tokens": 9540, "request_artifacts": 27, "canonical_files": 27, "files_with_usage": 27, "usage_events_with_missing_fields": 0, "input_plus_output_tokens": 384380, "coverage": "complete", "recovered_network_notice_count": 4, "recovered_network_affected_requests": 3, "unverified_transport_requests": 0}

Wall-time scopes: {"wrapper_wall_seconds": 1128.313, "trainer_wall_seconds": 979.2, "note": "Different scopes: wrapper spans the probe; trainer timer starts after initial selection-baseline evaluation and includes later evaluations. Do not sum these nested timings or call their difference pure model time. Verify source references and manifest before attributing this scope."}

Transport recovery evidence: {"terminal_infrastructure_events": 0, "observed_reconnect_notice_count": 4, "recovered_network_notice_count": 4, "recovered_network_affected_requests": 3, "unverified_transport_requests": 0, "note": "Only explicit Reconnecting network notices may recover, with completed turn, matching sidecar count <=2, max=2, recovered=true, status=completed and exit=0. Auth, turn.failed and terminal ERROR/FATAL always invalidate. Missing/inconsistent recovery evidence fails the core gate; recovered notices remain reported."}

No price assumed. Prefer canonical raw events: target tracker records zero tokens; Codex and Claude expose the same common tracker and the trainer sums both, potentially doubling optimizer counters. No blanket correction factor applied. Cached input is a subset, never added twice. Raw event counts are observed turns, not billed-request counts.

Completeness: {"required_selection_test_stages": {"selection_eval_baseline": true, "test_eval_baseline": true, "test_eval": true}, "all_task_transcript_and_trace_files_present": true, "core_probe_evidence_pass": true, "core_checks": {"training_summary_present": true, "recovery_summary_completed": true, "no_infrastructure_errors": true, "transport_recovery_evidence_consistent": true, "exact_four_sample_protocol": true, "matching_frozen_source": true, "complete_single_training_step": true, "all_task_stages_complete": true, "required_selection_test_stages_complete": true, "candidate_selection_evidence_verified": true, "final_selection_evidence_verified": true, "final_test_evidence_verified": true, "summary_scores_match_artifacts": true, "nonempty_assistant_transcripts_and_raw_files": true, "no_orphan_target_request_traces": true, "completed_optimizer_request": true, "patch_from_completed_reflection": true, "no_parse_issues": true}, "evaluation_provenance": {"candidate_selection": [{"stage": "steps/step_0001/selection_eval", "verified": true, "kind": "direct", "source": "steps/step_0001/selection_eval"}], "final_selection": {"verified": true, "kind": "direct", "source": "final_selection_eval", "derived_hard": 0.5, "derived_soft": 0.5, "derived_from_verified_source": true}, "final_test": {"verified": true, "kind": "same_skill_reuse", "source": "test_eval", "derived_hard": 0.25, "derived_soft": 0.25, "derived_from_verified_source": true}, "current_best_skill_hashes_match": true}, "boot_process_continuity": "requires_external_start_end_snapshots", "final_selection_metric_present": true, "final_test_metric_present": true, "conditional_reuse_note": "Reuse requires matching local skill fingerprints, matching scores and source task artifacts; final-test reuse additionally requires its persisted summary marker. Missing or unproven reuse fails the core gate. Reuse adds no independent samples.", "formal_prerequisite": "Persistent rejected-memory retrieval must be wired and observed before claiming CAM-Full/No-Memory formal activation; do not infer from config."}

Source references (current source; compare manifest hash before attributing to the run):

- rejected_memory_initialization: `skillopt/engine/trainer.py` lines [1016]
- rejected_memory_write_call: `skillopt/engine/trainer.py` lines [1734]
- rejected_memory_retrieve_call: `skillopt/engine/trainer.py` lines []
- separate_step_buffer_context: `skillopt/engine/trainer.py` lines [1189]
- budget_computation: `skillopt/engine/trainer.py` lines [1338]
- memory_write_stat: `skillopt/engine/trainer.py` lines [1747]
- baseline_selection_call_before_loop_timer: `skillopt/engine/trainer.py` lines [1050]
- trainer_wall_timer_start: `skillopt/engine/trainer.py` lines [1073]
- trainer_wall_timer_end: `skillopt/engine/trainer.py` lines [2475]
- wrapper_wall_timer_start: `scripts/cam_recovery.py` lines [94, 285]
- wrapper_wall_timer_end: `scripts/cam_recovery.py` lines [296]
- memory_retrieve_definition: `skillopt/cam/rejected_memory.py` lines [128]
- codex_tracker_summary_added: `skillopt/model/__init__.py` lines [362]
- claude_tracker_summary_added: `skillopt/model/__init__.py` lines [373]
- target_tracker_zero_tokens: `skillopt/model/codex_harness.py` lines [983]

Partial observations must not be promoted to completed scores. Completed task means execution/scoring pipeline produced a numeric outcome, not that the task succeeded.

Allowlisted aggregate/per-task metrics only; no task instructions, workbook contents, prompts, responses, credentials, raw trace text or memory content exported.
