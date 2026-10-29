# Continuous Adaptation v3

This is a new, opt-in sequential study. It does not modify or reuse the v1 study's output directory, freeze, or results. The protocol compares baseline with CAM v3 at seeds 42 and 43, using the same SpreadsheetBench task splits and task-ID selection rules as v1.

The four cells run in this fixed order:

1. baseline, seed 42
2. CAM v3, seed 42
3. CAM v3, seed 43
4. baseline, seed 43

Each cell trains for four steps with a batch size of 20. The steps alternate Cell-Level and Sheet-Level tasks (A/B/A/B); each seed pair uses identical task IDs. Both arms use final selection validation and disable built-in test evaluation. After training, the runner evaluates only the selected best skill against the same frozen 120-task test subset, stratified as 83 Cell-Level and 37 Sheet-Level tasks.

CAM v3 enables the bootstrap gate, noninferiority soft-acceptance policy with margin 0.05, descriptive per-task-type group guard with tolerance 0.10 and at least five pairs per group, and evidence-v3 memory and budget policies. The subgroup guard is a descriptive mean-regression tolerance, not a confidence guarantee.

## Offline preparation and sequential execution

Run `python -X utf8 scripts/run_continuous_adaptation_v3.py --prepare` to create the v3 freeze and an initial runtime queue. Preparation performs no model calls. Repeating preparation is idempotent when all frozen inputs match; it rejects changed inputs and never resets an existing runtime queue. The freeze records the resolved configs, ordered task IDs, fixed test IDs and hashes, task scope, and source fingerprints/snapshots. After the first freeze, source changes require a new study version.

Review the freeze and the sanitized two-role authentication proof at `reports/cam_v3_online/auth/probe_report.json`. `--run` requires a recent successful HTTP 200 proof for the exact optimizer and target model, endpoint, authentication mode, and credential-source names in the frozen configuration. It validates the proof and binds the already-probed credentials before importing model backends. It does not probe the service or store credential values. If proof validation fails, no training cell starts. The runner holds one operating-system lock for the queue and allows one lane only. Every cell must pass its strict artifact validator before the next cell starts.

The freeze, runtime queue, task-order manifests, and source snapshot are under `outputs/studies/continuous_adaptation_v3/`. Cell outputs are under `outputs/continuous_adaptation_v3/spreadsheetbench/qwen/<cell>/`; cell logs and study reports are under `reports/continuous_adaptation_v3/`. The reporter is `scripts/report_continuous_adaptation_v3.py` and accepts only cells certified by the runner's validator.

The cell validator checks the resolved frozen configuration, exactly four contiguous training steps and matching action counts, per-step CAM gate evidence for CAM cells, absence of CAM evidence in baseline cells, the 120 unique fixed test IDs and their hash, the 83/37 task-type counts, and agreement between row-level and recorded scores. It also rejects extra test evaluations. Freeze/source checks run before and after each child process.

Returned backend token totals are refreshed after the posttraining test. Legacy token accounting is marked partial; it does not claim complete per-HTTP billing attribution. Authentication preflight is reported as an auth check only, with no billing estimate.

No online run is started by `--prepare`; a run requires the explicit `--run` flag and a valid proof file. Tests for this runner use synthetic/local artifacts and do not call a model API.
