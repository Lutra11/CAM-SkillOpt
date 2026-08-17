"""Stage-8 runtime preflight for CAM-SkillOpt.

Checks the local Python environment, materialized SpreadsheetBench data, CAM
experiment configs, and model credential variables. It does not call model APIs
or start training.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "outputs" / "stage8_env_preflight"
CORE_IMPORTS = [
    "openai",
    "yaml",
    "numpy",
    "openpyxl",
    "azure.identity",
    "azure.core",
    "httpx",
]
CAM_CONFIGS = [
    "configs/cam_experiments/spreadsheetbench_skillopt_baseline.yaml",
    "configs/cam_experiments/spreadsheetbench_cam_full.yaml",
    "configs/cam_experiments/spreadsheetbench_cam_no_bootstrap.yaml",
    "configs/cam_experiments/spreadsheetbench_cam_no_adaptive_budget.yaml",
    "configs/cam_experiments/spreadsheetbench_cam_no_memory.yaml",
]
MODEL_ENV_KEYS = [
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_KEY",
    "OPTIMIZER_AZURE_OPENAI_ENDPOINT",
    "OPTIMIZER_AZURE_OPENAI_API_KEY",
    "TARGET_AZURE_OPENAI_ENDPOINT",
    "TARGET_AZURE_OPENAI_API_KEY",
]


def check_imports() -> dict[str, dict]:
    checks: dict[str, dict] = {}
    for name in CORE_IMPORTS:
        try:
            module = importlib.import_module(name)
            checks[name] = {
                "ok": True,
                "version": getattr(module, "__version__", ""),
            }
        except Exception as exc:
            checks[name] = {"ok": False, "error": repr(exc)}
    return checks


def count_split(split: str) -> int | None:
    path = REPO_ROOT / "data" / "spreadsheetbench_split" / split / "items.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return len(payload) if isinstance(payload, list) else None


def env_presence() -> dict[str, bool]:
    return {key: bool(os.environ.get(key, "").strip()) for key in MODEL_ENV_KEYS}


def build_report() -> dict:
    imports = check_imports()
    data_counts = {split: count_split(split) for split in ("train", "val", "test")}
    data_ready = data_counts == {"train": 80, "val": 40, "test": 280}
    config_ready = {path: (REPO_ROOT / path).exists() for path in CAM_CONFIGS}
    env_ready = env_presence()
    shared_model_ready = env_ready["AZURE_OPENAI_ENDPOINT"] and env_ready["AZURE_OPENAI_API_KEY"]
    split_model_ready = (
        env_ready["OPTIMIZER_AZURE_OPENAI_ENDPOINT"]
        and env_ready["OPTIMIZER_AZURE_OPENAI_API_KEY"]
        and env_ready["TARGET_AZURE_OPENAI_ENDPOINT"]
        and env_ready["TARGET_AZURE_OPENAI_API_KEY"]
    )
    blockers = []
    if not all(item["ok"] for item in imports.values()):
        blockers.append("One or more core Python imports failed.")
    if not data_ready:
        blockers.append("SpreadsheetBench materialized split is not ready.")
    if not all(config_ready.values()):
        blockers.append("One or more CAM experiment configs are missing.")
    if not (shared_model_ready or split_model_ready):
        blockers.append(
            "Model credentials are missing. Set shared Azure OpenAI vars or optimizer/target role-specific vars."
        )

    return {
        "report_type": "cam_stage8_env_preflight",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(REPO_ROOT),
        "python": sys.executable,
        "python_version": sys.version,
        "core_imports": imports,
        "data": {
            "spreadsheetbench_split_counts": data_counts,
            "spreadsheetbench_split_ready": data_ready,
        },
        "configs": config_ready,
        "model_env_present": env_ready,
        "model_ready": bool(shared_model_ready or split_model_ready),
        "overall_status": "READY" if not blockers else "BLOCKED",
        "blockers": blockers,
    }


def write_markdown(report: dict, path: Path) -> None:
    lines = [
        "# CAM-SkillOpt Stage 8 Environment Preflight",
        "",
        f"- Status: `{report['overall_status']}`",
        f"- Python: `{report['python']}`",
        "",
        "## Core Imports",
        "",
        "| Package | OK | Version/Error |",
        "|---|---|---|",
    ]
    for name, info in report["core_imports"].items():
        detail = info.get("version") or info.get("error", "")
        lines.append(f"| {name} | `{info['ok']}` | `{detail}` |")
    lines.extend(
        [
            "",
            "## Data And Config",
            "",
            f"- SpreadsheetBench split: `{report['data']['spreadsheetbench_split_ready']}`; counts={report['data']['spreadsheetbench_split_counts']}",
            f"- CAM configs ready: `{all(report['configs'].values())}`",
            f"- Model credentials ready: `{report['model_ready']}`",
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
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report = build_report()
    json_path = OUT_DIR / "report.json"
    md_path = OUT_DIR / "report.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    write_markdown(report, md_path)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nWrote {json_path}")
    print(f"Wrote {md_path}")


if __name__ == "__main__":
    main()
