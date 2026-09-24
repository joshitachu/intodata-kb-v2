"""The generic runtime tool registry, exposed to the documentation generator.

`src/runtime/client.py` already routes 25 provider-neutral runtime tools, but
the only caller was `src/generation/agent_loop.py` -- an in-process call on the
chat turn. A documentation run happens in the MCP server, in another process,
so it could reach exactly two runtime facts (`GET /runtime/doc-evidence` and
`GET /runtime/elastic-aggregates`) and nothing else: no execution timeline, no
failure analysis, no schedule profile, no component hotspots. These two routes
are the transport that closes that gap, and they add no new capability -- the
allowlist, the provider resolution, the timeout, the result cap and the
credential redaction are all `execute_runtime_tool`'s, unchanged.

Same posture as the two machine endpoints it sits beside:

* **Same auth.** The MCP ingest key (`verify_machine_topology_ingest_api_key`),
  not a user session. No new credential.
* **Scope is pinned to the requested project.** There is no user behind this
  key, so there is no user scope to derive and nothing to intersect against;
  `ProjectScope` is built from `project_id` and from nothing else. This is the
  narrowest possible boundary rather than the unrestricted `None` that the
  in-process caller passes for a view-all user.
* **Scope NEVER comes from `arguments`.** `execute_runtime_tool` states this as
  an invariant in its own docstring, and the store enforces it structurally:
  `RuntimeStoreRepository._apply_scope` emits the `project_scope` predicate
  BEFORE any `project_id` / `project_ids` / `read_terms` carried on a tool's
  input model, and the two are ANDed, so an argument can only ever narrow the
  read. The body therefore carries `project_id` as a FIELD OF ITS OWN, outside
  `arguments`: whatever the generation agent puts in `arguments` is untrusted
  input to a filter, never to the boundary.

KNOWN LIMITATION -- no read-grant widening. A project that reads a SHARED
runtime mirror through `runtime_source_read_grants` gets evidence from
`GET /runtime/doc-evidence` (which resolves grants inline) but nothing from
these tools, so a single documentation run could describe two different
populations. The fix is NOT a second inline resolution here:
`src/runtime/read_scope.py` exists to end exactly that duplication, and applying
grants in this caller alone would recreate the drift it removed. It needs
`read_terms` threaded through `execute_runtime_tool` -> provider ->
`_apply_read_terms`, which closes the same gap for the in-process chat tools at
the same time. Harmless today (zero grant rows) and wrong the moment one exists.

A tool-level failure -- unknown name, bad arguments, provider error, timeout --
is a 200 carrying the registry's structured `RuntimeToolResult`
(`success: false` plus a typed `error.code`), not an HTTP error. The caller is
an agent that has to decide what to do next, and "this tool does not exist" is
an answer it can act on; a 500 is an outage it cannot tell apart from a broken
backend. HTTP status is reserved for the request envelope itself: a malformed
body or a missing `project_id` is a 422, and a bad key is a 401.

TRANSPORT, AND A GAP THE CALLER MUST HANDLE: `POST` is a state-changing method
to `CSRFMiddleware`, which is enabled by default (`CSRF_ENABLED=true` in the
production template and in Compose). `/api/v1/runtime/tools` is NOT in that
middleware's `_EXEMPT_PREFIXES`, so until it is, an mcp-server call must send a
matching double-submit pair (`Cookie: csrf_token=<v>` plus `X-CSRF-Token: <v>`)
or every tool call returns 403 -- the exact failure already recorded there for
`/api/v1/labs/mcp`. The two sibling machine endpoints never hit this only
because they are GETs. The right fix is the prefix exemption (this lane is
server-to-server, authenticated by the ingest key, and derives no identity from
any cookie, so there is no cross-site request to forge); it is deliberately not
made here because that middleware is shared security surface.

NOT DONE: unlike `GET /runtime/doc-evidence`, this lane does not widen scope
through stored read grants (`resolve_runtime_read_scope`), so a project that
reads a shared runtime mirror it does not own gets documentation evidence from
the doc-evidence route but nothing from these tools. That is the same gap
`src/runtime/read_scope.py` records for the agent tool lane, and closing it
belongs in one place for both callers rather than here.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, Field

from src.api.dependencies import DBSession
from src.api.routes.ingest import verify_machine_topology_ingest_api_key
from src.runtime.client import execute_runtime_tool, list_runtime_tools
from src.runtime.models import (
    RuntimeToolCall,
    RuntimeToolDefinition,
    RuntimeToolResult,
)
from src.runtime.store import ProjectScope

router = APIRouter(prefix="/runtime", tags=["runtime"])


class RuntimeToolListResponse(BaseModel):
    """Every tool the registry allows, with the JSON schema of its arguments.

    Served whole and unfiltered: the allowlist is closed, the definitions carry
    no project data, and an agent that cannot see a tool's input schema cannot
    call it correctly.
    """

    tools: list[RuntimeToolDefinition]


class RuntimeToolInvocation(BaseModel):
    """One tool call from a machine caller.

    `project_id` is deliberately a sibling of `arguments`, not a key inside it.
    The scope this request runs under is read from here and nowhere else, so the
    two can never be confused at a call site -- see the module docstring.
    """

    project_id: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


@router.get(
    "/tools",
    response_model=RuntimeToolListResponse,
    dependencies=[Depends(verify_machine_topology_ingest_api_key)],
    summary="List the runtime tools a documentation run may call",
)
async def list_runtime_tool_catalog() -> RuntimeToolListResponse:
    """Discovery for the generation agent: what can be asked, and with what.

    Static: the registry is a fixed allowlist, so the response depends on
    neither the project nor the configured provider. A tool listed here can
    still answer `unavailable` when the deployment has no runtime provider --
    that is a per-call fact, reported per call.
    """
    return RuntimeToolListResponse(tools=list_runtime_tools())


@router.post(
    "/tools/{tool_name}",
    response_model=RuntimeToolResult,
    dependencies=[Depends(verify_machine_topology_ingest_api_key)],
    summary="Execute one runtime tool for a project, as the documentation generator",
)
async def invoke_runtime_tool(
    db: DBSession,
    request: RuntimeToolInvocation,
    tool_name: str = Path(min_length=1),
) -> RuntimeToolResult:
    """Dispatch one call through the same router the chat agent uses.

    The result is returned verbatim: `success`, `data`, `error.code`,
    `latency_ms` and `truncated` are the registry's own envelope, already
    bounded by `runtime_tool_result_max_chars` and already credential-redacted,
    because it was built to be eligible for LLM context.
    """
    # The ONE place scope is decided, and it reads one field. `arguments` is
    # never consulted here; see the module docstring for why that is structural
    # rather than a convention.
    project_scope: ProjectScope = [request.project_id]
    return await execute_runtime_tool(
        RuntimeToolCall(tool_name=tool_name, arguments=request.arguments),
        project_scope=project_scope,
        db=db,
    )
