"""Dispatch surface for the topology tools.

INTERFACE CONTRACT -- pinned before implementation so the tool layer and the
agent-loop seam can be built independently. Signatures and the `data` payload
shape below are fixed; do not change them without updating both sides.

Deliberately a separate dispatcher from `src.runtime.client.execute_runtime_tool`:
that one short-circuits to `DISABLED` when `settings.runtime_provider_key` is
empty. Topology is not runtime data, and must stay available on deployments with
live runtime turned off.

Identity discipline, mirroring `execute_runtime_tool`: `project_scope` and
`access_token` are keyword-only and supplied by authenticated chat code. Neither
may ever be derived from LLM-supplied `call.arguments`. `access_token` is
forwarded upstream so MCP performs the authoritative project-access check
(`require_project_access`); `project_scope` is this service's own allow-list and
must be enforced here as well, so dropping the forward can never silently widen
access.

Every successful result carries this `data` shape:

    {
      "content": { ... bounded facts, provenance preserved ... },
      "targets": [
        {
          "label": "SAP ERP",
          "level": { "kind": "integration",
                     "selection": {"kind": "system", "systemId": "landscape-system:..."} },
          "intent": "reference",
          "visual_role": "primary"
        },
        {
          "label": "Quintiq2Maximo_WorkOrderPickedUpXML",
          "level": { "kind": "job", "jobName": "...", "sourceRoot": "..." },
          "intent": "reference",
          "visual_role": "supporting"
        },
        ...
      ]
    }

`targets[].level` mirrors the frontend `TopologyLevel` union in
`frontend/hooks/useTopologyNavigation.ts` exactly, including its id-vs-name
split: `integration` selections carry ids (`applicationId` / `systemId` /
`edgeKey`, or a `relationship`'s `source` / `target` / `streamKeys`), while
`group` / `job` / `component` carry names plus `sourceRoot`. Keys are camelCase
and an absent `sourceRoot` is OMITTED, never serialised as null. Producing the
wrong variant yields a chip that navigates nowhere.

`targets[].intent` is `"navigate"` or `"reference"`, set per call by the model
from the user's wording and stamped onto every target the call produces.
`"reference"` is the default at every layer: an omitted or unrecognized value
must never resolve to `"navigate"`, because that moves the user's canvas unbidden.

`targets[].visual_role` is server-authored after artifact identity resolution:
`"primary"`, `"supporting"`, or `"path"`. It is never accepted as model input.
A resolved component target may also carry bounded `visual_detail` mapping
identities derived from its component graph; these emphasize loaded tables,
columns and mapping edges but do not create another navigable level.

`content` must always be a bounded projection. The landscape artifact is a
whole-project document and a real job carries up to ~250 components; returning
either raw would exhaust the context window.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from src.config import settings
from src.runtime.models import (
    RuntimeToolCall,
    RuntimeToolDefinition,
    RuntimeToolError,
    RuntimeToolErrorCode,
    RuntimeToolResult,
)
from src.runtime.store import ProjectScope
from src.topology import provider as provider_module
from src.topology.provider import (
    TopologyArtifactUnavailableError,
    TopologyDesignUnavailableError,
    TopologyMappingUnavailableError,
    TopologyPayloadError,
    TopologyProviderError,
    TopologyProviderTimeoutError,
)
from src.topology.registry import (
    TopologyToolValidationError,
    UnknownTopologyToolError,
    list_topology_tool_definitions,
    resolve_topology_tool_call,
    run_topology_tool,
)

logger = logging.getLogger(__name__)

#: Fixed allowlist. Unknown names are rejected before any provider is reached.
TOPOLOGY_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "get_topology_overview",
        "find_topology_node",
        "get_topology_node",
        "list_topology_dependencies",
        "get_job_detail",
        "get_component_detail",
        "get_field_lineage",
    }
)


def is_topology_tool(name: str) -> bool:
    """Whether `name` routes to this dispatcher rather than the runtime one."""
    return name in TOPOLOGY_TOOL_NAMES


def list_topology_tools() -> list[RuntimeToolDefinition]:
    """Tool definitions to merge into the model's catalogue."""
    return list_topology_tool_definitions()


