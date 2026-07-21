"""Guarded capability executor for Nexus harness runtimes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from enum import StrEnum
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from src.harness import CapabilityDisabledError, UnknownCapabilityError
from src.harness.capability_runtime import (
    CapabilityExecutionContext,
    CapabilityExecutionRequest,
    CapabilityHandlerServices,
    CapabilityObservation,
    ConversationTaskConflictError,
    ConversationTaskExecutionError,
    ConversationTaskInputError,
)
from src.harness.conversation_state import (
    ConversationStateConflict,
    ConversationStateDenied,
)
from src.harness.models import CapabilityDefinition, HarnessRiskTier
from src.harness.providers.mcp_projects import McpProjectsProvider, McpProjectsProviderError
from src.harness.providers.mcp_docs import McpDocumentationProvider, McpDocumentationProviderError
from src.harness.packs.draft_service import (
    DraftConflictError,
    DraftNotFoundError,
    DraftServiceError,
    DraftValidationError,
)
from src.harness.registry import CapabilityRegistry, CapabilityRegistryError
from src.harness.connectors import ConnectorPolicyError, ConnectorService
from src.harness.connectors.repository import ConnectorNotFoundError


class CapabilityPolicyCode(StrEnum):
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"
    VALIDATION = "validation"
    PERMISSION = "permission"
    SCOPE = "scope"
    ALLOWLIST = "allowlist"
    APPROVAL = "approval"
    TURN_CLAIM = "turn_claim"
    TOOL_BUDGET = "tool_budget"
    COST_BUDGET = "cost_budget"
    HANDLER_FAILURE = "handler_failure"


class CapabilityExecutionDenied(Exception):
    """Raised when a capability request fails Nexus policy validation."""

    def __init__(
        self,
        code: CapabilityPolicyCode,
        *,
        field_path: str | None = None,
    ) -> None:
        super().__init__(code.value)
        self.code = code
        self.field_path = field_path


class CapabilityPolicyEngine:
    """Single deterministic gate executed before every capability handler."""

    def __init__(self, registry: CapabilityRegistry) -> None:
        self._registry = registry

    def validate(
        self,
        request: CapabilityExecutionRequest,
        context: CapabilityExecutionContext,
    ) -> CapabilityDefinition:
        capability = self._registry.require_available(request.capability_id)
        if context.remaining_tool_calls is not None and context.remaining_tool_calls < 1:
            raise CapabilityExecutionDenied(CapabilityPolicyCode.TOOL_BUDGET)
        estimated_cost = capability.metadata.get("estimated_cost_usd", 0)
        if (
            context.remaining_cost_usd is not None
            and isinstance(estimated_cost, (int, float))
            and float(estimated_cost) > context.remaining_cost_usd
        ):
            raise CapabilityExecutionDenied(CapabilityPolicyCode.COST_BUDGET)
        if request.capability_id in context.denied_capability_ids:
            raise CapabilityExecutionDenied(CapabilityPolicyCode.ALLOWLIST)
        if (
            context.allowed_capability_ids is not None
            and request.capability_id not in context.allowed_capability_ids
        ):
            raise CapabilityExecutionDenied(CapabilityPolicyCode.ALLOWLIST)
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
            raise CapabilityExecutionDenied(CapabilityPolicyCode.PERMISSION)

    def _validate_risk(
        self,
        capability: CapabilityDefinition,
        context: CapabilityExecutionContext,
    ) -> None:
        if (
            capability.metadata.get("requires_explicit_approval") is True
            and capability.id not in context.approvals
        ):
            raise CapabilityExecutionDenied(CapabilityPolicyCode.APPROVAL)
        if capability.risk_tier == HarnessRiskTier.WRITES_CONTENT:
            if not context.allow_writes_content and capability.id not in context.approvals:
                raise CapabilityExecutionDenied(CapabilityPolicyCode.APPROVAL)
        if capability.risk_tier == HarnessRiskTier.EXTERNAL_SIDE_EFFECT:
            if not context.allow_external_side_effects and capability.id not in context.approvals:
                raise CapabilityExecutionDenied(CapabilityPolicyCode.APPROVAL)
        if capability.metadata.get("requires_turn_claim") is True:
            if (
                context.current_message_id is None
                or context.conversation_state_version is None
                or context.conversation_turn_claim_id is None
                or context.conversation_turn_claim_token is None
            ):
                raise CapabilityExecutionDenied(CapabilityPolicyCode.TURN_CLAIM)

    def _validate_schema(
        self,
        capability: CapabilityDefinition,
        arguments: Mapping[str, Any],
    ) -> None:
        schema = capability.input_schema or {}
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        error = next(
            iter(
                sorted(
                    validator.iter_errors(dict(arguments)),
                    key=lambda item: tuple(str(part) for part in item.absolute_path),
                )
            ),
            None,
        )
        if error is not None:
            path = ".".join(str(part) for part in error.absolute_path) or "$"
            raise CapabilityExecutionDenied(
                CapabilityPolicyCode.VALIDATION,
                field_path=path,
            )

    def _validate_project_scope(
        self,
        arguments: Mapping[str, Any],
        context: CapabilityExecutionContext,
    ) -> None:
        selected = []
        project_id = arguments.get("project_id")
        if project_id is not None:
            selected.append(str(project_id))
        project_ids = arguments.get("project_ids")
        if project_ids is not None:
            if not isinstance(project_ids, list) or not all(
                isinstance(value, str) and value for value in project_ids
            ):
                raise CapabilityExecutionDenied(
                    CapabilityPolicyCode.VALIDATION,
                    field_path="project_ids",
                )
            selected.extend(project_ids)
        if not selected or context.project_scope is None:
            return
        if any(value not in context.project_scope for value in selected):
            raise CapabilityExecutionDenied(CapabilityPolicyCode.SCOPE)


class HarnessCapabilityExecutor:
    """Policy gate and registry/legacy adapter invocation for capability calls."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        *,
        docs_provider: McpDocumentationProvider | None = None,
        projects_provider: McpProjectsProvider | None = None,
        connector_service: ConnectorService | None = None,
        workflow_cancellation_controller: Any = None,
        pack_draft_service: Any = None,
        conversation_state_repository: Any = None,
        conversation_task_service: Any = None,
        evidence_handlers: Mapping[
            str,
            Callable[[dict[str, Any], CapabilityExecutionContext], Awaitable[Any]],
        ] | None = None,
    ) -> None:
        self._registry = registry
        self._docs_provider = docs_provider
        self._projects_provider = projects_provider
        self._connector_service = connector_service
        self._evidence_handlers = dict(evidence_handlers or {})
        self._workflow_cancellation_controller = workflow_cancellation_controller
        self._pack_draft_service = pack_draft_service
        self._conversation_state_repository = conversation_state_repository
        self._conversation_task_service = conversation_task_service
        self._policy = CapabilityPolicyEngine(registry)

    async def execute(
        self,
        request: CapabilityExecutionRequest,
        context: CapabilityExecutionContext,
    ) -> CapabilityObservation:
        try:
            capability = self._policy.validate(request, context)
            handler = self._registry.handler_for(capability.id)
            return await handler(
                capability,
                request,
                context,
                CapabilityHandlerServices(
                    docs_provider=self._docs_provider,
                    projects_provider=self._projects_provider,
                    connector_service=self._connector_service,
                    workflow_cancellation_controller=self._workflow_cancellation_controller,
                    evidence_handlers=self._evidence_handlers,
                    pack_draft_service=self._pack_draft_service,
                    conversation_state_repository=self._conversation_state_repository,
                    conversation_task_service=self._conversation_task_service,
                ),
            )
        except CapabilityExecutionDenied as exc:
            safe_details = {"reason": exc.code.value}
            if exc.field_path is not None:
                safe_details["field_path"] = exc.field_path
            return CapabilityObservation(
                capability_id=request.capability_id,
                status="denied",
                summary=_policy_summary(exc.code),
                error_message=_policy_summary(exc.code),
                error_code=exc.code.value,
                safe_details=safe_details,
            )
        except UnknownCapabilityError:
            return CapabilityObservation(
                capability_id=request.capability_id,
                status="denied",
                summary="Unknown capability.",
                error_message="Unknown capability.",
                error_code=CapabilityPolicyCode.UNKNOWN.value,
                safe_details={"reason": CapabilityPolicyCode.UNKNOWN.value},
            )
        except (CapabilityDisabledError, CapabilityRegistryError):
            return CapabilityObservation(
                capability_id=request.capability_id,
                status="denied",
                summary="The requested capability is unavailable.",
                error_message="The requested capability is unavailable.",
                error_code=CapabilityPolicyCode.UNAVAILABLE.value,
                safe_details={"reason": CapabilityPolicyCode.UNAVAILABLE.value},
            )
        except McpDocumentationProviderError as exc:
            return CapabilityObservation(
                capability_id=request.capability_id,
                status="failed",
                output_type="provider_error",
                summary=exc.safe_message,
                error_message=exc.safe_message,
                error_code=CapabilityPolicyCode.HANDLER_FAILURE.value,
                retryable=True,
                safe_details={"provider": "mcp.docs"},
            )
        except McpProjectsProviderError as exc:
            return CapabilityObservation(
                capability_id=request.capability_id,
                status="failed",
                output_type="provider_error",
                summary=exc.safe_message,
                error_message=exc.safe_message,
                error_code=CapabilityPolicyCode.HANDLER_FAILURE.value,
                retryable=True,
                safe_details={"provider_type": "project_source"},
            )
        except ConversationStateDenied as exc:
            return CapabilityObservation(
                capability_id=request.capability_id,
                status="denied",
                output_type="conversation_state_error",
                summary=str(exc),
                error_message=str(exc),
                error_code="conversation_state_denied",
                safe_details={"reason": "conversation_state_denied"},
            )
        except ConversationStateConflict as exc:
            return CapabilityObservation(
                capability_id=request.capability_id,
                status="failed",
                output_type="conversation_state_error",
                summary=str(exc),
                error_message=str(exc),
                error_code="conversation_state_conflict",
                retryable=True,
                safe_details={"reason": "conversation_state_conflict"},
            )
        except DraftServiceError as exc:
            if isinstance(exc, (DraftValidationError, DraftNotFoundError)):
                status = "denied"
            else:
                status = "failed"
            return CapabilityObservation(
                capability_id=request.capability_id,
                status=status,
                output_type="pack_draft_error",
                summary=str(exc),
                error_message=str(exc),
                error_code=(
                    "pack_draft_conflict"
                    if isinstance(exc, DraftConflictError)
                    else "pack_draft_invalid"
                ),
                retryable=isinstance(exc, DraftConflictError),
                safe_details={"reason": exc.__class__.__name__},
            )
        except ConversationTaskConflictError as exc:
            return CapabilityObservation(
                capability_id=request.capability_id,
                status="failed",
                output_type="conversation_task_error",
                summary=str(exc),
                error_message=str(exc),
                error_code="conversation_task_conflict",
                retryable=True,
                safe_details={"reason": "conversation_task_conflict"},
            )
        except ConversationTaskInputError as exc:
            return CapabilityObservation(
                capability_id=request.capability_id,
                status="denied",
                output_type="conversation_task_error",
                summary=str(exc),
                error_message=str(exc),
                error_code="conversation_task_invalid",
                safe_details={"reason": "conversation_task_invalid"},
            )
        except ConversationTaskExecutionError as exc:
            return CapabilityObservation(
                capability_id=request.capability_id,
                status="failed",
                output_type="conversation_task_error",
                summary=str(exc),
                error_message=str(exc),
                error_code="conversation_task_execution_failed",
                retryable=False,
                safe_details={"reason": "conversation_task_execution_failed"},
            )
        except (ConnectorPolicyError, ConnectorNotFoundError, KeyError, ValueError) as exc:
            return CapabilityObservation(
                capability_id=request.capability_id,
                status="denied",
                output_type="connector_error",
                summary="The connector is unavailable for this scope.",
                error_message="The connector is unavailable for this scope.",
                error_code=exc.__class__.__name__,
                safe_details={"reason": exc.__class__.__name__},
            )
        except RuntimeError:
            return CapabilityObservation(
                capability_id=request.capability_id,
                status="failed",
                output_type="handler_error",
                summary="The capability handler is not available for this request.",
                error_message="The capability handler is not available for this request.",
                error_code=CapabilityPolicyCode.HANDLER_FAILURE.value,
                retryable=False,
                safe_details={"reason": "handler_dependency_unavailable"},
            )

def _policy_summary(code: CapabilityPolicyCode) -> str:
    summaries = {
        CapabilityPolicyCode.VALIDATION: "The capability arguments did not match the contract.",
        CapabilityPolicyCode.PERMISSION: "Missing required permission.",
        CapabilityPolicyCode.SCOPE: "Project access required.",
        CapabilityPolicyCode.ALLOWLIST: "Capability is outside the turn evidence policy.",
        CapabilityPolicyCode.APPROVAL: "Approval required before this capability can execute.",
        CapabilityPolicyCode.TURN_CLAIM: "An authoritative turn claim is required.",
        CapabilityPolicyCode.TOOL_BUDGET: "The tool-call budget is exhausted.",
        CapabilityPolicyCode.COST_BUDGET: "The capability cost budget is exhausted.",
    }
    return summaries.get(code, "The capability request was denied.")


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
