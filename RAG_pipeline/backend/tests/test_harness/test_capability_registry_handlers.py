from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.harness.capability_handlers import (
    BoundConnectorEvidenceHandler,
    WorkflowCancellationHandler,
)
from src.harness.capability_runtime import (
    CapabilityHandlerServices,
    CapabilityObservation,
)
from src.harness.connectors.models import ConnectorReadResult, ConnectorResultItem
from src.harness.executor import (
    CapabilityExecutionContext,
    CapabilityExecutionRequest,
    HarnessCapabilityExecutor,
)
from src.harness.models import (
    DOCUMENTATION_GENERATION_CAPABILITY,
    DOCUMENTATION_GENERATION_STATUS_CAPABILITY,
    PROJECT_ACTIVE_WORKFLOW_STATUS_CAPABILITY,
    WORKFLOW_CANCELLATION_CAPABILITY,
    CapabilityDefinition,
    HarnessProviderType,
    HarnessRiskTier,
)
from src.harness.registry import (
    CapabilityCatalog,
    CapabilityRegistry,
    CapabilityRegistryError,
    base_capability_registry,
    default_capability_registry,
)
from src.harness.workflow_control import (
    WorkflowCancellationOutcome,
    WorkflowCancellationResult,
)


class RecordingHandler:
    def __init__(self, capability: CapabilityDefinition) -> None:
        self.capability_id = capability.id
        self.input_schema = capability.input_schema
        self.output_type = capability.output_type
        self.task_types = tuple(capability.task_types)
        self.calls = 0

    async def __call__(self, capability, request, context, services):  # noqa: ANN001
        del request, context, services
        self.calls += 1
        return CapabilityObservation(
            capability_id=capability.id,
            status="succeeded",
            output_type=capability.output_type,
            summary="registered handler ran",
        )


def _read_capability(capability_id: str = "test.read") -> CapabilityDefinition:
    return CapabilityDefinition(
        id=capability_id,
        label="Read safely",
        provider_type=HarnessProviderType.SOURCE,
        provider_id="test.provider",
        action="read",
        input_schema={
            "type": "object",
            "required": ["project_id"],
            "properties": {"project_id": {"type": "string"}},
            "additionalProperties": False,
        },
        output_type="safe_read",
        risk_tier=HarnessRiskTier.READ_ONLY,
    )


def test_production_registry_classifies_migrated_capabilities_as_handlers() -> None:
    expected = {
        DOCUMENTATION_GENERATION_CAPABILITY.id,
        DOCUMENTATION_GENERATION_STATUS_CAPABILITY.id,
        WORKFLOW_CANCELLATION_CAPABILITY.id,
        PROJECT_ACTIVE_WORKFLOW_STATUS_CAPABILITY.id,
    }
    for registry in (base_capability_registry(), default_capability_registry()):
        for capability_id in expected:
            assert registry.handler_for(capability_id).capability_id == capability_id


def test_required_handler_omission_fails_registry_construction() -> None:
    with pytest.raises(CapabilityRegistryError, match="Missing registered handler"):
        CapabilityRegistry(
            [DOCUMENTATION_GENERATION_CAPABILITY],
            handlers=[],
        )


def test_definition_only_catalog_is_an_explicit_non_executable_boundary() -> None:
    capability = _read_capability()
    catalog = CapabilityCatalog([capability])

    assert catalog.get(capability.id) == capability
    assert not hasattr(catalog, "handler_for")


def test_duplicate_or_mismatched_registry_entries_fail_closed() -> None:
    capability = _read_capability()
    with pytest.raises(CapabilityRegistryError, match="Duplicate capability definition"):
        CapabilityRegistry([capability, capability])

    handler = RecordingHandler(capability)
    handler.output_type = "wrong_output"
    with pytest.raises(CapabilityRegistryError, match="output contract mismatch"):
        CapabilityRegistry(
            [capability],
            handlers=[handler],
        )

    schema_handler = RecordingHandler(capability)
    schema_handler.input_schema = {"type": "object", "properties": {}}
    with pytest.raises(CapabilityRegistryError, match="input schema mismatch"):
        CapabilityRegistry(
            [capability],
            handlers=[schema_handler],
        )

    orphan_handler = RecordingHandler(capability)
    with pytest.raises(CapabilityRegistryError, match="no capability definition"):
        CapabilityRegistry(
            [],
            handlers=[orphan_handler],
        )


async def test_policy_denial_and_budget_exhaustion_run_before_registered_handler() -> None:
    capability = _read_capability().model_copy(
        update={"metadata": {"estimated_cost_usd": 0.25}}
    )
    handler = RecordingHandler(capability)
    registry = CapabilityRegistry(
        [capability],
        handlers=[handler],
    )
    executor = HarnessCapabilityExecutor(registry)
    request = CapabilityExecutionRequest(
        capability_id=capability.id,
        arguments={"project_id": "project-b"},
    )

    denied_scope = await executor.execute(
        request,
        CapabilityExecutionContext(user_id=1, project_scope=["project-a"]),
    )
    denied_budget = await executor.execute(
        request,
        CapabilityExecutionContext(
            user_id=1,
            project_scope=["project-b"],
            remaining_tool_calls=0,
        ),
    )
    denied_cost = await executor.execute(
        request,
        CapabilityExecutionContext(
            user_id=1,
            project_scope=["project-b"],
            remaining_tool_calls=1,
            remaining_cost_usd=0.10,
        ),
    )

    assert denied_scope.status == "denied"
    assert denied_budget.status == "denied"
    assert denied_cost.status == "denied"
    assert handler.calls == 0

    succeeded = await executor.execute(
        request,
        CapabilityExecutionContext(
            user_id=1,
            project_scope=["project-b"],
            remaining_tool_calls=1,
        ),
    )
    assert succeeded.status == "succeeded"
    assert handler.calls == 1


