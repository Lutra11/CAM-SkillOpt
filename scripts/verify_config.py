"""Dry-run config resolution: show exactly what a planned run will use.

Why this exists: `--cfg-options cam_enabled=false` (flat form) is SILENTLY
DROPPED by `flatten_config`, which would run the wrong experiment arm without
any error. This tool makes the resolved values visible before spending hours.

Usage:
    python scripts/verify_config.py --config configs/experiments/zgci_cam_matrix.yaml \
        --cfg-options cam.enabled=false env.out_root=outputs/x

Exits non-zero if a requested override did not take effect.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Same key groups the experiment protocol requires to be identical across arms.
REQUIRED_CAM_KEYS = [
    "cam_enabled", "cam_bootstrap_gate", "cam_confidence_level",
    "cam_meaningful_improvement", "cam_bootstrap_samples", "cam_seed",
    "cam_adaptive_budget", "cam_min_budget", "cam_max_budget",
    "cam_memory_enabled", "cam_memory_top_k",
    "cam_soft_accept", "cam_drift_patience", "cam_drift_tolerance",
    "cam_memory_inconclusive",
]
REQUIRED_RUN_KEYS = [
    "optimizer_model", "target_model", "optimizer_backend", "target_backend",
    "reasoning_effort", "num_epochs", "train_size", "batch_size", "seed",
    "minibatch_size", "merge_batch_size", "analyst_workers", "max_analyst_rounds",
    "edit_budget", "min_edit_budget", "lr_scheduler", "use_gate",
    "sel_env_num", "test_env_num", "eval_test", "env", "mode", "workers",
    "exec_timeout", "limit", "split_dir", "data_root", "out_root", "skill_init",
    "use_slow_update", "use_meta_skill", "skill_update_mode",
    # env.* keys passed through to the adapter by flatten_config; the adapter
    # binds them by parameter name, so a silent miss changes model behaviour.
    "max_completion_tokens", "max_turns",
]


def _normalize(value: object) -> str:
    """Compare CLI strings against resolved values regardless of YAML typing.

    ``--cfg-options optimizer.use_meta_skill=true`` legitimately resolves to the
    Python bool ``True``; that is a match, not a mismatch.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value).strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--cfg-options", nargs="+", default=[])
    parser.add_argument("--json", action="store_true", help="Print the whole resolved config as JSON.")
    args = parser.parse_args()

    from skillopt.config import _FLATTEN_MAP, flatten_config, load_config, apply_overrides

    cfg = load_config(args.config)
    if args.cfg_options:
        # apply_overrides mutates cfg in place and returns None.
        apply_overrides(cfg, args.cfg_options)
    flat = flatten_config(cfg)

    print(f"config : {args.config}")
    print(f"keys   : {len(flat)} resolved\n")

    groups = [("模型 / 后端", REQUIRED_RUN_KEYS), ("CAM 机制", REQUIRED_CAM_KEYS)]
    for title, keys in groups:
        print(f"── {title} " + "─" * (60 - len(title)))
        for key in keys:
            value = flat.get(key, "<MISSING>")
            print(f"  {key:<28} = {value!r}")
        print()

    # Verify every requested override actually landed.
    print("── 覆盖校验 " + "─" * 48)
    problems: list[str] = []
    for item in args.cfg_options:
        if "=" not in item:
            problems.append(f"malformed override (no '='): {item}")
            continue
        key, _, expected = item.partition("=")
        key = key.strip()
        # The override is expressed in dotted OR flat form; try both.
        resolved_key = _FLATTEN_MAP.get(key, key)
        if key.startswith("env.") and key not in _FLATTEN_MAP:
            resolved_key = key.split(".", 1)[1]
        actual = flat.get(resolved_key)
        if actual is None:
            problems.append(f"DROPPED  {key}={expected}  (not present in resolved config)")
        elif _normalize(actual).lower() != _normalize(expected).lower():
            problems.append(f"MISMATCH {key}: requested {expected!r}, resolved {actual!r}")
        else:
            print(f"  OK       {key} = {actual!r}")
    for problem in problems:
        print(f"  {problem}")
    print()

    if args.json:
        print(json.dumps(flat, indent=2, ensure_ascii=False, default=str))

    if problems:
        print(f"FAIL: {len(problems)} override problem(s). The run would use wrong settings.")
        return 1
    print("PASS: config resolves and every override took effect.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
