"""Stage-10 OpenAI-compatible provider preflight.

Validates the DeepSeek-as-optimizer and GLM-as-target configuration without
printing secrets. The optional connectivity probe is only attempted when both
keys are present and ``--probe`` is provided.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
OUT_DIR = REPO_ROOT / "outputs" / "stage10_compat_preflight"
DEFAULT_CONFIG_PATH = "configs/cam_experiments/spreadsheetbench_cam_full_deepseek_glm.yaml"


def load_dotenv() -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
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


def redacted_env(key: str) -> dict:
    value = os.environ.get(key, "").strip()
    if not value:
        return {"present": False, "length": 0, "redacted": ""}
    redacted = "*" * len(value) if len(value) <= 8 else f"{value[:4]}...{value[-4:]}"
    return {"present": True, "length": len(value), "redacted": redacted}


def probe_openai_compatible(name: str, base_url: str, api_key: str, model: str) -> dict:
    from openai import OpenAI

    result = {"provider": name, "ok": False, "model": model, "error": ""}
    try:
        client = OpenAI(base_url=base_url.rstrip("/"), api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with OK only."}],
            max_tokens=4,
            temperature=0,
            timeout=30,
        )
        text = response.choices[0].message.content or ""
        result.update({"ok": True, "response_preview": text[:40]})
    except Exception as exc:
        result["error"] = repr(exc)
    return result


def build_report(probe: bool, config_path: str, deepseek_only: bool) -> dict:
    load_dotenv()
    from skillopt.config import flatten_config, load_config

    cfg = flatten_config(load_config(config_path))
    keys = [
        "OPTIMIZER_AZURE_OPENAI_API_KEY",
        "TARGET_AZURE_OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
        "GLM_API_KEY",
        "ZHIPUAI_API_KEY",
    ]
    env = {key: redacted_env(key) for key in keys}
    optimizer_key = os.environ.get("OPTIMIZER_AZURE_OPENAI_API_KEY", "").strip() or os.environ.get("DEEPSEEK_API_KEY", "").strip()
    target_key = (
        optimizer_key
        if deepseek_only
        else (
            os.environ.get("TARGET_AZURE_OPENAI_API_KEY", "").strip()
            or os.environ.get("GLM_API_KEY", "").strip()
            or os.environ.get("ZHIPUAI_API_KEY", "").strip()
        )
    )
    blockers = []
    if not optimizer_key:
        blockers.append("DeepSeek optimizer key missing. Set OPTIMIZER_AZURE_OPENAI_API_KEY or DEEPSEEK_API_KEY.")
    if not target_key:
        blockers.append("GLM target key missing. Set TARGET_AZURE_OPENAI_API_KEY or GLM_API_KEY/ZHIPUAI_API_KEY.")

    probes = []
    if probe and not blockers:
        probes.append(
            probe_openai_compatible(
                "deepseek",
                cfg["optimizer_azure_openai_endpoint"],
                optimizer_key,
                cfg["optimizer_model"],
            )
        )
        probes.append(
            probe_openai_compatible(
                "deepseek_target" if deepseek_only else "glm",
                cfg["target_azure_openai_endpoint"],
                target_key,
                cfg["target_model"],
            )
        )
        if not all(item["ok"] for item in probes):
            blockers.append("One or more OpenAI-compatible connectivity probes failed.")

    return {
        "report_type": "cam_stage10_compat_preflight",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(REPO_ROOT),
        "python": sys.executable,
        "config": config_path,
        "config_summary": {
            "optimizer_model": cfg["optimizer_model"],
            "target_model": cfg["target_model"],
            "optimizer_endpoint": cfg["optimizer_azure_openai_endpoint"],
            "target_endpoint": cfg["target_azure_openai_endpoint"],
            "optimizer_auth_mode": cfg["optimizer_azure_openai_auth_mode"],
            "target_auth_mode": cfg["target_azure_openai_auth_mode"],
        },
        "env": env,
        "probe_requested": probe,
        "probes": probes,
        "overall_status": "READY" if not blockers else "BLOCKED",
        "blockers": blockers,
        "deepseek_only": deepseek_only,
        "recommended_env": (
            ["DEEPSEEK_API_KEY=<deepseek-key>"]
            if deepseek_only
            else [
                "OPTIMIZER_AZURE_OPENAI_API_KEY=<deepseek-key>",
                "TARGET_AZURE_OPENAI_API_KEY=<glm-key>",
            ]
        ),
    }


def write_markdown(report: dict, path: Path) -> None:
    lines = [
        "# CAM-SkillOpt Stage 10 Compatibility Preflight",
        "",
        f"- Status: `{report['overall_status']}`",
        f"- Config: `{report['config']}`",
        f"- Optimizer: `{report['config_summary']['optimizer_model']}` at `{report['config_summary']['optimizer_endpoint']}`",
        f"- Target: `{report['config_summary']['target_model']}` at `{report['config_summary']['target_endpoint']}`",
        "",
        "## Redacted Keys",
        "",
        "| Key | Present | Length | Redacted |",
        "|---|---|---:|---|",
    ]
    for key, info in report["env"].items():
        lines.append(f"| {key} | `{info['present']}` | {info['length']} | `{info['redacted']}` |")
    lines.extend(["", "## Probes", ""])
    if report["probes"]:
        for probe in report["probes"]:
            detail = probe.get("response_preview") or probe.get("error", "")
            lines.append(f"- {probe['provider']}: ok=`{probe['ok']}` detail=`{detail}`")
    else:
        lines.append(f"- Not run. probe_requested=`{report['probe_requested']}`")
    lines.extend(["", "## Blockers", ""])
    if report["blockers"]:
        lines.extend(f"- {blocker}" for blocker in report["blockers"])
    else:
        lines.append("- None.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true", help="Run a tiny live API probe for both providers.")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="Config file to validate.")
    parser.add_argument("--deepseek-only", action="store_true", help="Reuse the DeepSeek key for both optimizer and target.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report = build_report(
        probe=args.probe,
        config_path=args.config,
        deepseek_only=args.deepseek_only,
    )
    json_path = OUT_DIR / "report.json"
    md_path = OUT_DIR / "report.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    write_markdown(report, md_path)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nWrote {json_path}")
    print(f"Wrote {md_path}")


if __name__ == "__main__":
    main()