def test_manifest_only_capability_cannot_be_constructed_as_executable() -> None:
    capability = _read_capability()
    with pytest.raises(CapabilityRegistryError, match="Missing registered handler"):
        CapabilityRegistry(
            [capability],
        )


async def test_bound_connector_handler_preserves_safe_provenance_without_dispatch() -> None:
    capability = _read_capability("connector.instance_7.search_records").model_copy(
        update={
            "input_schema": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string"},
                    "project_id": {"type": ["string", "null"]},
                    "limit": {"type": "integer"},
                },
                "additionalProperties": False,
            },
            "output_type": "evidence_items",
            "metadata": {
                "connector_instance_id": 7,
                "connector_capability_id": "search.records",
            },
        }
    )

    class ConnectorService:
        async def read(self, *args):
            assert args[:4] == (7, "search.records", "project-a", "security")
            return ConnectorReadResult(
                items=[
                    ConnectorResultItem(
                        source_ref="https://evidence.example/items/17?view=full",
                        title="Finding 17",
                        excerpt="A bounded evidence excerpt.",
                        metadata={"document_id": "17", "api_key": "[REDACTED]"},
                    )
                ]
            )

    observation = await BoundConnectorEvidenceHandler(capability)(
        capability,
        CapabilityExecutionRequest(
            capability_id=capability.id,
            arguments={
                "query": "finding",
                "project_id": "project-a",
                "limit": 3,
            },
        ),
        CapabilityExecutionContext(
            user_id=1,
            project_scope=["project-a"],
            domain="security",
        ),
        CapabilityHandlerServices(connector_service=ConnectorService()),
    )

    assert observation.status == "succeeded"
    assert observation.data["items"][0]["id"] == (
        "https://evidence.example/items/17?view=full"
    )
    assert observation.data["items"][0]["uri"] == (
        "https://evidence.example/items/17"
    )
    assert observation.evidence_refs == [
        {
            "evidence_id": "https://evidence.example/items/17?view=full",
            "source_type": "connector",
            "uri": "https://evidence.example/items/17",
        }
    ]


@dataclass
class CancellationController:
    result: WorkflowCancellationResult
    calls: int = 0

    async def cancel(self, **_kwargs):
        self.calls += 1
        return self.result


@pytest.mark.parametrize(
    ("outcome", "expected_status", "retryable"),
    [
        (WorkflowCancellationOutcome.CANCELLED, "succeeded", False),
        (WorkflowCancellationOutcome.ALREADY_FAILED, "succeeded", False),
        (WorkflowCancellationOutcome.NO_MATCH, "denied", False),
        (WorkflowCancellationOutcome.PROVIDER_UNAVAILABLE, "failed", True),
        (WorkflowCancellationOutcome.CONFLICT, "failed", True),
    ],
)
async def test_cancellation_handler_returns_reasonable_typed_outcomes(
    outcome: WorkflowCancellationOutcome,
    expected_status: str,
    retryable: bool,
) -> None:
    handler = WorkflowCancellationHandler()
    registry = CapabilityRegistry(
        [WORKFLOW_CANCELLATION_CAPABILITY],
        handlers=[handler],
    )
    executor = HarnessCapabilityExecutor(
        registry,
        workflow_cancellation_controller=CancellationController(
            WorkflowCancellationResult(outcome=outcome, message="resolved")
        ),
    )

    observation = await executor.execute(
        CapabilityExecutionRequest(
            capability_id=WORKFLOW_CANCELLATION_CAPABILITY.id,
            arguments={"workflow_reference": None},
        ),
        CapabilityExecutionContext(
            user_id=1,
            conversation_id=2,
            approvals={WORKFLOW_CANCELLATION_CAPABILITY.id},
        ),
    )

    assert observation.status == expected_status
    assert observation.retryable is retryable
    assert observation.data == {
        "outcome": outcome.value,
        "workflow_run_id": None,
        "project_id": None,
        "workflow_status": None,
    }
    assert "runtime_metadata" not in observation.model_dump_json()


async def test_missing_handler_dependency_returns_safe_observation() -> None:
    registry = CapabilityRegistry(
        [WORKFLOW_CANCELLATION_CAPABILITY],
        handlers=[WorkflowCancellationHandler()],
    )
    observation = await HarnessCapabilityExecutor(registry).execute(
        CapabilityExecutionRequest(
            capability_id=WORKFLOW_CANCELLATION_CAPABILITY.id,
            arguments={},
        ),
        CapabilityExecutionContext(
            user_id=1,
            conversation_id=2,
            approvals={WORKFLOW_CANCELLATION_CAPABILITY.id},
        ),
    )

    assert observation.status == "failed"
    assert observation.error_code == "capability_handler_unavailable"
    assert observation.safe_details == {"reason": "handler_dependency_unavailable"}


async def test_cancellation_policy_denial_prevents_controller_call() -> None:
    controller = CancellationController(
        WorkflowCancellationResult(
            outcome=WorkflowCancellationOutcome.CANCELLED,
            message="cancelled",
        )
    )
    registry = CapabilityRegistry(
        [WORKFLOW_CANCELLATION_CAPABILITY],
        handlers=[WorkflowCancellationHandler()],
    )
    observation = await HarnessCapabilityExecutor(
        registry,
        workflow_cancellation_controller=controller,
    ).execute(
        CapabilityExecutionRequest(
            capability_id=WORKFLOW_CANCELLATION_CAPABILITY.id,
            arguments={},
        ),
        CapabilityExecutionContext(user_id=1, conversation_id=2),
    )

    assert observation.status == "denied"
    assert "Approval required" in (observation.error_message or "")
    assert controller.calls == 0
