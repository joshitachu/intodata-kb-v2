"""Task-token routing for the experiment MCP HTTP boundary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from mcp_server.experiments.mcp_tools.auth import TaskTokenClaims, TaskTokenError, TaskTokenVerifier
from mcp_server.experiments.mcp_tools.server import ExperimentTaskToolServer, ToolInvocationDenied


class TransportAuthorizationDenied(PermissionError):
    """An unauthenticated or unregistered task MCP transport invocation."""


TaskServerKey = tuple[str, str, str]


def extract_bearer_token(headers: Mapping[str, str] | None) -> str:
    """Return a bearer value without recording it anywhere in transport state."""

    authorization = headers.get("authorization") if headers is not None else None
    if not isinstance(authorization, str):
        raise TransportAuthorizationDenied("missing bearer authorization")
    parts = authorization.split()
    if len(parts) != 2 or parts[0].casefold() != "bearer" or not parts[1]:
        raise TransportAuthorizationDenied("malformed bearer authorization")
    return parts[1]


@dataclass(slots=True)
class TaskServerRegistry:
    """Routes a verified task claim to exactly one in-process task server.

    The registry verifier authenticates the untrusted HTTP token before lookup.
    The selected task server repeats scoped verification before performing its
    operation, keeping the transport routing decision independent of tool-level
    authorization.
    """

    verifier: TaskTokenVerifier
    _servers: dict[TaskServerKey, ExperimentTaskToolServer] = field(default_factory=dict)

    def register(self, server: ExperimentTaskToolServer) -> None:
        key = _server_key(server)
        if key in self._servers:
            raise ValueError("a task MCP server is already registered for this task")
        self._servers[key] = server

    def unregister(self, *, project_id: str, experiment_id: str, task_id: str) -> None:
        self._servers.pop((project_id, experiment_id, task_id), None)

    def dispatch(
        self,
        *,
        bearer_token: str,
        tool_name: str,
        request: Mapping[str, Any],
        operation_id: str,
        now: object | None = None,
    ) -> Mapping[str, Any]:
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise TransportAuthorizationDenied("operation_id is required")
        try:
            claims = self.verifier.verify(bearer_token, now=now)
        except TaskTokenError as exc:
            raise TransportAuthorizationDenied(str(exc)) from exc
        server = self._servers.get(_claims_key(claims))
        if server is None:
            raise TransportAuthorizationDenied("task token has no registered task server")
        try:
            return server.invoke(
                bearer_token=bearer_token,
                tool_name=tool_name,
                request=request,
                operation_id=operation_id,
                now=now,
            )
        except ToolInvocationDenied as exc:
            raise TransportAuthorizationDenied(str(exc)) from exc


def _claims_key(claims: TaskTokenClaims) -> TaskServerKey:
    return (claims.project_id, claims.experiment_id, claims.task_id)


def _server_key(server: ExperimentTaskToolServer) -> TaskServerKey:
    context = server.context
    return (context.project_id, context.experiment_id, context.task_id)
