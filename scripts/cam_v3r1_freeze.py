"""Offline source inventory and immutable freeze helpers for CAM v3r1.

This module deliberately has no model, authentication, dotenv, or network imports.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import shutil

STUDY = "cam_v3r1_runtime_diagnostics"
CONFIG = "configs/experiments/cam_v3r1_runtime.yaml"
BASE_CONFIG = "configs/experiments/cam_v3_mechanisms.yaml"
SPLIT = "data/spreadsheetbench_split"
TYPE_ORDER = ("Cell-Level Manipulation", "Sheet-Level Manipulation")
SEEDS = (42, 43)
METHODS = ("baseline", "cam_v3")
BATCH_SIZE = 20
EPOCHS = 4
TEST_SUBSET_SEED = 20261018
TEST_SIZE = 120


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def read_items(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"Expected a JSON list: {path}")
    return payload


def cells() -> list[dict]:
    order = (("baseline", 42), ("cam_v3", 42), ("cam_v3", 43), ("baseline", 43))
    return [{
        "cell_id": f"{method}_seed{seed}", "method": method, "seed": seed,
        "block": f"paired_seed{seed}",
        "out_root": f"outputs/{STUDY}/spreadsheetbench/qwen/{method}_seed{seed}",
    } for method, seed in order]


def _source_paths(root: Path) -> list[Path]:
    """Collect inputs by file class; missing required roots fail closed."""
    paths: set[Path] = set()
    required = [root / "scripts", root / "skillopt", root / "configs",
                root / SPLIT, root / "data/spreadsheetbench_id_split",
                root / "data/spreadsheetbench_verified_400",
                root / "skillopt/envs/spreadsheetbench/skills/initial.md",
                root / "skillopt/prompts"]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise FileNotFoundError("Required freeze inputs missing: " + ", ".join(missing))

    paths.update((root / "scripts").rglob("*.py"))
    paths.update((root / "skillopt").rglob("*.py"))
    paths.update((root / "tests").rglob("*.py"))
    paths.update((root / "configs").rglob("*.yaml"))
    # Prompt coverage is recursive and explicit, including markdown nested below prompts/.
    paths.update((root / "skillopt/prompts").rglob("*.md"))
    paths.add(root / "skillopt/envs/spreadsheetbench/skills/initial.md")
    for directory in (root / SPLIT, root / "data/spreadsheetbench_id_split",
                      root / "data/spreadsheetbench_verified_400"):
        paths.update(p for p in directory.rglob("*") if p.is_file())
    for exact in (root / "docs/experiments/CONTINUOUS_ADAPTATION_V3.md",
                  root / "docs/experiments/CAM_V3R1_RUNTIME_DIAGNOSTICS.md",
                  root / "outputs/studies/cam_v3r1_runtime_diagnostics/prechange_manifest.json"):
        if exact.is_file():
            paths.add(exact)
    return sorted((p for p in paths if p.is_file()), key=lambda p: p.relative_to(root).as_posix())


def collect_files(root: Path) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): sha256(path) for path in _source_paths(root)}


def verify_files(root: Path, expected: dict[str, str]) -> None:
    current = collect_files(root)
    if current != expected:
        added = sorted(current.keys() - expected.keys())
        removed = sorted(expected.keys() - current.keys())
        changed = sorted(k for k in current.keys() & expected.keys() if current[k] != expected[k])
        raise RuntimeError(f"Frozen inputs changed (added={added[:5]}, removed={removed[:5]}, changed={changed[:5]})")


def build_task_order(root: Path, seed: int) -> dict:
    train_items = read_items(root / SPLIT / "train/items.json")
    pools = {task_type: [row for row in train_items if row.get("instruction_type") == task_type]
             for task_type in TYPE_ORDER}
    if any(len(pool) < BATCH_SIZE for pool in pools.values()):
        raise RuntimeError("Both task types must supply the frozen 20-item training batch")
    if len({str(row.get("id")) for row in train_items}) != len(train_items):
        raise RuntimeError("Training split task IDs must be unique")
    selected = {task_type: random.Random(seed * 100_000 + index).sample(pools[task_type], BATCH_SIZE)
                for index, task_type in enumerate(TYPE_ORDER)}
    batches = [{
        "global_step": step + 1, "epoch": step + 1,
        "task_type": TYPE_ORDER[step % len(TYPE_ORDER)],
        "task_ids": [str(row["id"]) for row in selected[TYPE_ORDER[step % len(TYPE_ORDER)]]],
    } for step in range(EPOCHS)]
    return {"seed": seed, "steps_per_epoch": 1, "batch_size": BATCH_SIZE, "batches": batches}


def build_test_subset(root: Path) -> dict:
    path = root / SPLIT / "test/items.json"
    test_items = read_items(path)
    counts = {TYPE_ORDER[0]: 83, TYPE_ORDER[1]: 37}
    pools = {task_type: [row for row in test_items if row.get("instruction_type") == task_type]
             for task_type in TYPE_ORDER}
    if any(len(pools[t]) < counts[t] for t in TYPE_ORDER):
        raise RuntimeError("Test split cannot provide the fixed 83/37 metadata-stratified subset")
    ids = set()
    for index, task_type in enumerate(TYPE_ORDER):
        ids.update(str(row["id"]) for row in random.Random(TEST_SUBSET_SEED + index).sample(pools[task_type], counts[task_type]))
    ordered = [str(row["id"]) for row in test_items if str(row.get("id")) in ids]
    return {"source": f"{SPLIT}/test/items.json", "source_sha256": sha256(path),
            "selection_seed": TEST_SUBSET_SEED, "size": len(ordered), "counts": counts,
            "task_ids": ordered,
            "ids_sha256": hashlib.sha256("\n".join(ordered).encode("utf-8")).hexdigest()}


def _is_credential_key(key: object) -> bool:
    normalized = str(key).lower().replace("-", "_").replace(".", "_")
    parts = set(normalized.split("_"))
    return ("api_key" in normalized or bool(parts & {"password", "secret", "credential"})
            or normalized in {"token", "auth_token", "access_token", "refresh_token",
                              "id_token", "bearer_token"})


def _resolved_configs(root: Path) -> dict[str, dict]:
    # Config resolution is safe: skillopt.config parses YAML and does not load credentials.
    from skillopt.config import flatten_config, load_config

    result = {}
    for cell in cells():
        method = cell["method"]
        overrides = [
            f"train.seed={cell['seed']}",
            "model.optimizer=glm", "model.target=qwen",
            "model.optimizer_backend=openai_chat", "model.target_backend=openai_chat",
            "model.reasoning_effort=",
            "env.name=spreadsheetbench",
            "env.skill_init=skillopt/envs/spreadsheetbench/skills/initial.md",
            "env.mode=single", "env.workers=8", "env.limit=0",
            "env.split_mode=split_dir", "env.split_dir=data/spreadsheetbench_split",
            "env.data_root=data/spreadsheetbench_verified_400",
            f"env.out_root={(root / cell['out_root']).resolve()}",
            f"train.num_epochs={EPOCHS}", "train.train_size=20", f"train.batch_size={BATCH_SIZE}",
            "train.accumulation=1", "train.steps_per_epoch=1", "evaluation.sel_env_num=40",
            f"evaluation.test_env_num={TEST_SIZE}", "evaluation.eval_test=false",
            "evaluation.final_selection=true",
            f"cam.enabled={'true' if method == 'cam_v3' else 'false'}",
            "cam.max_budget=4", "cam.min_budget=2", f"cam.seed={cell['seed']}",
            "cam.bootstrap_gate=true", "cam.soft_accept_policy=noninferiority",
            "cam.noninferiority_margin=0.05", "cam.group_guard=true",
            "cam.group_max_regression=0.10", "cam.group_min_pairs=5",
            "cam.adaptive_budget=true", "cam.budget_policy=evidence_v3",
            "cam.budget_min_support_tasks=2", "cam.memory_enabled=true",
            "cam.memory_policy=evidence_v3", "cam.memory_inconclusive=true",
        ]
        config = flatten_config(load_config(str(root / CONFIG), overrides=overrides))
        for key, value in config.items():
            if _is_credential_key(key) and value:
                raise RuntimeError(f"Credential-like setting cannot be frozen: {key}")
        result[cell["cell_id"]] = config
    return result


def _reject_secret_config_values(root: Path) -> None:
    import yaml

    def walk(value: object, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if _is_credential_key(key) and child not in (None, "", False):
                    raise RuntimeError(f"Credential-like config value cannot be frozen: {path}:{key}")
                walk(child, path)
        elif isinstance(value, list):
            for child in value:
                walk(child, path)

    for path in (root / "configs").rglob("*.yaml"):
        with path.open(encoding="utf-8") as stream:
            walk(yaml.safe_load(stream) or {}, path.relative_to(root).as_posix())


def make_freeze(root: Path) -> dict:
    _reject_secret_config_values(root)
    files = collect_files(root)
    return {
        "study_id": STUDY, "protocol_revision": 1, "created_at": now(),
        "status": "offline runtime-diagnostics preparation; no experiment plan authorized; no model calls",
        "execution_policy": "offline_only; no runtime queue; no --run or --cell mode",
        "protocol_reference": "Existing v3 mechanism and task-selection settings, recorded for diagnostics only",
        "files": files, "source_fingerprints": files, "cells": cells(),
        "configs": _resolved_configs(root),
        "task_orders": {f"{method}_seed{seed}": build_task_order(root, seed)
                        for method, seed in (("baseline", 42), ("cam_v3", 42),
                                             ("cam_v3", 43), ("baseline", 43))},
        "test_subset": build_test_subset(root),
        "primary_endpoint": "diagnostic reference only; no endpoint outcome authorized",
        "auth_proof_required_at_future_authorized_run": "reports/cam_v3_online/auth/probe_report.json",
        "prompt_inventory": sorted(path for path in files if path.startswith("skillopt/prompts/") and path.endswith(".md")),
    }


def prepare(root: Path, artifact_root: Path) -> dict:
    """Create immutable source/config/order snapshots, without a runtime queue."""
    freeze_path = artifact_root / "freeze.json"
    if freeze_path.exists():
        old = json.loads(freeze_path.read_text(encoding="utf-8"))
        candidate = make_freeze(root)
        if {k: v for k, v in old.items() if k != "created_at"} != {k: v for k, v in candidate.items() if k != "created_at"}:
            raise RuntimeError("Frozen r1 diagnostics inputs changed; revision is immutable")
        _verify_artifacts(root, artifact_root, old)
        return old
    # A prechange registration manifest is provenance only and can never stand in for this freeze.
    if (artifact_root / "queue_state.json").exists():
        raise RuntimeError("Runtime queue exists without an r1 freeze; refusing adoption")
    if (root / "outputs/studies/continuous_adaptation_v3/freeze.json").exists():
        # The old freeze may coexist; it is never read or reused.
        pass
    freeze = make_freeze(root)
    artifact_root.mkdir(parents=True, exist_ok=True)
    write_json(freeze_path, freeze)
    for relative, expected_hash in freeze["files"].items():
        source = root / Path(relative)
        if sha256(source) != expected_hash:
            raise RuntimeError(f"Input changed while snapshotting: {relative}")
        destination = artifact_root / "source_snapshot" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    for cell_id, order in freeze["task_orders"].items():
        write_json(artifact_root / "task_orders" / f"{cell_id}.json", order)
    write_json(artifact_root / "test_subset.json", freeze["test_subset"])
    write_json(artifact_root / "resolved_configs.json", freeze["configs"])
    (artifact_root / "OFFLINE_ONLY.md").write_text(
        "# Offline-only runtime diagnostics\n\nThis revision records configuration and task-order references only. "
        "It has no runtime queue and does not authorize a formal experiment or model call.\n",
        encoding="utf-8")
    return freeze


def _verify_artifacts(root: Path, artifact_root: Path, freeze: dict) -> None:
    verify_files(root, freeze.get("files", {}))
    snapshot_root = artifact_root / "source_snapshot"
    if not snapshot_root.is_dir():
        raise RuntimeError("Frozen source snapshot is missing")
    snapshot_files = {
        path.relative_to(snapshot_root).as_posix(): sha256(path)
        for path in snapshot_root.rglob("*") if path.is_file()
    }
    if snapshot_files != freeze.get("files", {}):
        added = sorted(snapshot_files.keys() - freeze["files"].keys())
        removed = sorted(freeze["files"].keys() - snapshot_files.keys())
        changed = sorted(k for k in snapshot_files.keys() & freeze["files"].keys()
                         if snapshot_files[k] != freeze["files"][k])
        raise RuntimeError(f"Frozen source snapshot is incomplete or changed (added={added[:5]}, removed={removed[:5]}, changed={changed[:5]})")

    for cell_id, expected in freeze.get("task_orders", {}).items():
        path = artifact_root / "task_orders" / f"{cell_id}.json"
        if not path.is_file() or json.loads(path.read_text(encoding="utf-8")) != expected:
            raise RuntimeError(f"Task-order artifact does not match freeze: {cell_id}")
    for name, expected in (("test_subset.json", freeze.get("test_subset")),
                           ("resolved_configs.json", freeze.get("configs"))):
        path = artifact_root / name
        if not path.is_file() or json.loads(path.read_text(encoding="utf-8")) != expected:
            raise RuntimeError(f"{name} does not match the immutable freeze")


def verify(root: Path, artifact_root: Path) -> dict:
    freeze_path = artifact_root / "freeze.json"
    if not freeze_path.is_file():
        raise FileNotFoundError(f"No r1 diagnostics freeze: {freeze_path}")
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    if freeze.get("study_id") != STUDY:
        raise RuntimeError("Refusing to verify a freeze belonging to another study")
    _verify_artifacts(root, artifact_root, freeze)
    return freeze
