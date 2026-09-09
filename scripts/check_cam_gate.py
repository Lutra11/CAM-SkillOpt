"""Offline CAM state-transition acceptance; synthetic evidence, no live models."""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="New directory; existing paths are refused")
    args = parser.parse_args()
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=False)
    from tests.test_cam_gate_trainer import CAMGateTrainerTests, INITIAL_SKILL
    case_directories = {}

    class DurableTrainerTests(CAMGateTrainerTests):
        def setUp(self):
            # Keep nested rollout artifacts below Windows' common path limit.
            case_id = hashlib.sha256(self._testMethodName.encode("utf-8")).hexdigest()[:10]
            self.root = output / "synthetic_evidence" / case_id
            case_directories[self._testMethodName] = self.root.relative_to(output).as_posix()
            self.root.mkdir(parents=True, exist_ok=False)
            self.initial = self.root / "initial.md"
            self.initial.write_text(INITIAL_SKILL, encoding="utf-8")

    loader = unittest.TestLoader()
    suite = unittest.TestSuite([loader.loadTestsFromName("tests.test_cam_selection_guard"),
                               loader.loadTestsFromTestCase(DurableTrainerTests)])
    captured = io.StringIO()
    started = time.perf_counter()
    with redirect_stdout(captured), redirect_stderr(captured):
        result = unittest.TextTestRunner(stream=captured, verbosity=2).run(suite)
    source_files = ["skillopt/engine/trainer.py", "skillopt/cam/selection_guard.py",
                    "skillopt/cam/bootstrap_gate.py", "tests/test_cam_selection_guard.py",
                    "tests/test_cam_gate_trainer.py", "scripts/check_cam_gate.py"]
    report = {
        "schema_version": 1, "stage": "cam_gate_offline_acceptance",
        "evidence_type": "synthetic_offline_tests_not_spreadsheetbench_performance",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "passed" if result.wasSuccessful() else "failed", "tests_run": result.testsRun,
        "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped),
        "wall_time_s": round(time.perf_counter() - started, 3), "external_model_calls": 0,
        "source_sha256_by_file": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in source_files},
        "synthetic_scenarios": {},
        "case_directories": case_directories,
        "next_required_step": "Fix call/token accounting, then rerun fixed n=4 P0; require natural Memory write/hit/injection before n=8/16 or ablations",
    }
    for summary_file in (output / "synthetic_evidence").glob("*/*/summary.json"):
        if summary_file.parent.name not in {"uncertain", "accept", "slow"}:
            continue
        summary = json.loads(summary_file.read_text(encoding="utf-8"))
        report["synthetic_scenarios"][summary_file.parent.name] = {
            key: summary.get(key) for key in ("baseline_selection_hard", "best_selection_hard",
                                              "final_selection_hard", "best_step", "best_origin",
                                              "cam_selection_guard")}
    (output / "test_output.txt").write_text(captured.getvalue(), encoding="utf-8")
    (output / "acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
