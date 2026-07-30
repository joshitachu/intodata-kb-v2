from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from mcp_server.experiments.application.execute_task import TaskExecutionService
from mcp_server.experiments.application.preflight import (
    CredentialReferences,
    PaidExecutionDenied,
    PaidTaskContract,
    PaidTaskPreflight,
    PinnedOpenCodeRoute,
    RunReservation,
    TaskTokenServerBinding,
    VerifiedRuntimeIdentity,
)
from mcp_server.experiments.domain.runtime import (
    ArtifactReference,
    ModelCapability,
    OutputContract,
    RuntimeAvailability,
    RuntimeTaskEvent,
    RuntimeTaskStatus,
    RuntimeUsage,
)
from mcp_server.experiments.domain.sources import RedactionState
from mcp_server.experiments.domain.tasks import CompiledTask
from mcp_server.experiments.infrastructure.opencode.runtime import PINNED_OPENCODE_VERSION
from mcp_server.experiments.infrastructure.runtime.fake import FakeAgentRuntime, FakeRuntimeScenario
from mcp_server.experiments.infrastructure.validation.cost import parse_frozen_price_card
from mcp_server.experiments.mcp_tools.config_compiler import (
    OneTaskOpenCodeConfigInput,
    compile_one_task_opencode_config,
)


NOW = datetime(2026, 7, 27, tzinfo=timezone.utc)
OUTPUT = ArtifactReference("answer.md", "a" * 64, "text/markdown", 8)


class FakeTaskTokenServer:
    def __init__(self) -> None:
        self.bindings: list[dict[str, object]] = []

    def bind_task(self, **scope: object) -> TaskTokenServerBinding:
        self.bindings.append(scope)
        return TaskTokenServerBinding(
            mcp_server_url="http://nexus-internal/experiment-mcp/",
            task_token_ref=str(scope["task_token_ref"]),
            source_roles=scope["source_roles"],  # type: ignore[arg-type]
            subject_ids=scope["subject_ids"],  # type: ignore[arg-type]
            allowed_tools=scope["allowed_tools"],  # type: ignore[arg-type]
            reservation=scope["reservation"],  # type: ignore[arg-type]
            output_contract=scope["output_contract"],  # type: ignore[arg-type]
        )


class VerifiedFakeAgentRuntime(FakeAgentRuntime):
    """Offline fake that represents the preflight-pinned OpenCode identity."""

    runtime_id = "opencode"
    runtime_version = PINNED_OPENCODE_VERSION


class CorruptTaskTokenServer(FakeTaskTokenServer):
    def bind_task(self, **scope: object) -> TaskTokenServerBinding:
        binding = super().bind_task(**scope)
        return TaskTokenServerBinding(
            mcp_server_url=binding.mcp_server_url,
            task_token_ref=binding.task_token_ref,
            source_roles=binding.source_roles,
            subject_ids=("other-subject",),
            allowed_tools=binding.allowed_tools,
            reservation=binding.reservation,
            output_contract=binding.output_contract,
        )


def _price_card(*, zero: bool = False):
    rate = "0" if zero else "10"
    return parse_frozen_price_card(
        {
            "schema_version": "1",
            "price_card_id": "approved-unit-price-card",
            "version": "2026-07",
            "currency": "USD",
            "rates": [{
                "provider_id": "deepseek",
                "model_id": "deepseek-v4-pro",
                "input_usd_per_million": rate,
                "output_usd_per_million": rate,
                "cache_read_usd_per_million": rate,
                "cache_write_usd_per_million": rate,
            }],
        }
    )


def _identity(
    *,
    capabilities: frozenset[str] = frozenset({"tools"}),
    available: bool = True,
    availability: RuntimeAvailability = RuntimeAvailability.AVAILABLE,
):
    return VerifiedRuntimeIdentity.from_capabilities(
        runtime_id="opencode",
        runtime_version=PINNED_OPENCODE_VERSION,
        checked_at=NOW,
        availability=availability,
        models=(ModelCapability("deepseek", "deepseek-v4-pro", capabilities, available),),
    )


