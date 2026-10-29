"""Block until something interesting happens (or a deadline), then print status.

Purpose: while long experiment cells run, the orchestrating agent needs to wait
without busy-polling. This watcher sleeps, checks a handful of cheap files, and
exits as soon as a cell finishes, a cell looks wedged, or `--max-wait` elapses.
It never modifies anything.

Exit codes:
    0  deadline reached, nothing to report
    2  at least one cell finished (a summary / completed static eval appeared)
    3  a cell looks wedged (no file change for --stale-after seconds)

Usage:
    python -X utf8 scripts/wait_progress.py --max-wait 900
    python -X utf8 scripts/wait_progress.py --max-wait 3600 --cells glm/baseline_seed42
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_MATRIX = _ROOT / "outputs" / "matrix"


def _rows(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(encoding="utf-8", errors="replace") as handle:
        return sum(1 for line in handle if line.strip())


def _stage(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def cell_snapshot(cell: Path) -> dict:
    """Return {label, finished, detail, newest_mtime, progress}."""
    name = f"{cell.parent.parent.name}/{cell.parent.name}/{cell.name}"
    if not cell.exists():
        return {"label": name, "finished": False, "detail": "not created", "newest": 0.0, "progress": ""}

    newest = 0.0
    for path in cell.rglob("*"):
        if path.is_file():
            try:
                newest = max(newest, path.stat().st_mtime)
            except OSError:
                pass

    summary = cell / "summary.json"
    if summary.exists():
        data = _stage(summary) or {}
        return {
            "label": name,
            "finished": True,
            "detail": f"summary status={data.get('status')} steps={len(json.loads((cell / 'history.json').read_text(encoding='utf-8'))) if (cell / 'history.json').exists() else '?'} test_hard={data.get('test_hard')}",
            "newest": newest,
            "progress": "done",
        }

    # static arm: eval_summary.json appears only when the whole split is scored
    eval_summary = cell / "eval_summary.json"
    if eval_summary.exists():
        data = _stage(eval_summary) or {}
        n = data.get("n_items")
        if n:
            return {
                "label": name,
                "finished": True,
                "detail": f"static n_items={n} hard={data.get('hard')}",
                "newest": newest,
                "progress": f"{n} items",
            }

    stages = sorted(glob.glob(str(cell / "*" / "stage_stats.json"))) + sorted(
        glob.glob(str(cell / "steps" / "*" / "*" / "stage_stats.json"))
    )
    latest = None
    for path in stages:
        stat = _stage(Path(path))
        if stat:
            latest = (path, stat)
    progress = ""
    if latest:
        path, stat = latest
        rel = os.path.relpath(path, cell).replace(os.sep, "/").replace("/stage_stats.json", "")
        progress = f"{rel} {stat.get('n_recorded')}/{stat.get('n_expected')} {stat.get('status')}"
    else:
        progress = f"{_rows(cell / 'results.jsonl')} rows"

    return {"label": name, "finished": False, "detail": "running", "newest": newest, "progress": progress}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--max-wait", type=int, default=900, help="seconds before giving up (default 900)")
    parser.add_argument("--poll", type=int, default=20, help="poll interval seconds (default 20)")
    parser.add_argument("--stale-after", type=int, default=1500,
                        help="flag a cell as wedged if nothing was written for this long (default 1500)")
    parser.add_argument("--cells", nargs="*", default=None,
                        help="relative cells under outputs/matrix, e.g. glm/baseline_seed42; "
                             "default = every cell that has no summary yet")
    args = parser.parse_args()

    if args.cells:
        cells = [(_MATRIX / c) for c in args.cells]
    else:
        cells = []
        for bench in sorted(p for p in _MATRIX.iterdir() if p.is_dir()):
            for target in sorted(p for p in bench.iterdir() if p.is_dir()):
                for unit in sorted(p for p in target.iterdir() if p.is_dir()):
                    # Already-finished cells would make every wait return at once.
                    if (unit / "summary.json").exists() or (unit / "eval_summary.json").exists():
                        continue
                    cells.append(unit)

    started = time.time()
    while True:
        snaps = [cell_snapshot(cell) for cell in cells]
        finished = [s for s in snaps if s["finished"]]
        wedged = []
        now = time.time()
        for snap, cell in zip(snaps, cells):
            if snap["finished"] or not cell.exists():
                continue
            age = now - snap["newest"] if snap["newest"] else 0
            if age > args.stale_after:
                wedged.append((snap, age))

        if finished or wedged:
            elapsed = now - started
            print(f"[{time.strftime('%H:%M:%S')}] watcher exit after {elapsed / 60:.1f} min")
            for snap in snaps:
                flag = "DONE" if snap["finished"] else "    "
                print(f"  {flag} {snap['label']:<46} {snap['progress']}")
            if wedged:
                print("  WEDGED:")
                for snap, age in wedged:
                    print(f"    {snap['label']} — nothing written for {age / 60:.1f} min ({snap['progress']})")
                return 3
            return 2

        if time.time() - started >= args.max_wait:
            print(f"[{time.strftime('%H:%M:%S')}] watcher deadline reached")
            for snap in snaps:
                age = (time.time() - snap["newest"]) / 60 if snap["newest"] else -1
                print(f"      {snap['label']:<46} {snap['progress']}  (last write {age:.1f} min ago)")
            return 0

        time.sleep(args.poll)


if __name__ == "__main__":
    raise SystemExit(main())
