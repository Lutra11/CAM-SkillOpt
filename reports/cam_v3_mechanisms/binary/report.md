# CAM v3 binary-score offline mechanism supplement

> Synthetic mechanism verification only. This is not SpreadsheetBench accuracy, calibration, or generalization evidence.
> The earlier continuous-score fixture is retained separately as a fractional-score diagnostic, not an actual hard-task score distribution.

Protocol SHA-256: `bb3307d4e42bf2cf23f53855ca94c32e95e5593c4a869a454308ae7501747546`  
Completed UTC: `2026-10-18T11:54:14.534616+00:00`  
Runtime: `3.12.15 | packaged by Anaconda, Inc. | (main, Oct  1 2026, 20:14:50) [MSC v.1942 64 bit (AMD64)]` / `Windows-11-10.0.26200-SP0`

The frozen design uses seeds 0–19, 40 paired binary items per run (20 per type, 10 current passes per type), and 500 paired bootstrap resamples. Both arms receive the same ID-aligned pairs.
v3 uses noninferiority margin 0.05 and subgroup mean regression tolerance 0.10 with at least five pairs per group.

| Scenario | Mean Δ | Cell Δ | Sheet Δ | v2 actions | v3 actions | v3 group guard |
|---|---:|---:|---:|---|---|---|
| clear_improvement | 0.250 | 0.250 | 0.250 | {"accept_new_best": 20} | {"accept_new_best": 20} | {"passed": 20} |
| clear_loss | -0.250 | -0.250 | -0.250 | {"reject": 20} | {"reject": 20} | {"blocked": 20} |
| unchanged | 0.000 | 0.000 | 0.000 | {"soft_accept": 20} | {"soft_accept": 20} | {"passed": 20} |
| sparse_loss | -0.025 | -0.050 | 0.000 | {"soft_accept": 20} | {"cam_re_evaluate": 20} | {"passed": 20} |
| noisy_exchange | 0.000 | 0.000 | 0.000 | {"soft_accept": 20} | {"cam_re_evaluate": 20} | {"passed": 20} |
| masked_group_loss | 0.175 | 0.500 | -0.150 | {"accept_new_best": 9, "soft_accept": 11} | {"cam_re_evaluate": 20} | {"blocked": 20} |

The full per-seed decisions, confidence bounds, counts, hypotheses, and hashes are in `results.json`. Offline guards reject socket connections and subprocess/model execution before importing the selection module; no model API packages or environment files are read.
