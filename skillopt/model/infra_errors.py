"""Typed, credential-safe infrastructure failures shared by experiment layers.

A failed model request is not a benchmark answer. Callers must propagate these
errors and leave hard/soft scores unset instead of treating them as model errors.
"""
from __future__ import annotations

import json
import math
import os
import queue
import re
import signal
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FAILURE_TYPES = frozenset({
    "auth_error", "network_error", "model_unavailable", "provider_error", "llm_timeout",
    "artifact_missing", "worker_timeout",
})


def codex_transport_args() -> list[str]:
    """Explicit transport override with the official authenticated endpoint.

    The built-in OpenAI provider may ignore its supports_websockets override.
    A named provider retains the same ChatGPT authentication and Responses API
    while selecting HTTP transport. No arbitrary endpoint or credential is read.
    """
    transport = os.environ.get("SKILLOPT_CODEX_TRANSPORT", "default").strip().lower()
    if transport == "default":
        return []
    if transport != "http":
        raise ValueError("SKILLOPT_CODEX_TRANSPORT must be 'default' or 'http'")
    overrides = {
        "model_provider": '"cam_openai_http"',
        "model_providers.cam_openai_http.name": '"OpenAI HTTP"',
        "model_providers.cam_openai_http.base_url": '"https://chatgpt.com/backend-api/codex"',
        "model_providers.cam_openai_http.requires_openai_auth": "true",
        "model_providers.cam_openai_http.wire_api": '"responses"',
        "model_providers.cam_openai_http.supports_websockets": "false",
        "model_providers.cam_openai_http.request_max_retries": "0",
        "model_providers.cam_openai_http.stream_max_retries": "0",
    }
    args = []
    for key, value in overrides.items():
        args.extend(["-c", f"{key}={value}"])
    return args


def sanitize_details(value: Any) -> Any:
    """Retain diagnostic evidence while removing credential values recursively."""
    if isinstance(value, dict):
        return {
            str(key): (
                "[REDACTED]"
                if re.search(r"^(?:access_token|refresh_token|id_token|api_key|apikey|authorization|password|secret)$", str(key), re.I)
                else sanitize_details(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_details(item) for item in value]
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if not isinstance(value, str):
        return value
    value = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1[REDACTED]", value)
    value = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[REDACTED]", value)
    value = re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b", "[REDACTED]", value)
    value = re.sub(
        r'''(?ix)((?:["']?)(?:access_token|refresh_token|id_token|api_key|apikey|authorization|password|secret)(?:["']?)\s*[:=]\s*["']?)([^\s,"'&}\]]+)''',
        r"\1[REDACTED]", value,
    )
    return value


class InfraError(RuntimeError):
    """A model infrastructure failure that must terminate an experiment stage."""

    def __init__(
        self, failure_type: str, message: str, *, stage: str = "", model: str = "",
        details: dict[str, Any] | None = None, evidence_dir: str = "",
    ) -> None:
        if failure_type not in FAILURE_TYPES:
            raise ValueError(f"Unknown infrastructure failure type: {failure_type}")
        self.failure_type = failure_type
        self.stage = stage
        self.model = model
        self.details = sanitize_details(details or {})
        self.evidence_dir = evidence_dir
        self.message = sanitize_details(str(message))
        super().__init__(f"{failure_type}: {self.message}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "infra_error", "failure_type": self.failure_type,
            "message": self.message, "stage": self.stage, "model": self.model,
            "details": self.details, "evidence_dir": self.evidence_dir,
        }


def detect_infra_error(
    text: str, *, stage: str = "", model: str = "", returncode: int | None = None,
) -> InfraError | None:
    """Classify *error-channel* text, never ordinary generated answer text."""
    lowered = str(text or "").lower()
    failure_type = ""
    if re.search(
        r"\b401\b|invalid_refresh_token|refresh_token_reused|invalid_grant|"
        r"unauthorized|not authenticated|authenticationerror|"
        r"(?:authentication|authorization).{0,35}(?:failed|failure|error|required|invalid)|"
        r"(?:refresh|access) token.{0,60}(?:expired|invalid|revoked|reused)|"
        r"(?:log ?in|sign ?in) (?:again|required)|incorrect api key|invalid api key",
        lowered,
    ):
        failure_type = "auth_error"
    elif re.search(
        r"model_not_found|unsupported_model|model.{0,160}(?:not found|does not exist|"
        r"not available|unavailable|not supported|unsupported|no access)|"
        r"(?:not have access|not supported).{0,100}model",
        lowered,
    ):
        failure_type = "model_unavailable"
    elif re.search(r"timed? ?out|timeout|deadline exceeded", lowered):
        failure_type = "llm_timeout"
    elif re.search(
        r"connection (?:error|reset|refused|aborted|closed)|connecterror|connectionerror|"
        r"dns|name resolution|network (?:error|unreachable)|error sending request|"
        r"stream disconnected|failed to (?:connect|resolve)|tls|ssl|proxy error|"
        r"econnreset|econnrefused|socket hang up|transport error",
        lowered,
    ):
        failure_type = "network_error"
    elif returncode not in (None, 0) or re.search(
        r"\b(?:403|408|429|500|502|503|504)\b|rate.?limit|quota|insufficient_quota|"
        r"server error|service unavailable|turn\.failed|request failed|\"type\"\s*:\s*\"error\"",
        lowered,
    ):
        failure_type = "provider_error"
    if not failure_type:
        return None
    return InfraError(
        failure_type, str(text or "Model process failed")[:8000], stage=stage,
        model=model, details={"returncode": returncode},
    )


def classify_infra_error(
    exc: BaseException, *, stage: str = "", model: str = "", role: str = "",
) -> InfraError | None:
    if isinstance(exc, InfraError):
        if stage and not exc.stage:
            exc.stage = stage
        if model and not exc.model:
            exc.model = model
        return exc
    detected = detect_infra_error(f"{type(exc).__name__}: {exc}", stage=stage or role, model=model)
    if detected:
        return detected
    if isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)):
        return InfraError("llm_timeout", str(exc), stage=stage or role, model=model)
    if isinstance(exc, ConnectionError):
        return InfraError("network_error", str(exc), stage=stage or role, model=model)
    return None


