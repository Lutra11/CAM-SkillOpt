# CAM v3 offline mechanism verification

> Synthetic mechanism verification only. This is not SpreadsheetBench accuracy, calibration, or generalization evidence.

Protocol SHA-256: `b2d2d1e5364857fdfe04b4c605febc70d0937972ee2576c99aa9e1a7706c57c4`  
Completed UTC: `2026-10-18T11:51:41.713701+00:00`  
Runtime: `3.12.15 | packaged by Anaconda, Inc. | (main, Oct  1 2026, 20:14:50) [MSC v.1942 64 bit (AMD64)]` / `Windows-11-10.0.26200-SP0`

## Paired gate scenarios

Each row aggregates 20 fixed seeds over 40 paired items (20 per task type), with 500 paired bootstrap resamples.
v3 uses a 0.05 noninferiority margin and a 0.10 subgroup mean regression tolerance with at least five pairs per group.

| Scenario | Mean Δ | v2 actions | v3 actions | v3 group guard |
|---|---:|---|---|---|
| clear_improvement | 0.1216 | {"accept_new_best": 20} | {"accept_new_best": 20} | {"passed": 20} |
| clear_loss | -0.1224 | {"reject": 20} | {"reject": 20} | {"blocked": 20} |
| unchanged | 0.0000 | {"soft_accept": 20} | {"soft_accept": 20} | {"passed": 20} |
| small_loss | -0.0303 | {"reject": 20} | {"reject": 20} | {"passed": 20} |
| noisy_mixed_gains_losses | 0.0159 | {"soft_accept": 20} | {"soft_accept": 20} | {"passed": 20} |
| subgroup_regression_masked_by_gains | 0.0245 | {"soft_accept": 20} | {"cam_re_evaluate": 20} | {"blocked": 20} |

## Memory fixture

| Policy | Hits | Zero-delta retained | Cross-type distractor retrieved |
|---|---|---|---|
| legacy | sheet-distractor, cell-zero | True | True |
| evidence_v3 | cell-zero | True | False |

Bundle record count for 3 edits: legacy=3, evidence_v3=1.

## Observed mechanism details

- All six scenario outcomes are generated synthetic score data; they do not estimate real-task performance.
- In small_loss, the paired bootstrap returned reject in all 20 seeds. This follows the gate's conservative precedence: statistically demonstrated harm is rejected; the 0.05 noninferiority margin is considered for bootstrap-inconclusive candidates.
- The subgroup-regression fixture had aggregate mean improvement near +0.025 while sheet_level regressed near -0.15; v2 soft-accepted all 20 runs and the v3 group guard requested reevaluation in all 20.

## Budget fixtures

| Fixture | Budget | Candidates | Eligible | Duplicates | Conflicts | Unknown provenance |
|---|---:|---:|---:|---:|---:|---|
| duplicate | 2 | 2 | 2 | 1 | 0 | False |
| conflict | 1 | 3 | 1 | 0 | 2 | False |
| no_provenance | 2 | 2 | 2 | 0 | 0 | True |
| explicit_ids | 2 | 3 | 2 | 0 | 0 | False |

## Reproducibility

The full 120 gate decisions, fixtures, protocol, and source hashes are in the adjacent JSON files. The script patches socket connections and subprocess entry points to raise before importing CAM modules; no model API imports or network calls are used.