async def execute_topology_tool(
    call: RuntimeToolCall,
    *,
    project_scope: ProjectScope,
    access_token: str | None,
) -> RuntimeToolResult:
    """Execute one topology tool call against the MCP-published artifacts."""
    t0 = time.perf_counter()

    try:
        spec, params = resolve_topology_tool_call(call.tool_name, call.arguments)
    except UnknownTopologyToolError:
        return _error_result(
            call.tool_name,
            RuntimeToolErrorCode.UNKNOWN_TOOL,
            f"Unknown tool: {call.tool_name}",
            t0,
        )
    except TopologyToolValidationError as exc:
        return _error_result(call.tool_name, RuntimeToolErrorCode.VALIDATION_ERROR, exc.message, t0)

    project_id = params.project_id
    if not _in_scope(project_id, project_scope):
        # Refused locally, before any HTTP call: forwarding the token alone would
        # mean a future refactor that drops the forward silently widens access.
        # The message matches the missing-artifact wording on purpose, so an
        # out-of-scope project cannot be used to probe which projects exist.
        logger.info("Topology tool %s refused: project out of scope", call.tool_name)
        return _error_result(
            call.tool_name,
            RuntimeToolErrorCode.UNAVAILABLE,
            f"No topology data is available for project '{project_id}'.",
            t0,
        )

    provider = provider_module.default_provider()
    try:
        payload = await asyncio.wait_for(
            run_topology_tool(spec, params, provider=provider, access_token=access_token),
            timeout=settings.runtime_tool_timeout,
        )
    except TimeoutError:
        logger.warning("Topology tool %s timed out", call.tool_name)
        return _error_result(
            call.tool_name, RuntimeToolErrorCode.TIMEOUT, f"{call.tool_name} timed out", t0
        )
    except TopologyProviderTimeoutError as exc:
        return _error_result(call.tool_name, RuntimeToolErrorCode.TIMEOUT, exc.safe_message, t0)
    except (
        TopologyArtifactUnavailableError,
        TopologyDesignUnavailableError,
        TopologyMappingUnavailableError,
    ) as exc:
        # Not failures: the project has nothing published yet, or the named design
        # or component is not in the scan, or that component publishes no deeper
        # graph. The model is expected to say which, not to apologise for an error
        # and certainly not to answer from another source instead. The tool layer
        # already handles the reachable cases; this is the safety net so a new
        # call path cannot turn one of them into an unhandled exception.
        return _error_result(call.tool_name, RuntimeToolErrorCode.UNAVAILABLE, exc.safe_message, t0)
    except TopologyPayloadError as exc:
        logger.warning("Topology tool %s got an unusable artifact", call.tool_name)
        return _error_result(
            call.tool_name, RuntimeToolErrorCode.PROVIDER_ERROR, exc.safe_message, t0
        )
    except TopologyProviderError as exc:
        logger.warning("Topology tool %s provider error", call.tool_name)
        return _error_result(
            call.tool_name, RuntimeToolErrorCode.PROVIDER_ERROR, exc.safe_message, t0
        )

    data, oversized = _bounded(payload.as_data())
    latency_ms = int((time.perf_counter() - t0) * 1000)
    logger.info("Topology tool %s succeeded (%dms)", call.tool_name, latency_ms)
    return RuntimeToolResult(
        tool_name=call.tool_name,
        success=True,
        data=data,
        error=None,
        latency_ms=latency_ms,
        truncated=payload.hit_cap or oversized,
    )


def _in_scope(project_id: str, project_scope: ProjectScope) -> bool:
    """None means unrestricted; [] means no access. Same semantics as the store."""
    return project_scope is None or project_id in project_scope


def _bounded(data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Backstop on serialized size, using the runtime tool boundary's ceiling.

    The per-tool caps already bound the projection structurally, so this only
    fires on a pathologically long single field. It degrades by dropping the
    collections and naming them, rather than returning a truncated JSON string:
    a half-serialized document is worse than a short honest summary.
    """
    serialized = json.dumps(data, default=str)
    if len(serialized) <= settings.runtime_tool_result_max_chars:
        return data, False

    content = data.get("content")
    if not isinstance(content, dict):
        content = {}
    dropped = [key for key, value in content.items() if isinstance(value, (list, dict))]
    reduced = {key: value for key, value in content.items() if key not in dropped}
    reduced["truncated"] = True
    reduced["omitted_fields"] = dropped
    return {"content": reduced, "targets": data.get("targets", [])[:3]}, True


def _error_result(
    tool_name: str, code: RuntimeToolErrorCode, message: str, t0: float
) -> RuntimeToolResult:
    return RuntimeToolResult(
        tool_name=tool_name,
        success=False,
        data=None,
        error=RuntimeToolError(code=code, message=message),
        latency_ms=int((time.perf_counter() - t0) * 1000),
        truncated=False,
    )
