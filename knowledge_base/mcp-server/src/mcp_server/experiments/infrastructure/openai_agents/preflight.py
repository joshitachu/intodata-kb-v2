"""Read-only capability preflight for optional OpenAI Agents runtime wiring."""

from __future__ import annotations

from dataclasses import dataclass

from mcp_server.experiments.domain.runtime import AgentRuntime, RuntimeAvailability
from mcp_server.experiments.infrastructure.openai_agents.runtime import (
    VALIDATED_OPENAI_AGENTS_VERSION,
)


@dataclass(frozen=True, slots=True)
class VerifiedOpenAIAgentsRoute:
    runtime_id: str
    runtime_version: str
    provider_id: str
    model_id: str
    capabilities: frozenset[str]


async def verify_openai_agents_route(
    runtime: AgentRuntime,
    *,
    provider_id: str,
    model_id: str,
    required_capabilities: frozenset[str],
) -> VerifiedOpenAIAgentsRoute:
    """Verify static health/capabilities without issuing a provider model request."""

    health = await runtime.health()
    capabilities = await runtime.capabilities()
    if (
        health.availability != RuntimeAvailability.AVAILABLE
        or health.runtime_id != "openai-agents"
        or capabilities.runtime_id != health.runtime_id
        or health.runtime_version != VALIDATED_OPENAI_AGENTS_VERSION
        or capabilities.runtime_version != health.runtime_version
    ):
        raise ValueError("OpenAI Agents runtime identity/version preflight failed")
    route = next(
        (
            model
            for model in capabilities.models
            if model.provider_id == provider_id and model.model_id == model_id
        ),
        None,
    )
    mandatory = required_capabilities | frozenset({"streaming", "usage-reporting"})
    if route is None or not route.available or not mandatory.issubset(route.capabilities):
        raise ValueError("OpenAI Agents provider/model capability preflight failed")
    return VerifiedOpenAIAgentsRoute(
        runtime_id=health.runtime_id,
        runtime_version=health.runtime_version,
        provider_id=route.provider_id,
        model_id=route.model_id,
        capabilities=route.capabilities,
    )
