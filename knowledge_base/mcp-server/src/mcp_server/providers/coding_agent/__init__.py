"""Provider-neutral coding-agent execution contracts.

This package is intentionally independent from both the legacy documentation
harness and the goal-driven domain pipeline.  Domain code supplies a run
request; provider adapters own only execution mechanics.

``factory`` is resolved lazily (PEP 562). It is the only module here that
imports a concrete transport, and that transport pulls in ``opencode_cli`` ->
``mcp_server.config``, so an eager re-export made ``import
mcp_server.providers.coding_agent.models`` -- three dataclasses -- load the
whole CLI stack and settings object. ``agent_workspace.contract`` imports
exactly that module, so the cost landed on every consumer of the new lane's
seam. Everything else here is contracts only and stays eager.
"""

from typing import TYPE_CHECKING, Any

from mcp_server.providers.coding_agent.base import (
    CodingAgentRuntime,
    ExecutionObserver,
    ExecutionTransport,
    IncompatibleCheckpointError,
    ProviderExecutionRequest,
    ProviderExecutionResult,
    UnsupportedCodingAgentError,
)
from mcp_server.providers.coding_agent.models import (
    AgentCapabilities,
    InvestigationCheckpoint,
    InvestigationFingerprints,
    InvestigationRequest,
    InvestigationResult,
    ResourceCeiling,
    UsageReceipt,
)
from mcp_server.providers.coding_agent.opencode import OpenCodeCodingAgent
from mcp_server.providers.coding_agent.registry import CodingAgentRegistry

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mcp_server.providers.coding_agent.factory import (
        build_agent_provider_registry,
        build_coding_agent_registry,
        register_agent_provider,
        register_coding_agent_runtime,
        unregister_agent_provider,
        unregister_coding_agent_runtime,
    )

_LAZY_FACTORY_EXPORTS = frozenset(
    {
        "build_agent_provider_registry",
        "build_coding_agent_registry",
        "register_agent_provider",
        "register_coding_agent_runtime",
        "unregister_agent_provider",
        "unregister_coding_agent_runtime",
    }
)


def __getattr__(name: str) -> Any:
    if name in _LAZY_FACTORY_EXPORTS:
        from mcp_server.providers.coding_agent import factory

        return getattr(factory, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | _LAZY_FACTORY_EXPORTS)


__all__ = [
    "AgentCapabilities",
    "CodingAgentRegistry",
    "CodingAgentRuntime",
    "ExecutionObserver",
    "ExecutionTransport",
    "IncompatibleCheckpointError",
    "InvestigationCheckpoint",
    "InvestigationFingerprints",
    "InvestigationRequest",
    "InvestigationResult",
    "OpenCodeCodingAgent",
    "ProviderExecutionRequest",
    "ProviderExecutionResult",
    "ResourceCeiling",
    "UnsupportedCodingAgentError",
    "UsageReceipt",
    "build_agent_provider_registry",
    "build_coding_agent_registry",
    "register_agent_provider",
    "register_coding_agent_runtime",
    "unregister_agent_provider",
    "unregister_coding_agent_runtime",
]
