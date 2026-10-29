"""Offline-only source-freeze tests for the new v3r1 diagnostics entry."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import cam_v3r1_freeze as freeze


def fixture_root(root: Path) -> Path:
    for directory in (
        "scripts", "skillopt/prompts/nested/deeper", "skillopt/envs/spreadsheetbench/skills",
        "configs/experiments", "data/spreadsheetbench_split/train", "data/spreadsheetbench_split/test",
        "data/spreadsheetbench_id_split/train", "data/spreadsheetbench_id_split/test",
        "data/spreadsheetbench_verified_400", "tests",
    ):
        (root / directory).mkdir(parents=True, exist_ok=True)
    (root / "skillopt/envs/spreadsheetbench/skills/initial.md").write_text("initial skill\n", encoding="utf-8")
    for i in range(21):
        (root / "skillopt/prompts" / f"prompt_{i:02}.md").write_text(f"prompt {i}\n", encoding="utf-8")
    (root / "skillopt/prompts/nested/deeper/extra.md").write_text("nested prompt\n", encoding="utf-8")
    (root / "scripts/sample.py").write_text("# source\n", encoding="utf-8")
    (root / "tests/sample.py").write_text("# test source\n", encoding="utf-8")
    (root / "configs/experiments/cam_v3_mechanisms.yaml").write_text(
        "model: {target: qwen, qwen_chat_max_tokens: 2048}\n"
        "train: {num_epochs: 4}\noptimizer: {token_budget: 12}\n"
        "evaluation: {}\ncam: {}\nenv: {}\n", encoding="utf-8")
    (root / "configs/experiments/cam_v3r1_runtime.yaml").write_text(
        "_base_: cam_v3_mechanisms.yaml\n", encoding="utf-8")
    (root / "data/spreadsheetbench_id_split/train/items.json").write_text("[]\n", encoding="utf-8")
    train = []
    for task_type, prefix in ((freeze.TYPE_ORDER[0], "cell"), (freeze.TYPE_ORDER[1], "sheet")):
        train.extend({"id": f"{prefix}-{i}", "instruction_type": task_type} for i in range(20))
    (root / "data/spreadsheetbench_split/train/items.json").write_text(json.dumps(train), encoding="utf-8")
    test = []
    for task_type, prefix, count in ((freeze.TYPE_ORDER[0], "cell", 83), (freeze.TYPE_ORDER[1], "sheet", 37)):
        test.extend({"id": f"{prefix}-test-{i}", "instruction_type": task_type} for i in range(count))
    (root / "data/spreadsheetbench_split/test/items.json").write_text(json.dumps(test), encoding="utf-8")
    (root / "data/spreadsheetbench_id_split/test/items.json").write_text("[]\n", encoding="utf-8")
    (root / "data/spreadsheetbench_verified_400/workbook.xlsx").write_bytes(b"synthetic fixture")
    return root


class ContinuousAdaptationV3R1FreezeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = fixture_root(Path(self.temp.name) / "repo")
        self.artifacts = self.root / "outputs/studies" / freeze.STUDY

    def tearDown(self):
        self.temp.cleanup()

    def test_recursive_prompt_coverage_includes_all_markdown(self):
        files = freeze.collect_files(self.root)
        prompts = {name for name in files if name.startswith("skillopt/prompts/") and name.endswith(".md")}
        actual = {p.relative_to(self.root).as_posix() for p in (self.root / "skillopt/prompts").rglob("*.md")}
        self.assertEqual(prompts, actual)
        self.assertEqual(len(prompts), 22)
        self.assertIn("skillopt/prompts/nested/deeper/extra.md", prompts)
        self.assertIn("skillopt/envs/spreadsheetbench/skills/initial.md", files)

    def test_prepare_is_idempotent_immutable_and_creates_no_runtime_queue(self):
        first = freeze.prepare(self.root, self.artifacts)
        second = freeze.prepare(self.root, self.artifacts)
        verified = freeze.verify(self.root, self.artifacts)
        self.assertEqual(first, second)
        self.assertEqual(first, verified)
        self.assertFalse((self.artifacts / "queue_state.json").exists())
        self.assertFalse((self.artifacts / "auth_preflight.json").exists())
        self.assertEqual(len(first["prompt_inventory"]), 22)
        self.assertEqual(set(first["configs"]), {cell["cell_id"] for cell in freeze.cells()})
        self.assertEqual(first["configs"]["baseline_seed43"]["seed"], 43)
        self.assertEqual(first["configs"]["baseline_seed43"]["cam_seed"], 43)
        self.assertEqual(first["configs"]["cam_v3_seed43"]["num_epochs"], 4)
        self.assertTrue((self.artifacts / "resolved_configs.json").is_file())
        self.assertTrue((self.artifacts / "source_snapshot/skillopt/prompts/nested/deeper/extra.md").is_file())
        before = (self.artifacts / "freeze.json").read_bytes()
        (self.root / "skillopt/prompts/prompt_00.md").write_text("changed\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "immutable"):
            freeze.prepare(self.root, self.artifacts)
        self.assertEqual((self.artifacts / "freeze.json").read_bytes(), before)

    def test_verify_detects_prompt_addition_removal_and_change(self):
        expected = freeze.collect_files(self.root)
        nested = self.root / "skillopt/prompts/nested/deeper/extra.md"
        original = nested.read_bytes()
        nested.write_text("changed\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "changed"):
            freeze.verify_files(self.root, expected)
        nested.write_bytes(original)
        added = self.root / "skillopt/prompts/new.md"
        added.write_text("added\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "added"):
            freeze.verify_files(self.root, expected)
        added.unlink()
        nested.unlink()
        with self.assertRaisesRegex(RuntimeError, "removed"):
            freeze.verify_files(self.root, expected)

    def test_old_study_freeze_is_never_adopted_or_modified(self):
        old = self.root / "outputs/studies/continuous_adaptation_v3/freeze.json"
        old.parent.mkdir(parents=True)
        old.write_text('{"study_id":"continuous_adaptation_v3","sentinel":true}\n', encoding="utf-8")
        old_bytes = old.read_bytes()
        fresh = freeze.prepare(self.root, self.artifacts)
        self.assertEqual(fresh["study_id"], freeze.STUDY)
        self.assertEqual(old.read_bytes(), old_bytes)

    def test_old_study_freeze_at_target_is_refused(self):
        self.artifacts.mkdir(parents=True)
        old = self.artifacts / "freeze.json"
        old.write_text('{"study_id":"continuous_adaptation_v3"}\n', encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "immutable"):
            freeze.prepare(self.root, self.artifacts)

    def test_secret_files_and_values_are_not_frozen(self):
        (self.root / ".env").write_text("API_KEY=do-not-freeze\n", encoding="utf-8")
        base = self.root / "configs/experiments/cam_v3_mechanisms.yaml"
        base.write_text(
            "model: {target: qwen, azure_api_key: do-not-freeze, qwen_chat_max_tokens: 2048}\n"
            "train: {num_epochs: 4}\noptimizer: {token_budget: 12}\nevaluation: {}\ncam: {}\nenv: {}\n",
            encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "Credential-like setting"):
            freeze._resolved_configs(self.root)
        with self.assertRaisesRegex(RuntimeError, "Credential-like config"):
            freeze.make_freeze(self.root)
        base.write_text(
            "model: {target: qwen, azure_api_key: '', qwen_chat_max_tokens: 2048}\n"
            "train: {num_epochs: 4}\noptimizer: {token_budget: 12}\nevaluation: {}\ncam: {}\nenv: {}\n",
            encoding="utf-8")
        result = freeze.make_freeze(self.root)
        self.assertFalse(any(path.endswith(".env") for path in result["files"]))
        self.assertNotIn("do-not-freeze", json.dumps(result))
        self.assertEqual(result["configs"]["baseline_seed42"]["qwen_chat_max_tokens"], 2048)

    def test_snapshot_or_derived_artifact_drift_fails_closed(self):
        freeze.prepare(self.root, self.artifacts)
        snapshot = self.artifacts / "source_snapshot/skillopt/prompts/prompt_00.md"
        original = snapshot.read_bytes()
        snapshot.write_text("tampered snapshot\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "source snapshot"):
            freeze.verify(self.root, self.artifacts)
        with self.assertRaisesRegex(RuntimeError, "source snapshot"):
            freeze.prepare(self.root, self.artifacts)
        snapshot.write_bytes(original)
        snapshot.unlink()
        with self.assertRaisesRegex(RuntimeError, "source snapshot"):
            freeze.verify(self.root, self.artifacts)

    def test_task_order_subset_and_resolved_config_artifacts_must_match_freeze(self):
        prepared = freeze.prepare(self.root, self.artifacts)
        order = self.artifacts / "task_orders/baseline_seed42.json"
        order.write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "Task-order artifact"):
            freeze.verify(self.root, self.artifacts)
        freeze.write_json(order, prepared["task_orders"]["baseline_seed42"])
        subset = self.artifacts / "test_subset.json"
        subset.write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "test_subset.json"):
            freeze.verify(self.root, self.artifacts)
        freeze.write_json(subset, prepared["test_subset"])
        configs = self.artifacts / "resolved_configs.json"
        configs.write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "resolved_configs.json"):
            freeze.verify(self.root, self.artifacts)

    def test_actual_cli_prepare_and_verify_work_from_a_temp_workspace(self):
        script = Path(__file__).resolve().parents[1] / "scripts/run_continuous_adaptation_v3r1.py"
        outside_workspace = Path(self.temp.name)
        for action_name in ("--prepare", "--verify"):
            result = subprocess.run(
                [sys.executable, "-X", "utf8", str(script), action_name,
                 "--workspace-root", str(self.root)],
                cwd=outside_workspace, text=True, capture_output=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("cam_v3r1_runtime_diagnostics", result.stdout)
        self.assertFalse((self.artifacts / "queue_state.json").exists())

    def test_missing_prompt_directory_fails_closed(self):
        (self.root / "skillopt/prompts").rename(self.root / "skillopt/prompts-missing")
        with self.assertRaisesRegex(FileNotFoundError, "skillopt.*prompts"):
            freeze.collect_files(self.root)

    def test_offline_cli_rejects_run_and_cell_before_project_imports(self):
        env = os.environ.copy()
        env["NO_PROXY"] = "*"
        env["no_proxy"] = "*"
        command = [sys.executable, "-X", "utf8", "scripts/run_continuous_adaptation_v3r1.py", "--run"]
        result = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], env=env,
                                text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 2)
        self.assertIn("OFFLINE ONLY", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        command = [sys.executable, "-X", "utf8", "scripts/run_continuous_adaptation_v3r1.py", "--cell", "cam_v3_seed42"]
        result = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], env=env,
                                text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
