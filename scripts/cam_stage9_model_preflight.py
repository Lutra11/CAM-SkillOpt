"""Stage-9 model-environment preflight for CAM-SkillOpt.

The script loads project-local .env values, checks whether model credentials are
available, and writes a redacted report. It does not call any model endpoint.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "outputs" / "stage9_model_preflight"
SHARED_KEYS = [
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_API_VERSION",
    "AZURE_OPENAI_AUTH_MODE",
]
ROLE_KEYS = [
    "OPTIMIZER_AZURE_OPENAI_ENDPOINT",
    "OPTIMIZER_AZURE_OPENAI_API_KEY",
    "TARGET_AZURE_OPENAI_ENDPOINT",
    "TARGET_AZURE_OPENAI_API_KEY",
]
DEPLOYMENT_KEYS = [
    "OPTIMIZER_DEPLOYMENT",
    "TARGET_DEPLOYMENT",
    "AZURE_OPENAI_DEPLOYMENT",
]


def load_project_dotenv() -> dict[str, bool]:
    loaded: dict[str, bool] = {}
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return loaded
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded[key] = True
        elif key:
            loaded[key] = False
    return loaded


def redact(value: str) -> dict:
    if not value:
        return {"present": False, "length": 0, "redacted": ""}
    if len(value) <= 8:
        redacted = "*" * len(value)
    else:
        redacted = f"{value[:4]}...{value[-4:]}"
    return {"present": True, "length": len(value), "redacted": redacted}


def env_snapshot(keys: list[str]) -> dict[str, dict]:
    return {key: redact(os.environ.get(key, "").strip()) for key in keys}


def build_report() -> dict:
    loaded_from_dotenv = load_project_dotenv()
    shared = env_snapshot(SHARED_KEYS)
    role = env_snapshot(ROLE_KEYS)
    deployments = env_snapshot(DEPLOYMENT_KEYS)
    shared_ready = (
        shared["AZURE_OPENAI_ENDPOINT"]["present"]
        and (
            shared["AZURE_OPENAI_API_KEY"]["present"]
            or shared["AZURE_OPENAI_AUTH_MODE"]["redacted"] in {"azure_cli", "managed_identity"}
        )
    )
    role_ready = all(role[key]["present"] for key in ROLE_KEYS)
    deployment_ready = (
        deployments["OPTIMIZER_DEPLOYMENT"]["present"]
        and deployments["TARGET_DEPLOYMENT"]["present"]
    ) or deployments["AZURE_OPENAI_DEPLOYMENT"]["present"]
    blockers = []
    if not (shared_ready or role_ready):
        blockers.append(
            "Missing usable Azure/OpenAI credentials. Provide shared AZURE_OPENAI_* values or optimizer/target role-specific values."
        )
    if not deployment_ready:
        blockers.append(
            "Deployment names are not explicitly set. Configure OPTIMIZER_DEPLOYMENT and TARGET_DEPLOYMENT, or AZURE_OPENAI_DEPLOYMENT."
        )

    return {
        "report_type": "cam_stage9_model_preflight",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(REPO_ROOT),
        "python": sys.executable,
        "dotenv_exists": (REPO_ROOT / ".env").exists(),
        "loaded_from_dotenv": loaded_from_dotenv,
        "shared_env": shared,
        "role_env": role,
        "deployment_env": deployments,
        "shared_ready": bool(shared_ready),
        "role_specific_ready": bool(role_ready),
        "deployment_ready": bool(deployment_ready),
        "overall_status": "READY" if not blockers else "BLOCKED",
        "blockers": blockers,
        "recommended_env_template": [
            "AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/",
            "AZURE_OPENAI_API_VERSION=2024-12-01-preview",
            "AZURE_OPENAI_API_KEY=<your-key>",
            "OPTIMIZER_DEPLOYMENT=<optimizer-deployment-name>",
            "TARGET_DEPLOYMENT=<target-deployment-name>",
        ],
    }


def write_markdown(report: dict, path: Path) -> None:
    lines = [
        "# CAM-SkillOpt Stage 9 Model Preflight",
        "",
        f"- Status: `{report['overall_status']}`",
        f"- .env exists: `{report['dotenv_exists']}`",
        f"- Shared credentials ready: `{report['shared_ready']}`",
        f"- Role-specific credentials ready: `{report['role_specific_ready']}`",
        f"- Deployment names ready: `{report['deployment_ready']}`",
        "",
        "## Redacted Environment",
        "",
        "| Key | Present | Length | Redacted |",
        "|---|---|---:|---|",
    ]
    for section in ("shared_env", "role_env", "deployment_env"):
        for key, info in report[section].items():
            lines.append(
                f"| {key} | `{info['present']}` | {info['length']} | `{info['redacted']}` |"
            )
    lines.extend(["", "## Blockers", ""])
    if report["blockers"]:
        lines.extend(f"- {blocker}" for blocker in report["blockers"])
    else:
        lines.append("- None.")
    lines.extend(
        [
            "",
            "## Minimal .env Template",
            "",
            "```dotenv",
            *report["recommended_env_template"],
            "```",
        ]
    )
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
