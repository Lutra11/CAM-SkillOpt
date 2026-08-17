import json

import pytest

from skillopt.envs.searchqa.rollout import run_batch


def test_cached_systemic_rollout_failure_aborts(tmp_path):
    (tmp_path / "results.jsonl").write_text(
        "\n".join([
            json.dumps({"id": "1", "agent_ok": False, "fail_reason": "endpoint missing"}),
            json.dumps({"id": "2", "agent_ok": False, "fail_reason": "endpoint missing"}),
        ]),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="endpoint missing"):
        run_batch([{"id": "1"}, {"id": "2"}], str(tmp_path), "skill")


def test_cached_answered_wrong_rollout_does_not_abort(tmp_path):
    result = {"id": "1", "agent_ok": True, "hard": 0, "fail_reason": "wrong answer"}
    (tmp_path / "results.jsonl").write_text(json.dumps(result), encoding="utf-8")

    assert run_batch([{"id": "1"}], str(tmp_path), "skill") == [result]
