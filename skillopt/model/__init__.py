"""ReflACT model API with runtime backend selection for the target path."""

from __future__ import annotations

import os
from typing import Any

from skillopt.model.usage_accounting import UsageLedger, configure_usage_accounting, summarize_usage

from skillopt.model import azure_openai as _openai
from skillopt.model import claude_backend as _claude
from skillopt.model import codex_backend as _codex
from skillopt.model import minimax_backend as _minimax
from skillopt.model import qwen_backend as _qwen
from skillopt.model.backend_config import (  # noqa: F401
    configure_claude_code_exec,
    configure_codex_exec,
    get_claude_code_exec_config,
    get_codex_exec_config,
    get_target_backend,
    get_optimizer_backend,
    is_target_chat_backend,
    is_target_exec_backend,
    is_optimizer_chat_backend,
    set_target_backend,
    set_optimizer_backend,
)


def set_backend(name: str | None) -> str:
    """Backward-compatible global backend setter.

    Historically the codebase used one shared backend for both optimizer and
    target. Keep that entry point so older scripts continue to work, while
    mapping it onto the split optimizer/target backend model.
    """
    normalized = str(name or "azure_openai").strip().lower()
    if normalized in {"azure_openai", "openai_chat", "azure", "azure-openai"}:
        set_optimizer_backend("openai_chat")
        set_target_backend("openai_chat")
        return "azure_openai"
    if normalized in {"claude", "claude_chat", "anthropic"}:
        set_optimizer_backend("claude_chat")
        set_target_backend("claude_chat")
        return "claude_chat"
    if normalized == "codex":
        set_optimizer_backend("openai_chat")
        set_target_backend("codex_exec")
        return "codex"
    if normalized == "codex_cli":
        set_optimizer_backend("codex_cli")
        set_target_backend("openai_chat")
        return "codex_cli"
    if normalized in {"codex_exec", "claude_code_exec"}:
        set_optimizer_backend("openai_chat")
        set_target_backend(normalized)
        return normalized
    if normalized in {"qwen", "qwen_chat"}:
        set_optimizer_backend("openai_chat")
        set_target_backend("qwen_chat")
        return "qwen_chat"
    if normalized in {"minimax", "minimax_chat"}:
        set_optimizer_backend("openai_chat")
        set_target_backend("minimax_chat")
        return "minimax_chat"
    raise ValueError(f"Unsupported legacy backend: {name!r}")


def get_backend_name() -> str:
    """Best-effort backward-compatible backend summary."""
    optimizer = get_optimizer_backend()
    target = get_target_backend()
    if optimizer == "claude_chat" and target == "claude_chat":
        return "claude_chat"
    if optimizer == "qwen_chat" and target == "qwen_chat":
        return "qwen_chat"
    if optimizer == "openai_chat" and target == "openai_chat":
        return "azure_openai"
    if optimizer == "openai_chat" and target == "codex_exec":
        return "codex"
    if optimizer == "codex_cli":
        return f"codex_cli+{target}"
    if optimizer == "openai_chat" and target == "qwen_chat":
        return "qwen_chat"
    if optimizer == "openai_chat" and target == "minimax_chat":
        return "minimax_chat"
    return f"{optimizer}+{target}"


def chat_optimizer(
    system: str,
    user: str,
    max_completion_tokens: int = 16384,
    retries: int = 5,
    stage: str = "optimizer",
    reasoning_effort: str | None = None,
    timeout: int | None = None,
) -> tuple[str, dict]:
    if get_optimizer_backend() == "claude_chat":
        return _claude.chat_optimizer(
            system=system,
            user=user,
            max_completion_tokens=max_completion_tokens,
            retries=retries,
            stage=stage,
            timeout=timeout,
        )
    if get_optimizer_backend() == "qwen_chat":
        return _qwen.chat_optimizer(
            system=system,
            user=user,
            max_completion_tokens=max_completion_tokens,
            retries=retries,
            stage=stage,
            reasoning_effort=reasoning_effort,
            timeout=timeout,
        )
    if get_optimizer_backend() == "codex_cli":
        return _codex.chat_optimizer(
            system=system,
            user=user,
            max_completion_tokens=max_completion_tokens,
            retries=retries,
            stage=stage,
            timeout=timeout,
        )
    return _openai.chat_optimizer(
        system=system,
        user=user,
        max_completion_tokens=max_completion_tokens,
        retries=retries,
        stage=stage,
        reasoning_effort=reasoning_effort,
        timeout=timeout,
    )


