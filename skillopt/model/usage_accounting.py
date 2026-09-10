"""Request-ID accounting for CLI invocations, not inferred HTTP billing calls.

Missing usage stays unknown. Cached input is a subset of input; reasoning is a
subset of output. Neither is added to input + output when calculating total.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid


TOKEN_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens", "cache_write_input_tokens")
FINAL_STATUSES = {"completed", "ok", "success", "infra_error", "failed", "error", "cancelled", "timeout"}


class UsageAccountingError(ValueError):
    """Conflicting accounting evidence cannot be silently overwritten or added."""


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _event_usage(value):
    value = _mapping(value)
    fields = {
        "input_tokens": _count(value.get("input_tokens", value.get("prompt_tokens"))),
        "output_tokens": _count(value.get("output_tokens", value.get("completion_tokens"))),
        "cached_input_tokens": _count(value.get("cached_input_tokens",
            _mapping(value.get("input_tokens_details", value.get("prompt_tokens_details"))).get("cached_tokens"))),
        "reasoning_output_tokens": _count(value.get("reasoning_output_tokens",
            _mapping(value.get("output_tokens_details", value.get("completion_tokens_details"))).get("reasoning_tokens"))),
        "cache_write_input_tokens": _count(value.get("cache_write_input_tokens", value.get("cache_creation_input_tokens"))),
    }
    issues = []
    for subset, whole in (("cached_input_tokens", "input_tokens"), ("reasoning_output_tokens", "output_tokens"),
                          ("cache_write_input_tokens", "input_tokens")):
        if fields[subset] is not None and fields[whole] is not None and fields[subset] > fields[whole]:
            fields[subset] = None
            issues.append(subset + "_exceeds_parent")
    return fields, issues


def parse_codex_usage(raw: str) -> dict:
    """Aggregate every distinct turn.completed in one canonical CLI trace.

    Stable turn/event IDs permit exact deduplication. Repeated anonymous events
    with identical contents are ambiguous (a copied event or distinct identical
    turns): exact totals become None; known_* retain a deduplicated lower bound.
    A thread ID alone never identifies a turn. Different anonymous terminal
    events are all aggregated, rather than silently taking the final one.
    """
    events, stable, anonymous = [], {}, set()
    observed = duplicates = ambiguous = 0
    issues = []
    thread_id = ""
    channel = "stdout"
    for line in str(raw or "").splitlines():
        if line.strip() in {"[stdout]", "[stderr]"}:
            channel = line.strip()[1:-1]
            continue
        if line.startswith("===== CODEX CLI ATTEMPT"):
            channel = "stdout"
        if channel != "stdout":
            continue
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "thread.started":
            thread_id = str(event.get("thread_id", ""))
        if event.get("type") != "turn.completed":
            continue
        observed += 1
        fingerprint = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        stable_id = event.get("turn_id") or _mapping(event.get("turn")).get("id") or event.get("event_id") or event.get("id")
        if stable_id:
            identity = (str(event.get("thread_id", thread_id)), str(stable_id))
            if identity in stable:
                previous_fingerprint, previous_index = stable[identity]
                if previous_fingerprint == fingerprint:
                    duplicates += 1
                else:
                    ambiguous += 1
                    issues.append("conflicting_stable_terminal_id")
                    # Contradictory evidence is not a reliable known lower bound.
                    events[previous_index] = dict.fromkeys(TOKEN_FIELDS)
                continue
            stable[identity] = (fingerprint, len(events))
        else:
            identity = (str(event.get("thread_id", thread_id)), fingerprint)
            if identity in anonymous:
                ambiguous += 1
                issues.append("ambiguous_duplicate_terminal_events")
                continue
            anonymous.add(identity)
        usage, event_issues = _event_usage(event.get("usage"))
        events.append(usage)
        issues.extend(event_issues)
    result = {}
    for field in TOKEN_FIELDS:
        values = [event[field] for event in events]
        result["known_" + field] = sum(value for value in values if value is not None)
        result[field] = sum(values) if values and None not in values and not ambiguous else None
    input_tokens, output_tokens = result["input_tokens"], result["output_tokens"]
    result.update(
        prompt_tokens=input_tokens, completion_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens if input_tokens is not None and output_tokens is not None else None,
        known_tokens=result["known_input_tokens"] + result["known_output_tokens"],
        usage_complete=input_tokens is not None and output_tokens is not None,
        cache_usage_complete=result["cached_input_tokens"] is not None,
        completed_turn_events=observed, distinct_terminal_events=len(events),
        duplicate_terminal_events=duplicates, ambiguous_terminal_events=ambiguous,
        accounting_warnings=sorted(set(issues)),
        usage_completeness_definition="all_distinct_terminal_input_and_output_fields_known_without_identity_ambiguity",
        deduplication_policy="stable_turn_or_event_id; identical_anonymous_events_ambiguous; distinct_events_aggregated",
    )
    return result


def _normalized_request(record):
    if not isinstance(record, dict) or not isinstance(record.get("request_id"), str) or not record["request_id"]:
        raise UsageAccountingError("Accounting records require a nonempty request_id")
    normalized = deepcopy(record)
    usage = dict(_mapping(record.get("usage")))
    for key in (*TOKEN_FIELDS, "prompt_tokens", "completion_tokens", "total_tokens", "usage_complete", "cache_usage_complete", "known_tokens",
                *("known_" + field for field in TOKEN_FIELDS)):
        if key in record:
            if key in usage and record[key] != usage[key]:
                raise UsageAccountingError("Flat and nested request usage disagree")
            usage[key] = record[key]
    if "input_tokens" not in usage:
        usage["input_tokens"] = usage.get("prompt_tokens")
    if "output_tokens" not in usage:
        usage["output_tokens"] = usage.get("completion_tokens")
    for field in TOKEN_FIELDS:
        usage[field] = _count(usage.get(field))
        known = _count(usage.get("known_" + field))
        usage["known_" + field] = known if known is not None else usage[field] if usage[field] is not None else 0
    input_tokens, output_tokens = usage["input_tokens"], usage["output_tokens"]
    usage.update(prompt_tokens=input_tokens, completion_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens if input_tokens is not None and output_tokens is not None else None,
        usage_complete=input_tokens is not None and output_tokens is not None and usage.get("usage_complete", True) is not False,
        cache_usage_complete=usage["cached_input_tokens"] is not None,
        known_tokens=usage["known_input_tokens"] + usage["known_output_tokens"])
    normalized.update(usage)
    normalized["usage"] = usage
    normalized.setdefault("stage", "unknown")
    normalized.setdefault("role", normalized["stage"])
    normalized.setdefault("status", "completed")
    return normalized


def _same_request(left, right):
    keys = ("stage", "role", "backend", "model", "status", "failure_type", "recovered_warning_count",
            *TOKEN_FIELDS, "usage_complete", "known_tokens")
    if any(left.get(key) != right.get(key) for key in keys):
        return False
    return not (left.get("raw_sha256") and right.get("raw_sha256") and left["raw_sha256"] != right["raw_sha256"])


def merge_request_records(records) -> list[dict]:
    """Merge exact request IDs, permitting only running -> terminal progression."""
    merged = {}
    for raw_record in records:
        record = _normalized_request(raw_record)
        request_id = record["request_id"]
        previous = merged.get(request_id)
        if previous is None or _same_request(previous, record):
            merged[request_id] = record
            continue
        identity_matches = all(previous.get(key) == record.get(key) for key in ("stage", "role", "backend", "model"))
        if identity_matches and previous.get("status") == "running" and record.get("status") in FINAL_STATUSES:
            merged[request_id] = record
        elif identity_matches and record.get("status") == "running" and previous.get("status") in FINAL_STATUSES:
            continue
        else:
            raise UsageAccountingError("Conflicting records for the same request_id")
    return [merged[key] for key in sorted(merged)]


def summarize_usage(records, *, legacy_summaries=()) -> dict:
    """Return stage/_total summaries; unknown exact totals remain None.

    known_tokens is a lower bound from available input/output fields, not an
    estimate. Legacy counters lack request IDs and are explicitly separate.
    """
    requests = merge_request_records(records)
    grouped = {}

    def empty():
        return {"calls": 0, "cli_invocations": 0, "legacy_calls": 0, "unknown_calls": 0,
                "input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0,
                "reasoning_output_tokens": 0, "cache_write_input_tokens": 0,
                "known_tokens": 0, **{"known_" + field: 0 for field in TOKEN_FIELDS},
                "recovered_warning_count": 0, "running_calls": 0, "failed_calls": 0,
                "successful_calls": 0, "infra_error_calls": 0, "failure_types": {}}

    def add(stage, values, calls, legacy=False):
        entry = grouped.setdefault(stage, empty())
        entry["calls"] += calls
        entry["legacy_calls" if legacy else "cli_invocations"] += calls
        usage_complete = values.get("usage_complete", values.get("input_tokens") is not None and values.get("output_tokens") is not None)
        entry["unknown_calls"] += _count(values.get("unknown_calls")) if _count(values.get("unknown_calls")) is not None else 0 if usage_complete else calls
        for field in TOKEN_FIELDS:
            value = _count(values.get(field))
            if entry[field] is not None:
                entry[field] = entry[field] + value if value is not None else None
            known = _count(values.get("known_" + field))
            entry["known_" + field] += known if known is not None else value if value is not None else 0
        entry["recovered_warning_count"] += _count(values.get("recovered_warning_count")) or 0
        entry["running_calls"] += values.get("status") == "running"
        entry["failed_calls"] += values.get("status") in {"infra_error", "failed", "error", "cancelled", "timeout"}
        entry["successful_calls"] += values.get("status") in {"completed", "ok", "success"}
        entry["infra_error_calls"] += values.get("status") == "infra_error"
        if values.get("failure_type"):
            failure_type = str(values["failure_type"])
            entry["failure_types"][failure_type] = entry["failure_types"].get(failure_type, 0) + calls

    for record in requests:
        add(record["stage"], record, 1)
    for summary in legacy_summaries:
        for stage, raw_values in summary.items():
            if stage == "_total" or not isinstance(raw_values, dict):
                continue
            calls = _count(raw_values.get("calls"))
            if not calls:
                continue
            values = dict(raw_values)
            values.setdefault("input_tokens", values.get("prompt_tokens"))
            values.setdefault("output_tokens", values.get("completion_tokens"))
            add(stage, values, calls, legacy=True)
    total = empty()
    for entry in grouped.values():
        for key in total:
            if key == "failure_types":
                for failure_type, count in entry[key].items():
                    total[key][failure_type] = total[key].get(failure_type, 0) + count
                continue
            if total[key] is not None:
                total[key] = total[key] + entry[key] if entry[key] is not None else None
    output = {stage: grouped[stage] for stage in sorted(grouped)}
    output["_total"] = total
    for entry in output.values():
        entry.update(prompt_tokens=entry["input_tokens"], completion_tokens=entry["output_tokens"],
            total_tokens=entry["input_tokens"] + entry["output_tokens"] if entry["input_tokens"] is not None and entry["output_tokens"] is not None else None,
            known_tokens=entry["known_input_tokens"] + entry["known_output_tokens"],
            usage_complete=entry["unknown_calls"] == 0,
            cache_usage_complete=entry["cached_input_tokens"] is not None,
            call_unit="CLI_invocations_plus_separately_labeled_legacy_calls_not_HTTP_billing_requests",
            request_coverage="partial_legacy" if entry["legacy_calls"] else "complete",
            coverage="partial_legacy" if entry["legacy_calls"] else "complete" if entry["unknown_calls"] == 0 else "partial_or_unavailable")
    return output


@contextmanager
def _request_lock(directory: Path):
    """OS-released per-request lock; independent workers never share a ledger lock."""
    with (directory / ".lock").open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        deadline = time.monotonic() + 5
        while True:
            stream.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise UsageAccountingError("Timed out acquiring request accounting lock")
                time.sleep(0.01)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _atomic_text(path: Path, text: str):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


class UsageLedger:
    """One durable accounting directory per invocation, safe across workers."""
    def __init__(self, root):
        self.root = Path(root).resolve()

    def request_dir(self, request_id):
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id):
            raise UsageAccountingError("Invalid accounting request_id")
        path = self.root / "requests" / request_id
        if self.root not in path.resolve().parents:
            raise UsageAccountingError("Accounting request path escapes its ledger")
        return path

    def raw_trace_path(self, request_id):
        return self.request_dir(request_id) / "raw_trace.txt"

    def start(self, stage, backend, model, request_id=None):
        request_id = request_id or uuid.uuid4().hex
        for name, value in (("stage", stage), ("backend", backend), ("model", model)):
            if not isinstance(value, str) or not value.strip():
                raise UsageAccountingError(f"Accounting {name} must be a nonempty string")
        # Resolve children only after their shared parent exists. Windows
        # realpath can observe a changing missing tail during concurrent mkdir.
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "requests").mkdir(exist_ok=True)
        directory = self.request_dir(request_id)
        directory.mkdir(parents=True, exist_ok=True)
        with _request_lock(directory):
            path = directory / "request.json"
            if path.exists():
                existing = json.loads(path.read_text(encoding="utf-8"))
                if any(existing.get(key) != value for key, value in (("stage", stage), ("backend", backend), ("model", model))):
                    raise UsageAccountingError("Repeated request start has conflicting identity")
                return request_id
            usage = parse_codex_usage("")
            record = {"schema_version": 1, "request_id": request_id, "stage": stage, "role": stage, "backend": backend,
                "model": model, "status": "running", "failure_type": None, "recovered_warning_count": 0,
                "started_at": datetime.now(timezone.utc).isoformat(), "finished_at": None,
                "raw_trace": str(self.raw_trace_path(request_id)), "raw_trace_path": str(self.raw_trace_path(request_id)),
                "raw_sha256": None, "usage": usage, **usage}
            _atomic_text(directory / "raw_trace.txt", "")
            _atomic_text(path, json.dumps(record, ensure_ascii=False, indent=2))
        return request_id

    def finish(self, request_id, raw, status, failure_type=None, recovered_warning_count=0):
        if status not in FINAL_STATUSES or _count(recovered_warning_count) is None:
            raise UsageAccountingError("Invalid final status or recovered warning count")
        if not isinstance(raw, str) or (failure_type is not None and not isinstance(failure_type, str)):
            raise UsageAccountingError("Raw usage evidence and failure type must be strings")
        directory = self.request_dir(request_id)
        if not (directory / "request.json").is_file():
            raise UsageAccountingError("Cannot finish an invocation that was not started")
        # Imported lazily to avoid coupling the pure parser/tracker to transport.
        from skillopt.model.infra_errors import sanitize_details
        sanitized_raw = sanitize_details(raw)
        raw_hash = hashlib.sha256(sanitized_raw.encode("utf-8")).hexdigest()
        finish_input_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        usage = parse_codex_usage(sanitized_raw)
        with _request_lock(directory):
            path = directory / "request.json"
            previous = json.loads(path.read_text(encoding="utf-8"))
            identity = {"raw_sha256": raw_hash, "status": status, "failure_type": failure_type,
                        "recovered_warning_count": recovered_warning_count, "finish_input_sha256": finish_input_hash}
            if previous.get("status") != "running":
                if all(previous.get(key) == value for key, value in identity.items()):
                    return previous
                raise UsageAccountingError("Conflicting finish would overwrite completed accounting evidence")
            record = {**previous, **identity, "finished_at": datetime.now(timezone.utc).isoformat(), "usage": usage, **usage}
            _atomic_text(directory / "raw_trace.txt", sanitized_raw)
            _atomic_text(path, json.dumps(record, ensure_ascii=False, indent=2))
            return record

    def records(self):
        records = []
        for path in sorted((self.root / "requests").glob("*/request.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise UsageAccountingError("Unreadable accounting request record") from exc
            if record.get("request_id") != path.parent.name:
                raise UsageAccountingError("Accounting record ID does not match its directory")
            expected_raw = self.raw_trace_path(record["request_id"])
            if any(record.get(key) != str(expected_raw) for key in ("raw_trace", "raw_trace_path")):
                raise UsageAccountingError("Accounting raw trace path does not match its request")
            if record.get("role") != record.get("stage"):
                raise UsageAccountingError("Accounting role does not match its recorded stage")
            if record.get("status") != "running":
                if record.get("status") not in FINAL_STATUSES:
                    raise UsageAccountingError("Unknown accounting request status")
                try:
                    raw_bytes = expected_raw.read_bytes()
                    expected_usage = parse_codex_usage(raw_bytes.decode("utf-8"))
                except (OSError, UnicodeError) as exc:
                    raise UsageAccountingError("Unreadable accounting raw trace") from exc
                if hashlib.sha256(raw_bytes).hexdigest() != record.get("raw_sha256"):
                    raise UsageAccountingError("Accounting raw trace hash mismatch")
                if record.get("usage") != expected_usage or any(record.get(key) != value for key, value in expected_usage.items()):
                    raise UsageAccountingError("Accounting usage does not match raw trace evidence")
            _normalized_request(record)
            records.append(record)
        return records

    def summary(self):
        return summarize_usage(self.records())


def configure_usage_accounting(root):
    """Set the process-tree ledger root; None disables it without deleting files."""
    if root is None:
        os.environ.pop("SKILLOPT_USAGE_ROOT", None)
        return None
    ledger = UsageLedger(root)
    os.environ["SKILLOPT_USAGE_ROOT"] = str(ledger.root)
    return ledger
