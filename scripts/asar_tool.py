#!/usr/bin/env python3
"""Minimal Electron asar reader + selective extractor.

Electron's asar layout:
    uint32  4                          (size of the size field)
    uint32  json_len + 8
    uint32  json_len + 4
    uint32  json_len
    bytes   json_len                   (UTF-8 JSON header)
    bytes   content                    (all file payloads concatenated)

Every file entry in the header carries {size, offset}. Use this script to list
entries or extract a subset without unpacking the whole archive.

Usage:
    python asar_tool.py list  <archive> [substring]
    python asar_tool.py extract <archive> <dest-dir> [substring]
"""
from __future__ import annotations

import json
import os
import struct
import sys


def read_header(archive: str) -> tuple[dict, int]:
    with open(archive, "rb") as handle:
        head = handle.read(16)
        _, _, _, json_len = struct.unpack("<IIII", head)
        header = json.loads(handle.read(json_len).decode("utf-8"))
        content_offset = 16 + json_len
    return header, content_offset


def walk(node: dict, prefix: str = ""):
    """Yield (path, size, offset) for every file."""
    for name, meta in node.items():
        path = f"{prefix}/{name}" if prefix else name
        if "files" in meta:
            yield from walk(meta["files"], path)
        else:
            yield path, meta.get("size", 0), meta.get("offset")


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    command, archive = sys.argv[1], sys.argv[2]
    header, content_offset = read_header(archive)
    files = list(walk(header.get("files", {})))

    if command == "list":
        needle = sys.argv[3].lower() if len(sys.argv) > 3 else ""
        matched = [f for f in files if needle in f[0].lower()] if needle else files
        print(f"# {len(files)} files total, {len(matched)} matched", file=sys.stderr)
        for path, size, _ in sorted(matched):
            print(f"{size:>10}  {path}")
        return 0

    if command == "extract":
        dest = sys.argv[3]
        needle = sys.argv[4].lower() if len(sys.argv) > 4 else ""
        targets = [f for f in files if needle in f[0].lower()] if needle else files
        written = 0
        with open(archive, "rb") as handle:
            for path, size, offset in targets:
                if offset is None:
                    continue
                out = os.path.join(dest, path)
                os.makedirs(os.path.dirname(out), exist_ok=True)
                handle.seek(content_offset + int(offset))
                payload = handle.read(int(size))
                with open(out, "wb") as fh:
                    fh.write(payload)
                written += 1
        print(f"extracted {written} file(s) to {dest}", file=sys.stderr)
        return 0

    print(f"unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
