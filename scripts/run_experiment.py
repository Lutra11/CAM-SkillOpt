#!/usr/bin/env python3
"""CAM-SkillOpt experiment-cell launcher for the ZGCI Model Hub backend.

Why a launcher instead of hand-typed commands:

1. ``PYTHONUTF8`` must be set before the interpreter starts. This machine's
   Windows locale defaults to GBK, which makes unqualified ``open()`` calls
   (e.g. ``livemathematicianbench/dataloader.py``) raise UnicodeDecodeError.
   A launcher can re-exec itself with UTF-8 mode enabled.
2. Long ``--cfg-options`` lists are error-prone, and a flat key such as
   ``cam_enabled=false`` is *silently dropped* by ``flatten_config`` — which
   would run the wrong experiment arm for hours without any error.
3. Every cell must be validated before it burns hours.

Usage
-----
    python scripts/run_experiment.py --action train --method cam_full --target glm
    python scripts/run_experiment.py --action static --target qwen --benchmark livemathematicianbench
    python scripts/run_experiment.py --action train --method baseline --target glm --dry-run
    python scripts/run_experiment.py --list-cells
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)

# ---------------------------------------------------------------------------
# UTF-8 mode bootstrap: re-exec once with -X utf8 so that every unqualified
# open() in the codebase (and in subprocesses we spawn) defaults to UTF-8.
# ---------------------------------------------------------------------------
if not sys.flags.utf8_mode:
    argv = [sys.executable, "-X", "utf8", os.path.abspath(__file__), *sys.argv[1:]]
    os.execv(sys.executable, argv)

CONFIG = "configs/experiments/zgci_cam_matrix.yaml"

# optimizer is pinned to glm: it is the only model on this gateway that emits
# strict JSON, which the optimizer needs to produce parseable edit patches.
OPTIMIZER_MODEL = "glm"

# Benchmark scale parameters.
#   Train   : must equal the loaded train split size (trainer.py:470-474 hard-checks)
#   Sel     : must be > 0 and equal the val split size (trainer.py:1282)
#   Test    : test split size
#   SplitDir: use a pre-materialised split; empty => build one from DataPath
BENCHMARKS: dict[str, dict] = {
    "spreadsheetbench": {
        "Train": 80, "Sel": 40, "Test": 280,
        "SplitDir": "data/spreadsheetbench_split",
        "DataPath": "", "DataRoot": "data/spreadsheetbench_verified_400",
        "SkillDir": "spreadsheetbench",
    },
    "livemathematicianbench": {
        # No released split manifest (see data/README note); the loader derives a
        # deterministic 2:1:7 split from data_path with split_seed. Counts below
        # were computed from the actual 177 loaded items and match the manifest
        # counts quoted in data/README.md (35 / 18 / 124).
        "Train": 35, "Sel": 18, "Test": 124,
        "SplitDir": "", "DataPath": "data/livemathematicianbench_split",
        "DataRoot": "", "SkillDir": "livemathematicianbench",
        # ratio mode materializes into <out_root>/_generated_splits/<env>_2-1-7_seed<seed>
        "Ratio": True,
    },
    "searchqa": {
        "Train": 400, "Sel": 200, "Test": 1400,
        "SplitDir": "data/searchqa_split", "DataPath": "", "DataRoot": "",
        "SkillDir": "searchqa",
    },
    "officeqa": {
        # Counts match data/officeqa_split (materialized at 2:1:7 / seed 42).
        # train_size / sel_env_num must equal the actual split sizes.
        "Train": 49, "Sel": 25, "Test": 172,
        "SplitDir": "data/officeqa_split", "DataPath": "", "DataRoot": "",
        "SkillDir": "officeqa",
        # officeqa default search_mode is "offline": it embeds candidate
        # documents in the prompt and needs NO external search service (the
        # code paths that require search_api_url belong to "custom_search" /
        # "azure_search" modes). Only the corpus has to be present on disk.
        "DataDirs": "data/officeqa_docs_official/treasury_bulletins_parsed/transformed",
    },
}

METHODS = {
    "baseline":               ["cam.enabled=false"],
    "cam_full":               ["cam.enabled=true", "cam.bootstrap_gate=true",
                               "cam.adaptive_budget=true", "cam.memory_enabled=true"],
    "cam_no_bootstrap":       ["cam.enabled=true", "cam.bootstrap_gate=false",
                               "cam.adaptive_budget=true", "cam.memory_enabled=true"],
    "cam_no_adaptive_budget": ["cam.enabled=true", "cam.bootstrap_gate=true",
                               "cam.adaptive_budget=false", "cam.memory_enabled=true"],
    "cam_no_memory":          ["cam.enabled=true", "cam.bootstrap_gate=true",
                               "cam.adaptive_budget=true", "cam.memory_enabled=false"],
}

# Target candidates, strongest to weakest. See docs/experiments/ZGCI_MODEL_HUB_API.md.
TARGETS = ["glm", "glm_nothink", "gemma4:26b", "qwen_nothink", "qwen"]


def cell_dir_name(target: str) -> str:
    """Filesystem-safe directory name for a target id.

    `gemma4:26b` is a valid model id on the gateway but ':' is illegal in a
    Windows path component, so the cell directory uses 'gemma4-26b'. Only the
    directory is renamed; `model.target` keeps the real id.
    """
    return target.replace(":", "-")


def scale_options(scale: str) -> tuple[list[str], int]:
    """Return (cfg-options, learning_rate) for a scale preset."""
    if scale == "project":
        # Project-selected scale. Deliberately differs from upstream on ONE key
        # (documented so the deviation is explicit, not silent):
        #   analyst_workers 16 -> 8 : chosen concurrency
        # batch_size is back to the upstream 40 (user decision, 2026-10-17):
        # train_size 80 / batch 40 = 2 steps/epoch, 4 epochs = 8 steps.
        # NOTE: this preset WINS over the YAML because cfg-options are applied
        # last. Keep it equal to configs/experiments/zgci_cam_matrix.yaml
        # `train.batch_size` — a mismatch here silently changes the protocol.
        return [
            "train.num_epochs=4",
            "train.batch_size=40",
            "gradient.minibatch_size=8",
            "gradient.merge_batch_size=8",
            "gradient.analyst_workers=8",
            "gradient.max_analyst_rounds=3",
            "optimizer.learning_rate=4",
            "optimizer.min_learning_rate=2",
            "optimizer.slow_update_samples=20",
            "optimizer.use_meta_skill=true",
            "optimizer.use_slow_update=true",
        ], 4
    if scale == "official":
        # Exact upstream protocol: batch_size=40 => 2 steps/epoch, 4 epochs = 8 steps.
        return [
            "train.num_epochs=4",
            "train.batch_size=40",
            "gradient.minibatch_size=8",
            "gradient.merge_batch_size=8",
            "gradient.analyst_workers=16",
            "gradient.max_analyst_rounds=3",
            "optimizer.learning_rate=4",
            "optimizer.min_learning_rate=2",
            "optimizer.slow_update_samples=20",
            "optimizer.use_meta_skill=true",
            "optimizer.use_slow_update=true",
        ], 4
    if scale == "quick":
        return [
            "train.num_epochs=2",
            "train.batch_size=4",
            "gradient.minibatch_size=2",
            "gradient.merge_batch_size=2",
            "gradient.analyst_workers=2",
            "gradient.max_analyst_rounds=1",
            "optimizer.learning_rate=2",
            "optimizer.min_learning_rate=2",
            "optimizer.slow_update_samples=4",
            "optimizer.use_meta_skill=false",
            "optimizer.use_slow_update=false",
        ], 2
    raise ValueError(f"unknown scale {scale!r}")


def build_cell(
    *, action: str, method: str, target: str, benchmark: str,
    scale: str, seed: int, workers: int, extra: list[str],
) -> list[str]:
    """Assemble the full --cfg-options list for one experiment cell."""
    bench = BENCHMARKS[benchmark]
    scale_opts, lr = scale_options(scale)

    # ---- benchmark wiring -------------------------------------------------
    bench_opts = [
        f"env.name={benchmark}",
        f"env.skill_init=skillopt/envs/{bench['SkillDir']}/skills/initial.md",
        "env.mode=single",
        f"env.workers={workers}",
    ]
    if bench["SplitDir"]:
        bench_opts += [f"env.split_dir={bench['SplitDir']}", "env.split_mode=split_dir"]
    else:
        # ratio mode ignores split_dir; clear it so the resolved config cannot be
        # mistaken for a split_dir run.
        bench_opts += ["env.split_dir=", "env.split_mode=ratio",
                       f"env.data_path={bench['DataPath']}"]
    if bench["DataRoot"]:
        bench_opts.append(f"env.data_root={bench['DataRoot']}")
    if bench.get("DataDirs"):
        bench_opts.append(f"env.data_dirs={bench['DataDirs']}")

    # ---- size -------------------------------------------------------------
    if scale == "quick":
        bench_opts += [
            "env.limit=8",
            "train.train_size=8",
            "evaluation.sel_env_num=4",
            "evaluation.test_env_num=4",
        ]
    elif bench["Train"] > 0:
        bench_opts += [
            "env.limit=0",
            f"train.train_size={bench['Train']}",
            f"evaluation.sel_env_num={bench['Sel']}",
            f"evaluation.test_env_num={bench['Test']}",
        ]
    else:
        # train_size / sel_env_num depend on the ratio split; must be given
        # explicitly once the split exists.
        bench_opts += ["env.limit=0", "train.train_size=0"]

    # ---- method -----------------------------------------------------------
    method_opts = list(METHODS[method])
    # cam.max_budget MUST equal optimizer.learning_rate: otherwise adaptive
    # budget saturates at its ceiling and silently doubles the edit budget,
    # confounding the mechanism with a budget change.
    method_opts += [
        f"cam.max_budget={lr}",
        "cam.min_budget=2",
        f"cam.seed={seed}",
    ]

    # ---- model ------------------------------------------------------------
    closing = target.endswith("_nothink")
    target_model = target[: -len("_nothink")] if closing else target
    model_opts = [
        f"model.optimizer={OPTIMIZER_MODEL}",
        f"model.target={target_model}",
        "model.optimizer_backend=openai_chat",
        "model.target_backend=openai_chat",
        # reasoning_effort is a BINARY switch on this gateway: none = off,
        # anything else (or omitted) = on. low/medium/high are equivalent.
        # The code only sends the parameter when it is non-empty, so "off"
        # must be spelled "none" — an empty value means "send nothing" = ON.
        f"model.reasoning_effort={'none' if closing else ''}",
    ]

    arm = "static" if action == "static" else method
    out_root = f"outputs/matrix/{benchmark}/{cell_dir_name(target)}/{arm}_seed{seed}"
    run_opts = [
        f"train.seed={seed}",
        "evaluation.eval_test=true",
        f"env.out_root={out_root}",
    ]

    order = run_opts + scale_opts + bench_opts + method_opts + model_opts + list(extra)
    # De-duplicate: last occurrence wins, matching --cfg-options semantics.
    seen: dict[str, str] = {}
    for item in order:
        seen[item.split("=", 1)[0]] = item
    return list(seen.values())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--action", choices=["train", "static"], required=True,
                        help="train = optimise then evaluate; static = evaluate only")
    parser.add_argument("--method", choices=sorted(METHODS), default="cam_full",
                        help="ignored when --action static")
    parser.add_argument("--target", required=True, help=f"one of: {', '.join(TARGETS)}")
    parser.add_argument("--benchmark", choices=sorted(BENCHMARKS), default="spreadsheetbench")
    parser.add_argument("--scale", choices=["project", "official", "quick"], default="project",
                        help="project = this study's chosen scale; official = exact upstream; quick = smoke")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--cfg-option", action="append", default=[],
                        help="extra dotted override; repeatable")
    parser.add_argument("--dry-run", action="store_true", help="print the command only")
    parser.add_argument("--skip-verify", action="store_true",
                        help="skip the config verification pre-flight")
    parser.add_argument("--list-cells", action="store_true",
                        help="print the full planned matrix and exit")
    args = parser.parse_args()

    if args.list_cells:
        return _list_cells()

    if args.target not in TARGETS:
        parser.error(f"--target must be one of {TARGETS}")

    options = build_cell(
        action=args.action, method=args.method, target=args.target,
        benchmark=args.benchmark, scale=args.scale, seed=args.seed,
        workers=args.workers, extra=args.cfg_option,
    )

    bench = BENCHMARKS[args.benchmark]
    arm = "static" if args.action == "static" else args.method
    target_model = args.target[: -len("_nothink")] if args.target.endswith("_nothink") else args.target
    thinking = "off (reasoning_effort=none)" if args.target.endswith("_nothink") else "on"

    print("=" * 72)
    print("  CAM-SkillOpt experiment cell")
    print("=" * 72)
    print(f"  benchmark   : {args.benchmark}")
    print(f"  action      : {args.action}   arm: {arm}")
    print(f"  optimizer   : {OPTIMIZER_MODEL}  (pinned)")
    print(f"  target      : {target_model}   thinking: {thinking}")
    print(f"  scale       : {args.scale}")
    print(f"  seed        : {args.seed}")
    print(f"  output      : outputs/matrix/{args.benchmark}/{cell_dir_name(args.target)}/{arm}_seed{args.seed}")
    print("=" * 72)

    script = "scripts/eval_only.py" if args.action == "static" else "scripts/train.py"
    argv = [sys.executable, "-X", "utf8", script, "--config", CONFIG, "--cfg-options", *options]
    if args.action == "static":
        argv += ["--skill", f"skillopt/envs/{bench['SkillDir']}/skills/initial.md",
                 "--split", "valid_unseen"]

    if not args.skip_verify:
        print("\n-- config pre-flight --")
        verify = subprocess.run(
            [sys.executable, "-X", "utf8", "scripts/verify_config.py",
             "--config", CONFIG, "--cfg-options", *options],
            cwd=_PROJECT_ROOT,
        )
        if verify.returncode != 0:
            print("\nABORTED: config verification failed. Refusing to burn hours on the wrong arm.")
            return verify.returncode

    if args.dry_run:
        print("\n-- DRY RUN --")
        print(" ".join(f'"{a}"' if " " in a else a for a in argv))
        return 0

    print("\n-- running --", flush=True)
    return subprocess.run(argv, cwd=_PROJECT_ROOT).returncode


def _list_cells() -> int:
    """Print the planned matrix with cell counts per batch."""
    batches = {
        "Batch 1 (core: strongest vs weakest target)": [
            (m, t) for t in ("glm", "qwen")
            for m in ("baseline", "cam_full")
        ],
        "Batch 2 (model sweep, CAM-Full only)": [
            ("cam_full", t) for t in ("glm_nothink", "gemma4:26b", "qwen_nothink")
        ],
        "Batch 3 (ablations on weakest target)": [
            (m, "qwen") for m in ("cam_no_bootstrap", "cam_no_adaptive_budget", "cam_no_memory")
        ],
    }
    total = 0
    for title, cells in batches.items():
        print(f"\n{title}")
        for method, target in cells:
            print(f"    --action train --method {method:<22} --target {target}")
            total += 1
    print(f"\nSpreadsheetBench training cells        : {total}")
    print(f"Plus static baselines ({len(TARGETS)} targets) : {len(TARGETS)}")
    print(f"Plus LiveMathematicianBench / SearchQA : per benchmark, core arms only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