def chat_target(
    system: str,
    user: str,
    max_completion_tokens: int = 16384,
    retries: int = 5,
    stage: str = "target",
    reasoning_effort: str | None = None,
    timeout: int | None = None,
) -> tuple[str, dict]:
    if get_target_backend() == "claude_chat":
        return _claude.chat_target(
            system=system,
            user=user,
            max_completion_tokens=max_completion_tokens,
            retries=retries,
            stage=stage,
            timeout=timeout,
        )
    if get_target_backend() == "qwen_chat":
        return _qwen.chat_target(
            system=system,
            user=user,
            max_completion_tokens=max_completion_tokens,
            retries=retries,
            stage=stage,
            reasoning_effort=reasoning_effort,
            timeout=timeout,
        )
    if get_target_backend() == "minimax_chat":
        return _minimax.chat_target(
            system=system,
            user=user,
            max_completion_tokens=max_completion_tokens,
            retries=retries,
            stage=stage,
            reasoning_effort=reasoning_effort,
        )
    if not is_target_chat_backend():
        raise NotImplementedError(
            "chat_target is only supported with target_backend=openai_chat, claude_chat, qwen_chat, or minimax_chat. "
            "Exec backends are handled in environment-specific rollout code."
        )
    return _openai.chat_target(
        system=system,
        user=user,
        max_completion_tokens=max_completion_tokens,
        retries=retries,
        stage=stage,
        reasoning_effort=reasoning_effort,
        timeout=timeout,
    )


def chat_optimizer_messages(
    messages: list[dict[str, Any]],
    max_completion_tokens: int = 16384,
    retries: int = 5,
    stage: str = "optimizer",
    reasoning_effort: str | None = None,
    *,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    return_message: bool = False,
    timeout: int | None = None,
) -> tuple[Any, dict]:
    if get_optimizer_backend() == "claude_chat":
        return _claude.chat_optimizer_messages(
            messages=messages,
            max_completion_tokens=max_completion_tokens,
            retries=retries,
            stage=stage,
            tools=tools,
            tool_choice=tool_choice,
            return_message=return_message,
            timeout=timeout,
        )
    if get_optimizer_backend() == "qwen_chat":
        return _qwen.chat_optimizer_messages(
            messages=messages,
            max_completion_tokens=max_completion_tokens,
            retries=retries,
            stage=stage,
            reasoning_effort=reasoning_effort,
            tools=tools,
            tool_choice=tool_choice,
            return_message=return_message,
            timeout=timeout,
        )
    if get_optimizer_backend() == "codex_cli":
        return _codex.chat_optimizer_messages(
            messages=messages,
            max_completion_tokens=max_completion_tokens,
            retries=retries,
            stage=stage,
            tools=tools,
            tool_choice=tool_choice,
            return_message=return_message,
            timeout=timeout,
        )
    return _openai.chat_optimizer_messages(
        messages=messages,
        max_completion_tokens=max_completion_tokens,
        retries=retries,
        stage=stage,
        reasoning_effort=reasoning_effort,
        tools=tools,
        tool_choice=tool_choice,
        return_message=return_message,
        timeout=timeout,
    )