def _reservation(*, input_tokens: int = 100) -> RunReservation:
    return RunReservation(
        max_input_tokens=input_tokens,
        max_output_tokens=100,
        max_elapsed_ms=1000,
        max_cost_usd=Decimal("1.00"),
        max_tool_calls=5,
        max_tool_read_bytes=512,
        max_tool_result_bytes=512,
        max_tool_write_bytes=512,
    )


def _contract(**overrides: object) -> PaidTaskContract:
    tools = ("nexus_experiment_write_artifact", "nexus_experiment_submit_task")
    values: dict[str, object] = {
        "project_id": "project-a",
        "experiment_id": "experiment-a",
        "task": CompiledTask(
            node_id="task-a", task_type="document", role="implementation", dependencies=(),
            output_ids=("answer.md",), semantic_prompt="Write the bounded answer from immutable evidence.",
        ),
        "output_contract": OutputContract("answer.md", "text/markdown", max_bytes=1024),
        "context_refs": (),
        "source_roles": ("implementation",),
        "subject_ids": ("subject-a",),
        "recipe_allowed_tools": tools,
        "allowed_tools": tools,
        "route": PinnedOpenCodeRoute("opencode", PINNED_OPENCODE_VERSION, "deepseek", "deepseek-v4-pro"),
        "credentials": CredentialReferences(
            task_token_ref="task-token-ref-a",
            opencode_basic_password_ref="opencode-basic-password-ref-a",
            provider_credential_ref="provider-credential-ref-a",
        ),
        "runtime_identity": _identity(),
        "price_card": _price_card(),
        "reservation": _reservation(),
        "deadline_at": NOW + timedelta(minutes=10),
        "expected_mcp_server_url": "http://nexus-internal/experiment-mcp/",
        "max_runtime_identity_age": timedelta(minutes=1),
        "experiment_enabled": True,
        "paid_execution_enabled": True,
        "redaction_state": RedactionState.APPROVED,
        "security_approved": True,
    }
    values.update(overrides)
    return PaidTaskContract(**values)  # type: ignore[arg-type]


def _service(runtime: FakeAgentRuntime, binding: FakeTaskTokenServer) -> TaskExecutionService:
    return TaskExecutionService(
        runtime=runtime,
        preflight=PaidTaskPreflight(task_token_server=binding, now=lambda: NOW),
    )


def _success_scenario(**overrides: object) -> FakeRuntimeScenario:
    values: dict[str, object] = {
        "output_refs": (OUTPUT,),
        "usage": RuntimeUsage(input_tokens=10, output_tokens=5, cache_read_tokens=0, cache_write_tokens=0),
        "elapsed_ms": 25,
    }
    values.update(overrides)
    return FakeRuntimeScenario(**values)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_paid_execution_binds_full_scope_compiles_config_and_normalizes_fake_runtime():
    binding = FakeTaskTokenServer()
    runtime = VerifiedFakeAgentRuntime(scenarios={"experiment-a:task-a:execution-a": _success_scenario()})
    contract = _contract()
    executed = await _service(runtime, binding).execute(contract=contract, execution_id="execution-a")

    assert len(runtime.started_requests) == 1
    assert binding.bindings[0]["source_roles"] == ("implementation",)
    assert binding.bindings[0]["subject_ids"] == ("subject-a",)
    assert binding.bindings[0]["reservation"] == contract.reservation
    assert binding.bindings[0]["output_contract"] == contract.output_contract
    expected_config = compile_one_task_opencode_config(
        OneTaskOpenCodeConfigInput("http://nexus-internal/experiment-mcp/", contract.allowed_tools)
    )
    assert executed.preflight.opencode_config_sha256 == expected_config.sha256
    assert executed.preflight.runtime_capability_set_sha256 == contract.runtime_identity.capability_set_sha256
    assert [event.sequence for event in executed.events] == [1, 2]
    assert executed.usage.normalized_cost_usd == Decimal("0.00015")
    assert executed.usage.billed_cost_known is False
    assert executed.usage.billed_cost_usd is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"experiment_enabled": False}, "experiment is not enabled"),
        ({"paid_execution_enabled": False}, "paid execution is not enabled"),
        ({"route": PinnedOpenCodeRoute("opencode", "1.0", "deepseek", "deepseek-v4-pro")}, "exact pinned"),
        ({"credentials": CredentialReferences("same", "same", "provider-credential-ref-a")}, "must be distinct"),
        ({"redaction_state": RedactionState.REJECTED}, "approved redaction"),
        ({"price_card": _price_card(zero=True)}, "nonzero frozen price"),
        ({"reservation": _reservation(input_tokens=0)}, "positive integer"),
        ({"allowed_tools": ("nexus_experiment_write_artifact",)}, "exactly match"),
        ({"deadline_at": datetime(2026, 7, 27, 1)}, "UTC-aware"),
        ({"runtime_identity": _identity(capabilities=frozenset())}, "capability mismatch"),
        ({"runtime_identity": _identity(available=False)}, "unavailable"),
        ({"runtime_identity": _identity(availability=RuntimeAvailability.DEGRADED)}, "unavailable or degraded"),
        ({"max_runtime_identity_age": timedelta()}, "maximum age must be positive"),
        ({"runtime_identity": replace(_identity(), checked_at=NOW - timedelta(minutes=2))}, "discovery is stale"),
        ({"required_capabilities": frozenset()}, "requires the tools runtime capability"),
        ({"reservation": replace(_reservation(), max_input_tokens=True)}, "positive integer"),
        ({"reservation": replace(_reservation(), max_cost_usd=Decimal("NaN"))}, "finite positive Decimal"),
        ({"reservation": replace(_reservation(), max_cost_usd=1.0)}, "finite positive Decimal"),
    ],
)
async def test_paid_run_guard_fails_before_task_binding_or_fake_runtime_starts(override, message):
    binding = FakeTaskTokenServer()
    runtime = VerifiedFakeAgentRuntime()
    with pytest.raises(PaidExecutionDenied, match=message):
        await _service(runtime, binding).execute(contract=_contract(**override), execution_id="blocked")

    assert runtime.started_requests == []
    assert binding.bindings == []


