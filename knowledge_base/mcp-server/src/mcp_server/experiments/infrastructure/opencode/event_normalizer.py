"""Redacted normalization of OpenCode event, status, and usage payloads."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from mcp_server.experiments.domain.runtime import (
    RuntimeEventCategory,
    RuntimeTaskEvent,
    RuntimeTaskStatus,
    RuntimeUsage,
    redact_payload,
)


def normalize_usage(payload: Mapping[str, Any]) -> RuntimeUsage:
    """Tolerate the known OpenCode usage nesting variants without fabricating data."""

    info = _mapping(payload.get("info")) or payload
    finish = _step_finish(payload)
    usage = _mapping(info.get("usage")) or _mapping(payload.get("usage")) or {}
    tokens = _mapping(finish.get("tokens")) if finish else None
    tokens = tokens or _mapping(usage.get("tokens")) or _mapping(payload.get("tokens")) or usage
    cache = _mapping(tokens.get("cache")) or {}
    cost = (
        _first(finish, "cost", "cost_usd")
        if finish
        else _first(info, "cost", "cost_usd", "billedCostUsd")
    )
    return RuntimeUsage(
        input_tokens=_integer(_first(tokens, "input_tokens", "inputTokens", "input")),
        output_tokens=_integer(_first(tokens, "output_tokens", "outputTokens", "output")),
        cache_read_tokens=_integer(
            _first(tokens, "cache_read_tokens", "cacheReadTokens", "cache_read")
            or _first(cache, "read", "input")
        ),
        cache_write_tokens=_integer(
            _first(tokens, "cache_write_tokens", "cacheWriteTokens", "cache_write")
            or _first(cache, "write", "output")
        ),
        billed_cost_usd=_number(cost),
        normalized_cost_usd=None,
    )


def normalize_status(value: object) -> RuntimeTaskStatus:
    normalized = str(value or "").lower()
    if normalized in {"completed", "complete", "succeeded", "success", "done"}:
        return RuntimeTaskStatus.SUCCEEDED
    if normalized in {"failed", "error", "rejected"}:
        return RuntimeTaskStatus.FAILED
    if normalized in {"cancelled", "canceled", "aborted"}:
        return RuntimeTaskStatus.CANCELLED
    if normalized in {"running", "busy", "pending", "queued"}:
        return RuntimeTaskStatus.RUNNING
    return RuntimeTaskStatus.UNKNOWN


class OpenCodeEventNormalizer:
    """Converts OpenCode envelopes to deduplicated, append-only generic events."""

    def __init__(self) -> None:
        self._seen_event_ids: set[str] = set()
        self._next_sequence = 1

    def normalize(self, payload: Mapping[str, Any], *, received_at: datetime | None = None) -> RuntimeTaskEvent | None:
        payload = redact_payload(payload)
        properties = _mapping(payload.get("properties")) or _mapping(payload.get("payload")) or {}
        event_id = str(
            payload.get("id")
            or payload.get("eventID")
            or properties.get("id")
            or (_mapping(properties.get("info")) or {}).get("id")
            or (_mapping(properties.get("part")) or {}).get("id")
            or ""
        )
        if not event_id:
            event_id = f"derived-{self._next_sequence}"
        if event_id in self._seen_event_ids:
            return None
        self._seen_event_ids.add(event_id)
        event_type = str(payload.get("type") or payload.get("event") or "lifecycle")
        body = _mapping(payload.get("properties")) or _mapping(payload.get("payload")) or payload
        event = RuntimeTaskEvent(
            event_id=event_id,
            sequence=self._next_sequence,
            category=_event_category(event_type),
            occurred_at=_timestamp(payload.get("time") or payload.get("timestamp"), received_at),
            task_status=_event_status(event_type, body),
            payload=body,
        )
        self._next_sequence += 1
        return event


def _event_category(event_type: str) -> RuntimeEventCategory:
    lower = event_type.lower()
    if "tool" in lower:
        return RuntimeEventCategory.TOOL
    if "error" in lower or "fail" in lower:
        return RuntimeEventCategory.ERROR
    if "usage" in lower or "cost" in lower:
        return RuntimeEventCategory.USAGE
    if "message" in lower or "text" in lower:
        return RuntimeEventCategory.MESSAGE
    if "warn" in lower:
        return RuntimeEventCategory.WARNING
    return RuntimeEventCategory.LIFECYCLE


def _event_status(event_type: str, body: Mapping[str, Any]) -> RuntimeTaskStatus | None:
    status = body.get("status") or body.get("state")
    if status is None and any(word in event_type.lower() for word in ("completed", "failed", "cancelled")):
        status = event_type
    normalized = normalize_status(status)
    return None if normalized == RuntimeTaskStatus.UNKNOWN else normalized


def _mapping(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _first(payload: Mapping[str, Any], *keys: str) -> object:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _integer(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _timestamp(value: object, fallback: datetime | None) -> datetime:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return fallback or datetime.now(timezone.utc)


def event_session_id(payload: Mapping[str, Any]) -> str | None:
    """Extract the session from global OpenCode events without trusting event type."""

    properties = _mapping(payload.get("properties")) or _mapping(payload.get("payload")) or payload
    for candidate in (
        properties.get("sessionID"),
        properties.get("sessionId"),
        _mapping(properties.get("info")) or {},
        _mapping(properties.get("part")) or {},
    ):
        if isinstance(candidate, Mapping):
            candidate = candidate.get("sessionID") or candidate.get("sessionId")
        if candidate:
            return str(candidate)
    return None


def message_info(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(payload.get("info")) or payload


def message_parts(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    parts = payload.get("parts")
    if not isinstance(parts, list):
        return ()
    return tuple(part for part in parts if isinstance(part, Mapping))


def message_failure(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    info = message_info(payload)
    if info.get("error"):
        return {"info_error": info["error"]}
    for part in message_parts(payload):
        if part.get("type") == "step-finish" and (part.get("error") or part.get("reason") in {"error", "aborted"}):
            return {"step_finish": part}
    return None


def completed_assistant_message(payload: Mapping[str, Any]) -> bool:
    info = message_info(payload)
    if str(info.get("role") or "").lower() != "assistant" or message_failure(payload):
        return False
    return any(
        part.get("type") == "step-finish" and part.get("reason") not in {"error", "aborted"}
        for part in message_parts(payload)
    )


def _step_finish(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    return next((part for part in message_parts(payload) if part.get("type") == "step-finish"), None)
