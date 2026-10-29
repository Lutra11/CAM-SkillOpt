"""Verify which OpenAI-compatible request parameters the ZGCI gateway accepts.

The SkillOpt code path sends `max_completion_tokens` and `reasoning_effort`
(newer OpenAI parameter names). Plain OpenAI-compatible gateways often reject
those and require `max_tokens`. This script checks each parameter in isolation
so the experiment config can be adapted correctly.

Usage:
    $env:ZGCI_API_KEY = "sk-..."
    python scripts/probe_zgci_params.py
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

BASE_URL = os.environ.get("ZGCI_BASE_URL", "https://hub.zgci.org/v1")
API_KEY = os.environ.get("ZGCI_API_KEY", "").strip()

CASES: list[tuple[str, str, dict]] = [
    ("baseline (max_tokens)", "glm", {"max_tokens": 32}),
    ("max_completion_tokens", "glm", {"max_completion_tokens": 32}),
    ("reasoning_effort=medium", "glm", {"max_tokens": 32, "reasoning_effort": "medium"}),
    ("reasoning_effort=none", "glm", {"max_tokens": 32, "reasoning_effort": "none"}),
    ("both max_* + effort", "glm", {"max_completion_tokens": 32, "reasoning_effort": "medium"}),
    ("temperature=0", "glm", {"max_tokens": 32, "temperature": 0}),
    ("top_p", "glm", {"max_tokens": 32, "top_p": 1}),
    ("n=1", "glm", {"max_tokens": 32, "n": 1}),
    ("seed", "glm", {"max_tokens": 32, "seed": 42}),
    ("stop", "glm", {"max_tokens": 32, "stop": ["zzz"]}),
    ("frequency_penalty", "glm", {"max_tokens": 32, "frequency_penalty": 0}),
    ("presence_penalty", "glm", {"max_tokens": 32, "presence_penalty": 0}),
    ("logprobs", "glm", {"max_tokens": 32, "logprobs": True}),
    ("stream=false", "glm", {"max_tokens": 32, "stream": False}),
    ("response_format json_object", "glm", {"max_tokens": 64, "response_format": {"type": "json_object"}}),
    ("parallel_tool_calls", "glm", {"max_tokens": 32, "parallel_tool_calls": False}),
    ("enable_thinking", "glm", {"max_tokens": 64, "enable_thinking": False}),
    ("thinking", "glm", {"max_tokens": 64, "thinking": {"type": "disabled"}}),
    ("max_completion_tokens", "qwen", {"max_completion_tokens": 32}),
    ("reasoning_effort=medium", "qwen", {"max_tokens": 32, "reasoning_effort": "medium"}),
    ("max_completion_tokens", "gemma4:26b", {"max_completion_tokens": 32}),
    ("max_completion_tokens", "smart-router-v2", {"max_completion_tokens": 32}),
]


def probe(model: str, extra: dict) -> tuple[bool, str, str]:
    payload = {"model": model, "messages": [{"role": "user", "content": "Say OK"}], **extra}
    request = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}", exc.read().decode("utf-8", errors="replace")[:220]
    except Exception as exc:  # noqa: BLE001
        return False, type(exc).__name__, str(exc)[:220]

    choices = data.get("choices") or []
    if not choices:
        return False, "no choices", json.dumps(data)[:220]
    message = choices[0].get("message") or {}
    return True, "ok", ((message.get("content") or "").strip() or "(empty)")[:60]


def main() -> int:
    if not API_KEY:
        print("ZGCI_API_KEY is not set.")
        return 2

    failures: list[str] = []
    for name, model, extra in CASES:
        ok, status, detail = probe(model, extra)
        marker = "PASS" if ok else "FAIL"
        if not ok:
            failures.append(f"{model} / {name}")
        print(f"[{marker}] {model:<16} {name:<26} {status:<10} {detail}")

    print("\n=== SUMMARY ===")
    print(f"passed {len(CASES) - len(failures)}/{len(CASES)}")
    for item in failures:
        print(f"  unsupported: {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