def persist_infra_error(
    error: InfraError, *, raw: str = "", evidence_dir: str | Path | None = None,
) -> InfraError:
    if evidence_dir is None:
        base = Path(os.environ.get("SKILLOPT_INFRA_ARTIFACT_DIR", "outputs/infra_errors"))
        evidence_dir = base / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}_{uuid.uuid4().hex[:10]}"
    destination = Path(evidence_dir)
    destination.mkdir(parents=True, exist_ok=True)
    error.evidence_dir = str(destination.resolve())
    (destination / "infra_raw_trace.txt").write_text(sanitize_details(raw or error.message), encoding="utf-8")
    (destination / "infra_error.json").write_text(
        json.dumps(error.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return error


def _error_event_text(line: str, channel: str) -> str:
    """Inspect definitive request failures, not recoverable startup telemetry.

    A WebSocket prewarm error can be followed by a successful HTTP request. Such
    timestamped diagnostic logs remain in the raw trace but cannot invalidate a
    turn. Authentication errors are definitive and always stop immediately.
    """
    if channel == "stderr":
        detected = detect_infra_error(line)
        if detected is not None and detected.failure_type == "auth_error":
            return line
        if re.match(r"\s*(?:ERROR:|FATAL:|Reconnecting\b)", line, re.I):
            return line
        # Other diagnostics are deferred until an error event/nonzero exit.
    try:
        payload = json.loads(line)
    except (ValueError, TypeError):
        return ""
    if isinstance(payload, dict) and payload.get("type") in {"error", "turn.failed"}:
        return json.dumps(payload, ensure_ascii=False)
    return ""


def _terminate_owned_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        # codex.cmd has a child Node process. Kill only this request's PID tree.
        try:
            stopped = subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True, timeout=5, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if stopped.returncode != 0 and proc.poll() is None:
                proc.kill()
        except (OSError, subprocess.SubprocessError):
            proc.kill()
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def run_cli_failfast(
    command: list[str], *, prompt: str, timeout: int | float | None,
    stage: str, model: str, cwd: str | None = None,
    evidence_dir: str | Path | None = None,
) -> subprocess.CompletedProcess:
    """Read both pipes continuously and stop this CLI tree at its first infra error.

    Codex's internal reconnect loop can otherwise spend minutes retrying a 401.
    JSON stdout ensures ordinary task/assistant text cannot trigger classification.
    """
    # Optimizer call sites historically omit a timeout. A silent child must
    # still have a finite deadline instead of blocking the experiment forever.
    timeout = float(timeout if timeout is not None else os.environ.get("SKILLOPT_CODEX_TIMEOUT_SECONDS", "420"))
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Codex model timeout must be finite and positive")
    chunks: dict[str, list[str]] = {"stdout": [], "stderr": []}
    events: queue.Queue = queue.Queue()
    started = time.monotonic()
    proc = None
    failure = None
    threads = []
    try:
        proc = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", cwd=cwd,
            bufsize=1, start_new_session=os.name != "nt",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )

        def reader(channel: str, pipe) -> None:
            try:
                for line in pipe:
                    events.put((channel, line))
            finally:
                events.put((channel, None))

        for channel in ("stdout", "stderr"):
            thread = threading.Thread(target=reader, args=(channel, getattr(proc, channel)), daemon=True)
            thread.start()
            threads.append(thread)
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
        except BrokenPipeError:
            pass
        closed = set()
        while len(closed) < 2 or proc.poll() is None:
            if timeout is not None and time.monotonic() - started >= timeout:
                failure = InfraError("llm_timeout", f"Codex CLI exceeded {timeout}s", stage=stage, model=model,
                                     details={"timeout_seconds": timeout})
                break
            try:
                channel, line = events.get(timeout=0.05)
            except queue.Empty:
                continue
            if line is None:
                closed.add(channel)
                continue
            chunks[channel].append(line)
            error_text = _error_event_text(line, channel)
            if error_text:
                failure = detect_infra_error(error_text, stage=stage, model=model)
                if failure is not None:
                    break
        if failure:
            _terminate_owned_process(proc)
        for thread in threads:
            thread.join(timeout=1)
        while not events.empty():
            channel, line = events.get_nowait()
            if line is not None:
                chunks[channel].append(line)
        stdout, stderr = "".join(chunks["stdout"]), "".join(chunks["stderr"])
        returncode = proc.wait(timeout=5)
        if failure is None and returncode:
            failure = detect_infra_error(stderr or stdout, stage=stage, model=model, returncode=returncode)
        if failure:
            failure.details.update({"returncode": returncode, "elapsed_s": time.monotonic() - started})
            persist_infra_error(failure, raw=f"[stdout]\n{stdout}\n[stderr]\n{stderr}", evidence_dir=evidence_dir)
            raise failure
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)
    except InfraError:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        failure = classify_infra_error(exc, stage=stage, model=model) or InfraError(
            "provider_error", f"Unable to run model CLI: {type(exc).__name__}: {exc}", stage=stage, model=model,
        )
        persist_infra_error(failure, raw=str(exc), evidence_dir=evidence_dir)
        raise failure from exc
    finally:
        if proc is not None:
            if proc.poll() is None:
                _terminate_owned_process(proc)
            for pipe in (proc.stdin, proc.stdout, proc.stderr):
                if pipe is not None:
                    pipe.close()
