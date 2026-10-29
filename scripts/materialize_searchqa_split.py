#!/usr/bin/env python3
"""Materialize a runnable SearchQA split.

Why this script exists: ``data/searchqa_id_split/`` is referenced by
``data/README.md`` and ``scripts/materialize_searchqa.py``, but it was **never
committed** to the repository (verified with ``git log --all -- data/searchqa_id_split``).
Only ``spreadsheetbench_id_split`` and ``alfworld_path_split`` are actually
tracked. So SearchQA cannot be materialized from a released manifest, and the
split has to be regenerated deterministically.

Split design
------------
``lucadiliello/searchqa`` provides ``train`` (117,384) and ``validation`` (16,980).
Evaluation uses the official *validation* split (never the train split), sampled
deterministically to 400 / 200 / 1400 = 2000 items, matching the sizes quoted in
``data/README.md`` for SearchQA (400 / 200 / 1400).

Determinism: the item selection is a seeded ``random.Random(seed).sample`` over
the source split, so the same inputs always yield the identical split. The
resulting ``split_manifest.json`` records the seed and source for reproducibility.

Usage
-----
    $env:HF_ENDPOINT = "https://hf-mirror.com"
    python -X utf8 scripts/materialize_searchqa_split.py
"""
from __future__ import annotations

import argparse
import json
import os
import random
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SIZES = {"train": 400, "val": 200, "test": 1400}
DATASET = "lucadiliello/searchqa"
SOURCE_SPLIT = "validation"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", type=Path,
                        default=PROJECT_ROOT / "data" / "searchqa_split")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--dataset", default=DATASET)
    args = parser.parse_args()

    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

    try:
        from datasets import load_dataset
    except ImportError:
        raise SystemExit("Missing dependency 'datasets'. Install with: pip install datasets")

    print(f"loading {args.dataset} (split={SOURCE_SPLIT}) ...")
    dataset = load_dataset(args.dataset, split=SOURCE_SPLIT)
    rows = list(dataset)
    print(f"  source rows: {len(rows)}")

    total = sum(SIZES.values())
    if len(rows) < total:
        raise SystemExit(f"source split has {len(rows)} rows, need at least {total}")

    # Seeded sampling => identical split on every machine and every re-run.
    pool = list(range(len(rows)))
    random.Random(args.seed).shuffle(pool)
    picked = pool[:total]

    offsets = {}
    cursor = 0
    for name in ("train", "val", "test"):
        offsets[name] = (cursor, cursor + SIZES[name])
        cursor += SIZES[name]

    covered = [False] * len(rows)
    counts: dict[str, int] = {}
    for name, (start, end) in offsets.items():
        items = []
        for index in picked[start:end]:
            row = rows[index]
            items.append({
                "id": str(row["key"]),
                "question": row["question"],
                "context": row["context"],
                "answers": list(row["answers"]),
            })
        # Guarantee the three splits never share an item.
        for index in picked[start:end]:
            if covered[index]:
                raise SystemExit(f"item {index} assigned to more than one split")
            covered[index] = True

        split_dir = args.output_dir / name
        split_dir.mkdir(parents=True, exist_ok=True)
        with (split_dir / "items.json").open("w", encoding="utf-8") as handle:
            json.dump(items, handle, ensure_ascii=False, indent=2)
        counts[name] = len(items)
        print(f"  wrote {name}: {len(items)} items")

    manifest = {
        "generator": "scripts/materialize_searchqa_split.py",
        "dataset": args.dataset,
        "source_split": SOURCE_SPLIT,
        "source_rows": len(rows),
        "seed": args.seed,
        "counts": counts,
        "note": ("data/searchqa_id_split was never committed to this repository; "
                 "this split is regenerated deterministically instead."),
        "item_fields": ["id", "question", "context", "answers"],
    }
    with (args.output_dir / "split_manifest.json").open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)

    print(f"\nwrote {args.output_dir.resolve()}  counts={counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
