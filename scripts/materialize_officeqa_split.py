#!/usr/bin/env python3
"""Materialize the OfficeQA train/val/test split.

OfficeQA ships as a single `officeqa_full.csv` (246 questions) plus a corpus of
Treasury Bulletin text files. The experiment config uses `split_mode: split_dir`,
so a fixed split directory must exist before training.

This script does NOT invent its own splitting. It drives the loader's own
`_materialize_ratio_split`, which is the identical code path a
`split_mode: ratio` run would take:

    random.Random(split_seed).shuffle(items)  ->  first train_n / then val_n / then test_n

Doing it here means the split is produced once, deterministically, and can be
inspected before any training budget is spent.

Expected counts for 246 items at 2:1:7 with seed 42: 49 / 25 / 172.

Usage:
    python -X utf8 scripts/materialize_officeqa_split.py [--force] [--verify-only]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

SOURCE_CSV = _PROJECT_ROOT / "data" / "officeqa_docs_official" / "officeqa_full.csv"
SPLIT_DIR = _PROJECT_ROOT / "data" / "officeqa_split"
SPLIT_RATIO = "2:1:7"
SPLIT_SEED = 42


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=SOURCE_CSV)
    parser.add_argument("--split-dir", type=Path, default=SPLIT_DIR)
    parser.add_argument("--ratio", default=SPLIT_RATIO)
    parser.add_argument("--seed", type=int, default=SPLIT_SEED)
    parser.add_argument("--force", action="store_true", help="overwrite an existing split")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()

    if not args.source.exists():
        raise SystemExit(
            f"source CSV not found: {args.source}\n"
            "Run: python -X utf8 scripts/download_officeqa.py"
        )

    with args.source.open(encoding="utf-8", newline="") as handle:
        raw_rows = list(csv.DictReader(handle))
    print(f"source : {args.source}")
    print(f"rows   : {len(raw_rows)}")

    existing = sorted(p for p in args.split_dir.glob("*/items.*") if p.is_file())
    if existing and not (args.force or args.verify_only):
        print(f"\nsplit already exists ({len(existing)} files):")
        for path in existing:
            print(f"  {path.relative_to(_PROJECT_ROOT)}")
        print("\nre-run with --force to regenerate, or --verify-only to check it.")
        return 0

    if not args.verify_only:
        if args.split_dir.exists() and args.force:
            import shutil
            shutil.rmtree(args.split_dir)
            print(f"\nremoved existing {args.split_dir.relative_to(_PROJECT_ROOT)}")

        # Drive the loader's own split logic so this matches a `split_mode: ratio`
        # run exactly. OfficeQADataLoader reads split directories as CSV, but the
        # base `load_raw_items` (used by the ratio path) wants a single JSON file,
        # so stage the CSV rows as JSON in a scratch directory first.
        import tempfile

        from skillopt.envs.officeqa.dataloader import OfficeQADataLoader, _normalize_row

        # `_materialize_ratio_split` splits raw rows verbatim, so normalize first:
        # this gives the stored items the same shape as the other benchmarks
        # (id/ground_truth/task_type/source_files) instead of raw CSV column names.
        normalized = [_normalize_row(row) for row in raw_rows]
        empty_ids = [i for i in normalized if not i["id"]]
        if empty_ids:
            raise SystemExit(f"{len(empty_ids)} rows have no uid/id; refusing to split")
        print(f"normalized {len(normalized)} rows")

        with tempfile.TemporaryDirectory(prefix="officeqa_stage_") as scratch:
            staged = Path(scratch) / "officeqa_full.json"
            staged.write_text(json.dumps(normalized, ensure_ascii=False), encoding="utf-8")

            loader = OfficeQADataLoader(
                split_dir="",
                data_path=str(staged),
                split_mode="ratio",
                split_ratio=args.ratio,
                split_seed=args.seed,
                split_output_dir=str(args.split_dir),
                seed=args.seed,
                limit=0,
            )
            split_dir = loader._materialize_ratio_split(
                {"split_output_dir": str(args.split_dir), "env": "officeqa"}
            )
        print(f"\nmaterialized at {split_dir}")

    # ---- verification ----------------------------------------------------
    print("\n=== verification ===")
    counts: dict[str, int] = {}
    seen_ids: set[str] = set()
    duplicates: list[str] = []
    missing_source: list[tuple[str, str]] = []
    corpus_dir = _PROJECT_ROOT / "data" / "officeqa_docs_official" / "treasury_bulletins_parsed" / "transformed"
    empty_questions = 0
    empty_answers = 0
    no_source_ref = 0
    n_refs = 0
    difficulties: dict[str, int] = {}

    for split in ("train", "val", "test"):
        path = args.split_dir / split / "items.json"
        if not path.exists():
            raise SystemExit(f"missing split file: {path}")
        with path.open(encoding="utf-8") as handle:
            items = json.load(handle)
        counts[split] = len(items)
        for item in items:
            item_id = str(item.get("id") or item.get("uid") or "")
            if item_id in seen_ids:
                duplicates.append(item_id)
            seen_ids.add(item_id)
            if not item.get("question"):
                empty_questions += 1
            if not item.get("ground_truth"):
                empty_answers += 1
            refs = item.get("source_files") or []
            if not refs:
                no_source_ref += 1
            for name in refs:
                n_refs += 1
                if not (corpus_dir / name).exists():
                    missing_source.append((item_id, name))
            key = str(item.get("difficulty") or item.get("task_type") or "?")
            difficulties[key] = difficulties.get(key, 0) + 1

    print(f"counts           : {counts}  (total {sum(counts.values())})")
    print(f"unique ids       : {len(seen_ids)}")
    print(f"duplicate ids    : {len(duplicates)}")
    print(f"empty questions  : {empty_questions}")
    print(f"empty answers    : {empty_answers}")
    print(f"items w/o source : {no_source_ref}")
    print(f"source refs      : {n_refs}  (missing {len(missing_source)})")
    print(f"difficulty split : {difficulties}")
    if missing_source:
        for item_id, name in missing_source[:10]:
            print(f"    {item_id}: {name}")
    if duplicates:
        print(f"    first duplicates: {duplicates[:5]}")

    expected_total = len(raw_rows)
    ok = (
        sum(counts.values()) == expected_total
        and not duplicates
        and not missing_source
        and empty_questions == 0
        and empty_answers == 0
    )
    print(f"\n{'PASS' if ok else 'FAIL'}: split covers {sum(counts.values())}/{expected_total} "
          f"questions, corpus references resolvable, ids unique")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
