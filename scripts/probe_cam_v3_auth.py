"""Safely probe CAM v3 model-role credentials against the configured ZGCI hub.

Loads the same project-local .env values as scripts/train.py without printing
or persisting any credential material. Makes at most one tiny request per role
using its selected credential, and one fallback request only after a 401 when
an existing, distinct, allow-listed same-hub credential is available.

Run from the project root with the configured Python environment:
    python -X utf8 scripts/probe_cam_v3_auth.py
"""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import sys
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs" / "experiments" / "cam_v3_mechanisms.yaml"
REPORT_DIR = ROOT / "reports" / "cam_v3_online" / "auth"
PROMPT = "Reply exactly: OK"
ROLES = {
    "optimizer": {
        "model_key": "optimizer_model",
        "config_key": "optimizer_azure_openai_api_key",
        "target_env_key": "OPTIMIZER_AZURE_OPENAI_API_KEY",
        "sources": (
            "OPTIMIZER_AZURE_OPENAI_API_KEY",
            "AZURE_OPENAI_OPTIMIZER_API_KEY",
            "AZURE_OPENAI_API_KEY",
            "DEEPSEEK_API_KEY",
            "TARGET_AZURE_OPENAI_API_KEY",
            "AZURE_OPENAI_TARGET_API_KEY",
        ),
    },
    "target": {
        "model_key": "target_model",
        "config_key": "target_azure_openai_api_key",
        "target_env_key": "TARGET_AZURE_OPENAI_API_KEY",
        "sources": (
            "TARGET_AZURE_OPENAI_API_KEY",
            "AZURE_OPENAI_TARGET_API_KEY",
            "AZURE_OPENAI_API_KEY",
            "DEEPSEEK_API_KEY",
            "OPTIMIZER_AZURE_OPENAI_API_KEY",
            "AZURE_OPENAI_OPTIMIZER_API_KEY",
        ),
    },
}


def load_project_dotenv() -> dict[str, str]:
    """Load missing project .env entries and return their source variable names."""
    provenance: dict[str, str] = {}
    if os.environ.get("SKILLOPT_LOAD_DOTENV", "1").strip().lower() in {"0", "false", "no"}:
        return provenance
    env_path = ROOT / ".env"
    if not env_path.exists():
        return provenance
    with env_path.open(encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].strip()
            if "=" not in line:
                continue
            name, value = line.split("=", 1)
            name = name.strip()
            value = value.strip().strip('"').strip("'")
            if name and name not in os.environ:
                os.environ[name] = value
                provenance[name] = f"project_dotenv:{name}"
    return provenance


def _load_training_aliases(provenance: dict[str, str]) -> None:
    """Mirror scripts/train.py's existing DeepSeek alias without exposing it."""
    source = "DEEPSEEK_API_KEY"
    if not os.environ.get(source):
        return
    for role_key in ("OPTIMIZER_AZURE_OPENAI_API_KEY", "TARGET_AZURE_OPENAI_API_KEY"):
        if role_key not in os.environ:
            os.environ[role_key] = os.environ[source]
            provenance[role_key] = provenance.get(source, f"process_environment:{source}")


def _source_name(name: str, provenance: dict[str, str]) -> str:
    return provenance.get(name, f"process_environment:{name}")


def select_role_credential(role: str, source_var: str) -> str:
    """Select an already-loaded credential for a role in this process only.

    Call before importing/configuring the model backend. The allow-list prevents
    callers from selecting unrelated environment variables. No file is changed.
    """
    if role not in ROLES or source_var not in ROLES[role]["sources"]:
        raise ValueError("Unsupported role credential selection")
    value = os.environ.get(source_var, "").strip()
    if not value:
        raise ValueError("Selected credential is unavailable")
    os.environ[ROLES[role]["target_env_key"]] = value
    return ROLES[role]["target_env_key"]


def _credential_candidates(role: str, config_key: str, cfg: dict[str, Any],
                           provenance: dict[str, str]) -> list[tuple[str, str]]:
    role_cfg = ROLES[role]
    config_value = cfg.get(config_key)
    candidates: list[tuple[str, str]] = []
    if isinstance(config_value, str) and config_value.strip():
        candidates.append((f"config:{config_key}", config_value.strip()))
    for name in role_cfg["sources"]:
        value = os.environ.get(name, "").strip()
        if value and not any(value == old for _source, old in candidates):
            candidates.append((_source_name(name, provenance), value))
    return candidates[:2]


def _probe(role: str, model: str, endpoint: str, source: str, credential: str) -> dict[str, Any]:
    """Make one request and return only sanitized response metadata."""
    from openai import OpenAI

    result: dict[str, Any] = {
        "role": role,
        "model": model,
        "endpoint": endpoint,
        "credential_source": source,
        "status": None,
        "request_id": None,
        "error_type": None,
    }
    try:
        client = OpenAI(base_url=endpoint.rstrip("/"), api_key=credential,
                        max_retries=0, timeout=30.0)
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": PROMPT}],
            max_tokens=32,
        )
        result["status"] = 200
        result["request_id"] = getattr(response, "_request_id", None)
        return result
    except Exception as exc:  # Deliberately omit exception text/body/headers.
        status = getattr(exc, "status_code", None)
        result["status"] = status if isinstance(status, int) else None
        request_id = getattr(exc, "request_id", None)
        result["request_id"] = request_id if isinstance(request_id, str) else None
        result["error_type"] = type(exc).__name__
        return result


