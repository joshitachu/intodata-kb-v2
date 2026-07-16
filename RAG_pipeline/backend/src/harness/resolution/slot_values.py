"""Context-aware value resolution for pending continuation slots."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime

from .models import (
    ContinuationSlotValueResolution,
    SlotValueResolutionStatus,
)
from .time_windows import resolve_time_window

_SLOT_DURATION = re.compile(r"^(\d+)\s*(hours?|hrs?|h|days?|d)$", re.IGNORECASE)
_TIME_WINDOW_GUIDANCE = (
    'Please include both a number and a unit, for example "24 hours", '
    '"24 h", or "2 days". You can also use "today" or "yesterday".'
)

SlotValueResolver = Callable[[str, datetime | None], ContinuationSlotValueResolution]


def resolve_continuation_slot_value(
    slot_name: str,
    text: str,
    *,
    now: datetime | None = None,
) -> ContinuationSlotValueResolution:
    """Resolve user text only in the context of a declared pending slot."""

    resolver = _SLOT_VALUE_RESOLVERS.get(slot_name)
    if resolver is None:
        return ContinuationSlotValueResolution(
            slot_name=slot_name,
            status=SlotValueResolutionStatus.NOT_APPLICABLE,
        )
    return resolver(text, now)


def _resolve_time_window_slot_value(
    text: str,
    now: datetime | None,
) -> ContinuationSlotValueResolution:
    value = re.sub(r"\s+", " ", text.strip())
    if not value:
        return _invalid_time_window()

    try:
        window = resolve_time_window(value, now=now)
    except ValueError:
        duration = _SLOT_DURATION.fullmatch(value)
        if duration is None:
            return _invalid_time_window()
        amount = duration.group(1)
        raw_unit = duration.group(2).casefold()
        unit = "hours" if raw_unit.startswith(("h", "hr", "hour")) else "days"
        try:
            window = resolve_time_window(f"last {amount} {unit}", now=now)
        except ValueError:
            return _invalid_time_window()
        window = window.model_copy(update={"expression": value})

    return ContinuationSlotValueResolution(
        slot_name="time_window",
        status=SlotValueResolutionStatus.RESOLVED,
        value=window,
    )


def _invalid_time_window() -> ContinuationSlotValueResolution:
    return ContinuationSlotValueResolution(
        slot_name="time_window",
        status=SlotValueResolutionStatus.INVALID,
        clarification=_TIME_WINDOW_GUIDANCE,
    )


_SLOT_VALUE_RESOLVERS: dict[str, SlotValueResolver] = {
    "time_window": _resolve_time_window_slot_value,
}
