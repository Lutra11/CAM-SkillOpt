"""Diagnose the openai_compatible auth path end to end.

Reproduces exactly what skillopt.model.azure_openai._make_client(role) builds,
then makes one minimal request. Use when a run reports auth_error / 401.

Usage:
    python scripts/diagnose_zgci_auth.py
"""
from __future__ import annotations

import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


def _mask(value: str) -> str:
    if not value:
        return "<EMPTY>"
    return f"{value[:8]}...{value[-4:]} (len={len(value)})"


def main() -> int:
    # Mirror scripts/train.py's .env loader so the diagnosis covers both entry points.
    env_path = os.path.join(_PROJECT_ROOT, ".env")
    print(f".env exists: {os.path.exists(env_path)}  ({env_path})")
    if os.path.exists(env_path):
        loaded = []
        with open(env_path, encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export "):]
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key, value = key.strip(), value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
                    loaded.append(key)
        print(f".env loaded {len(loaded)} new vars: {loaded}")

    print("\n── 环境变量 ─────────────────────────────────")
    for key in (
        "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_AUTH_MODE", "AZURE_OPENAI_API_KEY",
        "OPTIMIZER_AZURE_OPENAI_ENDPOINT", "OPTIMIZER_AZURE_OPENAI_AUTH_MODE",
        "OPTIMIZER_AZURE_OPENAI_API_KEY",
        "TARGET_AZURE_OPENAI_ENDPOINT", "TARGET_AZURE_OPENAI_AUTH_MODE",
        "TARGET_AZURE_OPENAI_API_KEY",
        "OPTIMIZER_DEPLOYMENT", "TARGET_DEPLOYMENT",
    ):
        value = os.environ.get(key, "")
        shown = _mask(value) if "KEY" in key else (value or "<EMPTY>")
        print(f"  {key:<42} = {shown}")

    # Force the module-level config to be re-read now that env is populated.
    for module in [m for m in list(sys.modules) if m.startswith("skillopt")]:
        del sys.modules[module]
    from skillopt.model import azure_openai as llm
    from skillopt.model.backend_config import set_optimizer_backend, set_target_backend

    set_optimizer_backend("openai_chat")
    set_target_backend("openai_chat")
    llm.set_optimizer_deployment("glm")
    llm.set_target_deployment("glm")

    print("\n── 解析后的 role config ──────────────────────")
    for role in ("optimizer", "target"):
        cfg = llm._role_config(role)
        print(f"  [{role}]")
        for key in ("endpoint", "auth_mode", "api_version"):
            print(f"    {key:<14} = {cfg.get(key)!r}")
        print(f"    api_key        = {_mask(cfg.get('api_key', ''))}")

    print("\n── 构造 client 并发一次请求 ──────────────────")
    try:
        client = llm._make_client("target")
        print(f"  client type: {type(client).__name__}")
        print(f"  base_url   : {client.base_url}")
    except Exception as exc:  # noqa: BLE001
        print(f"  client construction FAILED: {type(exc).__name__}: {exc}")
        return 1

    try:
        resp = client.chat.completions.create(
            model="glm",
            messages=[{"role": "user", "content": "Reply with exactly: AUTH_OK"}],
            max_completion_tokens=32,
        )
        text = resp.choices[0].message.content
        print(f"  SUCCESS: {text!r}")
        print(f"  usage  : {resp.usage}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"  REQUEST FAILED: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
