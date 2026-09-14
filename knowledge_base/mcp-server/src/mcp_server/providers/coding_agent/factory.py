"""Application-facing construction for provider-neutral coding-agent adapters.

Two lanes are built here and they are deliberately separate registries:

* :func:`build_coding_agent_registry` -- the goal-driven investigation runtime
  (``CodingAgentRuntime``).
* :func:`build_agent_provider_registry` -- the ``agent-workspace-v1`` lane
  (``AgentProvider``): mount a staged workspace, run one session, report usage.

Both have an **injection seam**. Before this, the only way to substitute an
adapter was ``monkeypatch.setattr(factory, "OpenCodeCliProvider", Fake)``, which
replaces a transport class the registry happens to name -- so a test could not
register a *different* provider id at all, and any refactor of that name broke
the substitution silently. Registration is now explicit and keyed on the
provider id the profile actually selects.

The tables are process-level, which is the right scope: a provider adapter is a
property of the deployment, not of one request. Registrations made by a test
must be removed again; :func:`unregister_agent_provider` is idempotent so a
fixture teardown never needs a guard.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from mcp_server.providers.coding_agent.base import CodingAgentRuntime, LogCallback
from mcp_server.providers.coding_agent.opencode import OpenCodeCodingAgent
from mcp_server.providers.coding_agent.registry import CodingAgentRegistry

# Imported eagerly on purpose. This module is the one place that is allowed to
# know a concrete transport, and the existing goal-driven suite substitutes
# ``factory.OpenCodeCliProvider`` directly; moving it into the closure would
# break that seam while the explicit registration below is still settling in.
from mcp_server.providers.opencode_cli import (
    OpenCodeAgentWorkspaceProvider,
    OpenCodeCliProvider,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mcp_server.providers.agent_workspace.contract import AgentProvider

#: Builds one ``AgentProvider``. Receives the model route the run resolved, and
#: nothing else: an adapter that needs domain input is mis-designed (see
#: ``agent_workspace/contract.py``).
AgentProviderBuilder = Callable[[str], "AgentProvider"]

#: Builds one ``CodingAgentRuntime`` for the goal-driven lane.
CodingAgentRuntimeBuilder = Callable[[str, LogCallback, Any], CodingAgentRuntime]

_AGENT_PROVIDER_BUILDERS: dict[str, AgentProviderBuilder] = {}
_CODING_AGENT_RUNTIME_BUILDERS: dict[str, CodingAgentRuntimeBuilder] = {}


def _normalize(provider_id: str) -> str:
    key = provider_id.strip().lower()
    if not key:
        raise ValueError("provider_id must be non-empty")
    return key


def register_agent_provider(provider_id: str, builder: AgentProviderBuilder) -> None:
    """Make ``provider_id`` selectable by the agent-workspace lane."""

    _AGENT_PROVIDER_BUILDERS[_normalize(provider_id)] = builder


def unregister_agent_provider(provider_id: str) -> None:
    """Remove a registration. Idempotent, so teardown needs no guard."""

    _AGENT_PROVIDER_BUILDERS.pop(_normalize(provider_id), None)


def register_coding_agent_runtime(
    provider_id: str, builder: CodingAgentRuntimeBuilder
) -> None:
    """Make ``provider_id`` selectable by the goal-driven investigation lane."""

    _CODING_AGENT_RUNTIME_BUILDERS[_normalize(provider_id)] = builder


def unregister_coding_agent_runtime(provider_id: str) -> None:
    """Remove a registration. Idempotent, so teardown needs no guard."""

    _CODING_AGENT_RUNTIME_BUILDERS.pop(_normalize(provider_id), None)


def build_coding_agent_registry(
    *,
    model: str,
    log_cb: LogCallback,
    connector_runtime: Any | None = None,
) -> CodingAgentRegistry:
    """Build the runtimes that have a real execution transport in this process.

    Only OpenCode is built in.  Merely accepting another provider name in
    configuration is not treated as support: the registry fails closed until
    that provider has an actual transport adapter, or one is registered through
    :func:`register_coding_agent_runtime`.
    """

    registry: CodingAgentRegistry = CodingAgentRegistry()

    def build_opencode() -> CodingAgentRuntime:
        cli = OpenCodeCliProvider(model_route=model)

        async def transport(execution_request: Any, observer: Any) -> Any:
            return await cli.run_goal_driven_investigation(
                execution_request,
                observer,
                log_cb=log_cb,
                connector_runtime=connector_runtime,
            )

        return OpenCodeCodingAgent(model=model, transport=transport, native_delegation=True)

    registry.register("opencode", build_opencode)
    for provider_id, builder in _CODING_AGENT_RUNTIME_BUILDERS.items():
        registry.register(
            provider_id,
            (lambda bound=builder: bound(model, log_cb, connector_runtime)),
            replace=True,
        )
    return registry


def build_agent_provider_registry(
    *,
    model: str,
) -> CodingAgentRegistry[AgentProvider]:
    """Build the ``AgentProvider`` adapters available to ``agent-workspace-v1``.

    OpenCode is the only built-in adapter. ``codex`` and ``claude-code`` are
    accepted values in the generation profile but have no adapter yet, so the
    registry raises ``UnsupportedCodingAgentError`` for them -- naming a
    provider in configuration is not the same as being able to run it.
    """

    registry: CodingAgentRegistry[AgentProvider] = CodingAgentRegistry()

    def build_opencode() -> AgentProvider:
        return OpenCodeAgentWorkspaceProvider(model_route=model)

    registry.register("opencode", build_opencode)
    for provider_id, builder in _AGENT_PROVIDER_BUILDERS.items():
        registry.register(
            provider_id,
            (lambda bound=builder: bound(model)),
            replace=True,
        )
    return registry
