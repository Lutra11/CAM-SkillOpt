"""Test whether reasoning_effort actually changes behaviour on this gateway.

The user wants a model matrix with graded "thinking strength". Before putting
reasoning_effort into the experiment protocol we must know whether the gateway
honours it, or silently ignores it (which would create fake experimental axes).

Usage:
    $env:ZGCI_API_KEY = "sk-..."
    python scripts/probe_zgci_reasoning.py
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

BASE_URL = os.environ.get("ZGCI_BASE_URL", "https://hub.zgci.org/v1")
API_KEY = os.environ.get("ZGCI_API_KEY", "").strip()

# A prompt with a real reasoning step: the answer is easy to verify, and a
# model that "thinks" should spend more tokens on it.
PROMPT = (
    "A spreadsheet has rows with columns Date, Region, Amount. "
    "Region values are 'North', 'north ', 'NORTH', 'South'. "
    "How many distinct regions are there after normalising case and stripping "
    "whitespace? Answer with just the number."
)

MODELS = ["glm", "qwen", "gemma4:26b", "smart-router", "smart-router-v2"]
EFFORTS = [None, "", "none", "low", "medium", "high"]


def call(model: str, effort: str | None, max_tokens: int = 4096) -> dict:
    payload: dict = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "max_tokens": max_tokens,
        "temperature": 0,
    }
    if effort is not None and effort != "":
        payload["reasoning_effort"] = effort
    request = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        return {"error": f"HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:150]}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}

    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    usage = data.get("usage") or {}
    reasoning = message.get("reasoning_content") or ""
    return {
        "content": (message.get("content") or "").strip(),
        "reasoning_chars": len(reasoning) if isinstance(reasoning, str) else 0,
        "completion_tokens": usage.get("completion_tokens") or 0,
        "prompt_tokens": usage.get("prompt_tokens") or 0,
        "finish_reason": choice.get("finish_reason"),
    }


def main() -> int:
    if not API_KEY:
        print("ZGCI_API_KEY is not set.")
        return 2

    for model in MODELS:
        print(f"\n=== {model}")
        header = f"  {'reasoning_effort':<18} {'ctok':>6} {'reason_ch':>10}  answer"
        print(header)
        print("  " + "-" * (len(header) - 2))
        for effort in EFFORTS:
            label = "(omitted)" if effort is None else (f'"{effort}"' if effort == "" else effort)
            result = call(model, effort)
            if "error" in result:
                print(f"  {label:<18} {'--':>6} {'--':>10}  ERROR {result['error']}")
                continue
            answer = result["content"].replace("\n", " ")[:60]
            print(
                f"  {label:<18} {result['completion_tokens']:>6} "
                f"{result['reasoning_chars']:>10}  {answer}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
