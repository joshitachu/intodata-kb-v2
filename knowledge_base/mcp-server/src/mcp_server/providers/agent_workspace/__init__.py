"""The ``agent-workspace-v1`` documentation lane.

Nexus parses and sanitises, stages one readable workspace with the
instructions on how to analyse it, hands that workspace and a goal to a coding
agent, and publishes whatever Markdown the agent writes into ``output/``.

There is no orchestrator in this package on purpose. No phase driver, no batch
planner, no partitioner and no scope seeder: the agent decides what to read,
how deep to go, and whether to fan out. Nexus' remaining jobs are to make the
evidence readable, to say what good looks like, and to refuse to publish
something unsafe.

Nexus DOES enumerate the estate -- ``ledger.py`` projects one roster row per
parsed design -- and that is not a work plan. The roster says what exists so
the run can be accountable for all of it; it assigns nothing, orders nothing,
sizes no sub-agent and does not imply one document per row. The distinction is
the whole design: *the agent is free to choose how it investigates, but it is
not free to choose what must be accounted for.*

``contract.py`` is the seam -- layout constants and the provider-neutral run
contract. Every other module in this package imports it and none of them
redefine any part of it.
"""

from mcp_server.providers.agent_workspace.contract import (
    CONTRACT,
    AgentProvider,
    AgentRunRequest,
    AgentRunResult,
    WorkspaceBuilder,
    WorkspaceReceipt,
    WorkspaceUnavailableError,
)

__all__ = [
    "CONTRACT",
    "AgentProvider",
    "AgentRunRequest",
    "AgentRunResult",
    "WorkspaceBuilder",
    "WorkspaceReceipt",
    "WorkspaceUnavailableError",
]
