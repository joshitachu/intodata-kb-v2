"""Runtime status service — reports whether a live runtime provider (e.g. Talend
TMC) is configured and reachable, without depending on the provider itself.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from src.config import settings
from src.runtime.models import RuntimeConnectionStatus, RuntimeStatusResponse

logger = logging.getLogger(__name__)


async def get_runtime_status() -> RuntimeStatusResponse:
    provider_key = settings.runtime_provider_key or None
    provider_label = settings.runtime_provider_label

    if not provider_key:
        return RuntimeStatusResponse(
            status=RuntimeConnectionStatus.DISABLED,
            provider_key=None,
            provider_label=provider_label,
            message="Live runtime disabled",
            checked_at=datetime.now(UTC),
        )

    if not settings.runtime_provider_health_url:
        return RuntimeStatusResponse(
            status=RuntimeConnectionStatus.UNAVAILABLE,
            provider_key=provider_key,
            provider_label=provider_label,
            message=f"{provider_label} unavailable",
            checked_at=datetime.now(UTC),
        )

    if await _check_provider_health(provider_key):
        return RuntimeStatusResponse(
            status=RuntimeConnectionStatus.CONNECTED,
            provider_key=provider_key,
            provider_label=provider_label,
            message=f"{provider_label} connected",
            checked_at=datetime.now(UTC),
        )

    return RuntimeStatusResponse(
        status=RuntimeConnectionStatus.UNAVAILABLE,
        provider_key=provider_key,
        provider_label=provider_label,
        message=f"{provider_label} unavailable",
        checked_at=datetime.now(UTC),
    )


async def _check_provider_health(provider_key: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=settings.runtime_status_timeout) as client:
            resp = await client.get(settings.runtime_provider_health_url)
            return 200 <= resp.status_code < 300
    except httpx.HTTPError as e:
        logger.warning(f"Runtime provider health check failed for {provider_key}: {e}")
        return False
