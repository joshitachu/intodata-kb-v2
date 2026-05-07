"""MCP platform strategy facade."""

from mcp_server.platforms.registry import (
    get_platform_strategy,
    is_supported_platform,
    list_platform_strategies,
    supported_platform_ids,
)
from mcp_server.platforms.strategies import PlatformStrategy

__all__ = [
    "PlatformStrategy",
    "get_platform_strategy",
    "is_supported_platform",
    "list_platform_strategies",
    "supported_platform_ids",
]
