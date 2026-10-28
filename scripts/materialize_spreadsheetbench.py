"""Materialize SpreadsheetBench Verified 400 for SkillOpt/CAM-SkillOpt.

When the official archive ``spreadsheetbench_verified_400.tar.gz`` is missing
from ``data/`` this script downloads it automatically, trying the primary URL
first and then a list of Chinese mirrors (``ghproxy.com``, ``gh-proxy.com``) in
order. Pass ``--no-download`` to keep the old offline behaviour. After the
archive is on disk the script extracts it and writes the runnable split
directory consumed by ``configs/spreadsheetbench/default.yaml``.
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import shutil
import sys
import tarfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARCHIVE = REPO_ROOT / "data" / "spreadsheetbench_verified_400.tar.gz"
DEFAULT_MANIFEST = REPO_ROOT / "data" / "spreadsheetbench_id_split"
DEFAULT_PAYLOAD_ROOT = REPO_ROOT / "data" / "spreadsheetbench_verified_400"
DEFAULT_SPLIT_DIR = REPO_ROOT / "data" / "spreadsheetbench_split"
DEFAULT_REPORT_DIR = REPO_ROOT / "outputs" / "stage6_data_materialization"
DEFAULT_DOWNLOAD_URL = (
    # raw.githubusercontent.com serves the actual file; the GitHub blob URL
    # returns an HTML page even with ``Accept: application/octet-stream``.
    "https://raw.githubusercontent.com/RUCKBReasoning/SpreadsheetBench/"
    "main/data/spreadsheetbench_verified_400.tar.gz"
)
# Domestic mirrors used when the primary URL is blocked or slow. Each entry is
# a full URL (the mirror's domain prepended to the raw.githubusercontent.com
# path). ``download_archive_with_fallback`` walks the list in order after the
# primary URL fails.
DEFAULT_MIRROR_URLS = (
    "https://ghproxy.com/https://raw.githubusercontent.com/RUCKBReasoning/SpreadsheetBench/main/data/spreadsheetbench_verified_400.tar.gz",
    "https://gh-proxy.com/https://raw.githubusercontent.com/RUCKBReasoning/SpreadsheetBench/main/data/spreadsheetbench_verified_400.tar.gz",
)
REQUIRED_FIELDS = ("id", "instruction", "spreadsheet_path")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--manifest-dir", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--payload-root", type=Path, default=DEFAULT_PAYLOAD_ROOT)
    parser.add_argument("--output-split-dir", type=Path, default=DEFAULT_SPLIT_DIR)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing output split files after validation.",
    )
    parser.add_argument(
        "--no-download",
        action="store_true",
        help="Disable automatic download when the archive is missing (offline mode).",
    )
    parser.add_argument(
        "--download-url",
        type=str,
        default=DEFAULT_DOWNLOAD_URL,
        help="URL used to fetch the archive when --archive is missing.",
    )
    parser.add_argument(
        "--mirror-url",
        action="append",
        default=None,
        metavar="URL",
        help="Fallback URL tried after --download-url fails. Pass multiple times "
             "for multiple fallbacks (in declared order). When omitted, the "
             "script walks a built-in list of Chinese mirrors (ghproxy.com, "
             "gh-proxy.com).",
    )
    return parser.parse_args()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _extract_kwargs() -> dict[str, Any]:
    """Pass an explicit tar extraction filter when the runtime supports one.

    Python 3.12 deprecated the implicit filter (and emits a DeprecationWarning
    from ``__main__``), while 3.14 changes the default to ``"data"``.
    ``"fully_trusted"`` preserves the historical behaviour; the caller has
    already rejected every member that would escape the destination directory.
    """
    try:
        parameters = inspect.signature(tarfile.TarFile.extract).parameters
    except (TypeError, ValueError):  # pragma: no cover - defensive introspection
        return {}
    return {"filter": "fully_trusted"} if "filter" in parameters else {}


_EXTRACT_KWARGS = _extract_kwargs()


def _format_bytes(value: float) -> str:
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if amount < 1024 or unit == "GiB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} GiB"  # pragma: no cover - unreachable


def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


class _ExtractProgress:
    """Dependency-free extraction progress reporter.

    Progress goes to stderr because ``main`` prints the JSON report on stdout.
    The animated bar is only drawn on a TTY; redirected output gets a single
    start line and a single summary line instead of thousands of redraws. Set
    ``SKILLOPT_NO_PROGRESS=1`` to silence the reporter entirely.
    """

    WIDTH = 24
    MIN_INTERVAL = 0.1  # seconds between redraws; extraction is IO bound

    def __init__(self, total_bytes: int, total_members: int, destination: Path) -> None:
        self.total_bytes = total_bytes
        self.total_members = total_members
        self.done_bytes = 0
        self.done_members = 0
        self.started_at = time.monotonic()
        self._last_draw = 0.0
        disabled = os.environ.get("SKILLOPT_NO_PROGRESS", "").strip().lower()
        self.enabled = disabled not in {"1", "true", "yes", "on"}
        self.animated = self.enabled and sys.stderr.isatty()
        if self.enabled and not self.animated:
            self._write(
                f"  extracting {total_members} members "
                f"({_format_bytes(total_bytes)}) -> {destination}\n"
            )

    def _write(self, text: str) -> None:
        try:
            sys.stderr.write(text)
            sys.stderr.flush()
        except (OSError, ValueError):
            pass  # a closed stream must never abort a long extraction

    def _fraction(self) -> float:
        if self.total_bytes > 0:
            return min(1.0, self.done_bytes / self.total_bytes)
        if self.total_members > 0:
            return min(1.0, self.done_members / self.total_members)
        return 1.0

    def _line(self) -> str:
        fraction = self._fraction()
        filled = int(round(fraction * self.WIDTH))
        bar = "#" * filled + "-" * (self.WIDTH - filled)
        elapsed = time.monotonic() - self.started_at
        eta = (elapsed / fraction - elapsed) if fraction > 0 else 0.0
        return (
            f"\r  extracting [{bar}] {fraction * 100:5.1f}%  "
            f"{_format_bytes(self.done_bytes)}/{_format_bytes(self.total_bytes)}  "
            f"{self.done_members}/{self.total_members} entries  "
            f"elapsed {_format_duration(elapsed)}  eta {_format_duration(eta)}"
        )

    def advance(self, member: tarfile.TarInfo) -> None:
        if member.isfile():
            self.done_bytes += member.size
        self.done_members += 1
        if not self.animated:
            return
        now = time.monotonic()
        if self.done_members < self.total_members and now - self._last_draw < self.MIN_INTERVAL:
            return
        self._last_draw = now
        self._write(self._line())

    def finish(self) -> None:
        if not self.enabled:
            return
        elapsed = time.monotonic() - self.started_at
        if self.animated:
            self._write(self._line())
            self._write("\n")
        self._write(
            f"  extracted {self.done_members} entries "
            f"({_format_bytes(self.done_bytes)}) in {elapsed:.1f}s\n"
        )


def safe_extract_tar(archive: Path, destination: Path) -> None:
    """Extract ``archive`` into ``destination`` while reporting progress.

    Every member path is validated before a single byte is written, so an
    unsafe archive still fails without leaving a partial payload behind.
    """
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:*") as tar:
        members = tar.getmembers()
        for member in members:
            target = destination / member.name
            if not is_within(target, destination):
                raise RuntimeError(f"Unsafe archive member path: {member.name}")

        total_bytes = sum(member.size for member in members if member.isfile())
        progress = _ExtractProgress(total_bytes, len(members), destination)
        for member in members:
            tar.extract(member, destination, **_EXTRACT_KWARGS)
            progress.advance(member)
        progress.finish()


class _DownloadProgress:
    """Dependency-free download progress reporter.

    Mirrors :class:`_ExtractProgress`: a redrawing bar on a TTY, a single
    start/finish line when stderr is redirected, and silence when
    ``SKILLOPT_NO_PROGRESS=1``.
    """

    WIDTH = 24
    MIN_INTERVAL = 0.1  # seconds between redraws; network throughput varies

    def __init__(self, total_bytes: int, url: str, destination: Path) -> None:
        self.total_bytes = total_bytes
        self.done_bytes = 0
        self.started_at = time.monotonic()
        self._last_draw = 0.0
        disabled = os.environ.get("SKILLOPT_NO_PROGRESS", "").strip().lower()
        self.enabled = disabled not in {"1", "true", "yes", "on"}
        self.animated = self.enabled and sys.stderr.isatty()
        if self.enabled and not self.animated:
            size_hint = _format_bytes(total_bytes) if total_bytes else "unknown size"
            self._write(
                f"  downloading {url} ({size_hint}) -> {destination}\n"
            )

    def _write(self, text: str) -> None:
        try:
            sys.stderr.write(text)
            sys.stderr.flush()
        except (OSError, ValueError):
            pass  # a closed stream must never abort a long download

    def _fraction(self) -> float:
        if self.total_bytes > 0:
            return min(1.0, self.done_bytes / self.total_bytes)
        return 0.0

    def _line(self) -> str:
        fraction = self._fraction()
        filled = int(round(fraction * self.WIDTH))
        bar = "#" * filled + "-" * (self.WIDTH - filled)
        elapsed = time.monotonic() - self.started_at
        speed = self.done_bytes / elapsed if elapsed > 0 else 0.0
        size_total = _format_bytes(self.total_bytes) if self.total_bytes else "?"
        return (
            f"\r  downloading [{bar}] {fraction * 100:5.1f}%  "
            f"{_format_bytes(self.done_bytes)}/{size_total}  "
            f"{_format_bytes(speed)}/s  "
            f"elapsed {_format_duration(elapsed)}"
        )

    def advance(self, chunk_size: int) -> None:
        self.done_bytes += chunk_size
        if not self.animated:
            return
        now = time.monotonic()
        if now - self._last_draw < self.MIN_INTERVAL:
            return
        self._last_draw = now
        self._write(self._line())

    def finish(self) -> None:
        if not self.enabled:
            return
        elapsed = time.monotonic() - self.started_at
        speed = self.done_bytes / elapsed if elapsed > 0 else 0.0
        if self.animated:
            self._write(self._line())
            self._write("\n")
        self._write(
            f"  downloaded {_format_bytes(self.done_bytes)} in {elapsed:.1f}s "
            f"({_format_bytes(speed)}/s)\n"
        )


def download_archive(archive: Path, url: str) -> int:
    """Stream ``url`` into ``archive`` and return the number of bytes written.

    The download lands at ``archive.with_name(archive.name + '.part')`` and is
    atomically renamed on success, so an interrupted transfer never leaves a
    half-written file at the final path. ``Content-Length`` is enforced when
    the server advertises it; a mismatch is treated as a hard failure and the
    staging copy is removed.
    """
    archive.parent.mkdir(parents=True, exist_ok=True)
    staging = archive.with_name(archive.name + ".part")
    if staging.exists():
        staging.unlink()

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "SkillOpt/CAM-SkillOpt (data materialization)"},
    )

    try:
        response = urllib.request.urlopen(request, timeout=60)
    except urllib.error.URLError as exc:
        staging.unlink(missing_ok=True)
        raise RuntimeError(f"could not reach {url}: {exc.reason}") from exc
    except urllib.error.HTTPError as exc:
        staging.unlink(missing_ok=True)
        raise RuntimeError(
            f"server returned HTTP {exc.code} for {url}: {exc.reason}"
        ) from exc

    total_bytes = 0
    try:
        try:
            total_bytes = int(response.headers.get("Content-Length", 0) or 0)
        except (TypeError, ValueError):
            total_bytes = 0
        progress = _DownloadProgress(total_bytes, url, archive)
        written = 0
        try:
            with staging.open("wb") as fh:
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
                    written += len(chunk)
                    progress.advance(len(chunk))
        except Exception:
            staging.unlink(missing_ok=True)
            raise
        progress.finish()
    finally:
        response.close()

    if total_bytes and written != total_bytes:
        staging.unlink(missing_ok=True)
        raise RuntimeError(
            f"download truncated: server promised {total_bytes} bytes, "
            f"received {written}"
        )

    staging.replace(archive)
    return written


def download_archive_with_fallback(archive: Path, urls: list[str]) -> tuple[int, str]:
    """Try each URL in order; return ``(bytes_downloaded, url_used)``.

    The primary URL is tried first, then each mirror in declared order. Every
    failed attempt cleans its staging file (handled inside
    :func:`download_archive`) so the next URL starts from a clean slate. The
    collected failures are joined into the final exception so the caller can
    surface every attempt to the user, not just the last one.
    """
    attempts: list[tuple[str, Exception]] = []
    for url in urls:
        try:
            sys.stderr.write(f"[spreadsheetbench] trying {url}\n")
            sys.stderr.flush()
            downloaded = download_archive(archive, url)
            return downloaded, url
        except Exception as exc:
            attempts.append((url, exc))
            sys.stderr.write(
                f"[spreadsheetbench] download from {url} failed: {exc}\n"
            )
            sys.stderr.flush()
    summary = "; ".join(f"{url}: {exc}" for url, exc in attempts)
    raise RuntimeError(f"all {len(urls)} URLs failed ({summary})")


def archive_top_level_names(archive: Path) -> set[str]:
    with tarfile.open(archive, "r:*") as tar:
        return {
            Path(member.name).parts[0]
            for member in tar.getmembers()
            if Path(member.name).parts
        }


def choose_extract_destination(archive: Path, payload_root: Path) -> Path:
    top_level = archive_top_level_names(archive)
    if payload_root.name in top_level:
        return payload_root.parent
    return payload_root


def load_manifest_ids(manifest_dir: Path) -> dict[str, list[dict]]:
    splits: dict[str, list[dict]] = {}
    for split in ("train", "val", "test"):
        path = manifest_dir / split / "items.json"
        if not path.exists():
            splits[split] = []
            continue
        rows = load_json(path)
        splits[split] = rows if isinstance(rows, list) else []
    return splits


def iter_json_records(path: Path) -> list[dict]:
    records: list[dict] = []
    try:
        if path.suffix.lower() == ".jsonl":
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    row = json.loads(line)
                    if isinstance(row, dict):
                        records.append(row)
            return records
        payload = load_json(path)
    except Exception:
        return []

    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("data", "items", "examples", "records"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
        if "id" in payload:
            return [payload]
    return records


def index_payload_records(payload_root: Path) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for path in payload_root.rglob("*"):
        if path.suffix.lower() not in {".json", ".jsonl"} or not path.is_file():
            continue
        for row in iter_json_records(path):
            row_id = str(row.get("id", "")).strip()
            if row_id:
                index.setdefault(row_id, row)
    return index


def has_workbook_pair(task_dir: Path) -> bool:
    if not task_dir.exists():
        return False
    if (task_dir / "initial.xlsx").exists() and (task_dir / "golden.xlsx").exists():
        return True
    for input_path in task_dir.glob("*_input.xlsx"):
        if input_path.with_name(input_path.name.replace("_input.xlsx", "_answer.xlsx")).exists():
            return True
    for init_path in task_dir.glob("*_init.xlsx"):
        if init_path.with_name(init_path.name.replace("_init.xlsx", "_golden.xlsx")).exists():
            return True
    init_files = sorted(task_dir.glob("*_init.xlsx"))
    golden_files = sorted(task_dir.glob("*_golden.xlsx"))
    if len(init_files) == 1 and len(golden_files) == 1:
        return True
    return False


def validate_item(item: dict, payload_root: Path) -> dict:
    missing_fields = [field for field in REQUIRED_FIELDS if not item.get(field)]
    spreadsheet_path = str(item.get("spreadsheet_path", f"spreadsheet/{item.get('id', '')}"))
    task_dir = Path(spreadsheet_path)
    if not task_dir.is_absolute():
        task_dir = payload_root / task_dir
    workbook_ready = has_workbook_pair(task_dir)
    return {
        "id": str(item.get("id", "")),
        "missing_fields": missing_fields,
        "task_dir": str(task_dir),
        "workbook_ready": workbook_ready,
        "ready": not missing_fields and workbook_ready,
    }


def build_report(args: argparse.Namespace) -> dict:
    report: dict[str, Any] = {
        "report_type": "cam_stage6_spreadsheetbench_materialization",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(REPO_ROOT),
        "archive": str(args.archive),
        "archive_exists": args.archive.exists(),
        "manifest_dir": str(args.manifest_dir),
        "manifest_exists": args.manifest_dir.exists(),
        "payload_root": str(args.payload_root),
        "payload_exists": args.payload_root.exists(),
        "output_split_dir": str(args.output_split_dir),
        "download_url": args.download_url,
        "archive_downloaded": False,
        "status": "BLOCKED",
        "blockers": [],
        "splits": {},
    }

    if not args.manifest_dir.exists():
        report["blockers"].append("SpreadsheetBench manifest directory is missing.")
        return report

    if not args.archive.exists():
        if args.no_download:
            report["blockers"].append(
                "Official archive is missing. Expected "
                f"{args.archive}; rerun without --no-download to fetch it, "
                "or place the archive manually."
            )
            return report
        mirror_urls = (
            list(args.mirror_url)
            if args.mirror_url is not None
            else list(DEFAULT_MIRROR_URLS)
        )
        urls_to_try = [args.download_url, *mirror_urls]
        report["mirror_urls"] = mirror_urls
        try:
            sys.stderr.write(
                f"\n[spreadsheetbench] archive not found at {args.archive}\n"
            )
            sys.stderr.flush()
            downloaded, url_used = download_archive_with_fallback(
                args.archive, urls_to_try
            )
            report["archive_downloaded"] = True
            report["archive_bytes"] = downloaded
            report["archive_exists"] = args.archive.exists()
            report["download_url_used"] = url_used
        except Exception as exc:
            report["blockers"].append(
                f"Auto-download of {args.archive.name} failed: {exc}. "
                "Place the archive manually and rerun, or pass --no-download "
                "to suppress this attempt (offline mode)."
            )
            return report

    if not args.payload_root.exists():
        if args.archive.exists():
            extract_destination = choose_extract_destination(
                args.archive,
                args.payload_root,
            )
            safe_extract_tar(args.archive, extract_destination)
            report["payload_extracted"] = True
            report["extract_destination"] = str(extract_destination)
            report["payload_exists"] = args.payload_root.exists()
        else:
            report["blockers"].append(
                "Official archive is missing. Expected data/spreadsheetbench_verified_400.tar.gz."
            )
            return report

    manifests = load_manifest_ids(args.manifest_dir)
    payload_index = index_payload_records(args.payload_root)
    report["payload_record_count"] = len(payload_index)

    any_blocked = False
    materialized: dict[str, list[dict]] = {}
    for split, manifest_rows in manifests.items():
        rows: list[dict] = []
        missing_records: list[str] = []
        invalid: list[dict] = []
        for manifest_row in manifest_rows:
            row_id = str(manifest_row.get("id", "")).strip()
            full_row = dict(payload_index.get(row_id, {}))
            if not full_row:
                missing_records.append(row_id)
                full_row = dict(manifest_row)
            else:
                full_row.update({k: v for k, v in manifest_row.items() if k not in full_row})
            check = validate_item(full_row, args.payload_root)
            if not check["ready"]:
                invalid.append(check)
            rows.append(full_row)
        materialized[split] = rows
        split_ready = not missing_records and not invalid
        any_blocked = any_blocked or not split_ready
        report["splits"][split] = {
            "manifest_count": len(manifest_rows),
            "materialized_count": len(rows),
            "missing_records": missing_records[:20],
            "n_missing_records": len(missing_records),
            "invalid_examples": invalid[:20],
            "n_invalid": len(invalid),
            "ready": split_ready,
        }

    if any_blocked:
        report["blockers"].append(
            "Payload is present but does not yet provide all full JSON rows and workbook pairs required by SkillOpt."
        )
        return report

    if args.output_split_dir.exists() and not args.force:
        report["blockers"].append(
            "Output split directory already exists. Re-run with --force to overwrite."
        )
        return report

    if args.output_split_dir.exists():
        shutil.rmtree(args.output_split_dir)
    for split, rows in materialized.items():
        write_json(args.output_split_dir / split / "items.json", rows)
    report["status"] = "READY"
    return report


def write_markdown(report: dict, path: Path) -> None:
    archive_bytes = report.get("archive_bytes")
    archive_downloaded = report.get("archive_downloaded", False)
    archive_size = (
        _format_bytes(archive_bytes)
        if archive_downloaded and archive_bytes
        else "n/a"
    )
    download_url_used = report.get("download_url_used", "n/a")
    mirror_urls = report.get("mirror_urls", [])
    lines = [
        "# CAM-SkillOpt Stage 6 Data Materialization",
        "",
        f"- Status: `{report['status']}`",
        f"- Archive exists: `{report['archive_exists']}`",
        f"- Archive downloaded: `{archive_downloaded}` (size: {archive_size})",
        f"- Primary download URL: `{report.get('download_url', 'n/a')}`",
        f"- Download URL used: `{download_url_used}`",
    ]
    if mirror_urls:
        lines.append("- Mirror fallbacks:")
        lines.extend(f"  - `{url}`" for url in mirror_urls)
    else:
        lines.append("- Mirror fallbacks: *(none configured)*")
    lines.extend(
        [
            f"- Manifest exists: `{report['manifest_exists']}`",
            f"- Payload exists: `{report['payload_exists']}`",
            f"- Output split dir: `{report['output_split_dir']}`",
            "",
            "## Split Checks",
            "",
            "| Split | Manifest | Materialized | Missing Records | Invalid Items | Ready |",
            "|---|---:|---:|---:|---:|---|",
        ]
    )
    for split, info in report.get("splits", {}).items():
        lines.append(
            f"| {split} | {info['manifest_count']} | {info['materialized_count']} | "
            f"{info['n_missing_records']} | {info['n_invalid']} | `{info['ready']}` |"
        )
    lines.extend(["", "## Blockers", ""])
    if report.get("blockers"):
        lines.extend(f"- {blocker}" for blocker in report["blockers"])
    else:
        lines.append("- None.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    args.report_dir.mkdir(parents=True, exist_ok=True)
    report = build_report(args)
    json_path = args.report_dir / "report.json"
    md_path = args.report_dir / "report.md"
    write_json(json_path, report)
    write_markdown(report, md_path)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nWrote {json_path}")
    print(f"Wrote {md_path}")


if __name__ == "__main__":
    main()
