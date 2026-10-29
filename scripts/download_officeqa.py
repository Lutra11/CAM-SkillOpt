#!/usr/bin/env python3
"""Download only the OfficeQA files the experiment needs.

The full repository is 5.43 GB, nearly all of which is ``treasury_bulletin_pdfs/``
(original PDFs, ~4.5 GB) that no code path reads. OfficeQA's default
``search_mode: offline`` resolves candidate documents from ``data_dirs``, so only
the parsed text corpus and the task CSVs are required.

Two ways to fetch the corpus:
  * ``--mode zip``  (default) one request for ``treasury_bulletins_transformed.zip``
    (~97 MB) which contains the same 697 text files. Far more robust than 698
    sequential requests.
  * ``--mode files`` fetch each ``.txt`` individually, with a thread pool.

Files that matter:
  officeqa_full.csv / officeqa_pro.csv -> <dest>/
  treasury_bulletins_parsed/transformed/*.txt -> <dest>/treasury_bulletins_parsed/transformed/

Requires ``HF_TOKEN`` in the environment or in the project ``.env`` (read scope).
Set ``HF_ENDPOINT`` to a mirror when huggingface.co is unreachable.

Usage:
    python -X utf8 scripts/download_officeqa.py [--mode zip|files] [--dry-run]
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPO = "databricks/officeqa"
DEST = PROJECT_ROOT / "data" / "officeqa_docs_official"
WANTED_ROOT_FILES = ["officeqa_full.csv", "officeqa_pro.csv"]
TRANSFORMED_DIR = "treasury_bulletins_parsed/transformed"
CORPUS_ZIP = f"{TRANSFORMED_DIR}/treasury_bulletins_transformed.zip"


def load_token() -> str:
    token = os.environ.get("HF_TOKEN", "").strip()
    if token:
        return token
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("HF_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _request(endpoint: str, token: str, remote: str) -> urllib.request.Request:
    url = f"{endpoint}/datasets/{REPO}/resolve/main/{remote}"
    return urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0", "Authorization": f"Bearer {token}"}
    )


def list_dir(token: str, endpoint: str, path: str = "", retries: int = 4) -> list[dict]:
    """List one directory.

    The recursive ``tree`` endpoint is capped (1000 entries on the mirror), which
    silently truncates large repositories, so directories are listed one at a time.
    """
    suffix = f"/{path}" if path else ""
    url = f"{endpoint}/api/datasets/{REPO}/tree/main{suffix}"
    last = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0", "Authorization": f"Bearer {token}"}
            )
            with urllib.request.urlopen(request, timeout=90) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"could not list {path or '<root>'}: {last}")


def fetch(endpoint: str, token: str, remote: str, dest: Path, retries: int = 4) -> int:
    """Download one file to ``dest``; returns bytes written (0 when already present)."""
    if dest.exists() and dest.stat().st_size > 0:
        return 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(_request(endpoint, token, remote), timeout=600) as response:
                data = response.read()
            tmp = dest.with_suffix(dest.suffix + ".part")
            tmp.write_bytes(data)
            os.replace(tmp, dest)
            return len(data)
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"failed to download {remote}: {last}")


def extract_zip(archive: Path, dest: Path) -> int:
    """Extract the corpus zip into ``dest``; returns the number of files written."""
    written = 0
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        for name in names:
            if name.endswith("/"):
                continue
            # Flatten: the zip may or may not carry a leading directory.
            target = dest / TRANSFORMED_DIR / Path(name).name
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(name) as src, open(target, "wb") as out:
                out.write(src.read())
            written += 1
    return written


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default=os.environ.get("HF_ENDPOINT", "https://hf-mirror.com"))
    parser.add_argument("--dest", type=Path, default=DEST)
    parser.add_argument("--mode", choices=["zip", "files"], default="zip")
    parser.add_argument("--workers", type=int, default=12, help="used by --mode files")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    token = load_token()
    if not token:
        raise SystemExit("HF_TOKEN not found in environment or .env")

    print(f"repository : {REPO}")
    print(f"endpoint   : {args.endpoint}")
    print(f"token      : {token[:7]}...{token[-4:]}")
    print(f"dest       : {args.dest}")
    print(f"mode       : {args.mode}\n")

    if args.mode == "zip":
        plan = [("file", CORPUS_ZIP, None)]
    else:
        listing = list_dir(token, args.endpoint, TRANSFORMED_DIR)
        texts = [i for i in listing if i.get("type") == "file" and i["path"].endswith(".txt")]
        plan = [("file", i["path"], i.get("size", 0)) for i in texts]

    root_total = sum(i.get("size", 0) or 0
                     for i in list_dir(token, args.endpoint, "")
                     if i.get("type") == "file" and i["path"] in WANTED_ROOT_FILES)
    corpus_total = sum(size or 0 for _, _, size in plan if size)
    print(f"root csv   : {len(WANTED_ROOT_FILES)} files, {root_total/1e6:.1f} MB")
    print(f"corpus     : {len(plan)} request(s), "
          f"{'zip ~97 MB' if args.mode == 'zip' else f'{corpus_total/1e6:.1f} MB'}")

    if args.dry_run:
        print("\n-- dry run, nothing downloaded --")
        return 0

    print("\nroot files ...")
    for name in WANTED_ROOT_FILES:
        size = fetch(args.endpoint, token, name, args.dest / name)
        print(f"  {'cached' if size == 0 else f'{size:,} bytes'}  {name}")

    print("\ncorpus ...")
    started = time.time()
    if args.mode == "zip":
        archive = args.dest / TRANSFORMED_DIR / "treasury_bulletins_transformed.zip"
        size = fetch(args.endpoint, token, CORPUS_ZIP, archive)
        print(f"  {'cached' if size == 0 else f'{size/1e6:.1f} MB'}  {CORPUS_ZIP}")
        count = extract_zip(archive, args.dest)
        print(f"  extracted {count} text files in {time.time()-started:.0f}s")
    else:
        done = skipped = failed = 0
        written = 0

        def worker(item: tuple) -> tuple:
            _, remote, _ = item
            target = args.dest / remote
            try:
                return remote, fetch(args.endpoint, token, remote, target), None
            except Exception as exc:  # noqa: BLE001
                return remote, -1, str(exc)[:100]

        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            for index, (remote, size, error) in enumerate(pool.map(worker, plan), 1):
                if error:
                    failed += 1
                    print(f"  FAIL {remote}: {error}")
                elif size == 0:
                    skipped += 1
                else:
                    done += 1
                    written += size
                if index % 100 == 0 or index == len(plan):
                    print(f"  {index}/{len(plan)}  new={done} cached={skipped} "
                          f"failed={failed}  {written/1e6:.1f} MB  "
                          f"({time.time()-started:.0f}s)", flush=True)
        print(f"\nfinished: {done} downloaded, {skipped} cached, {failed} failed")

    texts = list((args.dest / TRANSFORMED_DIR).glob("*.txt"))
    print(f"\ncorpus on disk: {len(texts)} .txt files, "
          f"{sum(p.stat().st_size for p in texts)/1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
