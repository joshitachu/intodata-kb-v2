"""Runtime status contract — provider-agnostic, no Talend-specific fields."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class RuntimeConnectionStatus(str, Enum):
    DISABLED = "disabled"
    CONNECTED = "connected"
    UNAVAILABLE = "unavailable"


class RuntimeStatusResponse(BaseModel):
    status: RuntimeConnectionStatus
    provider_key: str | None = None
    provider_label: str
    message: str
    checked_at: datetime
