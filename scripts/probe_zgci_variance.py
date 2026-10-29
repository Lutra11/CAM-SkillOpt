"""Characterise each alias's behaviour variance so model variants can be trusted.

Two goals:
  1. Is `reasoning_effort` a usable experimental axis? (binary on/off or graded?)
  2. Are the smart-router aliases stable enough to be treated as fixed targets,
     or do they silently switch backend models between calls?

Usage:
    $env:ZGCI_API_KEY = "sk-..."
    python scripts/probe_zgci_variance.py --repeats 6
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import time
import urllib.error
import urllib.request

BASE_URL = os.environ.get("ZGCI_BASE_URL", "https://hub.zgci.org/v1")
API_KEY = os.environ.get("ZGCI_API_KEY", "").strip()

MODELS = ["glm", "qwen", "gemma4:26b", "smart-router", "smart-router-v2"]
EFFORTS = ["none", "medium"]

# Hard enough that a weak model fails and a strong one succeeds, and that a
# "thinking" model needs real reasoning. Answer is machine-checkable.
PROMPT = (
    "Spreadsheet rows (Date, Region, Amount):\n"
    "2024-01-05, North, 100\n"
    "2024-01-07, north , 250\n"
    "2024-02-01, SOUTH, 75\n"
    "2024-02-03, South, 300\n"
    "2024-03-09, NORTH, 125\n"
    "Normalise Region to title case with whitespace stripped, group by "
    "(month of Date, normalised Region), sum Amount, then report the sum for "
    "(2024-02, south) plus the sum for (2024-01, north). "
    "Reply with only the final integer."
)
EXPECTED = "825"  # 2024-02 south = 75+300 = 375 ; 2024-01 north = 100+250 = 350


def call(model: str, effort: str, max_tokens: int = 8192) -> dict:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "max_tokens": max_tokens,
        "temperature": 0,
        "reasoning_effort": effort,
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
        return {"error": f"HTTP {exc.code}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": type(exc).__name__}

    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    usage = data.get("usage") or {}
    content = (message.get("content") or "").strip()
    digits = "".join(ch for ch in content if ch.isdigit())
    return {
        "reported": data.get("model"),
        "content": content[:40].replace("\n", " "),
        "correct": digits.lstrip("0") == EXPECTED or digits == EXPECTED,
        "ctok": usage.get("completion_tokens") or 0,
        "elapsed": round(time.time() - started, 2),
        "finish": choice.get("finish_reason"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=6)
    args = parser.parse_args()

    if not API_KEY:
        print("ZGCI_API_KEY is not set.")
        return 2

    print(f"expected answer: {EXPECTED}\n")
    for model in MODELS:
        for effort in EFFORTS:
            rows = [call(model, effort) for _ in range(args.repeats)]
            errors = [r for r in rows if "error" in r]
            ok_rows = [r for r in rows if "error" not in r]
            if not ok_rows:
                print(f"{model:<16} effort={effort:<7} ALL ERROR: {errors[0]['error']}")
                continue
            ctoks = [r["ctok"] for r in ok_rows]
            lat = [r["elapsed"] for r in ok_rows]
            correct = sum(1 for r in ok_rows if r["correct"])
            reported = {r["reported"] for r in ok_rows}
            spread = "STABLE" if len(reported) == 1 else f"VARIES({sorted(reported)})"
            print(
                f"{model:<16} effort={effort:<7} "
                f"correct={correct}/{len(ok_rows)} "
                f"ctok med={statistics.median(ctoks):>7.0f} (min {min(ctoks):>5} max {max(ctoks):>7}) "
                f"lat={statistics.median(lat):>6.1f}s  reported={spread}"
            )
            if len(set(r["content"] for r in ok_rows)) > 1:
                print(f"    answers vary: {sorted({r['content'] for r in ok_rows})[:4]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