@pytest.mark.asyncio
async def test_token_server_scope_echo_must_match_before_runtime_start():
    binding = CorruptTaskTokenServer()
    runtime = VerifiedFakeAgentRuntime()

    with pytest.raises(PaidExecutionDenied, match="complete frozen task scope"):
        await _service(runtime, binding).execute(contract=_contract(), execution_id="bad-scope")

    assert len(binding.bindings) == 1
    assert runtime.started_requests == []


@pytest.mark.asyncio
async def test_token_server_url_must_equal_the_pinned_internal_mcp_url_before_runtime_start():
    binding = FakeTaskTokenServer()
    runtime = VerifiedFakeAgentRuntime()
    with pytest.raises(PaidExecutionDenied, match="complete frozen task scope"):
        await _service(runtime, binding).execute(
            contract=_contract(expected_mcp_server_url="http://nexus-internal/another-mcp/"),
            execution_id="bad-url",
        )
    assert len(binding.bindings) == 1
    assert runtime.started_requests == []


@pytest.mark.asyncio
async def test_execution_records_terminal_failure_but_rejects_no_retry_and_billed_cost_overage():
    failed = VerifiedFakeAgentRuntime(
        scenarios={"experiment-a:task-a:failed": _success_scenario(outcome=RuntimeTaskStatus.FAILED)}
    )
    executed = await _service(failed, FakeTaskTokenServer()).execute(contract=_contract(), execution_id="failed")
    assert executed.result.status == RuntimeTaskStatus.FAILED
    assert executed.usage.reservation_state == "normalized_unknown_billing"
    assert executed.usage.billed_cost_known is False

    incomplete = VerifiedFakeAgentRuntime(
        scenarios={"experiment-a:task-a:incomplete": _success_scenario(outcome=RuntimeTaskStatus.FAILED, usage=RuntimeUsage())}
    )
    incomplete_result = await _service(incomplete, FakeTaskTokenServer()).execute(
        contract=_contract(), execution_id="incomplete"
    )
    assert incomplete_result.result.status == RuntimeTaskStatus.FAILED
    assert incomplete_result.usage.reservation_state == "ambiguous_incomplete_terminal_usage"
    assert incomplete_result.usage.billed_cost_usd is None
    assert incomplete_result.usage.billed_cost_known is False

    retrying = VerifiedFakeAgentRuntime(
        scenarios={"experiment-a:task-a:retry": _success_scenario(retry_count=1)}
    )
    with pytest.raises(PaidExecutionDenied, match="silent runtime retries"):
        await _service(retrying, FakeTaskTokenServer()).execute(contract=_contract(), execution_id="retry")

    billed = VerifiedFakeAgentRuntime(
        scenarios={"experiment-a:task-a:billed": _success_scenario(usage=RuntimeUsage(1, 1, 0, 0, billed_cost_usd=2.0))}
    )
    with pytest.raises(PaidExecutionDenied, match="billed cost"):
        await _service(billed, FakeTaskTokenServer()).execute(contract=_contract(), execution_id="billed")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "message"),
    [
        (_success_scenario(output_refs=()), "missing its declared required output"),
        (_success_scenario(output_refs=(ArtifactReference("answer.md", "bad", "text/markdown", 8),)), "invalid declared artifact shape"),
        (_success_scenario(usage=RuntimeUsage(-1, 1, 0, 0)), "input token usage"),
        (_success_scenario(elapsed_ms=-1), "runtime elapsed time"),
    ],
)
async def test_execution_rejects_invalid_outputs_and_negative_usage_or_timing(scenario, message):
    runtime = VerifiedFakeAgentRuntime(scenarios={"experiment-a:task-a:bad-result": scenario})
    with pytest.raises(PaidExecutionDenied, match=message):
        await _service(runtime, FakeTaskTokenServer()).execute(contract=_contract(), execution_id="bad-result")


