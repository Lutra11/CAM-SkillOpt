#!/usr/bin/env python3
"""Frozen four-cell Cell-Level/Sheet-Level adaptation study v3.

Prepare without model calls, review the freeze, then explicitly pass --run.
The internal --cell mode is used only by this runner's child processes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_experiment import build_cell
from skillopt.config import flatten_config, load_config
from skillopt.utils.run_lock import run_lock

STUDY = "continuous_adaptation_v3"
CONFIG = "configs/experiments/cam_v3_mechanisms.yaml"
SPLIT = "data/spreadsheetbench_split"
ID_SPLIT = "data/spreadsheetbench_id_split"
ARTIFACTS = ROOT / "outputs/studies" / STUDY
OUTPUTS = ROOT / "outputs" / STUDY
REPORTS = ROOT / "reports" / STUDY
AUTH_REPORT = ROOT / "reports/cam_v3_online/auth/probe_report.json"
SEEDS = (42, 43)
METHODS = ("baseline", "cam_v3")
TYPE_ORDER = ("Cell-Level Manipulation", "Sheet-Level Manipulation")
BATCH_SIZE = 20
EPOCHS = 4
TRAIN_SIZE = 20
TEST_SIZE = 120
TEST_SUBSET_SEED = 20261018


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_items(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"Expected JSON list: {path}")
    return payload


def is_secret_key(key: object) -> bool:
    normalized = str(key).lower().replace(".", "_")
    parts = set(normalized.split("_"))
    return ("api_key" in normalized or bool(
        parts.intersection({"token", "password", "secret", "credential"})
    ))


def cells() -> list[dict]:
    order = (("baseline", 42), ("cam_v3", 42), ("cam_v3", 43), ("baseline", 43))
    return [{
        "cell_id": f"{method}_seed{seed}",
        "method": method,
        "seed": seed,
        "block": f"paired_seed{seed}",
        "out_root": f"outputs/{STUDY}/spreadsheetbench/qwen/{method}_seed{seed}",
    } for method, seed in order]


def cfg_options(cell: dict) -> list[str]:
    source_method = "cam_full" if cell["method"] == "cam_v3" else "baseline"
    opts = build_cell(action="train", method=source_method, target="qwen",
        benchmark="spreadsheetbench", scale="project", seed=cell["seed"], workers=8,
        extra=[
            f"env.split_dir={SPLIT}",
            f"env.out_root={(ROOT / cell['out_root']).resolve()}",
            f"train.num_epochs={EPOCHS}", f"train.train_size={TRAIN_SIZE}",
            f"train.batch_size={BATCH_SIZE}", "train.accumulation=1",
            "train.steps_per_epoch=1", "evaluation.sel_env_num=40",
            "evaluation.test_env_num=120", "evaluation.eval_test=false",
            "evaluation.final_selection=true", "model.reasoning_effort=",
            f"cam.enabled={'true' if cell['method'] == 'cam_v3' else 'false'}",
            "cam.bootstrap_gate=true", "cam.soft_accept_policy=noninferiority",
            "cam.noninferiority_margin=0.05", "cam.group_guard=true",
            "cam.group_max_regression=0.10", "cam.group_min_pairs=5",
            "cam.adaptive_budget=true", "cam.budget_policy=evidence_v3",
            "cam.budget_min_support_tasks=2", "cam.memory_enabled=true",
            "cam.memory_policy=evidence_v3", "cam.memory_inconclusive=true",
        ])
    return opts


def build_task_order(seed: int, steps_per_epoch: int = 1) -> dict:
    train_items = read_items(ROOT / SPLIT / "train" / "items.json")
    pools = {task_type: [row for row in train_items
                         if row.get("instruction_type") == task_type]
             for task_type in TYPE_ORDER}
    if any(not pool for pool in pools.values()):
        raise RuntimeError("Both Cell-Level and Sheet-Level training items are required")
    if len({str(row.get("id")) for row in train_items}) != len(train_items):
        raise RuntimeError("Training split task IDs must be unique")

    # Pick one fixed batch per type. Reuse the same batch at its later
    # recurrence so both arms see the same task IDs within each seed pair.
    selected_by_type = {
        task_type: random.Random(seed * 100_000 + index).sample(pools[task_type], BATCH_SIZE)
        for index, task_type in enumerate(TYPE_ORDER)
    }
    batches = []
    total_steps = EPOCHS * steps_per_epoch
    for global_step in range(total_steps):
        task_type = TYPE_ORDER[global_step % len(TYPE_ORDER)]
        selected = selected_by_type[task_type]
        batches.append({
            "global_step": global_step + 1,
            "epoch": global_step // steps_per_epoch + 1,
            "task_type": task_type,
            "task_ids": [str(row["id"]) for row in selected],
        })
    return {"seed": seed, "steps_per_epoch": steps_per_epoch,
            "batch_size": BATCH_SIZE, "batches": batches}


def build_test_subset() -> dict:
    source_path = ROOT / SPLIT / "test" / "items.json"
    test_items = read_items(source_path)
    pools = {task_type: [row for row in test_items
                         if row.get("instruction_type") == task_type]
             for task_type in TYPE_ORDER}
    counts = {TYPE_ORDER[0]: 83, TYPE_ORDER[1]: 37}
    if sum(counts.values()) != TEST_SIZE or any(len(pools[t]) < counts[t] for t in TYPE_ORDER):
        raise RuntimeError("Test split cannot provide the frozen 83/37 metadata-stratified subset")
    selected_ids = set()
    for index, task_type in enumerate(TYPE_ORDER):
        selected_ids.update(str(row["id"]) for row in random.Random(
            TEST_SUBSET_SEED + index).sample(pools[task_type], counts[task_type]))
    selected = [row for row in test_items if str(row.get("id")) in selected_ids]
    return {
        "source": f"{SPLIT}/test/items.json",
        "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        "selection_seed": TEST_SUBSET_SEED,
        "size": len(selected),
        "counts": counts,
        "task_ids": [str(row["id"]) for row in selected],
        "ids_sha256": hashlib.sha256("\n".join(str(row["id"]) for row in selected).encode()).hexdigest(),
    }


def validate_source_splits() -> None:
    expected = {"train": 80, "val": 40, "test": 280}
    id_sets = {}
    for split, count in expected.items():
        items = read_items(ROOT / SPLIT / split / "items.json")
        if len(items) != count:
            raise RuntimeError(f"{split} split has {len(items)} items; protocol requires {count}")
        ids = [str(row.get("id")) for row in items]
        if len(set(ids)) != count:
            raise RuntimeError(f"{split} split has missing or duplicate task IDs")
        required = {"id", "instruction", "instruction_type", "spreadsheet_path"}
        missing = [index for index, row in enumerate(items) if not required.issubset(row)]
        if missing:
            raise RuntimeError(f"{split} split lacks codegen fields at rows {missing[:5]}")
        id_sets[split] = set(ids)
    for left, right in (("train", "val"), ("train", "test"), ("val", "test")):
        overlap = id_sets[left] & id_sets[right]
        if overlap:
            raise RuntimeError(f"Task ID leakage between {left} and {right}: {sorted(overlap)[:5]}")
    for split in expected:
        metadata_items = read_items(ROOT / ID_SPLIT / split / "items.json")
        metadata_by_id = {str(row["id"]): row for row in metadata_items}
        canonical_items = read_items(ROOT / SPLIT / split / "items.json")
        if set(metadata_by_id) != {str(row["id"]) for row in canonical_items}:
            raise RuntimeError(f"Canonical and metadata-only {split} task IDs differ")
        canonical_types = {str(row["id"]): row.get("instruction_type") for row in canonical_items}
        if any(metadata_by_id[task_id].get("instruction_type") != task_type
               for task_id, task_type in canonical_types.items()):
            raise RuntimeError(f"Canonical and metadata-only {split} task types differ")


def frozen_files() -> dict[str, str]:
    paths = [ROOT / "scripts/run_continuous_adaptation_v1.py",
             ROOT / "scripts/run_continuous_adaptation_v3.py",
             ROOT / "scripts/run_experiment.py", ROOT / "scripts/train.py",
             ROOT / "skillopt/envs/spreadsheetbench/skills/initial.md",
             ROOT / CONFIG,
             ROOT / "docs/experiments/CONTINUOUS_ADAPTATION_V1.md",
             ROOT / "docs/experiments/CONTINUOUS_ADAPTATION_V3.md"]
    paths.extend((ROOT / "skillopt").rglob("*.py"))
    paths.extend((ROOT / "scripts").rglob("*.py"))
    paths.extend((ROOT / "configs").rglob("*.yaml"))
    paths.extend((ROOT / SPLIT).rglob("*"))
    paths.extend((ROOT / ID_SPLIT).rglob("*"))
    data_root = ROOT / "data/spreadsheetbench_verified_400"
    if not data_root.is_dir():
        raise FileNotFoundError(f"Required workbook data missing: {data_root}")
    paths.extend(data_root.rglob("*"))
    files = {}
    for path in sorted(set(p for p in paths if p.is_file())):
        files[path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def config_for(cell: dict) -> dict:
    cfg = flatten_config(load_config(str(ROOT / CONFIG), overrides=cfg_options(cell)))
    for key, value in cfg.items():
        if is_secret_key(key) and value:
            raise RuntimeError(f"Credential must not be embedded in frozen config: {key}")
    return cfg


def _role_config(cfg: dict, role: str) -> dict:
    endpoint = (cfg.get(f"{role}_azure_openai_endpoint") or cfg.get("azure_openai_endpoint")
                or cfg.get("azure_endpoint"))
    return {"model": cfg.get(f"{role}_model"), "endpoint": endpoint,
            "auth_mode": cfg.get(f"{role}_azure_openai_auth_mode") or cfg.get("azure_openai_auth_mode")
            or "openai_compatible"}


def _canonical_task_type(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    token = value.strip().lower().replace("-", "_").replace(" ", "_")
    if token in {"cell_level", "cell_level_manipulation", "cell"}:
        return "cell_level"
    if token in {"sheet_level", "sheet_level_manipulation", "sheet"}:
        return "sheet_level"
    return None


def validate_auth_report(report_path: Path, configs: dict[str, dict]) -> dict:
    """Accept only a recent sanitized 200 proof for the frozen role/model/endpoint/source tuple."""
    try:
        raw_bytes = report_path.read_bytes()
        report = json.loads(raw_bytes)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Missing or invalid CAM v3 auth proof: {report_path}") from exc
    if report.get("schema_version") != 1 or report.get("config") != CONFIG:
        raise RuntimeError("CAM v3 auth proof uses an unexpected schema or config")
    from scripts.probe_cam_v3_auth import ROLES

    config_cells = list(configs.values())
    expected = {role: _role_config(config_cells[0], role) for role in ("optimizer", "target")}
    for config in config_cells[1:]:
        for role in expected:
            if _role_config(config, role) != expected[role]:
                raise RuntimeError(f"CAM v3 cells do not share the probed {role} model and endpoint")

    sanitized_roles = {}
    for role in ("optimizer", "target"):
        entry = report.get("roles", {}).get(role, {})
        attempts = entry.get("attempts")
        if (entry.get("status") != "ok" or not isinstance(attempts, list) or not attempts
                or not isinstance(attempts[-1], dict) or attempts[-1].get("status") != 200):
            raise RuntimeError(f"CAM v3 auth proof has no successful HTTP 200 for {role}")
        attempt = attempts[-1]
        source = entry.get("working_credential_source")
        source_var = source.rsplit(":", 1)[-1] if isinstance(source, str) else ""
        if (entry.get("role") != role or attempt.get("role") != role
                or entry.get("model") != expected[role]["model"]
                or attempt.get("model") != expected[role]["model"]
                or entry.get("endpoint") != expected[role]["endpoint"]
                or attempt.get("endpoint") != expected[role]["endpoint"]
                or entry.get("auth_mode") != expected[role]["auth_mode"]
                or attempt.get("credential_source") != source
                or source_var not in ROLES[role]["sources"]):
            raise RuntimeError(f"CAM v3 auth proof does not match the exact {role} model/endpoint/source")
        sanitized_roles[role] = {"role": role, **expected[role],
                                 "status": "ok", "http_status": 200,
                                 "credential_source": source, "source_var": source_var}

    config_hash = hashlib.sha256((ROOT / CONFIG).read_bytes()).hexdigest()
    if report.get("config_sha256") != config_hash:
        raise RuntimeError("CAM v3 auth proof config fingerprint does not match the frozen config")
    observed_at = report.get("observed_at_utc")
    if not isinstance(observed_at, str):
        raise RuntimeError("CAM v3 auth proof is missing its observation timestamp")
    try:
        observed_datetime = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        if observed_datetime.tzinfo is None:
            raise ValueError("timezone is required")
        observed_timestamp = observed_datetime.timestamp()
    except ValueError as exc:
        raise RuntimeError("CAM v3 auth proof has an invalid observation timestamp") from exc
    if report.get("timestamp_source") not in {"file_mtime", "probe_execution"}:
        raise RuntimeError("CAM v3 auth proof has an unrecognized timestamp source")
    if observed_timestamp > datetime.now(timezone.utc).timestamp() + 300:
        raise RuntimeError("CAM v3 auth proof observation timestamp is in the future")
    age = max(0.0, datetime.now(timezone.utc).timestamp() - observed_timestamp)
    if age > 24 * 60 * 60:
        raise RuntimeError("CAM v3 auth proof is older than 24 hours; refresh the sanitized preflight")
    return {"schema_version": 1, "config": CONFIG, "roles": sanitized_roles,
            "report_sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "report_mtime_ns": report_path.stat().st_mtime_ns,
            "config_sha256": config_hash, "observed_at_utc": observed_at,
            "timestamp_source": report.get("timestamp_source"),
            "validated_at": now(), "freshness_limit_seconds": 86_400,
            "billing_scope": "auth probe only; no HTTP billing estimate"}


def _bind_auth_roles(proof: dict) -> None:
    """Bind already-probed credentials in-process before model modules initialize."""
    from scripts.probe_cam_v3_auth import load_project_dotenv, select_role_credential

    load_project_dotenv()
    for role in ("optimizer", "target"):
        row = proof["roles"][role]
        select_role_credential(role, row["source_var"])


def make_freeze() -> dict:
    validate_source_splits()
    file_hashes = frozen_files()
    plans = {}
    configs = {}
    for cell in cells():
        plans[cell["cell_id"]] = build_task_order(cell["seed"])
        configs[cell["cell_id"]] = config_for(cell)
    return {
        "study_id": STUDY,
        "protocol_revision": 1,
        "created_at": now(),
        "protocol": "baseline versus CAM v3 at seeds 42 and 43 in order baseline42, CAM42, CAM43, baseline43; four homogeneous A/B/A/B train steps, 20 train items per step, then one fixed 120-item metadata-stratified best-skill test evaluation",
        "files": file_hashes,
        "source_fingerprints": file_hashes,
        "cells": cells(),
        "configs": configs,
        "task_orders": plans,
        "test_subset": build_test_subset(),
        "primary_endpoint": "test_hard",
        "secondary_endpoint": None,
        "test_split": f"{SPLIT}/test/items.json",
        "training_split": f"{SPLIT}/train/items.json",
        "task_scope": {"cell": TYPE_ORDER[0], "sheet": TYPE_ORDER[1]},
        "test_scope_counts": {"cell_level": 83, "sheet_level": 37},
        "auth_proof_required_at_run": "reports/cam_v3_online/auth/probe_report.json",
        "status": "prepared; no model runs launched",
    }


def prepare() -> dict:
    freeze_path = ARTIFACTS / "freeze.json"
    freeze = make_freeze()
    should_write = not freeze_path.exists()
    if freeze_path.exists():
        old = json.loads(freeze_path.read_text(encoding="utf-8"))
        comparable = {key: value for key, value in old.items() if key != "created_at"}
        candidate = {key: value for key, value in freeze.items() if key != "created_at"}
        if comparable != candidate:
            raise RuntimeError("Frozen v3 protocol is immutable; create a new study version")
        freeze = old
    if should_write:
        if (ARTIFACTS / "queue_state.json").exists():
            raise RuntimeError("Queue state exists without a freeze; refusing to reset or adopt it")
        write_json(freeze_path, freeze)
        for cell in cells():
            target = ARTIFACTS / "task_orders" / f"{cell['cell_id']}.json"
            write_json(target, freeze["task_orders"][cell["cell_id"]])
        write_json(ARTIFACTS / "test_subset.json", freeze["test_subset"])
        for relative in freeze["files"]:
            target = ARTIFACTS / "source_snapshot" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
    print(f"Study: {STUDY}; cells: {len(cells())}; status: prepared only")
    for cell in cells():
        print(f"  {cell['cell_id']:<22} {cell['out_root']}")
    print(f"Freeze: {freeze_path.relative_to(ROOT)}")
    queue_path = ARTIFACTS / "queue_state.json"
    if not queue_path.exists():
        write_json(queue_path, {"study_id": STUDY, "status": "prepared", "updated_at": now(),
                                "cells": [{**cell, "status": "pending"} for cell in cells()]})
    return freeze


def verify_freeze(freeze: dict) -> None:
    if frozen_files() != freeze["files"]:
        raise RuntimeError("Study inputs changed after freeze; create a new study version")


def verify_existing_cell(cell: dict, freeze: dict, folder: Path) -> None:
    config_path = folder / "config.json"
    if not config_path.exists():
        if any(folder.iterdir()):
            raise RuntimeError(f"Cell has artifacts but no config.json; refusing to reuse {folder}")
        return
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    expected = freeze["configs"][cell["cell_id"]]
    mismatches = [key for key, value in expected.items()
                  if not is_secret_key(key)
                  and (key not in saved or saved[key] != value)]
    if mismatches:
        raise RuntimeError(f"Cell config differs from frozen protocol ({cell['cell_id']}): {mismatches}")


def validate_complete_cell(cell: dict, freeze: dict, folder: Path) -> tuple[bool, str]:
    """Accept only a complete 4-step cell with the exact frozen test evidence."""
    try:
        verify_existing_cell(cell, freeze, folder)
        config = json.loads((folder / "config.json").read_text(encoding="utf-8"))
        summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
        history = json.loads((folder / "history.json").read_text(encoding="utf-8"))
        runtime = json.loads((folder / "runtime_state.json").read_text(encoding="utf-8"))
        if config.get("train_size") != TRAIN_SIZE or config.get("batch_size") != BATCH_SIZE:
            raise ValueError("resolved training size/batch differs from protocol")
        if config.get("num_epochs") != EPOCHS or config.get("steps_per_epoch") != 1:
            raise ValueError("resolved epoch/step schedule differs from protocol")
        if config.get("eval_test") is not False or config.get("test_env_num") != TEST_SIZE:
            raise ValueError("built-in test evaluation settings differ from protocol")
        if config.get("final_selection") is not True:
            raise ValueError("final selection validation is required for every v3 cell")
        try:
            final_selection_score = float(summary.get("final_selection_hard"))
        except (TypeError, ValueError):
            raise ValueError("final selection validation score is missing")
        if not math.isfinite(final_selection_score):
            raise ValueError("final selection validation score is not finite")
        if bool(config.get("cam_enabled")) != (cell["method"] == "cam_v3"):
            raise ValueError("resolved CAM arm differs from frozen cell")
        if len(history) != EPOCHS or [row.get("step") for row in history] != [1, 2, 3, 4]:
            raise ValueError("history must contain exactly the four contiguous training steps")
        if summary.get("total_steps") != EPOCHS or runtime.get("last_completed_step") != EPOCHS:
            raise ValueError("summary/runtime state do not certify all four training steps")
        if summary.get("status", "completed") not in {"completed", "complete"}:
            raise ValueError(f"run status is {summary.get('status')!r}")
        actions = {
            "total_accepts": {"accept", "accept_new_best", "force_accept"},
            "total_soft_accepts": {"soft_accept"},
            "total_rejects": {"reject"},
            "total_skips": {"skip_no_patches"},
        }
        for field, labels in actions.items():
            if summary.get(field, 0) != sum(row.get("action") in labels for row in history):
                raise ValueError(f"history and summary action count mismatch: {field}")

        frozen_order = freeze["task_orders"][cell["cell_id"]]
        if frozen_order.get("batch_size") != BATCH_SIZE or len(frozen_order.get("batches", [])) != EPOCHS:
            raise ValueError("frozen training order does not contain four fixed-size batches")
        if (ARTIFACTS / "task_orders" / f"{cell['cell_id']}.json").is_file():
            persisted_order = json.loads((ARTIFACTS / "task_orders" / f"{cell['cell_id']}.json").read_text(encoding="utf-8"))
            if persisted_order != frozen_order:
                raise ValueError("persisted training order differs from the frozen task IDs")
        for row, batch in zip(history, frozen_order["batches"]):
            if row.get("step") != batch["global_step"]:
                raise ValueError("history step order differs from the frozen task order")
            rollout_path = folder / "steps" / f"step_{batch['global_step']:04d}" / "rollout" / "results.jsonl"
            rollout_rows = [json.loads(line) for line in rollout_path.read_text(encoding="utf-8").splitlines()
                            if line.strip()]
            actual_ids = [str(result.get("id")) for result in rollout_rows]
            if (len(rollout_rows) != BATCH_SIZE or len(set(actual_ids)) != BATCH_SIZE
                    or set(actual_ids) != set(batch["task_ids"])):
                raise ValueError(f"step {batch['global_step']} rollout IDs differ from the frozen 20-task batch")
            expected_type = _canonical_task_type(batch["task_type"])
            if any(_canonical_task_type(result.get("task_type") or result.get("instruction_type"))
                   != expected_type for result in rollout_rows):
                raise ValueError(f"step {batch['global_step']} rollout task types differ from the frozen order")

        test_dir = folder / "test_eval"
        result_path = test_dir / "results.jsonl"
        rows = {}
        for line_no, line in enumerate(result_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            task_id = str(row["id"])
            if task_id in rows:
                raise ValueError(f"duplicate fixed-test task ID {task_id}")
            if isinstance(row.get("hard"), bool) or row.get("hard") not in (0, 1):
                raise ValueError(f"invalid hard score at test results line {line_no}")
            if row.get("status") in {"infra_error", "failed", "error", "cancelled", "timeout"}:
                raise ValueError(f"test result has infrastructure status at line {line_no}")
            rows[task_id] = row
        expected_ids = set(freeze["test_subset"]["task_ids"])
        if set(rows) != expected_ids or len(rows) != TEST_SIZE:
            raise ValueError("test results do not match the fixed 120 task IDs")
        test_summary = json.loads((test_dir / "summary.json").read_text(encoding="utf-8"))
        expected_hash = freeze["test_subset"]["ids_sha256"]
        score = sum(row["hard"] for row in rows.values()) / TEST_SIZE
        if (test_summary.get("task_ids_sha256") != expected_hash
                or test_summary.get("test_subset_size") != TEST_SIZE
                or summary.get("test_subset_ids_sha256") != expected_hash
                or summary.get("test_subset_size") != TEST_SIZE
                or not math.isclose(float(summary.get("test_hard")), score, abs_tol=1e-12)
                or not math.isclose(float(test_summary.get("test_hard")), score, abs_tol=1e-12)):
            raise ValueError("fixed-test hash, count, or aggregate score does not match artifacts")
        expected_buckets = {"cell_level": 83, "sheet_level": 37}
        actual_buckets = {"cell_level": 0, "sheet_level": 0}
        for row in rows.values():
            task_type = row.get("task_type")
            if task_type not in actual_buckets:
                raise ValueError(f"unexpected test task type: {task_type!r}")
            actual_buckets[task_type] += 1
        if actual_buckets != expected_buckets:
            raise ValueError(f"test metadata strata mismatch: {actual_buckets}")
        if (folder / "test_eval_baseline").exists() or (folder / "test_eval_final").exists():
            raise ValueError("unexpected extra initial/final test evaluation artifacts")

        gates = folder / "cam_gate_events.json"
        if cell["method"] == "cam_v3":
            gate_events = json.loads(gates.read_text(encoding="utf-8")) if gates.exists() else []
            if not isinstance(gate_events, list):
                raise ValueError("CAM v3 gate event journal is malformed")
            events_by_origin = {event.get("origin"): event for event in gate_events
                                if isinstance(event, dict) and isinstance(event.get("origin"), str)}
            for record in history:
                audit = record.get("cam_gate")
                step = record.get("step")
                if record.get("action") in {"skip_no_patches", "skip_no_rewrite"}:
                    previous_skill = (folder / "skills" / f"skill_v{step - 1:04d}.md").read_text(encoding="utf-8")
                    resulting_skill = (folder / "skills" / f"skill_v{step:04d}.md").read_text(encoding="utf-8")
                    if resulting_skill != previous_skill:
                        audit = record.get("appendix_cam_gate")
                        if not isinstance(audit, dict) or not audit.get("pair_ids"):
                            raise ValueError(f"CAM v3 skip step {step} changed skill without auxiliary paired evidence")
                    elif record.get("cam_gate_used"):
                        raise ValueError(f"CAM v3 no-op step {step} unexpectedly claims a primary candidate gate")
                    else:
                        continue
                if not record.get("cam_gate_used") and not isinstance(record.get("appendix_cam_gate"), dict):
                    raise ValueError(f"CAM v3 training step {step} has neither candidate nor auxiliary gate audit")
                audits = [audit] if isinstance(audit, dict) else []
                if isinstance(record.get("appendix_cam_gate"), dict):
                    audits.append(record["appendix_cam_gate"])
                for gate_audit in audits:
                    if not gate_audit.get("pair_ids"):
                        raise ValueError(f"CAM v3 training step {step} has gate audit without paired IDs")
                    group_guard = gate_audit.get("group_guard", {})
                    if group_guard.get("enabled") is not True:
                        raise ValueError("CAM v3 group guard was not enabled")
                if record.get("cam_gate_used"):
                    origin = f"step_{step:04d}"
                    journal = events_by_origin.get(origin)
                    if (not isinstance(journal, dict) or not isinstance(audit, dict)
                            or journal.get("pair_ids") != audit.get("pair_ids")
                            or not journal.get("action")):
                        raise ValueError(f"CAM v3 step {step} audit differs from gate event journal")
                auxiliary = record.get("appendix_cam_gate")
                if isinstance(auxiliary, dict):
                    origin = f"appendix_step_{step:04d}"
                    journal = events_by_origin.get(origin)
                    if (not isinstance(journal, dict)
                            or journal.get("pair_ids") != auxiliary.get("pair_ids")
                            or not journal.get("action")):
                        raise ValueError(f"CAM v3 step {step} auxiliary audit differs from gate event journal")
        elif gates.exists():
            raise ValueError("baseline cell unexpectedly contains CAM gate events")
        return True, "accepted: four train steps, paired A/B/A/B plan and exact fixed-test evidence verified"
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError, ZeroDivisionError) as exc:
        return False, str(exc)


def child_train(cell_id: str) -> int:
    freeze_path = ARTIFACTS / "freeze.json"
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    verify_freeze(freeze)
    cell = next((record for record in cells() if record["cell_id"] == cell_id), None)
    if cell is None:
        raise ValueError(f"Unknown cell: {cell_id}")
    if config_for(cell) != freeze["configs"][cell_id]:
        raise RuntimeError(f"Resolved configuration differs from frozen protocol: {cell_id}")
    auth_path = ARTIFACTS / "auth_preflight.json"
    stored_auth = json.loads(auth_path.read_text(encoding="utf-8"))
    current_auth = validate_auth_report(AUTH_REPORT, {cell_id: freeze["configs"][cell_id]})
    if (stored_auth.get("report_sha256") != current_auth["report_sha256"]
            or stored_auth.get("report_mtime_ns") != current_auth["report_mtime_ns"]):
        raise RuntimeError("CAM v3 auth preflight proof changed after authorization")
    _bind_auth_roles(stored_auth)
    order = freeze["task_orders"][cell_id]
    write_json(ARTIFACTS / "task_orders" / f"{cell_id}.json", order)

    from skillopt.datasets.base import BatchSpec
    from skillopt.envs.spreadsheetbench.dataloader import SpreadsheetBenchDataLoader

    def plan_train_epoch(self, *, epoch, steps_per_epoch, accumulation, batch_size,
                         seed, **kwargs):
        if batch_size != BATCH_SIZE or accumulation != 1 or steps_per_epoch != 1:
            raise RuntimeError("Trainer-derived schedule differs from frozen v3 protocol")
        rows = {str(row["id"]): row for row in self.train_items}
        selected_batches = [b for b in order["batches"] if b["epoch"] == epoch]
        if len(selected_batches) != steps_per_epoch:
            raise RuntimeError(f"Unexpected task order length for epoch {epoch}")
        return [BatchSpec(phase="train", split="train", seed=seed + b["global_step"],
                          batch_size=len(b["task_ids"]),
                          payload=[rows[task_id] for task_id in b["task_ids"]],
                          metadata={"task_type": b["task_type"]})
                for b in selected_batches]

    SpreadsheetBenchDataLoader.plan_train_epoch = plan_train_epoch
    SpreadsheetBenchDataLoader.get_train_size = lambda self: TRAIN_SIZE

    test_manifest = freeze["test_subset"]
    test_items_by_id = {str(row["id"]): row
                        for row in read_items(ROOT / SPLIT / "test" / "items.json")}
    selected_test_items = [test_items_by_id[task_id] for task_id in test_manifest["task_ids"]]
    original_build_eval_batch = SpreadsheetBenchDataLoader.build_eval_batch

    def build_eval_batch(self, env_num, split, seed, **kwargs):
        if split in {"valid_unseen", "test"}:
            return BatchSpec(phase="eval", split="test", seed=seed,
                             batch_size=len(selected_test_items), payload=selected_test_items)
        return original_build_eval_batch(self, env_num, split, seed, **kwargs)

    SpreadsheetBenchDataLoader.build_eval_batch = build_eval_batch

    # The trainer's built-in evaluation invokes three test rollouts (initial,
    # best, final). Disable those, then evaluate only the authorized best skill
    # once after the training method returns.
    from skillopt.engine.trainer import ReflACTTrainer, _compute_task_type_buckets
    from skillopt.utils import compute_score
    original_train = ReflACTTrainer.train

    def train_with_single_test(self):
        wrapper_started = time.monotonic()
        train_started = time.monotonic()
        result = original_train(self)
        training_wall_time = time.monotonic() - train_started
        trainer_reported_wall_time = result.get("total_wall_time_s")
        posttest_started = time.monotonic()
        output_root = Path(self.cfg["out_root"])
        best_skill = (output_root / "best_skill.md").read_text(encoding="utf-8")
        test_batch = self.adapter.get_dataloader().build_eval_batch(
            env_num=TEST_SIZE, split="valid_unseen", seed=int(self.cfg["seed"]))
        test_env = self.adapter.build_env_from_batch(test_batch, out_root=str(output_root))
        test_results = self.adapter.rollout(test_env, best_skill, str(output_root / "test_eval"))
        posttest_wall_time = time.monotonic() - posttest_started
        test_hard, test_soft = compute_score(test_results)
        buckets = _compute_task_type_buckets(test_results, self.adapter.get_task_types())
        from skillopt.model import get_token_summary
        refreshed_token_summary = get_token_summary()
        refreshed_accounting = self._usage_provenance(token_summary=refreshed_token_summary)
        result["baseline_test_hard"] = None
        result["baseline_test_soft"] = None
        result["test_hard"] = test_hard
        result["test_soft"] = test_soft
        result["final_test_hard"] = None
        result["final_test_soft"] = None
        result["test_subset_size"] = len(test_results)
        result["test_task_type_buckets"] = buckets
        result["test_subset_ids_sha256"] = test_manifest["ids_sha256"]
        result["token_summary"] = refreshed_token_summary
        result["training_wall_time_s"] = round(training_wall_time, 1)
        result["trainer_reported_wall_time_s"] = trainer_reported_wall_time
        result["posttraining_test_wall_time_s"] = round(posttest_wall_time, 1)
        result["total_wall_time_s"] = round(time.monotonic() - wrapper_started, 1)
        accounting = dict(refreshed_accounting)
        accounting.update(
            coverage="partial", usage_complete=False, legacy_usage_partial=True,
            posttraining_best_test_included=True, http_subrequest_count=None,
            billing_completeness="not_claimed",
            legacy_usage_note="Returned backend token totals were refreshed after the best-skill test; legacy backend counters do not establish complete HTTP billing.",
        )
        accounting["summary_sha256"] = hashlib.sha256(json.dumps(
            refreshed_token_summary, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        result["token_accounting"] = accounting
        write_json(output_root / "test_eval" / "summary.json", {
            "test_hard": test_hard, "test_soft": test_soft,
            "test_subset_size": len(test_results), "task_type_buckets": buckets,
            "task_ids_sha256": test_manifest["ids_sha256"],
        })
        write_json(output_root / "summary.json", result)
        return result

    ReflACTTrainer.train = train_with_single_test
    from scripts import train
    argv = ["train.py", "--config", CONFIG, "--cfg-options", *cfg_options(cell)]
    sys.argv = argv
    return train.main()


def run(freeze: dict, lanes: int, auth_proof: dict) -> int:
    return _run_locked(freeze, lanes, auth_proof)


def _run_locked(freeze: dict, lanes: int, auth_proof: dict) -> int:
    verify_freeze(freeze)
    if lanes != 1:
        raise ValueError("This exploratory paired study requires --lanes 1")
    # Inspect all existing cells before starting anything. Only completely
    # accepted cells may be skipped; partial or incompatible state fails closed.
    disposition = {}
    for cell in cells():
        folder = ROOT / cell["out_root"]
        if not folder.exists() or not any(folder.iterdir()):
            disposition[cell["cell_id"]] = ("pending", "fresh output directory")
            continue
        verify_existing_cell(cell, freeze, folder)
        accepted, reason = validate_complete_cell(cell, freeze, folder)
        if not accepted:
            raise RuntimeError(f"Refusing partial/incompatible cell {cell['cell_id']}: {reason}")
        disposition[cell["cell_id"]] = ("skipped_done", reason)

    REPORTS.mkdir(parents=True, exist_ok=True)
    state_path = ARTIFACTS / "queue_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    saved_cells = state.get("cells", [])
    if [row.get("cell_id") for row in saved_cells] != [cell["cell_id"] for cell in cells()]:
        raise RuntimeError("Runtime queue does not match the frozen cell order")
    state.update(status="running", started_at=state.get("started_at") or now(), updated_at=now(),
                 auth_proof_sha256=auth_proof["report_sha256"])
    by_id = {row["cell_id"]: row for row in saved_cells}
    for cell in cells():
        status, reason = disposition[cell["cell_id"]]
        by_id[cell["cell_id"]].update({**cell, "status": status, "acceptance": reason})
    write_json(state_path, state)
    for cell in cells():
        status, reason = disposition[cell["cell_id"]]
        if status == "skipped_done":
            continue
        verify_freeze(freeze)
        folder = ROOT / cell["out_root"]
        folder.mkdir(parents=True, exist_ok=True)
        log_path = REPORTS / "logs" / f"{cell['cell_id']}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        record = next(r for r in state["cells"] if r["cell_id"] == cell["cell_id"])
        record.update(status="running", started_at=now())
        state["updated_at"] = now()
        write_json(state_path, state)
        command = [sys.executable, "-X", "utf8", str(Path(__file__).resolve()),
                   "--cell", cell["cell_id"]]
        env = os.environ.copy()
        with log_path.open("a", encoding="utf-8") as sink:
            process = subprocess.run(command, cwd=ROOT, env=env,
                                     stdout=sink, stderr=subprocess.STDOUT)
        verify_freeze(freeze)
        accepted, acceptance = validate_complete_cell(cell, freeze, folder) if process.returncode == 0 else (False, "child process failed")
        record.update(status="accepted" if process.returncode == 0 and accepted else "failed",
                      exit_code=process.returncode, finished_at=now(), acceptance=acceptance,
                      log=log_path.relative_to(ROOT).as_posix())
        state["updated_at"] = now()
        write_json(state_path, state)
        if process.returncode or not accepted:
            state.update(status="needs_attention", finished_at=now())
            write_json(state_path, state)
            return process.returncode or 1
    state.update(status="complete", finished_at=now())
    write_json(state_path, state)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true", help="freeze and print protocol; offline only")
    parser.add_argument("--run", action="store_true", help="launch the four frozen cells after auth proof validation")
    parser.add_argument("--lanes", type=int, default=1)
    parser.add_argument("--cell", help=argparse.SUPPRESS)
    parser.add_argument("--cfg-option", action="append", default=[], help=argparse.SUPPRESS)
    args = parser.parse_args()
    os.chdir(ROOT)
    if args.cell:
        return child_train(args.cell)
    if args.prepare == args.run:
        parser.error("choose exactly one of --prepare or --run")
    if args.run:
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        with run_lock(ARTIFACTS / ".run.lock"):
            freeze = prepare()
            auth_proof = validate_auth_report(AUTH_REPORT, freeze["configs"])
            write_json(ARTIFACTS / "auth_preflight.json", auth_proof)
            return run(freeze, args.lanes, auth_proof)
    freeze = prepare()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
