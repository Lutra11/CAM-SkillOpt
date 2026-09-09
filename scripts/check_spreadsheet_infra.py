"""Offline fault injection. Exits 2 to demonstrate first-request abort.

The fixture is synthetic and no external model or benchmark data is used.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import openpyxl

from skillopt.engine.run_artifacts import write_json
from skillopt.engine.trainer import ReflACTTrainer
from skillopt.envs.spreadsheetbench import codegen_agent, rollout
from skillopt.model.infra_errors import InfraError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-root", required=True)
    args = parser.parse_args()
    output = Path(args.out_root).resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("Use an empty output directory; existing evidence is never overwritten.")
    output.mkdir(parents=True, exist_ok=True)
    items = []
    for index in range(4):
        task = output / "synthetic_fixture" / str(index)
        task.mkdir(parents=True)
        workbook = openpyxl.Workbook()
        workbook.active["A1"] = index
        workbook.save(task / "initial.xlsx")
        workbook.save(task / "golden.xlsx")
        workbook.close()
        items.append({"id": str(index), "instruction": "Keep A1 unchanged (synthetic fault injection).",
                      "spreadsheet_path": str(task), "instruction_type": "Cell-Level Manipulation",
                      "answer_position": "Sheet!A1"})
    call_count = 0
    def simulated_target(**kwargs):
        nonlocal call_count
        call_count += 1
        raise RuntimeError("SIMULATED HTTP 401 Unauthorized: invalid_refresh_token")
    stage = output / "baseline_selection"
    trainer = ReflACTTrainer({"out_root": str(output), "simulation": True}, None)
    def synthetic_training_body():
        return rollout.run_spreadsheet_batch_codegen(items, "", str(stage), "",
                                                     max_api_workers=1, task_timeout=420)
    with patch.object(codegen_agent, "run_single", simulated_target), \
            patch.object(trainer, "_train_impl", synthetic_training_body):
        try:
            trainer.train()
        except InfraError as error:
            summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
            checks = {
                "simulation": True, "external_api_calls": 0,
                "simulated_model_requests": call_count, "expected_exit_code": 2,
                "acceptance_passed": call_count == 1 and summary["validity"] == "invalid_auth"
                                     and summary["test_hard"] is None,
                "failure_type": error.failure_type,
                "summary_path": str(output / "summary.json"),
                "results_path": str(stage / "results.jsonl"),
            }
            write_json(str(output / "acceptance.json"), checks)
            print(json.dumps(checks, ensure_ascii=False, indent=2), flush=True)
            return 2 if checks["acceptance_passed"] else 1
    raise AssertionError("Injected 401 failed to abort the training run")


if __name__ == "__main__":
    raise SystemExit(main())