def chat_target_messages(
    messages: list[dict[str, Any]],
    max_completion_tokens: int = 16384,
    retries: int = 5,
    stage: str = "target",
    reasoning_effort: str | None = None,
    *,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    return_message: bool = False,
    timeout: int | None = None,
) -> tuple[Any, dict]:
    if get_target_backend() == "claude_chat":
        return _claude.chat_target_messages(
            messages=messages,
            max_completion_tokens=max_completion_tokens,
            retries=retries,
            stage=stage,
            tools=tools,
            tool_choice=tool_choice,
            return_message=return_message,
            timeout=timeout,
        )
    if get_target_backend() == "qwen_chat":
        return _qwen.chat_target_messages(
            messages=messages,
            max_completion_tokens=max_completion_tokens,
            retries=retries,
            stage=stage,
            reasoning_effort=reasoning_effort,
            tools=tools,
            tool_choice=tool_choice,
            return_message=return_message,
            timeout=timeout,
        )
    if get_target_backend() == "minimax_chat":
        return _minimax.chat_target_messages(
            messages=messages,
            max_completion_tokens=max_completion_tokens,
            retries=retries,
            stage=stage,
            reasoning_effort=reasoning_effort,
            tools=tools,
            tool_choice=tool_choice,
            return_message=return_message,
        )
    if not is_target_chat_backend():
        raise NotImplementedError(
            "chat_target_messages is only supported with target_backend=openai_chat, claude_chat, qwen_chat, or minimax_chat. "
            "Exec backends are handled in environment-specific rollout code."
        )
    return _openai.chat_target_messages(
        messages=messages,
        max_completion_tokens=max_completion_tokens,
        retries=retries,
        stage=stage,
        reasoning_effort=reasoning_effort,
        tools=tools,
        tool_choice=tool_choice,
        return_message=return_message,
        timeout=timeout,
    )


def chat_messages_with_deployment(
    deployment: str,
    messages: list[dict[str, Any]],
    max_completion_tokens: int = 16384,
    retries: int = 5,
    stage: str = "custom",
    reasoning_effort: str | None = None,
    *,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    return_message: bool = False,
    timeout: int | None = None,
) -> tuple[Any, dict]:
    return _openai.chat_messages_with_deployment(
        deployment=deployment,
        messages=messages,
        max_completion_tokens=max_completion_tokens,
        retries=retries,
        stage=stage,
        reasoning_effort=reasoning_effort,
        tools=tools,
        tool_choice=tool_choice,
        return_message=return_message,
        timeout=timeout,
    )


def chat_with_deployment(
    deployment: str,
    system: str,
    user: str,
    max_completion_tokens: int = 16384,
    retries: int = 5,
    stage: str = "custom",
    reasoning_effort: str | None = None,
    timeout: int | None = None,
) -> tuple[str, dict]:
    return _openai.chat_with_deployment(
        deployment=deployment,
        system=system,
        user=user,
        max_completion_tokens=max_completion_tokens,
        retries=retries,
        stage=stage,
        reasoning_effort=reasoning_effort,
        timeout=timeout,
    )


def get_token_summary() -> dict:
    """Merge shared objects once and request IDs once across processes and RAM."""
    requests, legacy = [], []
    root = os.environ.get("SKILLOPT_USAGE_ROOT")
    if root:
        requests.extend(UsageLedger(root).records())
    persisted_ids = {record["request_id"] for record in requests}
    for tracker in _unique_trackers():
        if hasattr(tracker, "records") and hasattr(tracker, "legacy_summary"):
            # An active ledger is the run boundary. Old RAM records from another
            # run cannot enter it; same-ID copies still undergo conflict checks.
            requests.extend(record for record in tracker.records()
                            if not root or record["request_id"] in persisted_ids)
            legacy.append(tracker.legacy_summary())
        else:
            legacy.append(tracker.summary())
    return summarize_usage(requests, legacy_summaries=legacy)


def _unique_trackers():
    seen = set()
    for backend in (_openai, _codex, _claude, _qwen, _minimax):
        tracker = backend.tracker
        if id(tracker) not in seen:
            seen.add(id(tracker))
            yield tracker


def reset_token_tracker() -> None:
    """Clear each in-process tracker once; persistent request history is retained."""
    for tracker in _unique_trackers():
        tracker.reset()


