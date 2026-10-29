# CAM v2r1 focused study results

Study status: **incomplete**. Protocol: [`docs/experiments/CAM_V2_PLAN.md`](../../docs/experiments/CAM_V2_PLAN.md).

The primary endpoint is `test_hard`, the test score for the validation-gate-authorized incumbent. `final_test_hard` is secondary. The larger test result is never selected after looking at test data.

## Cell status and descriptive metrics

| Cell | Status | Best test hard | Final test hard | Tokens | Wall time (s) | Actions A/A-B/S/R/skip | Drift rollbacks | Memory evidence |
|---|---|---:|---:|---:|---:|---|---:|---|
| `qwen_baseline_42` | eligible | 0.532 | 0.496 | 18192623 | 20639.2 | 0/1/0/7/0 | unknown | stored=0; injected/completed=0/0; retrieval hits=0 |
| `qwen_cam_full_42` | eligible | 0.532 | 0.546 | 18174410 | 21564.8 | 0/1/7/0/0 | 3 | stored=17; injected/completed=18/16; retrieval hits=51 |
| `qwen_baseline_43` | incomplete: [Errno 2] No such file or directory: 'E:\\Programs\\CAM\\CAM-SkillOpt\\outputs\\matrix_v2r1\\spreadsheetbench\\qwen\\baseline_seed43\\summary.json' | — | — | — | — | — | — | — |
| `qwen_cam_full_43` | incomplete: [Errno 2] No such file or directory: 'E:\\Programs\\CAM\\CAM-SkillOpt\\outputs\\matrix_v2r1\\spreadsheetbench\\qwen\\cam_full_seed43\\summary.json' | — | — | — | — | — | — | — |
| `qwen_baseline_44` | incomplete: cell directory is missing | — | — | — | — | — | — | — |
| `qwen_cam_full_44` | incomplete: cell directory is missing | — | — | — | — | — | — | — |
| `qwen_cam_no_bootstrap_42` | incomplete: cell directory is missing | — | — | — | — | — | — | — |
| `qwen_cam_no_memory_42` | incomplete: cell directory is missing | — | — | — | — | — | — | — |
| `qwen_cam_no_adaptive_budget_42` | incomplete: cell directory is missing | — | — | — | — | — | — | — |

## Paired effects

Effects are treatment minus reference. Per-seed 95% intervals bootstrap matched test items (20,000 resamples); McNemar discordance p-values are descriptive. Across-seed mean and sample SD summarize only the observed training seeds.

### Primary: `test_hard`

| Seed | Baseline | CAM full | Delta (pp) | Paired item 95% CI (pp) | A-only / CAM-only | McNemar p |
|---:|---:|---:|---:|---:|---:|---:|
| 42 | 0.532 | 0.532 | +0.00 | [-5.36, +5.36] | 29 / 29 | 1.0000 |
| 43 | — | — | — | — | — | — |
| 44 | — | — | — | — | — | — |

Across available seeds: mean delta **+0.00 pp**, sample SD **unknown pp** (1 paired seeds).

### Secondary: `final_test_hard`

| Seed | Baseline | CAM full | Delta (pp) | Paired item 95% CI (pp) | A-only / CAM-only | McNemar p |
|---:|---:|---:|---:|---:|---:|---:|
| 42 | 0.496 | 0.546 | +5.00 | [-0.71, +10.71] | 29 / 43 | 0.1249 |
| 43 | — | — | — | — | — | — |
| 44 | — | — | — | — | — | — |

Across available seeds: mean delta **+5.00 pp**, sample SD **unknown pp** (1 paired seeds).

## Seed-42 ablations

Positive deltas favor CAM full. Holm adjustment is reported only when all three planned primary-endpoint McNemar comparisons are available; these single-seed component comparisons are exploratory.

| Ablation | Primary delta (pp) | 95% paired item CI (pp) | McNemar p | Holm adjusted p | Secondary delta (pp) |
|---|---:|---:|---:|---:|---:|
| `cam_no_bootstrap` | — | — | — | — | — |
| `cam_no_memory` | — | — | — | — | — |
| `cam_no_adaptive_budget` | — | — | — | — | — |

## Interpretation and limits

This is an exploratory focused study: three paired training seeds share one fixed test split. Item bootstrap intervals measure item-level uncertainty conditional on a trained seed; they do not represent training-seed variability. The across-seed mean and sample SD use only three observed seeds and should not be read as strong evidence of generalization.

The v2 protocol was motivated by v1 observations and changes to soft acceptance, drift protection, inconclusive memory, and action accounting. This report therefore evaluates the frozen v2r1 configuration; it is not a preregistered confirmation of a hypothesis independent of prior experiments. See the protocol for the rationale and prior evidence.

Costs use `cost_summary.json` when available, including its attempt coverage. A summary fallback can omit failed/retried process usage and is marked in the JSON warnings. Persistent-memory hits, injected groups, and completed injected groups are reported from their respective evidence; unknown or absent evidence remains unknown.

## Data coverage notes

- Ablations: Holm-adjusted p-values withheld until all 3 planned seed-42 comparisons are available
- final_test_hard: only 1/3 complete paired training seeds; no full-study conclusion is available
- test_hard: only 1/3 complete paired training seeds; no full-study conclusion is available
