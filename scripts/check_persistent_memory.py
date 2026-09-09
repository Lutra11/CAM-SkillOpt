"""Offline Memory acceptance with durable synthetic evidence, never a benchmark run."""
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

    from tests.test_persistent_memory_trainer import PersistentMemoryTrainerTests

    class DurableTrainerTests(PersistentMemoryTrainerTests):
        def setUp(self):
            self.root = output / "synthetic_evidence" / self._testMethodName
            self.root.mkdir(parents=True, exist_ok=False)

    loader = unittest.TestLoader()
    suite = unittest.TestSuite([
        loader.loadTestsFromName("tests.test_persistent_memory_context"),
        loader.loadTestsFromName("tests.test_persistent_memory_reflection"),
        loader.loadTestsFromTestCase(DurableTrainerTests),
    ])
    captured = io.StringIO()
    start = time.perf_counter()
    with redirect_stdout(captured), redirect_stderr(captured):
        result = unittest.TextTestRunner(stream=captured, verbosity=2).run(suite)
    files = ["skillopt/cam/rejected_memory.py", "skillopt/cam/memory_context.py",
             "skillopt/gradient/reflect.py", "skillopt/envs/base.py", "skillopt/engine/trainer.py",
             "tests/test_persistent_memory_context.py", "tests/test_persistent_memory_reflection.py",
             "tests/test_persistent_memory_trainer.py", "scripts/check_persistent_memory.py"]
    report = {
        "schema_version": 1, "stage": "persistent_memory_offline_acceptance",
        "evidence_type": "synthetic_offline_tests_not_spreadsheetbench_performance",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "passed" if result.wasSuccessful() else "failed",
        "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
        "skipped": len(result.skipped), "wall_time_s": round(time.perf_counter() - start, 3),
        "external_model_calls": 0,
        "source_sha256_by_file": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in files},
        "synthetic_trainer_memory": {},
        "next_required_step": "Fix re_evaluate and final-best CAM Gate bypass; no live P0 launched here",
    }
    for summary in (output / "synthetic_evidence").rglob("summary.json"):
        payload = json.loads(summary.read_text(encoding="utf-8"))
        report["synthetic_trainer_memory"][summary.parent.name] = payload.get("persistent_memory")
    (output / "test_output.txt").write_text(captured.getvalue(), encoding="utf-8")
    (output / "acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
