"""Guarded capability executor for Nexus harness runtimes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field

from src.harness import CapabilityDisabledError, UnknownCapabilityError
from src.harness.models import (
    DOCUMENTATION_GENERATION_CAPABILITY,
    CapabilityDefinition,
    HarnessRiskTier,
)
from src.harness.providers.mcp_docs import (
    McpDocumentationProvider,
    McpDocumentationProviderError,
)
from src.harness.registry import CapabilityRegistry


class CapabilityExecutionContext(BaseModel):
    """Execution context enforced before any provider call."""

    user_id: int
    conversation_id: int | None = None
    project_scope: list[str] | None = None
    permissions: set[str] | None = None
    approvals: set[str] = Field(default_factory=set)
    access_token: str | None = None
    allow_writes_content: bool = False
    allow_external_side_effects: bool = False


class CapabilityExecutionRequest(BaseModel):
    capability_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None


class CapabilityObservation(BaseModel):
    """Sanitized result returned to the runtime."""

    capability_id: str
    status: str
    output_type: str | None = None
    summary: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    artifact_links: list[dict[str, Any]] = Field(default_factory=list)
    error_message: str | None = None
    safe_details: dict[str, Any] = Field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.status in {"succeeded", "triggered"}


class CapabilityExecutionDenied(Exception):
    """Raised when a capability request fails Nexus policy validation."""


class HarnessCapabilityExecutor:
    """Policy gate and provider dispatch for capability calls."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        *,
        docs_provider: McpDocumentationProvider | None = None,
    ) -> None:
        self._registry = registry
        self._docs_provider = docs_provider

    async def execute(
        self,
        request: CapabilityExecutionRequest,
        context: CapabilityExecutionContext,
    ) -> CapabilityObservation:
        try:
            capability = self._validate_request(request, context)
            return await self._dispatch(capability, request, context)
        except (CapabilityExecutionDenied, CapabilityDisabledError, UnknownCapabilityError) as exc:
            return CapabilityObservation(
                capability_id=request.capability_id,
                status="denied",
                summary=str(exc),
                error_message=str(exc),
                safe_details={"reason": exc.__class__.__name__},
            )
        except McpDocumentationProviderError as exc:
            return CapabilityObservation(
                capability_id=request.capability_id,
                status="failed",
                output_type="provider_error",
                summary=exc.safe_message,
                error_message=exc.safe_message,
                safe_details={"provider": "mcp.docs"},
            )

    def _validate_request(
        self,
        request: CapabilityExecutionRequest,
        context: CapabilityExecutionContext,
    ) -> CapabilityDefinition:
        capability = self._registry.require_available(request.capability_id)
        self._validate_permissions(capability, context)
        self._validate_risk(capability, context)
        self._validate_schema(capability, request.arguments)
        self._validate_project_scope(request.arguments, context)
        return capability

    def _validate_permissions(
        self,
        capability: CapabilityDefinition,
        context: CapabilityExecutionContext,
    ) -> None:
        if context.permissions is None:
            return
        required = set(capability.required_permissions)
        if required and not required.issubset(context.permissions):
            missing = ", ".join(sorted(required - context.permissions))
            raise CapabilityExecutionDenied(f"Missing required permission: {missing}")

    def _validate_risk(
        self,
        capability: CapabilityDefinition,
        context: CapabilityExecutionContext,
    ) -> None:
        if capability.risk_tier == HarnessRiskTier.WRITES_CONTENT:
            if not context.allow_writes_content and capability.id not in context.approvals:
                raise CapabilityExecutionDenied("Approval required before writing documentation content")
        if capability.risk_tier == HarnessRiskTier.EXTERNAL_SIDE_EFFECT:
            if not context.allow_external_side_effects and capability.id not in context.approvals:
                raise CapabilityExecutionDenied("Approval required before external side effects")

    def _validate_schema(
        self,
        capability: CapabilityDefinition,
        arguments: Mapping[str, Any],
    ) -> None:
        schema = capability.input_schema or {}
        for field_name in schema.get("required", []):
            if field_name not in arguments or arguments[field_name] is None:
                raise CapabilityExecutionDenied(f"Missing required input: {field_name}")

        properties = schema.get("properties", {})
        for field_name, value in arguments.items():
            expected = properties.get(field_name)
            if expected is None:
                continue
            if not _matches_json_schema_type(value, expected.get("type")):
                raise CapabilityExecutionDenied(f"Invalid input type for: {field_name}")

    def _validate_project_scope(
        self,
        arguments: Mapping[str, Any],
        context: CapabilityExecutionContext,
    ) -> None:
        project_id = arguments.get("project_id")
        if project_id is None or context.project_scope is None:
            return
        if str(project_id) not in context.project_scope:
            raise CapabilityExecutionDenied("Project access required")

    async def _dispatch(
        self,
        capability: CapabilityDefinition,
        request: CapabilityExecutionRequest,
        context: CapabilityExecutionContext,
    ) -> CapabilityObservation:
        if capability.id == DOCUMENTATION_GENERATION_CAPABILITY.id:
            provider = self._docs_provider or McpDocumentationProvider()
            result = await provider.trigger_generation(
                project_id=str(request.arguments["project_id"]),
                access_token=context.access_token,
                resume=bool(request.arguments.get("resume", True)),
                model=request.arguments.get("model"),
                write_model=request.arguments.get("write_model"),
            )
            return CapabilityObservation(
                capability_id=capability.id,
                status="triggered",
                output_type=capability.output_type,
                summary=result.message,
                data={
                    "project_id": result.project_id or request.arguments["project_id"],
                    "status": result.status,
                    "run_id": request.idempotency_key,
                },
                artifact_links=[
                    {"label": "Generation Status", "url": result.status_path, "kind": "status"},
                    {"label": "Project Documentation", "url": result.docs_path, "kind": "docs"},
                ],
                safe_details={"provider_type": capability.provider_type.value},
            )

        raise CapabilityExecutionDenied(f"No executor provider is registered for {capability.id}")


def _matches_json_schema_type(value: Any, expected_type: Any) -> bool:
    if expected_type is None:
        return True
    expected_types = expected_type if isinstance(expected_type, list) else [expected_type]
    for item in expected_types:
        if item == "null" and value is None:
            return True
        if item == "string" and isinstance(value, str):
            return True
        if item == "boolean" and isinstance(value, bool):
            return True
        if item == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
        if item == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
        if item == "array" and isinstance(value, list):
            return True
        if item == "object" and isinstance(value, dict):
            return True
    return False
