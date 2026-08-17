import json
from pathlib import Path

import pytest

from scripts.materialize_searchqa import materialize_searchqa_splits


def _write_manifest(root: Path, split_ids: dict[str, list[str]]) -> None:
    for split, ids in split_ids.items():
        split_dir = root / split
        split_dir.mkdir(parents=True)
        (split_dir / "items.json").write_text(
            json.dumps([{"id": item_id} for item_id in ids]),
            encoding="utf-8",
        )


def _row(key: str) -> dict:
    return {
        "key": key,
        "question": f"question {key}",
        "context": f"context {key}",
        "answers": [f"answer {key}"],
        "ignored": "not written",
    }


def test_materialize_searchqa_splits_preserves_manifest_order(tmp_path):
    manifest_dir = tmp_path / "manifest"
    output_dir = tmp_path / "out"
    _write_manifest(manifest_dir, {"train": ["b", "a"], "val": ["c"], "test": ["d"]})

    counts = materialize_searchqa_splits(
        manifest_dir,
        output_dir,
        {"train": [_row("a"), _row("b")], "validation": [_row("c"), _row("d")]},
        dataset_name="example/searchqa",
    )

    assert counts == {"train": 2, "val": 1, "test": 1}
    train_items = json.loads((output_dir / "train" / "items.json").read_text(encoding="utf-8"))
    assert [item["id"] for item in train_items] == ["b", "a"]
    assert train_items[0] == {
        "id": "b",
        "question": "question b",
        "context": "context b",
        "answers": ["answer b"],
    }

    split_manifest = json.loads((output_dir / "split_manifest.json").read_text(encoding="utf-8"))
    assert split_manifest["source_dataset"] == "example/searchqa"
    assert split_manifest["counts"] == counts


def test_materialize_searchqa_splits_fails_on_missing_manifest_id(tmp_path):
    manifest_dir = tmp_path / "manifest"
    _write_manifest(manifest_dir, {"train": ["a"], "val": ["missing"], "test": []})

    with pytest.raises(RuntimeError, match="missing"):
        materialize_searchqa_splits(
            manifest_dir,
            tmp_path / "out",
            {"train": [_row("a")]},
            dataset_name="example/searchqa",
        )
