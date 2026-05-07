"""Registry for MCP platform strategies."""

from __future__ import annotations

from mcp_server.platforms.strategies import (
    GenericPlatformStrategy,
    LobsterPlatformStrategy,
    PlatformStrategy,
    TalendPlatformStrategy,
    WorkatoPlatformStrategy,
)

_STRATEGIES: tuple[PlatformStrategy, ...] = (
    TalendPlatformStrategy(),
    WorkatoPlatformStrategy(),
    LobsterPlatformStrategy(),
    GenericPlatformStrategy(),
)

_STRATEGY_BY_ID: dict[str, PlatformStrategy] = {
    strategy.id: strategy for strategy in _STRATEGIES
}


def get_platform_strategy(platform: str) -> PlatformStrategy:
    normalized = platform.lower().strip()
    try:
        return _STRATEGY_BY_ID[normalized]
    except KeyError as exc:
        available = ", ".join(sorted(_STRATEGY_BY_ID))
        raise ValueError(f"Unknown platform '{platform}'. Available: {available}") from exc


def list_platform_strategies() -> list[PlatformStrategy]:
    return list(_STRATEGIES)


def supported_platform_ids() -> frozenset[str]:
    return frozenset(_STRATEGY_BY_ID)


def is_supported_platform(platform: str) -> bool:
    return platform.lower().strip() in _STRATEGY_BY_ID