@pytest.mark.asyncio
async def test_execution_rejects_nonterminal_or_wrong_terminal_event_scope_and_normalizes_cancel():
    nonterminal = VerifiedFakeAgentRuntime(
        scenarios={"experiment-a:task-a:running": _success_scenario(outcome=RuntimeTaskStatus.RUNNING)}
    )
    with pytest.raises(PaidExecutionDenied, match="nonterminal"):
        await _service(nonterminal, FakeTaskTokenServer()).execute(contract=_contract(), execution_id="running")

    wrong_event = VerifiedFakeAgentRuntime(
        scenarios={
            "experiment-a:task-a:wrong-event": _success_scenario(
                outcome=RuntimeTaskStatus.FAILED,
                failure_detail={"task_id": "other-task"},
            )
        }
    )
    with pytest.raises(PaidExecutionDenied, match="event task identity"):
        await _service(wrong_event, FakeTaskTokenServer()).execute(contract=_contract(), execution_id="wrong-event")

    cancellable = VerifiedFakeAgentRuntime(scenarios={"experiment-a:task-a:cancel": _success_scenario()})
    service = _service(cancellable, FakeTaskTokenServer())
    executed = await service.execute(contract=_contract(), execution_id="cancel")
    cancelled = await service.cancel(executed=executed, reason="operator-request")
    assert cancelled.accepted is True
    assert cancelled.status == "cancelled"


def test_event_contract_rejects_empty_or_noncontiguous_sequences():
    from mcp_server.experiments.application.execute_task import _assert_events

    receipt = PaidTaskPreflight(task_token_server=FakeTaskTokenServer(), now=lambda: NOW).prepare(
        contract=_contract(), execution_id="event-contract"
    ).receipt
    with pytest.raises(PaidExecutionDenied, match="non-empty"):
        _assert_events((), receipt, RuntimeTaskStatus.SUCCEEDED)
    event = RuntimeTaskEvent(
        event_id="event-2",
        sequence=2,
        category="lifecycle",
        occurred_at=NOW,
        task_status=RuntimeTaskStatus.SUCCEEDED,
    )
    with pytest.raises(PaidExecutionDenied, match="contiguous"):
        _assert_events((event,), receipt, RuntimeTaskStatus.SUCCEEDED)

    first = RuntimeTaskEvent(
        event_id="duplicate", sequence=1, category="lifecycle", occurred_at=NOW,
        task_status=RuntimeTaskStatus.SUCCEEDED,
    )
    second = RuntimeTaskEvent(
        event_id="duplicate", sequence=2, category="lifecycle", occurred_at=NOW,
        task_status=RuntimeTaskStatus.SUCCEEDED,
    )
    with pytest.raises(PaidExecutionDenied, match="unique and non-blank"):
        _assert_events((first, second), receipt, RuntimeTaskStatus.SUCCEEDED)
