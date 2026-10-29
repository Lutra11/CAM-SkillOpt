"""Fetch the ZGCI hub pricing/model page locally (the host resolves to a private IP).

Usage:
    python scripts/fetch_zgci_pricing.py [--out reports/zgci_pricing.html]
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import urllib.request

BASE = os.environ.get("ZGCI_HUB_URL", "https://hub.zgci.org")
API_KEY = os.environ.get("ZGCI_API_KEY", "").strip()

PAGES = ["/pricing", "/api/pricing", "/api/models", "/api/status", "/"]


def fetch(path: str, timeout: int = 60) -> tuple[int, str, dict]:
    headers = {"Accept": "*/*", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    request = urllib.request.Request(f"{BASE}{path}", headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", errors="replace"), dict(response.headers)
    except Exception as exc:  # noqa: BLE001
        body = ""
        if hasattr(exc, "read"):
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                body = ""
        return getattr(exc, "code", 0) or 0, f"{type(exc).__name__}: {exc}\n{body}", {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="reports/zgci_pricing.html")
    args = parser.parse_args()

    dump: list[str] = []
    for path in PAGES:
        status, body, headers = fetch(path)
        ctype = headers.get("Content-Type", "")
        print(f"=== GET {path} -> {status} ({ctype}, {len(body)} chars)")
        dump.append(f"\n\n===== GET {path} -> {status} ({ctype}) =====\n{body}")

        if "json" in ctype.lower():
            print(body[:4000])
        else:
            # Pull out JS bundle + title so we can find where pricing data lives.
            title = re.search(r"<title[^>]*>(.*?)</title>", body, re.S | re.I)
            print("  title:", (title.group(1).strip() if title else "-")[:200])
            assets = re.findall(r'(?:src|href)="([^"]+\.(?:js|json))"', body)
            for asset in assets[:25]:
                print("  asset:", asset)
            visible = re.sub(r"<script.*?</script>|<style.*?</style>", " ", body, flags=re.S | re.I)
            visible = re.sub(r"<[^>]+>", " ", visible)
            visible = re.sub(r"\s+", " ", visible).strip()
            print("  text:", visible[:2500])

    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write("".join(dump))
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
