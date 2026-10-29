"""Identify each ZGCI alias precisely: repeated calls, capabilities, and stability.

Usage:
    $env:ZGCI_API_KEY = "sk-..."
    python scripts/probe_zgci_identity.py --repeats 3
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

BASE_URL = os.environ.get("ZGCI_BASE_URL", "https://hub.zgci.org/v1")
API_KEY = os.environ.get("ZGCI_API_KEY", "").strip()

CHAT_ALIASES = ["glm", "glm-anthropic", "qwen", "minimax", "smart-router", "smart-router-v2", "gemma4:26b"]

IDENTITY_PROMPT = "What model are you? Answer with only the model name and version, nothing else."

# SkillOpt's optimizer must emit strict JSON; its target must follow an exact format.
JSON_PROMPT = 'Return only this JSON object with the value filled in: {"status": "ok", "value": 42}'

TOOL_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "submit_edit",
            "description": "Submit a skill edit",
            "parameters": {
                "type": "object",
                "properties": {"edit": {"type": "string"}},
                "required": ["edit"],
            },
        },
    }
]


def call(model: str, messages: list[dict], *, tools: list | None = None, max_tokens: int = 300,
         timeout: int = 180, temperature: float = 0) -> dict[str, Any]:
    payload: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
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
        return {"ok": False, "elapsed_s": round(time.time() - started, 2),
                "error": exc.read().decode("utf-8", errors="replace")[:300]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "elapsed_s": round(time.time() - started, 2), "error": f"{type(exc).__name__}: {exc}"}

    elapsed = round(time.time() - started, 2)
    choices = data.get("choices") or []
    message = (choices[0].get("message") if choices else {}) or {}
    return {
        "ok": True,
        "elapsed_s": elapsed,
        "reported_model": data.get("model"),
        "finish_reason": choices[0].get("finish_reason") if choices else None,
        "content": (message.get("content") or "").strip(),
        "tool_calls": message.get("tool_calls"),
        "reasoning_content": bool(message.get("reasoning_content")),
        "usage": data.get("usage"),
    }


def probe_alias(model: str, repeats: int) -> dict[str, Any]:
    out: dict[str, Any] = {"model": model, "identity": [], "json_ok": None, "tool_ok": None}

    for _ in range(repeats):
        result = call(model, [{"role": "user", "content": IDENTITY_PROMPT}], max_tokens=60)
        if result.get("ok"):
            out["identity"].append({
                "text": result["content"].replace("\n", " ")[:120],
                "reported": result.get("reported_model"),
                "elapsed_s": result["elapsed_s"],
            })
        else:
            out["identity"].append({"error": str(result.get("error"))[:200]})

    json_result = call(model, [{"role": "user", "content": JSON_PROMPT}], max_tokens=200)
    if json_result.get("ok"):
        text = json_result["content"]
        try:
            parsed = json.loads(text.strip().strip("`").removeprefix("json").strip())
            out["json_ok"] = parsed == {"status": "ok", "value": 42}
            out["json_parsed"] = parsed
        except Exception:  # noqa: BLE001
            out["json_ok"] = False
            out["json_raw"] = text[:200]
    else:
        out["json_ok"] = f"ERR {str(json_result.get('error'))[:120]}"

    tool_result = call(
        model,
        [{"role": "user", "content": "Use the submit_edit tool to submit the edit 'hello world'."}],
        tools=TOOL_SCHEMA,
        max_tokens=300,
    )
    if tool_result.get("ok"):
        calls = tool_result.get("tool_calls") or []
        out["tool_ok"] = bool(calls)
        if calls:
            out["tool_sample"] = json.dumps(calls[0], ensure_ascii=False)[:220]
        else:
            out["tool_raw"] = tool_result["content"][:220]
    else:
        out["tool_ok"] = f"ERR {str(tool_result.get('error'))[:120]}"

    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--models", default="")
    parser.add_argument("--json", default="reports/probe_zgci_identity.json")
    args = parser.parse_args()

    if not API_KEY:
        print("ZGCI_API_KEY is not set.")
        return 2

    models = [m.strip() for m in args.models.split(",") if m.strip()] or CHAT_ALIASES
    results: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for result in pool.map(lambda m: probe_alias(m, args.repeats), models):
            results.append(result)
            print(f"\n=== {result['model']}")
            for item in result["identity"]:
                if "error" in item:
                    print(f"  identity ERR : {item['error']}")
                else:
                    print(f"  identity [{item['elapsed_s']:>6}s] reported={item['reported']}: {item['text']}")
            print(f"  strict JSON : {result['json_ok']}  {result.get('json_parsed') or result.get('json_raw', '')}")
            print(f"  tool call   : {result['tool_ok']}  {result.get('tool_sample') or result.get('tool_raw', '')}")

    with open(args.json, "w", encoding="utf-8") as handle:
        json.dump({"results": results}, handle, ensure_ascii=False, indent=2)
    print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
