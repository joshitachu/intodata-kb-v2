"""Sanitized connector failures safe for APIs, receipts, and logs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ConnectorError(Exception):
    code: str
    message: str
    status_code: int | None = None
    retryable: bool = False

    def __str__(self) -> str:
        return self.message


def sanitize_provider_error(status_code: int | None, body: Any = None) -> ConnectorError:
    """Map vendor bodies to stable error codes without retaining body text."""
    if status_code == 401:
        return ConnectorError("authentication_failed", "Provider authentication failed", status_code)
    if status_code == 403:
        return ConnectorError("authorization_failed", "Provider authorization was denied", status_code)
    if status_code == 404:
        return ConnectorError("not_found", "Requested provider resource was not found", status_code)
    if status_code == 429:
        return ConnectorError("rate_limited", "Provider rate limit reached", status_code, retryable=True)
    if status_code is not None and status_code >= 500:
        return ConnectorError("provider_failure", "Provider service failed", status_code, retryable=True)
    return ConnectorError("provider_failure", "Provider request failed", status_code)
