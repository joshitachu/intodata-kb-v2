"""Read-only access to the MCP-published project topology artifacts.

Distinct from `src.runtime.topology`, which owns the *runtime* static-topology
snapshot stored in this service's own database (jobs, components, lineage facts
pushed via `POST /ingest/runtime-static-topology`).

This package instead reads the artifacts the topology *canvas* renders, straight
from the MCP server, so the assistant and the diagram can never disagree about
what exists:

  - landscape family: systems, applications, edges, channels, interfaces,
    diagnostics, business-overlay state
  - graph family: jobs, components, connections
  - lineage family: field-level mappings (source column -> target column plus
    the transformation expression), paged, per design or per functional group

Nothing here writes. The MCP landscape endpoint is deliberately read-only.
"""

from src.topology.client import (
    execute_topology_tool,
    is_topology_tool,
    list_topology_tools,
)

__all__ = [
    "execute_topology_tool",
    "is_topology_tool",
    "list_topology_tools",
]
