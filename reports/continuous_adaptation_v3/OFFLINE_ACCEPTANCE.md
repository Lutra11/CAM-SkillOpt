# Continuous Adaptation v3 Offline Acceptance

- Protocol tests: 141 passed in 10.176 seconds.
- Config validation: `configs/experiments/cam_v3_mechanisms.yaml` resolved 115 keys; every override passed.
- Commands:
  - `E:\MiniConda\envs\skillopt\python.exe -X utf8 -m unittest tests.test_continuous_adaptation_v3 tests.test_report_continuous_adaptation_v3 tests.test_cam_gate_trainer tests.test_cam_selection_guard tests.test_cam_v2 tests.test_cam_v3_gate tests.test_cam_v3_memory tests.test_cam_v3_budget tests.test_cam_v3_trainer -v`
  - `E:\MiniConda\envs\skillopt\python.exe -X utf8 scripts/verify_config.py --config configs/experiments/cam_v3_mechanisms.yaml`
- No model/API calls were made. The sanitized authentication proof was read and validated offline.
- No full test output log was captured; this artifact records the acceptance summary and exact commands.

## Frozen preparation

- Official `--prepare` was run twice; the second run preserved the freeze SHA-256 (`3e36613b9d9db411f806cca95a6644a6a66612924c925add240d44ffcd0bb597`) and `created_at` (`2026-10-18T12:36:42.365423+00:00`).
- The 1,378 frozen source fingerprints verify. The sanitized auth proof validates for optimizer and target; its SHA-256 is `b2d92daeeca77544a92f4f90ece9da9437fa8e71d4805ac7bef701c2b12295bd`.
- All four runtime queue cells remain pending. All train task orders for baseline/CAM and seeds 42/43 exactly match the v1 freeze; the fixed 120 test IDs, hash, and 83/37 counts also match.
- The official output is `outputs/studies/continuous_adaptation_v3/freeze.json`; the queue is `outputs/studies/continuous_adaptation_v3/queue_state.json`.
- No online run was launched and no model/API was called.
