# Stage 12 SpreadsheetBench Results Summary

Date: 2026-09-09  
Dataset: SpreadsheetBench verified split  
Target model: gpt-5.6-terra via Codex exec  
Optimizer model: gpt-5.6-sol via Codex CLI  
Formal setting: train_size=16, selection=8, test=8, seed=42, exec_timeout=420s

## 1. Concurrency probe

| Run | workers | analyst_workers | selection hard | test hard | wall time | status |
|---|---:|---:|---:|---:|---:|---|
| P0 fix1 | 1 | 1 | 0.5000 | 0.2500 | 915.1s | completed |
| P1 fix1 | 4 | 1 | 0.0000 | 0.0000 | 173.9s | completed, but no patch |
| P2 | 4 | 2 | 0.0000 | 0.0000 | 157.7s | completed, but no patch |

Selected formal concurrency:

```text
workers = 1
analyst_workers = 1
```

Rationale: P1/P2 were faster and technically stable, but both collapsed to zero success and produced no usable patches. P0 fix1 was slower but preserved non-zero selection/test signal.

## 2. Formal CAM ablation results

| Method | Source of final metric | selection hard | test hard | test soft | steps | skips | wall time | status |
|---|---|---:|---:|---:|---:|---:|---:|---|
| CAM-Full | eval_only supplementation | 0.0000 | 0.0000 | 0.0000 | 4 | 4 | n/a | completed via supplementation |
| CAM-No-Bootstrap | train summary | 0.0000 | 0.0000 | 0.0000 | 4 | 4 | 3201.3s | completed |
| CAM-No-Adaptive-Budget | train summary | 0.0000 | 0.0000 | 0.0000 | 4 | 4 | 2534.1s | completed |
| CAM-No-Memory | train summary | 0.0000 | 0.0000 | 0.0000 | 4 | 4 | 2633.9s | completed |

## 3. Reference baseline

| Reference | Split | n | hard | soft | Note |
|---|---|---:|---:|---:|---|
| CAM-Off best skill eval | valid_unseen | 8 | 0.1250 | 0.1250 | independent eval_only supplementation |
| P0 fix1 CAM-Full probe | valid_unseen | 4 | 0.2500 | 0.2500 | small probe, not formal-size result |

## 4. Interpretation for paper draft

The current formal SpreadsheetBench run is a negative but informative result. Under the Codex target-execution setting, all four CAM variants completed their training loops, but every training step produced `skip_no_patches`. This means the reflection/update chain did not receive usable edits from the optimizer side, so CAM gating variants could not express meaningful differences.

The most defensible paper claim is therefore not that CAM improves SpreadsheetBench in the current setting. The defensible claim is that CAM-SkillOpt can be instrumented and evaluated on real SpreadsheetBench tasks, but the current Codex-based target execution exposes a boundary condition: when target-side spreadsheet code generation fails systematically, confidence-aware skill updating has little material to optimize.

Recommended reporting:

- Keep the P0 fix1 probe as evidence that the repaired direct-code path can produce non-zero signal.
- Report the formal ablation table honestly as all-zero under the present seed/configuration.
- Discuss the bottleneck as a target-execution / patch-yield limitation rather than a pure CAM gate failure.
- Treat CAM-Off valid_unseen=0.1250 as a reference result, not a directly dominant full formal baseline, because it was obtained through a different completed run and later eval-only supplementation.

## 5. Artifacts

Key records are under:

```text
reports/stage12/STAGE12_NEXT_STEP_EXECUTION_LOG.md
reports/stage12/formal_cam_full_seed42_w1_a1_fix2_train/
reports/stage12/eval_only_cam_full_fix2_best_valid_seen_8_w1_a1/
reports/stage12/eval_only_cam_full_fix2_best_valid_unseen_8_w1_a1/
reports/stage12/formal_cam_no_bootstrap_seed42_w1_a1_fix1/
reports/stage12/formal_cam_no_adaptive_budget_seed42_w1_a1_fix1/
reports/stage12/formal_cam_no_memory_seed42_w1_a1_fix1/
```
