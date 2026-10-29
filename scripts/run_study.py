"""Frozen, resumable Qwen study: paired seeds first, then three component ablations."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.run_experiment import build_cell, CONFIG
from scripts.experiment_integrity import validate_cell
from skillopt.config import load_config, flatten_config
from skillopt.utils.run_lock import run_lock

STUDY = "cam_v2r1_qwen"
MATRIX = ROOT / "outputs/matrix_v2r1"
REPORTS = ROOT / "reports/v2r1"
ARTIFACTS = ROOT / "outputs/studies" / STUDY
SEEDS = (42, 43, 44)


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def is_secret_key(key):
    normalized = str(key).lower().replace(".", "_").split("_")
    return ("key" in normalized and "api" in normalized) or bool(
        {"token", "secret", "password", "credential"}.intersection(normalized)
    )


def cells():
    records = []
    for seed in SEEDS:
        # Alternate order to avoid making one arm systematically earlier.
        methods = ("baseline", "cam_full") if seed % 2 == 0 else ("cam_full", "baseline")
        for method in methods:
            records.append({"cell_id": f"qwen_{method}_{seed}", "method": method, "target": "qwen",
                "seed": seed, "block": f"paired_seed{seed}",
                "out_root": f"outputs/matrix_v2r1/spreadsheetbench/qwen/{method}_seed{seed}"})
    for method in ("cam_no_bootstrap", "cam_no_memory", "cam_no_adaptive_budget"):
        records.append({"cell_id": f"qwen_{method}_42", "method": method, "target": "qwen",
            "seed": 42, "block": "ablations", "out_root": f"outputs/matrix_v2r1/spreadsheetbench/qwen/{method}_seed42"})
    return records


def options(cell):
    return build_cell(action="train", method=cell["method"], target=cell["target"],
        benchmark="spreadsheetbench", scale="project", seed=cell["seed"], workers=8,
        extra=[f"env.out_root={(ROOT / cell['out_root']).resolve()}", "model.reasoning_effort=",
               "cam.soft_accept=true", "cam.drift_patience=2", "cam.drift_tolerance=0.0",
               "cam.memory_inconclusive=true"])


def frozen_files():
    selected = set()
    for folder in ("skillopt", "scripts", "tests"):
        selected.update((ROOT / folder).rglob("*.py"))
    selected.update((ROOT / "configs").rglob("*.yaml"))
    selected.update((ROOT / "data/spreadsheetbench_split").rglob("*.json"))
    # The split manifest names tasks, but each task's prompt and input/golden
    # workbooks are also experiment inputs. Fingerprint and snapshot the full
    # verified dataset so a changed workbook cannot silently alter a cell.
    dataset = ROOT / "data/spreadsheetbench_verified_400"
    if not dataset.is_dir():
        raise FileNotFoundError(f"Required dataset directory is missing: {dataset}")
    selected.update(path for path in dataset.rglob("*") if path.is_file())
    selected.add(ROOT / "skillopt/envs/spreadsheetbench/skills/initial.md")
    for name in ("pyproject.toml", "uv.lock"):
        selected.add(ROOT / name)
    return {str(path.relative_to(ROOT).as_posix()): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(selected) if path.is_file()}


def verify_freeze(freeze):
    current = frozen_files()
    if current != freeze["files"]:
        changed = sorted(k for k in set(current) | set(freeze["files"]) if current.get(k) != freeze["files"].get(k))
        raise RuntimeError(f"Study source/data changed after freeze: {changed}; start a new protocol namespace")
    if freeze["cells"] != cells():
        raise RuntimeError("Frozen cell plan changed")


def prepare():
    freeze_path = ARTIFACTS / "freeze.json"
    if freeze_path.exists():
        freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
        verify_freeze(freeze)
        return freeze
    configs = {}
    for cell in cells():
        cfg = flatten_config(load_config(str(ROOT / CONFIG), overrides=options(cell)))
        # Credentials must remain in .env, never in the experiment protocol.
        if any(value for key, value in cfg.items() if is_secret_key(key)):
            raise RuntimeError("Inline credentials are forbidden in frozen configurations")
        configs[cell["cell_id"]] = cfg
    files = frozen_files()
    for relative in files:
        target = ARTIFACTS / "source_snapshot" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    versions = {}
    for package in ("openai", "numpy", "PyYAML", "openpyxl"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    freeze = {"study_id": STUDY, "created_at": now(), "python": sys.version,
              "versions": versions, "files": files, "cells": cells(), "configs": configs,
              "primary_endpoint": "test_hard", "secondary_endpoint": "final_test_hard",
              "confidence_level": 0.95, "inference": "exploratory; three paired training seeds; fixed test split"}
    write_json(freeze_path, freeze)
    return freeze


def refresh_reports():
    REPORTS.mkdir(parents=True, exist_ok=True)
    commands = [
        ["scripts/collect_matrix.py", "--matrix-root", str(MATRIX), "--report", str(REPORTS / "matrix_summary.json"),
         "--md-out", str(REPORTS / "matrix.md"), "--json-only"],
        ["scripts/analyze_cam.py", "--matrix-root", str(MATRIX), "--report-dir", str(REPORTS), "--json-only"],
        ["scripts/report_study.py"],
    ]
    for command in commands:
        run = subprocess.run([sys.executable, "-X", "utf8", *command], cwd=ROOT)
        if run.returncode:
            raise RuntimeError(f"Report command failed: {command[0]}")


def run_study(freeze, lanes):
    lock = threading.Lock()
    state = {"study_id": STUDY, "started_at": now(), "updated_at": now(), "lanes": lanes,
             "status": "running", "cells": [{**cell, "status": "pending"} for cell in cells()]}

    def update(cell_id, **fields):
        with lock:
            record = next(cell for cell in state["cells"] if cell["cell_id"] == cell_id)
            record.update(fields)
            state["updated_at"] = now()
            write_json(REPORTS / "queue_state.json", state)

    def run_cell(cell):
        verify_freeze(freeze)
        cell_id = cell["cell_id"]
        folder = ROOT / cell["out_root"]
        complete, reason = validate_cell(folder)
        if complete:
            update(cell_id, status="skipped_done", finished_at=now(), acceptance=reason)
            return True
        cfg_path = folder / "config.json"
        if cfg_path.exists():
            previous = json.loads(cfg_path.read_text(encoding="utf-8"))
            expected = freeze["configs"][cell_id]
            # config.json is redacted; compare every non-secret input saved by the trainer.
            mismatches = [key for key, value in expected.items()
                          if not is_secret_key(key)
                          and (key not in previous or previous[key] != value)]
            if mismatches:
                raise RuntimeError(f"Cell protocol mismatch in {cell_id}: {mismatches}")
        log_path = REPORTS / "logs" / f"{cell_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        update(cell_id, status="running", started_at=now(), log=str(log_path.relative_to(ROOT)))
        command = [sys.executable, "-X", "utf8", "scripts/run_cell_robust.py", "--action", "train",
                   "--method", cell["method"], "--target", "qwen", "--seed", str(cell["seed"]),
                   "--out-root", cell["out_root"], "--max-attempts", "5", "--only-if-incomplete"]
        for option in options(cell):
            command.extend(["--cfg-option", option])
        started = time.monotonic()
        print(f"START {cell_id} {now()}", flush=True)
        with log_path.open("a", encoding="utf-8") as sink:
            process = subprocess.Popen(command, cwd=ROOT, stdout=sink, stderr=subprocess.STDOUT)
            update(cell_id, pid=process.pid)
            code = process.wait()
        verify_freeze(freeze)
        complete, reason = validate_cell(folder)
        ok = code == 0 and complete
        update(cell_id, status="done" if ok else "failed", exit_code=code, acceptance=reason,
               finished_at=now(), wall_time_s=round(time.monotonic() - started, 2))
        print(f"{'DONE' if ok else 'FAILED'} {cell_id}: {reason}", flush=True)
        return ok

    write_json(REPORTS / "queue_state.json", state)
    try:
        with ThreadPoolExecutor(max_workers=lanes) as executor:
            for block in ("paired_seed42", "paired_seed43", "paired_seed44", "ablations"):
                group = [cell for cell in cells() if cell["block"] == block]
                results = list(executor.map(run_cell, group))
                refresh_reports()
                if not all(results):
                    state["status"] = "needs_attention"
                    break
            else:
                state["status"] = "complete"
        state["finished_at"] = now()
        write_json(REPORTS / "queue_state.json", state)
        refresh_reports()
        return 0 if state["status"] == "complete" else 1
    except Exception as exc:
        state.update(status="needs_attention", error=str(exc), updated_at=now())
        write_json(REPORTS / "queue_state.json", state)
        raise


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--lanes", type=int, choices=(1, 2, 3), default=2)
    args = parser.parse_args()
    os.chdir(ROOT)
    for cell in cells():
        print(f"{cell['block']:15} {cell['cell_id']:38} {cell['out_root']}")
    if not args.run and not args.prepare:
        return 0
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with run_lock(ARTIFACTS / ".queue.lock"):
        freeze = prepare()
        print(f"Protocol frozen: {len(freeze['files'])} source/data files", flush=True)
        if args.run:
            return run_study(freeze, args.lanes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
