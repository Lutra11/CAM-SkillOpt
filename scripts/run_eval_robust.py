#!/usr/bin/env python3
"""Run `eval_only.py` to completion, surviving transient infra errors.

Why this exists
---------------
`skillopt/envs/*/rollout.py::_run_task_batch` calls `assert_valid_results` on the
resumed cache and **rejects any cached `infra_error` row by design**:

    "The batch runner resumes only completed task IDs and rejects cached
     infrastructure errors. A nonempty partial file is not a full stage."

So a single transient `llm_timeout` aborts the whole evaluation, and simply
re-running it aborts again immediately without doing any work. A long evaluation
(280 tasks) will therefore rarely finish in one shot.

What this wrapper does
----------------------
Run the evaluation; if it exits because of an infra error, drop only the
`infra_error` rows from `results.jsonl` (keeping every genuinely completed row)
and run again. Repeat until all requested items are complete, or the attempt
budget is exhausted.

This deliberately does NOT fabricate results: rows are never invented or
rewritten, only the failed marker is removed so the item is retried. The final
`results.jsonl` contains exactly the items that completed.

Usage
-----
    python -X utf8 scripts/run_eval_robust.py --target glm [same options as run_experiment.py]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent


def strip_infra_rows(results_path: Path) -> tuple[int, int]:
    """Remove `status == infra_error` rows. Returns (kept, removed)."""
    if not results_path.exists():
        return 0, 0
    raw = results_path.read_bytes()
    # Tolerate a BOM: PowerShell's `Set-Content -Encoding UTF8` adds one, and
    # json.loads rejects it ("Unexpected UTF-8 BOM").
    text = raw.decode("utf-8-sig")
    kept: list[str] = []
    removed = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            removed += 1
            continue
        if str(row.get("status", "")) == "infra_error" or row.get("hard") is None:
            removed += 1
            continue
        kept.append(json.dumps(row, ensure_ascii=False))
    if removed:
        # Write WITHOUT a BOM; newline="\n" keeps the file byte-clean.
        with results_path.open("w", encoding="utf-8", newline="\n") as handle:
            for line in kept:
                handle.write(line + "\n")
    return len(kept), removed


def count_completed(results_path: Path) -> int:
    if not results_path.exists():
        return 0
    text = results_path.read_bytes().decode("utf-8-sig")
    return sum(1 for line in text.splitlines() if line.strip())


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    # Everything except our own flags is forwarded verbatim to run_experiment.py.
    parser.add_argument("--max-attempts", type=int, default=12)
    parser.add_argument("--expected", type=int, default=0,
                        help="expected number of items; verified at the end")
    parser.add_argument("--benchmark", default="spreadsheetbench")
    parser.add_argument("--target", default="glm")
    parser.add_argument("-h", "--help", action="store_true")
    known, forwarded = parser.parse_known_args()

    if known.help:
        print(__doc__)
        print("Forwarded options: same as scripts/run_experiment.py "
              "(--action static --target <t> [--benchmark <b>] [--cfg-option k=v])")
        return 0

    arm = "static"
    out_root = _PROJECT_ROOT / "outputs" / "matrix" / known.benchmark / known.target / f"{arm}_seed42"
    results_path = out_root / "results.jsonl"

    print(f"wrapper : run_eval_robust.py")
    print(f"benchmark: {known.benchmark}")
    print(f"target  : {known.target}")
    print(f"out_root: {out_root}")
    print(f"max attempts: {known.max_attempts}\n")

    cmd = [sys.executable, "-X", "utf8", str(_SCRIPT_DIR / "run_experiment.py"),
           "--action", "static", "--target", known.target,
           "--benchmark", known.benchmark, *forwarded]

    for attempt in range(1, known.max_attempts + 1):
        done = count_completed(results_path)
        print(f"=== attempt {attempt}/{known.max_attempts}  (completed so far: {done}) ===", flush=True)
        started = time.time()
        proc = subprocess.run(cmd, cwd=_PROJECT_ROOT)
        elapsed = time.time() - started

        if proc.returncode == 0:
            total = count_completed(results_path)
            print(f"\nfinished cleanly in {elapsed:.0f}s with {total} completed items")
            if known.expected and total != known.expected:
                print(f"WARNING: expected {known.expected} items, got {total}")
                return 3
            return 0

        kept, removed = strip_infra_rows(results_path)
        print(f"\nattempt {attempt} exited {proc.returncode} after {elapsed:.0f}s; "
              f"dropped {removed} infra_error row(s), {kept} completed rows kept", flush=True)
        if removed == 0:
            print("no infra_error rows to drop and the run still failed -> not retryable")
            return proc.returncode
        time.sleep(5)

    print(f"\nexhausted {known.max_attempts} attempts; last completed count: "
          f"{count_completed(results_path)}")
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
