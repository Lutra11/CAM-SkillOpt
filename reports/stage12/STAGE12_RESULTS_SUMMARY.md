# Stage 12 SpreadsheetBench Results Summary — validity correction

Updated: 2026-09-09 (Asia/Shanghai)

Recovery update: a fresh n=4 P0 has now completed and passed artifact/infrastructure auditing. Baseline, best and final test scores are all 0.25; the patch candidate was not accepted by CAM. This does not validate the historical formal runs below or establish a method advantage. See the [final recovery report](recovery_20260909/P0_FINAL_REPORT.md) and [formal-experiment prerequisites](recovery_20260909/FORMAL_EXPERIMENT_PREREQUISITES.md).

Dataset: SpreadsheetBench verified split

Historical target: gpt-5.6-terra via Codex exec

Historical optimizer: gpt-5.6-sol via Codex CLI

Historical formal setting: train_size=16, selection=8, test=8, seed=42, exec_timeout=420s

## 1. Correction that supersedes the previous interpretation

The previously reported formal all-zero scores are **not valid negative scientific results**. A read-only audit of the original per-request results and raw traces confirms HTTP 401 / invalid_refresh_token failures before target code generation. No conversation.json was produced in the affected formal runs or CAM-Full supplementation evaluations. All four training steps were skipped with no patches, and the CAM gate was not used.

Therefore, previous descriptions such as “all four methods completed”, “negative but informative result”, and “P1/P2 were technically stable and faster” must not be used as claims about experimental validity. Reaching a training-loop endpoint or writing a summary does not establish model execution, optimization, or a fair ablation.

Original files under SkillOpt/outputs are preserved. The corrected status and audit are additive records; recorded hard/soft=0 values remain in historical raw files only and are excluded from paper result tables and comparisons.

## 2. Formal run validity

| Method / artifact | Status requested for tracking | Audit diagnosis | 401 raw traces / raw traces | conversation.json | Patches | CAM gate used | Eligible for paper |
|---|---|---|---:|---:|---:|---:|---|
| CAM-Full fix1 | invalid_auth | confirmed_auth_failure | 52/52 | 0 | 0 | 0 | No |
| CAM-Full fix2 | invalid_auth | confirmed_auth_failure | 26/26 | 0 | 0 | 0 | No |
| CAM-Full fix2 valid_seen supplementation | invalid_auth | confirmed_auth_failure | 8/8 | 0 | n/a | n/a | No |
| CAM-Full fix2 valid_unseen supplementation | invalid_auth | confirmed_auth_failure | 8/8 | 0 | n/a | n/a | No |
| CAM-No-Bootstrap fix1 | validity_pending_diagnosis | confirmed_auth_failure | 56/56 | 0 | 0 | 0 | No |
| CAM-No-Adaptive-Budget fix1 | validity_pending_diagnosis | confirmed_auth_failure | 56/56 | 0 | 0 | 0 | No |
| CAM-No-Memory fix1 | validity_pending_diagnosis | confirmed_auth_failure | 56/56 | 0 | 0 | 0 | No |

The three ablations retain the user's provisional tracking label. Independent per-run evidence now confirms authentication contamination in each, so the provisional label does **not** mean scientifically valid. Each of their 56 raw traces contains both 401 and invalid_refresh_token. A fresh valid run is required; these scores cannot be repaired by simply re-labeling the zero outcomes.

Raw trace file counts are artifact counts, not unique task counts or API request counts. Some aborted runs contain trace files without completed results rows. Error strings in results rows can be truncated; classification must also inspect the raw trace.

## 3. Concurrency probe correction

| Historical probe | workers / analyst_workers | 401 traces | Conversations | Patches | Reported analyst calls | CAM gate used | Interpretation |
|---|---|---:|---:|---:|---:|---:|---|
| P0 fix1 | 1 / 1 | 0/24 | 24 | 2 | 4 | 1 | Retain as historical small probe; selection hard=0.50, test hard=0.25 |
| P1 fix1 (directory suffix fix2) | 4 / 1 | 24/24 | 0 | 0 | unavailable in summary | 0 | invalid_auth; zero scores and 173.9s wall time cannot assess concurrency quality |
| P2 fix2 | 4 / 2 | 24/24 | 0 | 0 | unavailable in summary | 0 | invalid_auth; zero scores and 157.7s wall time cannot assess concurrency quality |

P0 fix1 demonstrates a historical successful target/reflection/patch/gate path on a small sample, not current authentication health or formal CAM effectiveness. Its 24 result rows are repeated evaluations of a four-sample probe protocol, not 24 independent test samples.

Recovery uses workers=1 and analyst_workers=1 as requested. No claim is made that this setting is empirically superior to concurrency 4, because the earlier concurrent runs were confounded by authentication failure.

## 4. Reference baseline and paper boundary

The historical CAM-Off eval_only reference records valid_unseen n=8, hard=0.125 and soft=0.125. This audit did not revalidate its per-request artifacts or establish a matched protocol with the formal CAM runs. It remains an archival reference, not a fair superiority comparison.

There is currently no validated formal CAM ablation table from this batch. Do not compute method differences, paired confidence intervals, patch-yield effectiveness, or performance conclusions from the invalid/pending runs. Their authentication incident counts can be reported as engineering diagnostics if clearly separated from task scores.

A publishable matrix requires fresh matched Static, CAM-Off, CAM-Full and ablation runs after the low-cost recovery gates succeed, with actual optimizer calls, patches, CAM gate/budget/memory evidence and complete selection/test artifacts. Start with seed=42; expand seeds only after validity is established.

## 5. Recovery gates for tasks 1–5

1. Verify the target and optimizer Codex environments with two consecutive minimal requests per role: normal exit, correct observed model, and no authentication errors.
2. Fail fast on infra_error; inject a 401 and verify termination within the first one or two requests with an error summary.
3. Persist per-stage results.jsonl, failure type, conversation artifact, raw trace and stage statistics, including failed stages. Do not fabricate a successful model conversation for failed authentication.
4. Run a one-sample end-to-end preflight that proves llm_ok, code_ok, exec_ok, scoring, real optimizer invocation and patch production.
5. Run the fixed four-sample P0 probe with workers=1 / analyst_workers=1. Require no infrastructure errors, at least one patch, analyst_calls>0, complete selection/test results and intact process/boot continuity. If authentication works but no patch is generated, compare target reasoning none vs low on the same four samples while holding every other parameter fixed.

Formal ablations and paper result construction remain downstream of these gates.

## 6. Evidence and independent Windows diagnostics

- [Historical audit, including per-stage counts](recovery_20260909/historical_validity_audit.json)
- [Audit method and Chinese correction note](recovery_20260909/VALIDITY_CORRECTION.md)
- [Read-only audit script](recovery_20260909/audit_historical_runs.ps1)
- [Windows diagnostic snapshot](recovery_20260909/windows_health_snapshot.json)
- [Windows diagnostic interpretation](recovery_20260909/WINDOWS_DIAGNOSTICS.md)
- [Read-only Windows snapshot script](../../scripts/cam_windows_health.ps1)

The original chronological execution log and copied run summaries are retained as historical records. If they describe the affected zero scores as completed valid results, this correction takes precedence. Restart evidence and authentication failures are tracked separately; neither proves the other caused it.
