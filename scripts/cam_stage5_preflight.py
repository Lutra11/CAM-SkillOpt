"""Stage-5 preflight for CAM-SkillOpt experiments.

This script does not train models or call external APIs. It verifies that the
local experiment matrix, raw benchmark payloads, and model credentials are ready
before expensive benchmark runs are attempted.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENTS = [
    {
        "name": "spreadsheetbench_skillopt_baseline",
        "config": "configs/cam_experiments/spreadsheetbench_skillopt_baseline.yaml",
        "role": "baseline",
    },
    {
        "name": "spreadsheetbench_cam_full",
        "config": "configs/cam_experiments/spreadsheetbench_cam_full.yaml",
        "role": "main_method",
    },
    {
        "name": "spreadsheetbench_cam_no_bootstrap",
        "config": "configs/cam_experiments/spreadsheetbench_cam_no_bootstrap.yaml",
        "role": "ablation",
    },
    {
        "name": "spreadsheetbench_cam_no_adaptive_budget",
        "config": "configs/cam_experiments/spreadsheetbench_cam_no_adaptive_budget.yaml",
        "role": "ablation",
    },
    {
        "name": "spreadsheetbench_cam_no_memory",
        "config": "configs/cam_experiments/spreadsheetbench_cam_no_memory.yaml",
        "role": "ablation",
    },
]


def _exists(relative_path: str) -> bool:
    return (REPO_ROOT / relative_path).exists()


def _count_items(relative_path: str) -> int | None:
    path = REPO_ROOT / relative_path
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return len(payload) if isinstance(payload, list) else None


def _env_status(names: list[str]) -> dict[str, bool]:
    return {name: bool(os.environ.get(name, "").strip()) for name in names}


def _status(ready: bool) -> str:
    return "READY" if ready else "BLOCKED"


def build_report() -> dict:
    config_checks = []
    for experiment in EXPERIMENTS:
        config_path = REPO_ROOT / experiment["config"]
        config_checks.append(
            {
                **experiment,
                "exists": config_path.exists(),
                "command": (
                    f"python scripts/train.py --config {experiment['config']}"
                ),
            }
        )

    materialized_split_counts = {
        "train": _count_items("data/spreadsheetbench_split/train/items.json"),
        "val": _count_items("data/spreadsheetbench_split/val/items.json"),
        "test": _count_items("data/spreadsheetbench_split/test/items.json"),
    }
    manifest_split_counts = {
        "train": _count_items("data/spreadsheetbench_id_split/train/items.json"),
        "val": _count_items("data/spreadsheetbench_id_split/val/items.json"),
        "test": _count_items("data/spreadsheetbench_id_split/test/items.json"),
    }
    split_ready = all(value is not None for value in materialized_split_counts.values())
    manifest_ready = all(value is not None for value in manifest_split_counts.values())
    payload_root = REPO_ROOT / "data/spreadsheetbench_verified_400"
    payload_ready = payload_root.exists() and any(payload_root.iterdir())
    model_env = _env_status(
        [
            "AZURE_OPENAI_ENDPOINT",
            "AZURE_OPENAI_API_KEY",
            "OPTIMIZER_AZURE_OPENAI_ENDPOINT",
            "OPTIMIZER_AZURE_OPENAI_API_KEY",
            "TARGET_AZURE_OPENAI_ENDPOINT",
            "TARGET_AZURE_OPENAI_API_KEY",
        ]
    )
    shared_model_ready = (
        model_env["AZURE_OPENAI_ENDPOINT"] and model_env["AZURE_OPENAI_API_KEY"]
    )
    split_model_ready = (
        model_env["OPTIMIZER_AZURE_OPENAI_ENDPOINT"]
        and model_env["OPTIMIZER_AZURE_OPENAI_API_KEY"]
        and model_env["TARGET_AZURE_OPENAI_ENDPOINT"]
        and model_env["TARGET_AZURE_OPENAI_API_KEY"]
    )
    model_ready = shared_model_ready or split_model_ready

    blockers = []
    if not split_ready:
        blockers.append("SpreadsheetBench split items are missing or unreadable.")
    if not payload_ready:
        blockers.append(
            "SpreadsheetBench raw payload is missing: data/spreadsheetbench_verified_400."
        )
    if not model_ready:
        blockers.append(
            "Model credentials are missing: provide shared Azure OpenAI env vars or optimizer/target role-specific env vars."
        )
    if not all(item["exists"] for item in config_checks):
        blockers.append("One or more experiment config files are missing.")

    return {
        "report_type": "cam_stage5_preflight",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(REPO_ROOT),
        "experiments": config_checks,
        "data": {
            "spreadsheetbench_manifest_ready": manifest_ready,
            "spreadsheetbench_manifest_counts": manifest_split_counts,
            "spreadsheetbench_split_ready": split_ready,
            "spreadsheetbench_split_counts": materialized_split_counts,
            "spreadsheetbench_payload_root": str(payload_root),
            "spreadsheetbench_payload_ready": payload_ready,
        },
        "model": {
            "env_present": model_env,
            "shared_azure_openai_ready": shared_model_ready,
            "role_specific_azure_openai_ready": split_model_ready,
            "model_ready": model_ready,
        },
        "overall_status": _status(not blockers),
        "blockers": blockers,
    }


def write_markdown(report: dict, path: Path) -> None:
    lines = [
        "# CAM-SkillOpt Stage 5 Preflight",
        "",
        f"- Status: `{report['overall_status']}`",
        f"- Repo: `{report['repo_root']}`",
        f"- Generated: `{report['generated_at']}`",
        "",
        "## Experiment Matrix",
        "",
        "| Experiment | Role | Config | Status | Command |",
        "|---|---|---|---|---|",
    ]
    for experiment in report["experiments"]:
        status = _status(experiment["exists"])
        lines.append(
            "| "
            + " | ".join(
                [
                    experiment["name"],
                    experiment["role"],
                    f"`{experiment['config']}`",
                    f"`{status}`",
                    f"`{experiment['command']}`",
                ]
            )
            + " |"
        )
    data = report["data"]
    lines.extend(
        [
            "",
            "## Readiness",
            "",
            f"- SpreadsheetBench split: `{_status(data['spreadsheetbench_split_ready'])}`; counts={data['spreadsheetbench_split_counts']}",
            f"- SpreadsheetBench manifest: `{_status(data['spreadsheetbench_manifest_ready'])}`; counts={data['spreadsheetbench_manifest_counts']}",
            f"- SpreadsheetBench raw payload: `{_status(data['spreadsheetbench_payload_ready'])}`; path=`{data['spreadsheetbench_payload_root']}`",
            f"- Model credentials: `{_status(report['model']['model_ready'])}`",
            "",
            "## Blockers",
            "",
        ]
    )
    if report["blockers"]:
        lines.extend(f"- {blocker}" for blocker in report["blockers"])
    else:
        lines.append("- None.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    out_dir = REPO_ROOT / "outputs" / "stage5_preflight"
    out_dir.mkdir(parents=True, exist_ok=True)
    report = build_report()
    json_path = out_dir / "report.json"
    md_path = out_dir / "report.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    write_markdown(report, md_path)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nWrote {json_path}")
    print(f"Wrote {md_path}")


if __name__ == "__main__":
    main()
