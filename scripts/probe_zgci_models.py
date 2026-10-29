"""Probe the ZGCI hub gateway: resolve aliases to real models and check reachability.

Usage:
    $env:ZGCI_API_KEY = "sk-..."
    python scripts/probe_zgci_models.py [--models glm,qwen,...] [--json out.json]

The API key is never written to disk or printed by this script.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any

BASE_URL = os.environ.get("ZGCI_BASE_URL", "https://hub.zgci.org/v1")
API_KEY = os.environ.get("ZGCI_API_KEY", "").strip()

PROMPT = (
    "Answer with one short line, no preamble: "
    "state the exact model name and version you are, and your knowledge cutoff."
)

# Alias candidates from GET /v1/models, plus concrete-version guesses that a
# gateway may accept even when it only advertises aliases.
DEFAULT_MODELS = [
    "glm",
    "qwen",
    "minimax",
    "gemma4:26b",
    "glm-anthropic",
    "smart-router",
    "smart-router-v2",
]


def _post(model: str, timeout: int = 120) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "max_tokens": 200,
        "temperature": 0,
    }
    request = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            headers = dict(response.headers)
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        headers = dict(exc.headers or {})
        status = exc.code
    except Exception as exc:  # noqa: BLE001 - probe reports any transport failure
        return {
            "model": model,
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_s": round(time.time() - started, 2),
        }

    elapsed = round(time.time() - started, 2)
    result: dict[str, Any] = {
        "model": model,
        "ok": 200 <= status < 300,
        "status": status,
        "elapsed_s": elapsed,
        "served_model": headers.get("X-Model") or headers.get("x-model"),
    }
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        result["raw"] = raw[:500]
        return result

    if "error" in data and data["error"]:
        result["error"] = data["error"]
        result["ok"] = False
        return result

    result["reported_model"] = data.get("model")
    result["usage"] = data.get("usage")
    choices = data.get("choices") or []
    if choices:
        message = choices[0].get("message") or {}
        result["content"] = (message.get("content") or "").strip()[:400]
        if message.get("reasoning_content"):
            result["has_reasoning_content"] = True
    return result


def list_models() -> list[str]:
    request = urllib.request.Request(
        f"{BASE_URL}/models",
        headers={"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        data = json.loads(response.read().decode("utf-8", errors="replace"))
    return sorted(item["id"] for item in data.get("data", []))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", default="", help="Comma-separated model ids to probe.")
    parser.add_argument("--json", default="", help="Write full results to this JSON path.")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    if not API_KEY:
        print("ZGCI_API_KEY is not set.", file=sys.stderr)
        return 2

    models = [m.strip() for m in args.models.split(",") if m.strip()] or DEFAULT_MODELS

    print(f"GET {BASE_URL}/models")
    try:
        advertised = list_models()
        print("advertised:", ", ".join(advertised))
    except Exception as exc:  # noqa: BLE001
        print(f"  failed: {type(exc).__name__}: {exc}")

    print(f"\nPOST {BASE_URL}/chat/completions  ({len(models)} models, {args.workers} workers)")
    results: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for result in pool.map(_post, models):
            results.append(result)
            status = "OK " if result.get("ok") else "FAIL"
            reported = result.get("reported_model") or "-"
            print(f"  [{status}] {result['model']:<18} reported={reported:<22} {result['elapsed_s']}s")
            if result.get("content"):
                print(f"         -> {result['content'][:200]}")
            if result.get("error"):
                print(f"         !! {str(result['error'])[:300]}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump({"results": results}, handle, ensure_ascii=False, indent=2)
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