def configure_azure_openai(
    *,
    endpoint: str | None = None,
    api_version: str | None = None,
    api_key: str | None = None,
    auth_mode: str | None = None,
    ad_scope: str | None = None,
    managed_identity_client_id: str | None = None,
    optimizer_endpoint: str | None = None,
    optimizer_api_version: str | None = None,
    optimizer_api_key: str | None = None,
    optimizer_auth_mode: str | None = None,
    optimizer_ad_scope: str | None = None,
    optimizer_managed_identity_client_id: str | None = None,
    target_endpoint: str | None = None,
    target_api_version: str | None = None,
    target_api_key: str | None = None,
    target_auth_mode: str | None = None,
    target_ad_scope: str | None = None,
    target_managed_identity_client_id: str | None = None,
) -> None:
    _openai.configure_azure_openai(
        endpoint=endpoint,
        api_version=api_version,
        api_key=api_key,
        auth_mode=auth_mode,
        ad_scope=ad_scope,
        managed_identity_client_id=managed_identity_client_id,
        optimizer_endpoint=optimizer_endpoint,
        optimizer_api_version=optimizer_api_version,
        optimizer_api_key=optimizer_api_key,
        optimizer_auth_mode=optimizer_auth_mode,
        optimizer_ad_scope=optimizer_ad_scope,
        optimizer_managed_identity_client_id=optimizer_managed_identity_client_id,
        target_endpoint=target_endpoint,
        target_api_version=target_api_version,
        target_api_key=target_api_key,
        target_auth_mode=target_auth_mode,
        target_ad_scope=target_ad_scope,
        target_managed_identity_client_id=target_managed_identity_client_id,
    )


def configure_qwen_chat(
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    temperature: float | str | None = None,
    timeout_seconds: float | str | None = None,
    max_tokens: int | str | None = None,
    enable_thinking: bool | str | None = None,
    optimizer_base_url: str | None = None,
    optimizer_api_key: str | None = None,
    optimizer_temperature: float | str | None = None,
    optimizer_timeout_seconds: float | str | None = None,
    optimizer_max_tokens: int | str | None = None,
    optimizer_enable_thinking: bool | str | None = None,
    target_base_url: str | None = None,
    target_api_key: str | None = None,
    target_temperature: float | str | None = None,
    target_timeout_seconds: float | str | None = None,
    target_max_tokens: int | str | None = None,
    target_enable_thinking: bool | str | None = None,
) -> None:
    _qwen.configure_qwen_chat(
        base_url=base_url,
        api_key=api_key,
        temperature=temperature,
        timeout_seconds=timeout_seconds,
        max_tokens=max_tokens,
        enable_thinking=enable_thinking,
        optimizer_base_url=optimizer_base_url,
        optimizer_api_key=optimizer_api_key,
        optimizer_temperature=optimizer_temperature,
        optimizer_timeout_seconds=optimizer_timeout_seconds,
        optimizer_max_tokens=optimizer_max_tokens,
        optimizer_enable_thinking=optimizer_enable_thinking,
        target_base_url=target_base_url,
        target_api_key=target_api_key,
        target_temperature=target_temperature,
        target_timeout_seconds=target_timeout_seconds,
        target_max_tokens=target_max_tokens,
        target_enable_thinking=target_enable_thinking,
    )


def configure_minimax_chat(
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    temperature: float | str | None = None,
    timeout_seconds: float | str | None = None,
    max_tokens: int | str | None = None,
    enable_thinking: bool | str | None = None,
) -> None:
    _minimax.configure_minimax_chat(
        base_url=base_url,
        api_key=api_key,
        temperature=temperature,
        timeout_seconds=timeout_seconds,
        max_tokens=max_tokens,
        enable_thinking=enable_thinking,
    )


def set_reasoning_effort(effort: str | None) -> None:
    _openai.set_reasoning_effort(effort)
    _codex.set_reasoning_effort(effort)
    _claude.set_reasoning_effort(effort)
    _qwen.set_reasoning_effort(effort)
    _minimax.set_reasoning_effort(effort)


def set_target_deployment(deployment: str) -> None:
    _openai.set_target_deployment(deployment)
    _codex.set_target_deployment(deployment)
    _claude.set_target_deployment(deployment)
    _qwen.set_target_deployment(deployment)
    _minimax.set_target_deployment(deployment)


def set_optimizer_deployment(deployment: str) -> None:
    _openai.set_optimizer_deployment(deployment)
    _codex.set_optimizer_deployment(deployment)
    _claude.set_optimizer_deployment(deployment)
    _qwen.set_optimizer_deployment(deployment)
