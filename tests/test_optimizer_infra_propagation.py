"""Offline checks that optimization cannot silently skip infrastructure failures."""
import contextlib
import io
import unittest
from unittest.mock import Mock, patch

from skillopt.gradient import aggregate
from skillopt.model.infra_errors import InfraError
from skillopt.optimizer import clip, lr_autonomous, meta_skill, rewrite, skill_aware, slow_update


class OptimizerInfraPropagationTests(unittest.TestCase):
    def cases(self):
        edits = {"edits": [{"op": "append", "content": "a"}, {"op": "append", "content": "b"}]}
        return [
            (aggregate, lambda chat: aggregate._merge_batch("skill", [edits, edits], "system", "patch")),
            (aggregate, lambda chat: aggregate.merge_patches("skill", [edits], [edits], verbose=False)),
            (clip, lambda chat: clip.rank_and_select("skill", edits, 1)),
            (lr_autonomous, lambda chat: lr_autonomous.decide_autonomous_learning_rate(
                skill_content="skill", merged_patch=edits, update_mode="patch",
                rollout_hard=0, rollout_soft=0, rollout_n=1,
            )),
            (meta_skill, lambda chat: meta_skill.run_meta_skill("before", "after", [])),
            (rewrite, lambda chat: rewrite.rewrite_skill_from_suggestions(
                "skill", {"revise_suggestions": [{"instruction": "improve"}]},
            )),
            (skill_aware, lambda chat: skill_aware.consolidate_appendix_notes(["a", "b"], chat_fn=chat)),
            (slow_update, lambda chat: slow_update.run_slow_update("skill", [], [], [], comparison_pairs=[])),
        ]

    def invoke(self, module, call, chat):
        with contextlib.ExitStack() as stack:
            if hasattr(module, "chat_optimizer"):
                stack.enter_context(patch.object(module, "chat_optimizer", chat))
            if hasattr(module, "load_prompt"):
                stack.enter_context(patch.object(module, "load_prompt", return_value="system"))
            return call(chat)

    def test_typed_and_legacy_infrastructure_failures_propagate_after_one_call(self):
        error_factories = [
            (lambda: InfraError("auth_error", "synthetic 401"), "auth_error"),
            (lambda: RuntimeError("401 invalid_refresh_token"), "auth_error"),
            (lambda: ConnectionError("connection reset"), "network_error"),
            (lambda: TimeoutError("synthetic request timeout"), "llm_timeout"),
        ]
        for module, call in self.cases():
            for make_error, expected in error_factories:
                with self.subTest(module=module.__name__, case=call, failure=expected):
                    chat = Mock(side_effect=make_error())
                    with self.assertRaises(InfraError) as captured:
                        self.invoke(module, call, chat)
                    self.assertEqual(captured.exception.failure_type, expected)
                    self.assertEqual(chat.call_count, 1)

    def test_task_answer_containing_401_is_not_an_infrastructure_error(self):
        for module, call in self.cases():
            with self.subTest(module=module.__name__, case=call):
                chat = Mock(return_value=('A task answer may refer to HTTP 401.', {}))
                self.invoke(module, call, chat)
                self.assertEqual(chat.call_count, 1)

    def test_parse_error_with_401_keeps_existing_fallback(self):
        for module, call in self.cases():
            if module is skill_aware:
                parser_path = "skillopt.utils.extract_json"
            else:
                parser_path = module.__name__ + ".extract_json"
            with self.subTest(module=module.__name__, case=call):
                chat = Mock(return_value=("malformed response", {}))
                with patch(parser_path, side_effect=ValueError("parse error at character 401")):
                    with contextlib.redirect_stderr(io.StringIO()):
                        self.invoke(module, call, chat)
                self.assertEqual(chat.call_count, 1)


if __name__ == "__main__":
    unittest.main()
