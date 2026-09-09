"""Offline acceptance: infrastructure failures never become benchmark scores."""
import json

import openpyxl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skillopt.engine.trainer import ReflACTTrainer
from skillopt.envs.spreadsheetbench import codegen_agent, rollout
from skillopt.envs.spreadsheetbench.adapter import SpreadsheetBenchAdapter
from skillopt.gradient import reflect
from skillopt.model.infra_errors import InfraError


def _items(tmp_path, n=4):
    items = []
    for index in range(n):
        task = tmp_path / "data" / str(index)
        task.mkdir(parents=True)
        workbook = openpyxl.Workbook()
        workbook.active["A1"] = 1
        workbook.save(task / "initial.xlsx")
        workbook.save(task / "golden.xlsx")
        workbook.close()
        items.append({"id": str(index), "instruction": "Keep A1 unchanged.",
                      "spreadsheet_path": str(task), "instruction_type": "Cell-Level Manipulation",
                      "answer_position": "Sheet!A1"})
    return items


class SpreadsheetInfraFailfastTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp_path = Path(temporary.name)

    def setattr(self, target, name, value):
        patcher = patch.object(target, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_401_aborts_first_request_and_writes_null_summary(self):
        tmp_path = self.tmp_path
        monkeypatch = self
        items = _items(tmp_path)
        calls = []
        def failed_target(**kwargs):
            calls.append(kwargs["instruction"])
            raise RuntimeError("401 Unauthorized: invalid_refresh_token")
        monkeypatch.setattr(codegen_agent, "run_single", failed_target)
        output = tmp_path / "experiment"
        trainer = ReflACTTrainer({"out_root": str(output)}, None)
        monkeypatch.setattr(trainer, "_train_impl", lambda: rollout.run_spreadsheet_batch_codegen(
            items, str(tmp_path / "data"), str(output / "baseline_selection"), "",
            max_api_workers=1, task_timeout=420,
        ))
        with self.assertRaisesRegex(InfraError, "auth_error"):
            trainer.train()
        assert len(calls) == 1
        summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
        assert summary["validity"] == "invalid_auth"
        assert summary["test_hard"] is None
        assert summary["best_selection_hard"] is None
        stage = output / "baseline_selection"
        rows = [json.loads(line) for line in (stage / "results.jsonl").read_text(encoding="utf-8").splitlines()]
        assert len(rows) == 1
        assert rows[0]["status"] == "infra_error"
        assert rows[0]["failure_type"] == "auth_error"
        assert rows[0]["hard"] is None and rows[0]["soft"] is None
        prediction = stage / "predictions" / "0"
        assert json.loads((prediction / "conversation.json").read_text(encoding="utf-8")) == []
        assert (prediction / "raw_trace.txt").is_file()
        stats = json.loads((stage / "stage_stats.json").read_text(encoding="utf-8"))
        assert stats["n_not_run"] == 3
        assert stats["n_scored"] == 0
        assert stats["hard"] is None
    
    
    def test_network_and_unavailable_model_are_not_zero_scores(self):
        tmp_path = self.tmp_path
        monkeypatch = self
        for message, expected in [("Connection reset by peer", "network_error"),
                                  ("model_not_found: requested model does not exist", "model_unavailable")]:
            output = tmp_path / expected
            def failed_target(**kwargs):
                raise RuntimeError(message)
            monkeypatch.setattr(codegen_agent, "run_single", failed_target)
            with self.assertRaises(InfraError) as caught:
                rollout.run_spreadsheet_batch_codegen(_items(output, 1), "", str(output / "out"), "",
                                                      max_api_workers=1)
            assert caught.exception.failure_type == expected
            row = json.loads((output / "out" / "results.jsonl").read_text(encoding="utf-8"))
            assert row["hard"] is None
    
    
    def test_retry_helper_never_retries_auth(self):
        tmp_path = self.tmp_path
        monkeypatch = self
        attempts = []
        def call(**kwargs):
            attempts.append(1)
            raise RuntimeError("HTTP 401 Unauthorized")
        monkeypatch.setattr(codegen_agent.time, "sleep", lambda _: self.fail("Auth must not retry/sleep"))
        with self.assertRaises(InfraError):
            codegen_agent._llm_call_with_retry(call, retries=5)
        assert len(attempts) == 1
    
    
    def test_optimizer_401_aborts_after_one_analyst_request(self):
        tmp_path = self.tmp_path
        monkeypatch = self
        pred = tmp_path / "predictions"
        rows = []
        for index in range(4):
            path = pred / str(index)
            path.mkdir(parents=True)
            (path / "conversation.json").write_text(json.dumps([
                {"role": "assistant", "content": "```python\nprint('wrong')\n```"}
            ]), encoding="utf-8")
            rows.append({"id": str(index), "hard": 0, "soft": 0, "task_description": "test"})
        calls = []
        def failed_optimizer(**kwargs):
            calls.append(1)
            raise InfraError("auth_error", "401 invalid_refresh_token", stage="optimizer")
        monkeypatch.setattr(reflect, "chat_optimizer", failed_optimizer)
        patches = tmp_path / "patches"
        with self.assertRaises(InfraError):
            reflect.run_minibatch_reflect(rows, "# Skill", str(pred), str(patches),
                                          workers=1, failure_only=False, minibatch_size=1)
        assert len(calls) == 1
        stats = json.loads((patches / "stage_stats.json").read_text(encoding="utf-8"))
        assert stats["status"] == "infra_error" and stats["analyst_calls"] == 1
        conversation = json.loads((patches / "minibatch_fail_000" / "conversation.json").read_text(encoding="utf-8"))
        assert all(message["role"] != "assistant" for message in conversation)
    
    
    def test_partial_resume_processes_only_missing_ids(self):
        tmp_path = self.tmp_path
        monkeypatch = self
        out = tmp_path / "out"
        out.mkdir()
        completed = {"id": "0", "hard": 0, "soft": 0, "ok": False, "exec_ok": True,
                     "phase": "exec", "status": "completed", "failure_type": "score_mismatch"}
        (out / "results.jsonl").write_text(json.dumps(completed) + "\n", encoding="utf-8")
        calls = []
        def run(item, **kwargs):
            calls.append(item["id"])
            return dict(completed, id=item["id"])
        monkeypatch.setattr(rollout, "process_one_codegen", run)
        result = rollout.run_spreadsheet_batch_codegen([{"id": "0"}, {"id": "1"}], "", str(out), "",
                                                       max_api_workers=1)
        assert calls == ["1"]
        assert len(result) == 2 and all(row["hard"] == 0 for row in result)
        assert json.loads((out / "stage_stats.json").read_text(encoding="utf-8"))["hard"] == 0
    
    
    def test_missing_conversation_is_not_no_patches(self):
        tmp_path = self.tmp_path
        monkeypatch = self
        adapter = object.__new__(SpreadsheetBenchAdapter)
        with self.assertRaisesRegex(InfraError, "artifact_missing"):
            adapter.reflect([{"id": "0", "hard": 0, "soft": 0}], "# Skill", str(tmp_path))
    
    
    def test_cached_legacy_auth_failure_is_rejected(self):
        tmp_path = self.tmp_path
        monkeypatch = self
        row = {"id": "0", "hard": 0, "soft": 0, "phase": "llm", "error": "401 invalid_refresh_token"}
        (tmp_path / "results.jsonl").write_text(json.dumps(row), encoding="utf-8")
        with self.assertRaisesRegex(InfraError, "auth_error"):
            rollout.run_spreadsheet_batch_codegen([{"id": "0"}], "", str(tmp_path), "", max_api_workers=1)

    def test_local_execution_401_and_timeout_remain_task_failures(self):
        for index, failure in enumerate([ValueError("Invalid cell range 401"),
                                         TimeoutError("Local workbook execution timed out")]):
            with self.subTest(failure=str(failure)):
                folder = self.tmp_path / str(index)
                items = _items(folder, 2)
                code = "import shutil\nshutil.copyfile(INPUT_PATH, OUTPUT_PATH)"
                reply = {"code": code, "raw": code, "n_turns": 1,
                         "conversation": [{"role": "assistant", "content": code}]}
                with patch.object(codegen_agent, "run_single", return_value=reply), \
                        patch.object(rollout, "run_generated_code", side_effect=failure):
                    result = rollout.run_spreadsheet_batch_codegen(
                        items, "", str(folder / "out"), "", max_api_workers=1)
                assert len(result) == 2
                assert all(row["hard"] == 0 and row["status"] == "completed" for row in result)
                assert all(row["phase"] == "exec" for row in result)
                assert all("infra_error" not in row for row in result)

    def test_setup_401_is_not_cached_auth_failure(self):
        with patch.object(rollout, "_find_test_cases", side_effect=ValueError("Invalid cell range 401")):
            result = rollout.run_spreadsheet_batch_codegen(
                [{"id": "0", "instruction": "test"}], "", str(self.tmp_path), "", max_api_workers=1)
        assert result[0]["hard"] == 0 and result[0]["phase"] == "setup"
        resumed = rollout.run_spreadsheet_batch_codegen(
            [{"id": "0", "instruction": "test"}], "", str(self.tmp_path), "", max_api_workers=1)
        assert resumed[0]["hard"] == 0

    def test_typed_auth_and_network_always_propagate_from_execution(self):
        for kind in ("auth_error", "network_error"):
            with self.subTest(kind=kind):
                folder = self.tmp_path / kind
                items = _items(folder, 2)
                code = "import shutil\nshutil.copyfile(INPUT_PATH, OUTPUT_PATH)"
                reply = {"code": code, "raw": code, "n_turns": 1,
                         "conversation": [{"role": "assistant", "content": code}]}
                with patch.object(codegen_agent, "run_single", return_value=reply), \
                        patch.object(rollout, "run_generated_code", side_effect=InfraError(kind, "typed test")):
                    with self.assertRaises(InfraError) as caught:
                        rollout.run_spreadsheet_batch_codegen(
                            items, "", str(folder / "out"), "", max_api_workers=1)
                assert caught.exception.failure_type == kind
                row = json.loads((folder / "out" / "results.jsonl").read_text(encoding="utf-8"))
                assert row["hard"] is None and row["failure_type"] == kind

    def test_trainer_does_not_reclassify_local_exception_text(self):
        trainer = ReflACTTrainer({"out_root": str(self.tmp_path)}, None)
        with patch.object(trainer, "_train_impl", side_effect=ValueError("Invalid cell range 401")):
            with self.assertRaises(ValueError):
                trainer.train()
        assert not (self.tmp_path / "summary.json").exists()

if __name__ == "__main__":
    unittest.main()
