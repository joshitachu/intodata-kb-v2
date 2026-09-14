"""Small provider adapter registry for coding-agent lanes.

Generic in the adapter type because two lanes now use it and they speak
different protocols: the goal-driven runtime (``CodingAgentRuntime``, a
resumable investigation) and the agent-workspace lane (``AgentProvider``, one
mechanical ``run_agent``). The lookup, the normalisation of the provider id and
the fail-closed error are identical; the adapter type is not. Keeping one class
means an unregistered provider fails the same way in both lanes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Generic, TypeVar

from mcp_server.providers.coding_agent.base import UnsupportedCodingAgentError

#: No PEP 696 default: the package targets Python 3.11, where ``TypeVar`` does
#: not accept one. An unparameterised ``CodingAgentRegistry()`` therefore keeps
#: working exactly as it did, which is what every existing call site does.
AdapterT = TypeVar("AdapterT")


class CodingAgentRegistry(Generic[AdapterT]):
    def __init__(self) -> None:
        self._factories: dict[str, Callable[[], AdapterT]] = {}

    def register(
        self,
        provider_id: str,
        factory: Callable[[], AdapterT],
        *,
        replace: bool = False,
    ) -> None:
        """Bind ``provider_id`` to a factory.

        ``replace`` exists for the injection seam in :mod:`factory`: a
        registered extension deliberately overrides the built-in adapter of the
        same name. A plain double-registration stays an error, because two
        callers each believing they own a provider id is a wiring bug.
        """

        key = provider_id.strip().lower()
        if not key:
            raise ValueError("provider_id must be non-empty")
        if key in self._factories and not replace:
            raise ValueError(f"coding-agent provider already registered: {key}")
        self._factories[key] = factory

    def create(self, provider_id: str) -> AdapterT:
        key = provider_id.strip().lower()
        try:
            return self._factories[key]()
        except KeyError as exc:
            raise UnsupportedCodingAgentError(
                f"coding-agent provider {key!r} has no registered execution transport"
            ) from exc

    def provider_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))
