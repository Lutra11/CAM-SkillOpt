"""Diagnose empty-content responses: find token budgets / params that work.

Some reasoning-capable models behind the gateway return an empty `content`
when the token budget is too small, because the budget is consumed by
internal reasoning. This script finds a workable configuration per alias.

Usage:
    $env:ZGCI_API_KEY = "sk-..."
    python scripts/probe_zgci_budget.py
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

BASE_URL = os.environ.get("ZGCI_BASE_URL", "https://hub.zgci.org/v1")
API_KEY = os.environ.get("ZGCI_API_KEY", "").strip()

MODELS = ["glm", "qwen", "minimax", "gemma4:26b", "smart-router", "smart-router-v2"]
BUDGETS = [256, 2048, 8192]
PROMPT = (
    "Write a Python function `clean(df)` that drops rows where column 'Amount' is null "
    "and returns the DataFrame. Reply with only a ```python fenced code block."
)


def call(model: str, max_tokens: int) -> dict:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "max_tokens": max_tokens,
        "temperature": 0,
    }
    request = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        method="POST",
    )
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        return {"error": f"HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:200]}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}

    elapsed = round(time.time() - started, 2)
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    usage = data.get("usage") or {}
    reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
    return {
        "elapsed_s": elapsed,
        "finish_reason": choice.get("finish_reason"),
        "content_len": len(message.get("content") or ""),
        "reasoning_len": len(reasoning) if isinstance(reasoning, str) else len(str(reasoning)),
        "completion_tokens": usage.get("completion_tokens"),
        "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
        "content_head": (message.get("content") or "")[:110].replace("\n", "\\n"),
    }


def main() -> int:
    if not API_KEY:
        print("ZGCI_API_KEY is not set.")
        return 2

    for model in MODELS:
        print(f"\n=== {model}")
        for budget in BUDGETS:
            result = call(model, budget)
            if "error" in result:
                print(f"  max_tokens={budget:<6} ERROR {result['error']}")
                continue
            empty = "  <-- EMPTY CONTENT" if result["content_len"] == 0 else ""
            print(
                f"  max_tokens={budget:<6} {result['elapsed_s']:>7}s "
                f"finish={result['finish_reason']:<12} "
                f"content={result['content_len']:>5}ch reasoning={result['reasoning_len']:>6}ch "
                f"ctok={result['completion_tokens']} rtok={result['reasoning_tokens']}{empty}"
            )
            if result["content_len"]:
                print(f"        {result['content_head']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