def _safe_endpoint(endpoint: str) -> bool:
    parsed = urlsplit(endpoint)
    return parsed.scheme == "https" and parsed.hostname == "hub.zgci.org" and not parsed.query and not parsed.username


def main() -> int:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    provenance = load_project_dotenv()
    _load_training_aliases(provenance)

    from skillopt.config import flatten_config, load_config

    cfg = flatten_config(load_config(str(CONFIG)))
    shared_endpoint = (cfg.get("azure_openai_endpoint") or cfg.get("azure_endpoint")
                       or os.environ.get("AZURE_OPENAI_ENDPOINT", ""))
    report: dict[str, Any] = {
        "schema_version": 1,
        "config": str(CONFIG.relative_to(ROOT)).replace(os.sep, "/"),
        "config_sha256": hashlib.sha256(CONFIG.read_bytes()).hexdigest(),
        "roles": {},
        "request_policy": {"max_requests_per_role": 2, "retries": 0,
                           "timeout_seconds": 30, "max_output_tokens": 32,
                           "prompt": PROMPT},
        "helper_api": "scripts.probe_cam_v3_auth.select_role_credential(role, source_var)",
    }
    for role, role_info in ROLES.items():
        endpoint = str(cfg.get(f"{role}_azure_openai_endpoint") or shared_endpoint or "").strip()
        model = str(cfg.get(role_info["model_key"]) or "").strip()
        role_result: dict[str, Any] = {
            "role": role,
            "model": model,
            "endpoint": endpoint,
            "auth_mode": str(cfg.get(f"{role}_azure_openai_auth_mode") or cfg.get("azure_openai_auth_mode") or "openai_compatible"),
            "attempts": [],
            "status": "not_probed",
            "working_credential_source": None,
        }
        if not model or not endpoint or not _safe_endpoint(endpoint):
            role_result["status"] = "invalid_or_unexpected_endpoint_config"
            report["roles"][role] = role_result
            continue
        candidates = _credential_candidates(role, role_info["config_key"], cfg, provenance)
        if not candidates:
            role_result["status"] = "no_existing_credential"
            report["roles"][role] = role_result
            continue
        for index, (source, credential) in enumerate(candidates):
            attempt = _probe(role, model, endpoint, source, credential)
            role_result["attempts"].append(attempt)
            if attempt["status"] == 200:
                role_result["status"] = "ok"
                role_result["working_credential_source"] = source
                if index > 0:
                    role_result["process_selection_helper"] = {
                        "function": "select_role_credential",
                        "role": role,
                        "source_var": source.rsplit(":", 1)[-1],
                        "target_env_var": role_info["target_env_key"],
                        "scope": "current_process_only",
                    }
                break
            if attempt["status"] != 401:
                role_result["status"] = "request_failed_without_fallback"
                break
        else:
            role_result["status"] = "all_existing_candidates_unauthorized"
        report["roles"][role] = role_result

    report["observed_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    report["timestamp_source"] = "probe_execution"
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORT_DIR / "probe_report.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# CAM v3 credential probe",
        "",
        f"Config: `{report['config']}`",
        f"Config SHA-256: `{report['config_sha256']}`",
        f"Observed at (UTC): `{report['observed_at_utc']}` (`{report['timestamp_source']}`)",
        "Requests use a fixed minimal prompt, at most two candidates per role, zero SDK retries, and a 30-second timeout.",
        "Credential values, prefixes, credential hashes, response bodies, and raw error text are never recorded.",
        "",
    ]
    for role in ("optimizer", "target"):
        entry = report["roles"][role]
        lines.extend([
            f"## {role}",
            "",
            f"- Model: `{entry['model']}`",
            f"- Endpoint: `{entry['endpoint']}`",
            f"- Auth mode: `{entry['auth_mode']}`",
            f"- Result: `{entry['status']}`",
        ])
        for attempt in entry["attempts"]:
            lines.append(
                f"- Candidate `{attempt['credential_source']}`: HTTP `{attempt['status']}`, "
                f"request ID `{attempt['request_id']}`, error type `{attempt['error_type']}`"
            )
        if entry.get("process_selection_helper"):
            helper = entry["process_selection_helper"]
            lines.append(
                f"- Process-only helper: `select_role_credential('{helper['role']}', "
                f"'{helper['source_var']}')` sets `{helper['target_env_var']}` before backend initialization."
            )
        lines.append("")
    (REPORT_DIR / "probe_report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Sanitized probe report: {json_path}")
    print(json.dumps(report["roles"], ensure_ascii=False, indent=2))
    return 0 if all(item["status"] == "ok" for item in report["roles"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
