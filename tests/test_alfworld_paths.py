import os

from skillopt.envs.alfworld.rollout import _resolve_alfworld_gamefile, _resolve_alfworld_gamefiles


def test_resolve_alfworld_gamefile_uses_alfworld_data_for_relative_paths(monkeypatch, tmp_path):
    data_root = tmp_path / "alfworld_data"
    monkeypatch.setenv("ALFWORLD_DATA", str(data_root))

    resolved = _resolve_alfworld_gamefile("json_2.1.1/valid_seen/task/game.tw-pddl")

    assert resolved == os.path.join(str(data_root), "json_2.1.1/valid_seen/task/game.tw-pddl")


def test_resolve_alfworld_gamefile_keeps_absolute_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("ALFWORLD_DATA", str(tmp_path / "alfworld_data"))
    absolute = tmp_path / "elsewhere" / "game.tw-pddl"

    assert _resolve_alfworld_gamefile(str(absolute)) == str(absolute)


def test_resolve_alfworld_gamefile_keeps_relative_path_without_alfworld_data(monkeypatch):
    monkeypatch.delenv("ALFWORLD_DATA", raising=False)

    assert _resolve_alfworld_gamefile("json_2.1.1/train/task/game.tw-pddl") == (
        "json_2.1.1/train/task/game.tw-pddl"
    )


def test_resolve_alfworld_gamefiles_handles_none(monkeypatch):
    monkeypatch.setenv("ALFWORLD_DATA", "/tmp/alfworld_data")

    assert _resolve_alfworld_gamefiles(None) is None
