# P0 recovery audit

Run: p0_http_02

Status: invalid_infrastructure

Scope: exploratory n=4; not a formal CAM ablation result.

| Stage | Recorded / expected | Scored | Hard (completed tasks) | Soft (completed tasks) | Complete |
|---|---:|---:|---:|---:|---|
| selection_eval_baseline | 4 / 4 | 4 | 0.5 | 0.5 | True |
| steps/step_0001/rollout | 3 / 4 | 2 | 0.0 | 0.0 | False |

Patch yield: 0 payload groups / 0 observed called groups; actual analyst request artifacts=0.

CAM Gate used=0; adaptive budget computations=0.

Budget transitions: []

Persistent rejected memory: {"file_present": false, "stored_items": 0, "observed_write_events": 0, "reported_items_added": 0, "write_records": [], "retrieval_wiring": "no_trainer_retrieve_call", "retrieval_calls": null, "retrieval_hits": null, "note": "Missing retrieval instrumentation is null, not zero measured hits. Step-buffer context is distinct from persistent rejected-memory retrieval. Initialization is not activation."}

Target canonical raw usage: {"completed_turn_events": 6, "input_tokens": 78517, "cached_input_tokens": 0, "output_tokens": 1250, "request_artifacts": 7, "canonical_files": 7, "files_with_usage": 6, "usage_events_with_missing_fields": 0, "input_plus_output_tokens": 79767, "coverage": "partial_or_unavailable"}

Optimizer canonical raw usage: {"completed_turn_events": 0, "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "request_artifacts": 0, "canonical_files": 0, "files_with_usage": 0, "usage_events_with_missing_fields": 0, "input_plus_output_tokens": null, "coverage": "partial_or_unavailable"}

No price assumed. Prefer canonical raw events: target tracker records zero tokens; Codex and Claude expose the same common tracker and the trainer sums both, potentially doubling optimizer counters. No blanket correction factor applied. Cached input is a subset, never added twice. Raw event counts are observed turns, not billed-request counts.

Completeness: {"required_selection_test_stages": {"selection_eval_baseline": true, "test_eval_baseline": false, "test_eval": false}, "all_task_transcript_and_trace_files_present": false, "core_probe_evidence_pass": false, "core_checks": {"training_summary_present": false, "recovery_summary_completed": false, "no_infrastructure_errors": false, "exact_four_sample_protocol": true, "matching_frozen_source": false, "complete_single_training_step": false, "all_task_stages_complete": false, "required_selection_test_stages_complete": false, "candidate_selection_evidence_verified": false, "final_selection_evidence_verified": false, "final_test_evidence_verified": false, "summary_scores_match_artifacts": false, "nonempty_assistant_transcripts_and_raw_files": false, "no_orphan_target_request_traces": true, "completed_optimizer_request": false, "patch_from_completed_reflection": false, "no_parse_issues": true}, "evaluation_provenance": {"candidate_selection": [], "final_selection": {"verified": false, "kind": "missing_or_unproven", "source": null}, "final_test": {"verified": false, "kind": "missing_or_unproven", "source": null}, "current_best_skill_hashes_match": false}, "boot_process_continuity": "requires_external_start_end_snapshots", "final_selection_metric_present": false, "final_test_metric_present": false, "conditional_reuse_note": "Reuse requires matching local skill fingerprints, matching scores and source task artifacts; final-test reuse additionally requires its persisted summary marker. Missing or unproven reuse fails the core gate. Reuse adds no independent samples.", "formal_prerequisite": "Persistent rejected-memory retrieval must be wired and observed before claiming CAM-Full/No-Memory formal activation; do not infer from config."}

Source references (current source; compare manifest hash before attributing to the run):

- rejected_memory_initialization: `skillopt/engine/trainer.py` lines [1016]
- rejected_memory_write_call: `skillopt/engine/trainer.py` lines [1734]
- rejected_memory_retrieve_call: `skillopt/engine/trainer.py` lines []
- separate_step_buffer_context: `skillopt/engine/trainer.py` lines [1189]
- budget_computation: `skillopt/engine/trainer.py` lines [1338]
- memory_write_stat: `skillopt/engine/trainer.py` lines [1747]
- memory_retrieve_definition: `skillopt/cam/rejected_memory.py` lines [128]
- codex_tracker_summary_added: `skillopt/model/__init__.py` lines [362]
- claude_tracker_summary_added: `skillopt/model/__init__.py` lines [373]
- target_tracker_zero_tokens: `skillopt/model/codex_harness.py` lines [983]

Partial observations must not be promoted to completed scores. Completed task means execution/scoring pipeline produced a numeric outcome, not that the task succeeded.

Allowlisted aggregate/per-task metrics only; no task instructions, workbook contents, prompts, responses, credentials, raw trace text or memory content exported.
