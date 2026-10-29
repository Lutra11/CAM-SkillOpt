#!/usr/bin/env python3
"""Run ONE experiment cell (train or static) to completion, surviving infra aborts.

Why this exists
---------------
`skillopt/envs/*/rollout.py::_run_task_batch` resumes from the cached
`results.jsonl` but calls `assert_valid_results`, which by design **rejects any
cached `infra_error` row**:

    "The batch runner resumes only completed task IDs and rejects cached
     infrastructure errors. A nonempty partial file is not a full stage."

Consequence (measured on this project): one transient `llm_timeout` aborts a
cell; re-running it aborts *again immediately*, doing no work at all. A training
cell has ~8 stages (baseline selection eval, then per step rollout + selection
eval, then test eval), so the chance of at least one transient failure across
~2 h is high. Without this wrapper a cell can never be resumed unattended.

What it does
------------
    loop:
        run `scripts/run_experiment.py <args>`
        if exit 0 -> verify summary/stats, return 0
        else     -> drop ONLY the failed markers under the cell's out_root
                    (infra_error rows + unparseable truncated lines, in every
                    results.jsonl below out_root) and run again

It never invents or rewrites a measurement: completed rows are preserved
byte-for-byte in content, only failed markers are removed so the item is
retried. If an attempt fails with nothing removable, the failure is NOT
retryable and the real exit code is propagated.

Usage
-----
    python -X utf8 scripts/run_cell_robust.py --action train --method baseline --target glm
    python -X utf8 scripts/run_cell_robust.py --action train --method cam_full --target glm --max-attempts 20
    python -X utf8 scripts/run_cell_robust.py --action static --target qwen --expected 280

Every unrecognised flag is forwarded verbatim to `run_experiment.py`.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import hashlib
import uuid
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Keys whose value is ours, not run_experiment.py's.
_OWN_FLAGS = {"--max-attempts", "--expected", "--out-root", "--only-if-incomplete", "--log-file"}

# Mirrors skillopt/model/infra_errors.py FAILURE_TYPES: only these mark a row as
# an infrastructure failure worth retrying. Anything else (score_mismatch,
# execution_error, code_missing, ...) is a real measurement and is kept.
_INFRA_FAILURE_TYPES = {
    "infra_error", "llm_timeout", "worker_timeout", "network_error",
    "auth_error", "provider_error", "model_unavailable", "task_timeout",
}


# Booleans among our own flags: they take NO value, so parsing must not consume
# the next argv token (`run_phase_queue.py` passes --only-if-incomplete bare).
_BOOLEAN_OWN_FLAGS = {"--only-if-incomplete"}


def _split_own(argv: list[str]) -> tuple[dict, list[str]]:
    """Consume our own flags, forward everything else untouched."""
    own: dict = {}
    forwarded: list[str] = []
    i = 0
    while i < len(argv):
        token = argv[i]
        key = token.split("=", 1)[0]
        if key in _BOOLEAN_OWN_FLAGS:
            own[key] = token.split("=", 1)[1] if "=" in token else True
        elif key in _OWN_FLAGS:
            if "=" in token:
                own[key] = token.split("=", 1)[1]
            else:
                if i + 1 >= len(argv):
                    raise SystemExit(f"{key} needs a value")
                own[key] = argv[i + 1]
                i += 1
        else:
            forwarded.append(token)
        i += 1
    return own, forwarded


def _flag(forwarded: list[str], name: str, default: str) -> str:
    if name in forwarded:
        return forwarded[forwarded.index(name) + 1]
    return default


def strip_failed_rows(results_path: Path) -> tuple[int, int]:
    """Remove infra/failed markers from one results.jsonl.

    Returns (kept, removed). A row is removed when
      * it is not valid JSON (truncated write from a killed process), or
      * status == infra_error / error / failed / cancelled / timeout, or
      * hard is None AND failure_type is an infrastructure failure type.

    Rows are NOT removed merely for lacking `hard`: `steps/*/patches/results.jsonl`
    legitimately holds patch records with no `hard` field. An earlier version
    deleted those (2026-10-17, qwen/cam_full), silently destroying evidence.
    """
    if not results_path.exists():
        return 0, 0
    text = results_path.read_bytes().decode("utf-8-sig")
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
        status = str(row.get("status", "")).lower()
        failure_type = str(row.get("failure_type", "")).lower()
        is_infra = status in {"infra_error", "error", "failed", "cancelled", "timeout"}
        is_infra = is_infra or (row.get("hard") is None and failure_type in _INFRA_FAILURE_TYPES)
        if is_infra:
            removed += 1
            continue
        kept.append(json.dumps(row, ensure_ascii=False))
    if removed:
        # No BOM, "\n" newlines: the resume path reads this file with plain
        # utf-8 and a BOM would raise "Unexpected UTF-8 BOM".
        with results_path.open("w", encoding="utf-8", newline="\n") as handle:
            for line in kept:
                handle.write(line + "\n")
    return len(kept), removed


def clean_cell(out_root: Path) -> tuple[int, int, int]:
    """Strip failed markers from every results.jsonl under the cell.

    Returns (files_touched, rows_removed, rows_kept).
    """
    touched = removed = kept = 0
    for path in sorted(out_root.rglob("results.jsonl")):
        k, r = strip_failed_rows(path)
        kept += k
        if r:
            touched += 1
            removed += r
            print(f"    cleaned {path.relative_to(_PROJECT_ROOT)}: -{r} failed row(s), {k} kept", flush=True)
    return touched, removed, kept


def repair_stale_cam_selection(cell: Path) -> bool:
    """Recover a CAM cell that died mid-selection-eval.

    Why this exists (measured 2026-10-17): `trainer.py::_ensure_cam_selection_identity`
    stores the candidate skill's sha256 in `steps/step_XXXX/selection_eval/
    selection_identity.json`. If the process is killed during that selection
    eval, the resumed run regenerates the candidate (LLM sampling is not
    deterministic), so its hash no longer matches the identity on disk and the
    whole cell aborts with:

        SelectionEvidenceError: Selection artifact directory belongs to another
        skill or protocol
        -> summary.status = invalid_selection_evidence

    Re-running does NOT help: the same stale identity is still there. The cell
    is only recoverable by dropping that step directory so the step is recomputed
    end to end (rollout included — consistency beats the cached rollout).

    Returns True when a repair was performed.
    """
    summary = cell / "summary.json"
    if not summary.exists():
        return False
    try:
        data = json.loads(summary.read_text(encoding="utf-8"))
    except Exception:
        return False
    if str(data.get("status")) != "invalid_selection_evidence":
        return False
    if "belongs to another skill or protocol" not in str(data.get("message", "")):
        # Certificate/checkpoint failures must never trigger heuristic stage deletion.
        return False

    # The offending step is the one right after the last completed step.
    last_done = 0
    runtime = cell / "runtime_state.json"
    if runtime.exists():
        try:
            last_done = int(json.loads(runtime.read_text(encoding="utf-8")).get("last_completed_step") or 0)
        except Exception:
            last_done = 0

    steps_dir = cell / "steps"
    candidates = sorted(p for p in steps_dir.glob("step_*") if p.is_dir())
    target = None
    wanted = f"step_{last_done + 1:04d}"
    for path in candidates:
        if path.name == wanted:
            target = path
            break
    if target is None:
        owners = [p for p in candidates if (p / "selection_eval" / "selection_identity.json").exists()]
        target = owners[-1] if owners else None

    # The SAME stale-identity failure also happens in the slow-update path
    # (trainer.py:2319 -> _cam_auxiliary -> _cam_decide), where the offending
    # artefact lives in slow_update/epoch_XX/ instead of steps/. Observed
    # 2026-10-18 on qwen_nothink/cam_full: repair reported "no offending step
    # dir found" and the cell stayed dead. Pick whichever selection_eval
    # directory was touched most recently.
    slow_candidates = []
    for epoch in sorted((cell / "slow_update").glob("epoch_*")):
        for sub in ("selection_eval", "rollout_curr", "rollout_prev"):
            path = epoch / sub
            if path.exists() and (path / "results.jsonl").exists():
                slow_candidates.append((path.stat().st_mtime, epoch))
                break

    if slow_candidates:
        slow_candidates.sort()
        newest_slow = slow_candidates[-1][1]
        if target is None:
            target = newest_slow
        else:
            try:
                if newest_slow.stat().st_mtime > (target / "selection_eval").stat().st_mtime:
                    target = newest_slow
            except OSError:
                pass

    if target is None:
        print("  [repair] invalid_selection_evidence but no offending step/slow_update dir found",
              flush=True)
        return False

    def _rel(path: Path) -> str:
        try:
            return str(path.relative_to(_PROJECT_ROOT))
        except ValueError:
            return str(path)

    print(f"  [repair] invalid_selection_evidence -> dropping {_rel(target)} "
          f"(that stage recomputes end to end)", flush=True)
    root = _PROJECT_ROOT.resolve()
    target = target.resolve()
    if not target.is_relative_to(cell.resolve()) or not target.is_relative_to(root):
        raise ValueError("Repair target escaped the experiment workspace")
    archive = root / "outputs" / "retry_archive" / hashlib.sha256(str(cell).encode()).hexdigest()[:12]
    archive.mkdir(parents=True, exist_ok=True)
    destination = archive / f"{time.time_ns()}_{target.name}"
    shutil.move(str(target), str(destination))
    print(f"  [repair] preserved stage at {destination.relative_to(root)}", flush=True)
    for name in ("summary.json", "cam_gate_error.json"):
        path = cell / name
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass
    return True


def completed_rows(out_root: Path) -> int:
    total = 0
    for path in out_root.rglob("results.jsonl"):
        text = path.read_bytes().decode("utf-8-sig", errors="replace")
        total += sum(1 for line in text.splitlines() if line.strip())
    return total


def llm_ok_rows(out_root: Path) -> tuple[int | None, int]:
    """Return (rows_with_llm_ok_true, rows_total).

    A cell can 'finish cleanly' while never calling the target model at all —
    observed on 2026-10-17: an unreadable data directory made every item fail
    instantly as `dataset_error` (hard=0, llm_ok=False) and the run produced a
    0.0000 baseline that looked like a legitimate measurement. This counter is
    the cheap guard against that class of silent garbage.
    """
    ok = total = 0
    has_llm_key = False
    for path in out_root.rglob("results.jsonl"):
        text = path.read_bytes().decode("utf-8-sig", errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "llm_ok" in row:
                has_llm_key = True
            if row.get("llm_ok") is True:
                ok += 1
    return (ok if has_llm_key else None), total


def record_attempt_cost(cell, *, started, elapsed, exit_code, prior_summary_mtime):
    """Keep returned provider usage from every process attempt, including failed ones.

    Provider-side failures and hard-killed processes may have unreturned usage;
    never describe this ledger as a complete billing record.
    """
    cell.mkdir(parents=True, exist_ok=True)
    summary_path = cell / "summary.json"
    captured = False
    tokens = {}
    try:
        if summary_path.stat().st_mtime_ns != prior_summary_mtime:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            tokens = summary.get("token_summary", {})
            captured = isinstance(tokens, dict)
            if not captured:
                tokens = {}
    except (OSError, ValueError):
        pass
    record = {"attempt_id": uuid.uuid4().hex, "started_at_unix": started,
              "wall_time_s": elapsed, "exit_code": exit_code,
              "usage_captured": captured, "token_summary": tokens}
    path = cell / "attempts.jsonl"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    totals = {}
    for attempt in records:
        for role, usage in attempt.get("token_summary", {}).items():
            if not isinstance(usage, dict):
                continue
            role_total = totals.setdefault(role, {})
            for key, value in usage.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    role_total[key] = role_total.get(key, 0) + value
    payload = {"attempts": len(records), "wall_time_s": sum(r["wall_time_s"] for r in records),
               "token_summary": totals, "attempts_with_usage": sum(r["usage_captured"] for r in records),
               "coverage": "returned_provider_usage_across_recorded_attempts; unreturned/killed requests not measurable"}
    temporary = cell / "cost_summary.json.tmp"
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, cell / "cost_summary.json")


def main() -> int:
    from skillopt.utils.run_lock import run_lock
    own, forwarded = _split_own(sys.argv[1:])
    seed = _flag(forwarded, "--seed", "42")
    target = _flag(forwarded, "--target", "glm").replace(":", "-")
    bench = _flag(forwarded, "--benchmark", "spreadsheetbench")
    arm = "static" if _flag(forwarded, "--action", "train") == "static" else _flag(forwarded, "--method", "cam_full")
    cell = (_PROJECT_ROOT / own.get("--out-root", f"outputs/matrix/{bench}/{target}/{arm}_seed{seed}")).resolve()
    with run_lock(cell / ".run.lock"):
        return _main_locked()


def _main_locked() -> int:
    own, forwarded = _split_own(sys.argv[1:])

    action = _flag(forwarded, "--action", "train")
    method = _flag(forwarded, "--method", "cam_full")
    target = _flag(forwarded, "--target", "glm")
    benchmark = _flag(forwarded, "--benchmark", "spreadsheetbench")
    seed = _flag(forwarded, "--seed", "42")
    arm = "static" if action == "static" else method

    out_root = own.get("--out-root")
    if out_root:
        cell = (_PROJECT_ROOT / out_root).resolve()
    else:
        # Mirrors run_experiment.py: ':' is illegal in a Windows path component,
        # so target 'gemma4:26b' lands in directory 'gemma4-26b'.
        cell = (_PROJECT_ROOT / "outputs" / "matrix" / benchmark
                / target.replace(":", "-") / f"{arm}_seed{seed}")

    max_attempts = int(own.get("--max-attempts", 12))
    expected = int(own.get("--expected", 0))
    only_if_incomplete = "--only-if-incomplete" in own

    print("=" * 72)
    print("  robust cell runner")
    print("=" * 72)
    print(f"  cell      : {action} / {arm} / {target} / {benchmark} / seed {seed}")
    print(f"  out_root  : {cell}")
    print(f"  attempts  : {max_attempts}")
    print("=" * 72, flush=True)

    if only_if_incomplete:
        from scripts.experiment_integrity import validate_cell
        complete, reason = validate_cell(cell)
        if complete:
            print(f"  SKIP: {reason}")
            return 0
        summary = cell / "summary.json"
        if summary.exists():
            try:
                data = json.loads(summary.read_text(encoding="utf-8"))
            except Exception:
                data = {}
            # A cleanly finished summary.json has NO `status` key (status /
            # validity / eligible_for_paper are only written on abort). So
            # "complete" = total_steps present and status not starting with
            # "invalid". Requiring a truthy status made the queue re-run
            # already-finished cells (2026-10-17).
            status = str(data.get("status", "") or "")
            print(f"  Existing summary does not pass completion checks: {reason}")

    cmd = [sys.executable, "-X", "utf8", str(_SCRIPT_DIR / "run_experiment.py"), *forwarded]
    if out_root:
        cmd += ["--cfg-option", f"env.out_root={cell}"]

    log_path = None
    if own.get("--log-file"):
        log_path = (_PROJECT_ROOT / own["--log-file"]).resolve()
        log_path.parent.mkdir(parents=True, exist_ok=True)

    def run_once() -> int:
        """Run the cell, streaming output to the console and, if given, a log."""
        if log_path is None:
            return subprocess.run(cmd, cwd=_PROJECT_ROOT).returncode
        with log_path.open("a", encoding="utf-8", newline="\n") as handle:
            proc = subprocess.Popen(
                cmd, cwd=_PROJECT_ROOT, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", bufsize=1,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
                handle.write(line)
                # Flush per line: a killed cell otherwise loses its tail (a
                # regular file is block-buffered, so the last ~8 KB never land —
                # that cost us the post-mortem of the 2026-10-17 19:47 kill).
                handle.flush()
            proc.wait()
            return proc.returncode

    for attempt in range(1, max_attempts + 1):
        before = completed_rows(cell)
        print(f"\n=== attempt {attempt}/{max_attempts}  "
              f"(rows already on disk: {before})  {time.strftime('%H:%M:%S')} ===", flush=True)
        started = time.time()
        summary_path = cell / "summary.json"
        prior_summary_mtime = summary_path.stat().st_mtime_ns if summary_path.exists() else None
        rc = run_once()
        elapsed = time.time() - started
        record_attempt_cost(cell, started=started, elapsed=elapsed, exit_code=rc,
                            prior_summary_mtime=prior_summary_mtime)
        after = completed_rows(cell)

        if rc == 0:
            if action == "train":
                from scripts.experiment_integrity import validate_cell
                complete, reason = validate_cell(cell)
                if not complete:
                    print(f"FATAL: completion check failed: {reason}", flush=True)
                    return 6
            llm_ok, rows = llm_ok_rows(cell)
            print(f"\n[{time.strftime('%H:%M:%S')}] cell finished cleanly in "
                  f"{elapsed / 60:.1f} min (rows on disk: {after})", flush=True)
            if expected and after < expected:
                print(f"WARNING: expected >= {expected} rows, found {after}")
            if rows and llm_ok == 0:
                print(f"FATAL: {rows} result rows exist but NONE has llm_ok=True — "
                      f"the target model was never called. This is not a valid "
                      f"measurement (check data_root / dataset errors).", flush=True)
                return 5
            print(f"llm_ok rows: {llm_ok}/{rows}")
            return 0

        # CAM cells killed mid-selection cannot resume: their candidate skill is
        # regenerated, so the stored selection identity no longer matches. Repair
        # before falling back to the infra-row cleanup (which would not help).
        if repair_stale_cam_selection(cell):
            print(f"[{time.strftime('%H:%M:%S')}] repaired stale CAM selection evidence; retrying",
                  flush=True)
            time.sleep(5)
            continue

        touched, removed, _kept = clean_cell(cell)
        print(f"\n[{time.strftime('%H:%M:%S')}] attempt {attempt} exited "
              f"{rc} after {elapsed / 60:.1f} min; "
              f"dropped {removed} failed row(s) in {touched} file(s); "
              f"rows kept: {after}", flush=True)
        if removed == 0:
            print("NOT RETRYABLE: the run failed but left no failed markers to "
                  "clean (a real bug, not an infra hiccup). Stopping.", flush=True)
            return rc
        time.sleep(15)

    print(f"\nEXHAUSTED {max_attempts} attempts; rows on disk: {completed_rows(cell)}")
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
