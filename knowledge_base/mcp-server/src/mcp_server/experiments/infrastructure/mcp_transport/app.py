"""FastMCP streamable-HTTP application factory for task-scoped tools."""

from __future__ import annotations

from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from mcp_server.experiments.infrastructure.mcp_transport.registry import (
    TaskServerRegistry,
    extract_bearer_token,
)


MCP_SERVER_NAME = "nexus-experiment-task"
MCP_STREAMABLE_HTTP_PATH = "/"


@dataclass(slots=True)
class ExperimentMcpTransport:
    """Mountable FastMCP server exposing only the seven task-native tools."""

    registry: TaskServerRegistry
    mcp: FastMCP = field(init=False)
    _lifecycle: AsyncExitStack | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.mcp = FastMCP(
            MCP_SERVER_NAME,
            json_response=True,
            stateless_http=True,
            streamable_http_path=MCP_STREAMABLE_HTTP_PATH,
        )
        self._register_tools()

    def asgi_app(self):
        """Return the Starlette Streamable HTTP application for FastAPI mounting."""

        return self.mcp.streamable_http_app()

    async def startup(self) -> None:
        """Optional parent-application lifecycle hook for a manually mounted app."""

        if self._lifecycle is not None:
            return
        self.asgi_app()
        lifecycle = AsyncExitStack()
        await lifecycle.enter_async_context(self.mcp.session_manager.run())
        self._lifecycle = lifecycle

    async def shutdown(self) -> None:
        """Optional parent-application lifecycle hook paired with ``startup``."""

        if self._lifecycle is None:
            return
        lifecycle, self._lifecycle = self._lifecycle, None
        await lifecycle.aclose()

    def invoke(
        self,
        *,
        authorization: str | None,
        tool_name: str,
        request: dict[str, Any],
        operation_id: str,
        now: object | None = None,
    ) -> dict[str, Any]:
        """Offline adapter seam used by transport tests; it has the HTTP auth rule."""

        token = extract_bearer_token({"authorization": authorization} if authorization is not None else None)
        return dict(
            self.registry.dispatch(
                bearer_token=token,
                tool_name=tool_name,
                request=request,
                operation_id=operation_id,
                now=now,
            )
        )

    def _register_tools(self) -> None:
        @self.mcp.tool(name="nexus_experiment_get_contract", description="Read the immutable contract for this task.")
        async def get_contract(operation_id: str, ctx: Context) -> dict[str, Any]:
            return self._from_context(ctx, "nexus_experiment_get_contract", {}, operation_id)

        @self.mcp.tool(name="nexus_experiment_list_evidence", description="List evidence authorized for this task.")
        async def list_evidence(operation_id: str, ctx: Context) -> dict[str, Any]:
            return self._from_context(ctx, "nexus_experiment_list_evidence", {}, operation_id)

        @self.mcp.tool(name="nexus_experiment_search_evidence", description="Search authorized immutable evidence.")
        async def search_evidence(query: str, operation_id: str, ctx: Context) -> dict[str, Any]:
            return self._from_context(ctx, "nexus_experiment_search_evidence", {"query": query}, operation_id)

        @self.mcp.tool(name="nexus_experiment_read_evidence", description="Read bounded bytes of authorized evidence.")
        async def read_evidence(evidence_id: str, max_bytes: int, operation_id: str, ctx: Context) -> dict[str, Any]:
            return self._from_context(
                ctx,
                "nexus_experiment_read_evidence",
                {"evidence_id": evidence_id, "max_bytes": max_bytes},
                operation_id,
            )

        @self.mcp.tool(name="nexus_experiment_traverse_relationships", description="Traverse authorized evidence relationships.")
        async def traverse_relationships(subject_id: str, operation_id: str, ctx: Context) -> dict[str, Any]:
            return self._from_context(
                ctx,
                "nexus_experiment_traverse_relationships",
                {"subject_id": subject_id},
                operation_id,
            )

        @self.mcp.tool(name="nexus_experiment_write_artifact", description="Write one declared task artifact exactly once.")
        async def write_artifact(
            logical_name: str,
            media_type: str,
            content: str,
            operation_id: str,
            ctx: Context,
        ) -> dict[str, Any]:
            return self._from_context(
                ctx,
                "nexus_experiment_write_artifact",
                {"logical_name": logical_name, "media_type": media_type, "content": content},
                operation_id,
            )

        @self.mcp.tool(name="nexus_experiment_submit_task", description="Submit a task after all declared artifacts exist.")
        async def submit_task(operation_id: str, ctx: Context) -> dict[str, Any]:
            return self._from_context(ctx, "nexus_experiment_submit_task", {}, operation_id)

    def _from_context(
        self,
        ctx: Context,
        tool_name: str,
        request: dict[str, Any],
        operation_id: str,
    ) -> dict[str, Any]:
        http_request = ctx.request_context.request
        headers = getattr(http_request, "headers", None)
        token = extract_bearer_token(headers)
        return dict(
            self.registry.dispatch(
                bearer_token=token,
                tool_name=tool_name,
                request=request,
                operation_id=operation_id,
            )
        )


def create_experiment_mcp_transport(registry: TaskServerRegistry) -> ExperimentMcpTransport:
    """Create a stateless JSON Streamable-HTTP transport for FastAPI mounting."""

    return ExperimentMcpTransport(registry=registry)
