#!/usr/bin/env python3
"""Offline-only CAM v3r1 runtime-diagnostics preparation and verification.

This entry point cannot launch training. It intentionally delays all project
imports until after rejecting run/cell flags, so those paths cannot initialize
model backends or load local credentials.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
STUDY = "cam_v3r1_runtime_diagnostics"
CONFIG = "configs/experiments/cam_v3r1_runtime.yaml"
ARTIFACTS = ROOT / "outputs/studies" / STUDY
OUTPUTS = ROOT / "outputs" / STUDY
REPORTS = ROOT / "reports" / STUDY
AUTH_REPORT = ROOT / "reports/cam_v3_online/auth/probe_report.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--prepare", action="store_true", help="prepare immutable offline references only")
    action.add_argument("--verify", action="store_true", help="verify an existing offline freeze")
    action.add_argument("--run", action="store_true", help="forbidden: this revision is offline-only")
    parser.add_argument("--cell", help=argparse.SUPPRESS)
    parser.add_argument("--workspace-root", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.run or args.cell:
        parser.error("this r1 revision is OFFLINE ONLY; --run and --cell are forbidden")

    # Keep these imports after flag rejection. They only parse local YAML/data;
    # this module does not import the previous runner or its execution launcher.
    workspace_root = args.workspace_root.resolve() if args.workspace_root else ROOT
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from scripts import cam_v3r1_freeze as freeze_tools

    artifact_root = workspace_root / "outputs/studies" / STUDY
    if args.prepare:
        freeze = freeze_tools.prepare(workspace_root, artifact_root)
        print(f"Study: {STUDY}; status: offline references prepared; no runtime queue")
        print(f"Cells referenced: {len(freeze['cells'])}; prompts frozen: {len(freeze['prompt_inventory'])}")
        print(f"Freeze: {artifact_root / 'freeze.json'}")
        return 0

    freeze = freeze_tools.verify(workspace_root, artifact_root)
    print(f"Study: {STUDY}; source freeze verified ({len(freeze['files'])} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
