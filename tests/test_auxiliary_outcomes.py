"""Offline outcome-contract and failure-propagation checks for auxiliary calls."""
import contextlib
import io
import json
import os
import tempfile
import traceback
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from skillopt.engine import trainer as trainer_module
from skillopt.engine.trainer import ReflACTTrainer, _load_meta_skill_content
from skillopt.model.infra_errors import InfraError
from skillopt.optimizer import meta_skill, slow_update
from skillopt.types import SlowUpdateResult


class AuxiliaryOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.prior_artifact_dir = os.environ.get("SKILLOPT_INFRA_ARTIFACT_DIR")
        os.environ["SKILLOPT_INFRA_ARTIFACT_DIR"] = os.path.join(self.temp.name, "infra")

    def tearDown(self):
        if self.prior_artifact_dir is None:
            os.environ.pop("SKILLOPT_INFRA_ARTIFACT_DIR", None)
        else:
            os.environ["SKILLOPT_INFRA_ARTIFACT_DIR"] = self.prior_artifact_dir
        self.temp.cleanup()

    def invoke(self, module, payload):
        call = module.run_slow_update if module is slow_update else module.run_meta_skill
        args = ("skill", [], [], [],) if module is slow_update else ("before", "after", [])
        with patch.object(module, "load_prompt", return_value="system"), patch.object(
            module, "chat_optimizer", return_value=(payload, {}),
        ) as chat:
            result = call(*args, comparison_pairs=[] if module is slow_update else None) if module is slow_update else call(*args)
        return result, chat

    def valid_modules(self):
        return (
            (slow_update, "slow_update_content"),
            (meta_skill, "meta_skill_content"),
        )

    def test_update_and_legacy_content_contract(self):
        for module, content_field in self.valid_modules():
            for payload, expected_kind in (
                (json.dumps({"outcome": "update", "reasoning": "because", content_field: "new text"}), "validated_update"),
                (json.dumps({"reasoning": "legacy", content_field: "legacy text"}), "validated_update"),
                (json.dumps({content_field: "legacy content only"}), "validated_update"),
            ):
                with self.subTest(module=module.__name__, payload=payload):
                    result, chat = self.invoke(module, payload)
                    self.assertEqual(result["outcome_kind"], expected_kind)
                    expected = next(text for text in ("new text", "legacy text", "legacy content only") if text in payload)
                    self.assertEqual(result[content_field], expected)
                    self.assertEqual(chat.call_count, 1)

    def test_explicit_no_update_contract(self):
        for module, _ in self.valid_modules():
            with self.subTest(module=module.__name__):
                result, chat = self.invoke(module, json.dumps({
                    "outcome": "no_update", "reasoning": "evidence is stable",
                    "no_update_reason": "The current text already covers these cases.",
                }))
                self.assertEqual(result["outcome_kind"], "validated_no_update")
                self.assertNotIn("slow_update_content", result)
                self.assertNotIn("meta_skill_content", result)
                self.assertEqual(chat.call_count, 1)

    def test_slow_update_public_type_roundtrips_no_update_without_content(self):
        result = SlowUpdateResult(
            reasoning="evidence is stable", outcome_kind="validated_no_update",
            no_update_reason="Keep the current guidance.", action="no_update",
        )
        serialized = result.to_dict()
        self.assertEqual(serialized["outcome_kind"], "validated_no_update")
        self.assertEqual(serialized["no_update_reason"], "Keep the current guidance.")
        self.assertNotIn("slow_update_content", serialized)
        restored = SlowUpdateResult.from_dict(serialized)
        self.assertEqual(restored.outcome_kind, "validated_no_update")
        self.assertEqual(restored.no_update_reason, "Keep the current guidance.")

    def test_invalid_response_shapes_fail_closed_with_safe_provider_error(self):
        invalid = (
            "not JSON", "[]", "{}",
            '{"outcome":"update","reasoning":"x"}',
            '{"outcome":"update","reasoning":null,"slow_update_content":"x","meta_skill_content":"x"}',
            '{"outcome":"update","reasoning":"x","slow_update_content":null,"meta_skill_content":null}',
            '{"outcome":"no_update","reasoning":"x","no_update_reason":7}',
            '{"outcome":"no_update","reasoning":"x","no_update_reason":"keep it","slow_update_content":"contradictory","meta_skill_content":"contradictory"}',
            '{"outcome":"unsupported","reasoning":"x"}',
        )
        for module, _ in self.valid_modules():
            for payload in invalid:
                with self.subTest(module=module.__name__, payload=payload):
                    with patch.object(module, "load_prompt", return_value="system"), patch.object(
                        module, "chat_optimizer", return_value=(payload, {}),
                    ) as chat:
                        call = module.run_slow_update if module is slow_update else module.run_meta_skill
                        args = ("skill", [], [], []) if module is slow_update else ("before", "after", [])
                        kwargs = {"comparison_pairs": []} if module is slow_update else {}
                        with self.assertRaises(InfraError) as captured:
                            call(*args, **kwargs)
                    self.assertEqual(captured.exception.failure_type, "provider_error")
                    self.assertEqual(captured.exception.stage, "slow_update" if module is slow_update else "meta_skill")
                    self.assertNotIn("401", captured.exception.message)
                    self.assertEqual(chat.call_count, 1)

    def test_unexpected_none_and_call_exception_are_failures(self):
        for module, _ in self.valid_modules():
            call = module.run_slow_update if module is slow_update else module.run_meta_skill
            args = ("skill", [], [], []) if module is slow_update else ("before", "after", [])
            kwargs = {"comparison_pairs": []} if module is slow_update else {}
            for response in ((None, {}), None):
                with patch.object(module, "load_prompt", return_value="system"), patch.object(
                    module, "chat_optimizer", return_value=response,
                ):
                    with self.assertRaises(InfraError):
                        call(*args, **kwargs)
            with patch.object(module, "load_prompt", return_value="system"), patch.object(
                module, "chat_optimizer", side_effect=RuntimeError("NoneType object has no attribute 'x'"),
            ) as chat:
                with self.assertRaises(InfraError) as captured:
                    call(*args, **kwargs)
            self.assertEqual(captured.exception.failure_type, "provider_error")
            self.assertEqual(chat.call_count, 1)

    def test_transport_infra_error_keeps_its_type_and_stage(self):
        for module, _ in self.valid_modules():
            error = InfraError("network_error", "synthetic transport failure")
            call = module.run_slow_update if module is slow_update else module.run_meta_skill
            args = ("skill", [], [], []) if module is slow_update else ("before", "after", [])
            kwargs = {"comparison_pairs": []} if module is slow_update else {}
            with patch.object(module, "load_prompt", return_value="system"), patch.object(
                module, "chat_optimizer", side_effect=error,
            ) as chat:
                with self.assertRaises(InfraError) as captured:
                    call(*args, **kwargs)
            self.assertIs(captured.exception, error)
            self.assertEqual(chat.call_count, 1)

    def test_plain_generated_401_is_schema_failure_not_auth_failure(self):
        for module, _ in self.valid_modules():
            call = module.run_slow_update if module is slow_update else module.run_meta_skill
            args = ("skill", [], [], []) if module is slow_update else ("before", "after", [])
            kwargs = {"comparison_pairs": []} if module is slow_update else {}
            with patch.object(module, "load_prompt", return_value="system"), patch.object(
                module, "chat_optimizer", return_value=("401", {}),
            ):
                with self.assertRaises(InfraError) as captured:
                    call(*args, **kwargs)
            self.assertEqual(captured.exception.failure_type, "provider_error")

    def test_response_body_is_neither_printed_nor_persisted(self):
        body = '{"outcome":"update","reasoning":"SECRET_SENTINEL", "slow_update_content":null}'
        with patch.object(slow_update, "load_prompt", return_value="system"), patch.object(
            slow_update, "chat_optimizer", return_value=(body, {}),
        ), patch.object(slow_update, "extract_json", side_effect=ValueError("SECRET_SENTINEL parser detail")), contextlib.redirect_stdout(io.StringIO()) as stdout:
            with self.assertRaises(InfraError) as captured:
                slow_update.run_slow_update("skill", [], [], [], comparison_pairs=[])
        trace = "".join(traceback.format_exception(captured.exception))
        persisted = "\n".join(p.read_text(encoding="utf-8") for p in Path(self.temp.name).rglob("*.*"))
        self.assertNotIn("SECRET_SENTINEL", stdout.getvalue())
        self.assertNotIn("SECRET_SENTINEL", trace)
        self.assertNotIn("SECRET_SENTINEL", persisted)

    def test_legacy_call_exception_text_is_suppressed_from_trace_and_artifacts(self):
        with patch.object(slow_update, "load_prompt", return_value="system"), patch.object(
            slow_update, "chat_optimizer", side_effect=RuntimeError("SECRET_SENTINEL provider detail"),
        ):
            with self.assertRaises(InfraError) as captured:
                slow_update.run_slow_update("skill", [], [], [], comparison_pairs=[])
        trace = "".join(traceback.format_exception(captured.exception))
        persisted = "\n".join(p.read_text(encoding="utf-8") for p in Path(self.temp.name).rglob("*.*"))
        self.assertNotIn("SECRET_SENTINEL", trace)
        self.assertNotIn("SECRET_SENTINEL", persisted)
        self.assertIn("RuntimeError", persisted)

    def test_meta_no_update_chain_uses_last_success_and_stops_at_legacy_failure(self):
        root = Path(self.temp.name) / "study"
        def save(epoch, result):
            path = root / "meta_skill" / f"epoch_{epoch:02d}" / "meta_skill_result.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(result), encoding="utf-8")

        save(1, {"action": "skip_first_epoch"})
        save(2, {"action": "write_meta_skill", "meta_skill_content": "successful memory"})
        save(3, {"action": "no_update", "outcome_kind": "validated_no_update"})
        save(4, {"action": "no_update", "outcome_kind": "validated_no_update"})
        self.assertEqual(_load_meta_skill_content(str(root), 4), "successful memory")
        save(2, {"action": "no_content"})
        self.assertEqual(_load_meta_skill_content(str(root), 4), "")

    def test_trainer_marks_auxiliary_infra_failure_and_does_not_complete(self):
        out_root = os.path.join(self.temp.name, "trainer")
        trainer = ReflACTTrainer({"out_root": out_root}, adapter=object())
        continued = []

        def failed_training_body():
            with patch.object(slow_update, "load_prompt", return_value="system"), patch.object(
                slow_update, "chat_optimizer", return_value=("invalid", {}),
            ):
                slow_update.run_slow_update("skill", [], [], [], comparison_pairs=[])
            continued.append("completed")
            return {"status": "completed", "hard": 1.0}

        with patch.object(trainer, "_train_impl", side_effect=failed_training_body):
            with self.assertRaises(InfraError):
                trainer.train()
        summary = json.loads(Path(out_root, "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["status"], "infra_error")
        self.assertEqual(summary["failure_type"], "provider_error")
        self.assertIsNone(summary["hard"])
        self.assertEqual(continued, [])


if __name__ == "__main__":
    unittest.main()
