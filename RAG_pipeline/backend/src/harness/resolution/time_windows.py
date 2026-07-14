"""UTC relative and explicit time-window parsing."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta

from .models import TimeWindow, TimeWindowKind

_RELATIVE = re.compile(r"^(?:past|last)\s+(\d+)\s*(hours?|hrs?|days?)$", re.I)


def resolve_time_window(
    expression: str | None = None,
    *,
    now: datetime | None = None,
    start: datetime | str | None = None,
    end: datetime | str | None = None,
) -> TimeWindow:
    """Normalize a supported relative expression or explicit ISO bounds to UTC."""

    reference = _as_utc(now or datetime.now(UTC), label="now")
    if start is not None or end is not None:
        if start is None or end is None:
            raise ValueError("explicit time windows require both start and end")
        return TimeWindow(
            start=_parse_iso_bound(start, label="start"),
            end=_parse_iso_bound(end, label="end"),
            kind=TimeWindowKind.EXPLICIT,
            expression=expression or f"{start}/{end}",
        )
    if expression is None or not expression.strip():
        raise ValueError("a time expression or explicit bounds are required")

    value = expression.strip()
    normalized = value.casefold()
    relative = _RELATIVE.fullmatch(normalized)
    if relative:
        amount = int(relative.group(1))
        if amount < 1:
            raise ValueError("relative time amount must be positive")
        unit = relative.group(2).casefold()
        delta = timedelta(hours=amount) if unit.startswith(("hour", "hr")) else timedelta(days=amount)
        return TimeWindow(
            start=reference - delta,
            end=reference,
            kind=TimeWindowKind.RELATIVE,
            expression=value,
        )

    today_start = datetime.combine(reference.date(), time.min, tzinfo=UTC)
    if normalized == "today":
        return TimeWindow(
            start=today_start,
            end=reference,
            kind=TimeWindowKind.CALENDAR_DAY,
            expression=value,
        )
    if normalized == "yesterday":
        return TimeWindow(
            start=today_start - timedelta(days=1),
            end=today_start,
            kind=TimeWindowKind.CALENDAR_DAY,
            expression=value,
        )

    if "/" in value:
        raw_start, separator, raw_end = value.partition("/")
        if separator and raw_start.strip() and raw_end.strip():
            return TimeWindow(
                start=_parse_iso_bound(raw_start.strip(), label="start"),
                end=_parse_iso_bound(raw_end.strip(), label="end"),
                kind=TimeWindowKind.EXPLICIT,
                expression=value,
            )
    raise ValueError(f"unsupported time window expression: {expression!r}")


def _parse_iso_bound(value: datetime | str, *, label: str) -> datetime:
    if isinstance(value, datetime):
        return _as_utc(value, label=label)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            return datetime.combine(date.fromisoformat(value), time.min, tzinfo=UTC)
        except ValueError as exc:
            raise ValueError(f"{label} must be an ISO-8601 timestamp or date") from exc
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp or date") from exc
    return _as_utc(parsed, label=label)


def _as_utc(value: datetime, *, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return value.astimezone(UTC)
