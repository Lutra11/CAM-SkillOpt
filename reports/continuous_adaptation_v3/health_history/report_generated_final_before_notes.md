# Continuous adaptation v3 report

Status: **complete_seed_pairs**

CAM v3 versus baseline; no contemporaneous online v2 arm.

Two seed-pair differences and their mean are descriptive only; task bootstrap intervals do not replace the training-seed sample size of two.

Frozen test subset: 120 IDs (83 Cell-Level, 37 Sheet-Level).

Performance rows include only cells with queue status `accepted`, runner validation accepted, and matching fixed test evidence.
Failed, pending, missing, or invalid cells retain their status and returned usage metadata, but do not contribute a score.

## Cell status and returned usage

| Cell | Queue status | Runner validation | Token coverage | Returned total tokens | Total wall time (s) |
|---|---|---|---|---:|---:|
| baseline_seed42 | accepted | accepted (accepted: four train steps, paired A/B/A/B plan and exact fixed-test evidence verified) | partial | 6531529 | 6666.9 |
| cam_v3_seed42 | accepted | accepted (accepted: four train steps, paired A/B/A/B plan and exact fixed-test evidence verified) | partial | 5770052 | 6760.5 |
| cam_v3_seed43 | accepted | accepted (accepted: four train steps, paired A/B/A/B plan and exact fixed-test evidence verified) | partial | 5302477 | 5730.4 |
| baseline_seed43 | accepted | accepted (accepted: four train steps, paired A/B/A/B plan and exact fixed-test evidence verified) | partial | 5271872 | 5508.5 |

## Validated paired results

| Seed | Baseline passes / 120 | CAM v3 passes / 120 | CAM v3 − baseline | Task-ID wins / losses / ties | Cell-Level pass counts | Sheet-Level pass counts |
|---:|---:|---:|---:|---:|---:|---:|
| 42 | 68 / 120 | 64 / 120 | -4 (-3.333%) | 13 / 17 / 90 | 46 → 46 / 83 | 22 → 18 / 37 |
| 43 | 65 / 120 | 70 / 120 | +5 (+4.167%) | 13 / 8 / 99 | 45 → 48 / 83 | 20 → 22 / 37 |

Mean of the two seed differences: **+0.417%** (descriptive, n=2 training seed pairs). Task-ID bootstrap intervals are reported in JSON as descriptive only.

## Cell outcomes

- Seed 42: `complete_seed_pair`
- Seed 43: `complete_seed_pair`

The JSON report preserves full queue records, validation reasons, token summaries/accounting and coverage flags, returned token totals, and recorded timing per cell.
