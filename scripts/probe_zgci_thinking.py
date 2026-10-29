"""Determine which parameter actually disables thinking on this platform.

The platform's own model documentation states that `qwen` (Qwen3.6-35B-A3B)
"supports turning thinking off" and that `gemma4:26b` emits a `reasoning_content`
field before the answer. This script finds the parameter that genuinely
suppresses reasoning, because SkillOpt sends `reasoning_effort` on every call and
a wrong switch silently changes the target model's behaviour.

Two measurements per variant:
  * `reasoning_content` length in the response message (direct evidence)
  * `completion_tokens` (indirect: a thinking model spends far more)

Usage:
    $env:ZGCI_API_KEY = "sk-..."
    python -X utf8 scripts/probe_zgci_thinking.py
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

BASE_URL = os.environ.get("ZGCI_BASE_URL", "https://hub.zgci.org/v1").rstrip("/")
API_KEY = os.environ.get("ZGCI_API_KEY", "").strip()

# A prompt that is trivial without thinking but invites a reasoning trace.
PROMPT = (
    "A spreadsheet has rows with columns Date, Region, Amount:\n"
    "2024-01-05, North, 100\n2024-01-07, north , 250\n"
    "2024-02-01, SOUTH, 75\n2024-02-03, South, 300\n"
    "Normalise Region to title case with whitespace stripped, group by "
    "(month, Region), sum Amount, and report the total for 2024-01 North "
    "plus 2024-02 South. Reply with only the integer."
)

MODELS = ["qwen", "glm", "gemma4:26b"]

# Each variant is a distinct hypothesis about how thinking is switched off.
VARIANTS: list[tuple[str, dict]] = [
    ("(baseline: no param)",        {}),
    ("reasoning_effort=none",       {"reasoning_effort": "none"}),
    ("reasoning_effort=low",        {"reasoning_effort": "low"}),
    ("reasoning_effort=medium",     {"reasoning_effort": "medium"}),
    ("enable_thinking=false",       {"enable_thinking": False}),
    ("enable_thinking=true",        {"enable_thinking": True}),
    ("thinking={type:disabled}",    {"thinking": {"type": "disabled"}}),
    ("thinking={type:enabled}",     {"thinking": {"type": "enabled"}}),
    ("chat_template_kwargs",        {"chat_template_kwargs": {"enable_thinking": False}}),
    ("extra_body enable_thinking",  {"extra_body": {"enable_thinking": False}}),
]


def call(model: str, extra: dict, max_tokens: int = 8192, timeout: int = 300) -> dict:
    payload: dict = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "max_tokens": max_tokens,
        "temperature": 0,
    }
    # `extra_body` is an SDK-side convention; merge it the way the SDK would.
    if "extra_body" in extra:
        payload.update(extra["extra_body"])
    for key, value in extra.items():
        if key != "extra_body":
            payload[key] = value

    request = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        method="POST",
    )
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        return {"error": f"HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:160]}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {str(exc)[:120]}"}

    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    usage = data.get("usage") or {}
    content = message.get("content") or ""
    reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
    return {
        "elapsed": round(time.time() - started, 1),
        "finish": choice.get("finish_reason"),
        "content_chars": len(content),
        "reasoning_chars": len(reasoning) if isinstance(reasoning, str) else 0,
        "completion_tokens": usage.get("completion_tokens") or 0,
        "answer": "".join(ch for ch in content if ch.isdigit())[:12],
        "reasoning_field_present": "reasoning_content" in message,
    }


def main() -> int:
    if not API_KEY:
        print("ZGCI_API_KEY is not set.")
        return 2

    for model in MODELS:
        print(f"\n{'='*104}")
        print(f"  {model}")
        print(f"{'='*104}")
        print(f"  {'variant':<28} {'ctok':>7} {'reason_ch':>10} {'content':>8} "
              f"{'finish':<8} {'answer':<8} elapsed")
        print("  " + "-" * 100)
        baseline_tokens = None
        for label, extra in VARIANTS:
            result = call(model, extra)
            if "error" in result:
                print(f"  {label:<28} {result['error'][:70]}")
                continue
            if baseline_tokens is None:
                baseline_tokens = result["completion_tokens"]
            print(f"  {label:<28} {result['completion_tokens']:>7} "
                  f"{result['reasoning_chars']:>10} {result['content_chars']:>8} "
                  f"{str(result['finish']):<8} {result['answer']:<8} {result['elapsed']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
