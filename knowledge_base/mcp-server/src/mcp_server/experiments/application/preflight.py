"""Fail-closed, receipt-producing preflight for paid experiment task runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
from typing import Callable, Protocol

from mcp_server.experiments.domain.evaluation import PriceCardReceipt
from mcp_server.experiments.domain.runtime import (
    AgentRuntime,
    ArtifactReference,
    ModelCapability,
    OutputContract,
    RuntimeAvailability,
    RuntimeBudgets,
    RuntimeTaskRequest,
)
from mcp_server.experiments.domain.sources import RedactionState
from mcp_server.experiments.domain.tasks import CompiledTask
from mcp_server.experiments.infrastructure.opencode.runtime import PINNED_OPENCODE_VERSION
from mcp_server.experiments.infrastructure.storage.canonical_json import canonical_json_bytes
from mcp_server.experiments.infrastructure.validation.cost import FrozenPriceCard
from mcp_server.experiments.mcp_tools.config_compiler import (
    NEXUS_EXPERIMENT_TOOLS,
    OneTaskOpenCodeConfigInput,
    compile_one_task_opencode_config,
)


PAID_RUNTIME_ID = "opencode"


class PaidExecutionDenied(PermissionError):
    """A paid run cannot start unless every frozen prerequisite is present."""


@dataclass(frozen=True, slots=True)
class PinnedOpenCodeRoute:
    runtime_id: str
    runtime_version: str
    provider_id: str
    model_id: str


@dataclass(frozen=True, slots=True)
class CredentialReferences:
    """Distinct reference names only; no credential values enter this boundary."""

    task_token_ref: str
    opencode_basic_password_ref: str
    provider_credential_ref: str


@dataclass(frozen=True, slots=True)
class RunReservation:
    max_input_tokens: int
    max_output_tokens: int
    max_elapsed_ms: int
    max_cost_usd: Decimal
    max_tool_calls: int
    max_tool_read_bytes: int
    max_tool_result_bytes: int
    max_tool_write_bytes: int


@dataclass(frozen=True, slots=True)
class VerifiedModelRoute:
    provider_id: str
    model_id: str
    capabilities: frozenset[str]
    available: bool
    capability_sha256: str


@dataclass(frozen=True, slots=True)
class VerifiedRuntimeIdentity:
    """Immutable runtime discovery evidence, captured outside this use case."""

    runtime_id: str
    runtime_version: str
    availability: RuntimeAvailability
    checked_at: datetime
    capability_set_sha256: str
    models: tuple[VerifiedModelRoute, ...]

    @classmethod
    async def discover(cls, runtime: AgentRuntime) -> "VerifiedRuntimeIdentity":
        """Capture health and provider/model discovery from an injected runtime port."""

        health = await runtime.health()
        capabilities = await runtime.capabilities()
        _assert_utc(health.checked_at, "runtime health checked_at")
        _assert_utc(capabilities.checked_at, "runtime capabilities checked_at")
        if (
            health.runtime_id != capabilities.runtime_id
            or health.runtime_version is None
            or health.runtime_version != capabilities.runtime_version
        ):
            raise PaidExecutionDenied("runtime health and capabilities discovery identities differ")
        return cls.from_capabilities(
            runtime_id=health.runtime_id,
            runtime_version=health.runtime_version,
            availability=health.availability,
            checked_at=capabilities.checked_at,
            models=capabilities.models,
        )

    @classmethod
    def from_capabilities(
        cls,
        *,
        runtime_id: str,
        runtime_version: str,
        availability: RuntimeAvailability = RuntimeAvailability.AVAILABLE,
        checked_at: datetime,
        models: tuple[ModelCapability, ...],
    ) -> "VerifiedRuntimeIdentity":
        routes = tuple(
            sorted(
                (
                    VerifiedModelRoute(
                        provider_id=model.provider_id,
                        model_id=model.model_id,
                        capabilities=model.capabilities,
                        available=model.available,
                        capability_sha256=_capability_hash(
                            model.provider_id, model.model_id, model.capabilities, model.available
                        ),
                    )
                    for model in models
                ),
                key=lambda item: (item.provider_id, item.model_id),
            )
        )
        return cls(
            runtime_id=runtime_id,
            runtime_version=runtime_version,
            availability=availability,
            checked_at=checked_at,
            capability_set_sha256=_capability_set_hash(routes),
            models=routes,
        )


@dataclass(frozen=True, slots=True)
class TaskTokenServerBinding:
    """Echoes the complete task scope that the S5 server bound to the token ref."""

    mcp_server_url: str
    task_token_ref: str
    source_roles: tuple[str, ...]
    subject_ids: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    reservation: RunReservation
    output_contract: OutputContract


class TaskTokenServer(Protocol):
    """S5 task-server seam; implementations hold and verify credentials."""

    def bind_task(
        self,
        *,
        project_id: str,
        experiment_id: str,
        task_id: str,
        task_token_ref: str,
        source_roles: tuple[str, ...],
        subject_ids: tuple[str, ...],
        allowed_tools: tuple[str, ...],
        reservation: RunReservation,
        output_contract: OutputContract,
    ) -> TaskTokenServerBinding: ...


@dataclass(frozen=True, slots=True)
class PaidTaskContract:
    project_id: str
    experiment_id: str
    task: CompiledTask
    output_contract: OutputContract
    context_refs: tuple[ArtifactReference, ...]
    source_roles: tuple[str, ...]
    subject_ids: tuple[str, ...]
    recipe_allowed_tools: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    route: PinnedOpenCodeRoute
    credentials: CredentialReferences
    runtime_identity: VerifiedRuntimeIdentity
    price_card: FrozenPriceCard
    reservation: RunReservation
    deadline_at: datetime
    expected_mcp_server_url: str
    max_runtime_identity_age: timedelta
    experiment_enabled: bool
    paid_execution_enabled: bool
    redaction_state: RedactionState
    security_approved: bool
    required_capabilities: frozenset[str] = frozenset({"tools"})


@dataclass(frozen=True, slots=True)
class ImmutablePreflightReceipt:
    execution_id: str
    project_id: str
    experiment_id: str
    task_id: str
    runtime_id: str
    runtime_version: str
    runtime_checked_at: datetime
    runtime_capability_set_sha256: str
    route_capability_sha256: str
    provider_id: str
    model_id: str
    opencode_config_sha256: str
    price_card: PriceCardReceipt
    reservation: RunReservation
    issued_at: datetime


@dataclass(frozen=True, slots=True)
class PreflightedTask:
    receipt: ImmutablePreflightReceipt
    runtime_request: RuntimeTaskRequest
    opencode_config_sha256: str


class PaidTaskPreflight:
    """Build a bounded runtime request only after all paid-run gates pass."""

    def __init__(
        self,
        *,
        task_token_server: TaskTokenServer,
        now: Callable[[], datetime],
    ) -> None:
        self._task_token_server = task_token_server
        self._now = now

    def prepare(self, *, contract: PaidTaskContract, execution_id: str) -> PreflightedTask:
        now = self._now()
        _assert_utc(now, "preflight receipt time")
        _assert_nonblank(execution_id, "execution_id")
        route = _validate_contract(contract, now=now)
        binding = self._task_token_server.bind_task(
            project_id=contract.project_id,
            experiment_id=contract.experiment_id,
            task_id=contract.task.node_id,
            task_token_ref=contract.credentials.task_token_ref,
            source_roles=contract.source_roles,
            subject_ids=contract.subject_ids,
            allowed_tools=contract.allowed_tools,
            reservation=contract.reservation,
            output_contract=contract.output_contract,
        )
        _assert_binding(contract, binding)
        config = compile_one_task_opencode_config(
            OneTaskOpenCodeConfigInput(
                mcp_server_url=binding.mcp_server_url,
                allowed_tools=contract.allowed_tools,
            )
        )
        request = RuntimeTaskRequest(
            experiment_id=contract.experiment_id,
            task_id=contract.task.node_id,
            idempotency_key=f"{contract.experiment_id}:{contract.task.node_id}:{execution_id}",
            provider_id=contract.route.provider_id,
            model_id=contract.route.model_id,
            semantic_prompt=contract.task.semantic_prompt,
            semantic_prompt_sha256=sha256(contract.task.semantic_prompt.encode("utf-8")).hexdigest(),
            context_refs=contract.context_refs,
            output_contract=contract.output_contract,
            allowed_tool_names=contract.allowed_tools,
            budgets=RuntimeBudgets(
                max_input_tokens=contract.reservation.max_input_tokens,
                max_output_tokens=contract.reservation.max_output_tokens,
                max_tool_calls=contract.reservation.max_tool_calls,
                max_tool_bytes=contract.reservation.max_tool_read_bytes,
                max_elapsed_ms=contract.reservation.max_elapsed_ms,
                # Keep the Decimal reservation exact at this boundary.  The
                # generic runtime port's float field cannot represent it safely.
                max_billed_cost_usd=contract.reservation.max_cost_usd,  # type: ignore[arg-type]
            ),
            deadline_at=contract.deadline_at,
            required_capabilities=contract.required_capabilities,
            agent_id="nexus-task",
            runtime_options={"opencode_config_sha256": config.sha256},
        )
        return PreflightedTask(
            receipt=ImmutablePreflightReceipt(
                execution_id=execution_id,
                project_id=contract.project_id,
                experiment_id=contract.experiment_id,
                task_id=contract.task.node_id,
                runtime_id=contract.runtime_identity.runtime_id,
                runtime_version=contract.runtime_identity.runtime_version,
                runtime_checked_at=contract.runtime_identity.checked_at,
                runtime_capability_set_sha256=contract.runtime_identity.capability_set_sha256,
                route_capability_sha256=route.capability_sha256,
                provider_id=contract.route.provider_id,
                model_id=contract.route.model_id,
                opencode_config_sha256=config.sha256,
                price_card=contract.price_card.receipt,
                reservation=contract.reservation,
                issued_at=now,
            ),
            runtime_request=request,
            opencode_config_sha256=config.sha256,
        )


def _validate_contract(contract: PaidTaskContract, *, now: datetime) -> VerifiedModelRoute:
    if not contract.experiment_enabled:
        raise PaidExecutionDenied("experiment is not enabled for execution")
    if not contract.paid_execution_enabled:
        raise PaidExecutionDenied("paid execution is not enabled")
    for value, label in (
        (contract.project_id, "project_id"),
        (contract.experiment_id, "experiment_id"),
        (contract.task.node_id, "task id"),
        (contract.route.provider_id, "provider id"),
        (contract.route.model_id, "model id"),
        (contract.credentials.task_token_ref, "task-token credential reference"),
        (contract.credentials.opencode_basic_password_ref, "OpenCode Basic-auth password reference"),
        (contract.credentials.provider_credential_ref, "provider credential reference"),
    ):
        _assert_nonblank(value, label)
    if len({
        contract.credentials.task_token_ref,
        contract.credentials.opencode_basic_password_ref,
        contract.credentials.provider_credential_ref,
    }) != 3:
        raise PaidExecutionDenied("paid execution credential references must be distinct")
    if not contract.task.semantic_prompt.strip():
        raise PaidExecutionDenied("task semantic prompt must be non-empty")
    if (
        contract.route.runtime_id != PAID_RUNTIME_ID
        or contract.route.runtime_version != PINNED_OPENCODE_VERSION
    ):
        raise PaidExecutionDenied("paid execution requires the exact pinned OpenCode runtime identity")
    _assert_utc(contract.deadline_at, "paid execution deadline")
    if contract.deadline_at <= now:
        raise PaidExecutionDenied("paid execution deadline has already passed")
    _assert_nonblank(contract.expected_mcp_server_url, "expected internal MCP server URL")
    _assert_runtime_identity(
        contract.runtime_identity,
        contract.route,
        contract.required_capabilities,
        contract.max_runtime_identity_age,
        now,
    )
    if contract.redaction_state is not RedactionState.APPROVED or not contract.security_approved:
        raise PaidExecutionDenied("paid execution requires approved redaction and security")
    _validate_task_scope(contract)
    reservation = contract.reservation
    for value, label in (
        (reservation.max_input_tokens, "maximum input token reservation"),
        (reservation.max_output_tokens, "maximum output token reservation"),
        (reservation.max_elapsed_ms, "maximum elapsed-time reservation"),
        (reservation.max_tool_calls, "maximum tool-call reservation"),
        (reservation.max_tool_read_bytes, "maximum tool-read reservation"),
        (reservation.max_tool_result_bytes, "maximum tool-result reservation"),
        (reservation.max_tool_write_bytes, "maximum tool-write reservation"),
    ):
        _assert_positive_int(value, label)
    _assert_positive_decimal(reservation.max_cost_usd, "maximum cost reservation")
    rate = contract.price_card.rate_for(contract.route.provider_id, contract.route.model_id)
    if rate is None or not any(
        value > Decimal("0")
        for value in (
            rate.input_usd_per_million,
            rate.output_usd_per_million,
            rate.cache_read_usd_per_million,
            rate.cache_write_usd_per_million,
        )
    ):
        raise PaidExecutionDenied("paid execution route requires a nonzero frozen price-card rate")
    return next(
        route
        for route in contract.runtime_identity.models
        if route.provider_id == contract.route.provider_id and route.model_id == contract.route.model_id
    )


def _assert_runtime_identity(
    identity: VerifiedRuntimeIdentity,
    requested: PinnedOpenCodeRoute,
    required_capabilities: frozenset[str],
    max_age: timedelta,
    now: datetime,
) -> None:
    _assert_utc(identity.checked_at, "runtime identity checked_at")
    if identity.checked_at > now:
        raise PaidExecutionDenied("runtime identity cannot be checked in the future")
    if not isinstance(max_age, timedelta) or max_age <= timedelta():
        raise PaidExecutionDenied("runtime identity maximum age must be positive")
    if now - identity.checked_at > max_age:
        raise PaidExecutionDenied("runtime identity discovery is stale")
    if identity.runtime_id != PAID_RUNTIME_ID or identity.runtime_version != PINNED_OPENCODE_VERSION:
        raise PaidExecutionDenied("verified runtime identity is not the pinned OpenCode runtime")
    if identity.availability is not RuntimeAvailability.AVAILABLE:
        raise PaidExecutionDenied("verified runtime is unavailable or degraded")
    if identity.capability_set_sha256 != _capability_set_hash(identity.models):
        raise PaidExecutionDenied("verified runtime capability receipt hash does not match its routes")
    routes = [
        route
        for route in identity.models
        if route.provider_id == requested.provider_id and route.model_id == requested.model_id
    ]
    if len(routes) != 1 or not routes[0].available:
        raise PaidExecutionDenied("exact pinned provider/model is unavailable in verified runtime discovery")
    route = routes[0]
    if route.capability_sha256 != _capability_hash(
        route.provider_id, route.model_id, route.capabilities, route.available
    ) or not required_capabilities.issubset(route.capabilities):
        raise PaidExecutionDenied("exact pinned provider/model capability mismatch")


def _validate_task_scope(contract: PaidTaskContract) -> None:
    if not contract.source_roles or not contract.subject_ids:
        raise PaidExecutionDenied("task token scope requires source roles and subject ids")
    for values, label in ((contract.source_roles, "source roles"), (contract.subject_ids, "subject ids")):
        if len(set(values)) != len(values):
            raise PaidExecutionDenied(f"task token {label} must be unique")
        for value in values:
            _assert_nonblank(value, f"task token {label}")
    if not contract.allowed_tools or len(set(contract.allowed_tools)) != len(contract.allowed_tools):
        raise PaidExecutionDenied("paid execution tool contract must be non-empty and unique")
    if any(tool not in NEXUS_EXPERIMENT_TOOLS for tool in contract.allowed_tools):
        raise PaidExecutionDenied("paid execution requires exact Nexus task tools")
    if "tools" not in contract.required_capabilities:
        raise PaidExecutionDenied("task tool use requires the tools runtime capability")
    if len(set(contract.recipe_allowed_tools)) != len(contract.recipe_allowed_tools) or set(
        contract.allowed_tools
    ) != set(contract.recipe_allowed_tools):
        raise PaidExecutionDenied("allowed tools must exactly match the compiled recipe task contract")


def _assert_binding(contract: PaidTaskContract, binding: TaskTokenServerBinding) -> None:
    _assert_nonblank(binding.mcp_server_url, "task MCP server URL")
    if (
        binding.mcp_server_url != contract.expected_mcp_server_url
        or
        binding.task_token_ref != contract.credentials.task_token_ref
        or binding.source_roles != contract.source_roles
        or binding.subject_ids != contract.subject_ids
        or binding.allowed_tools != contract.allowed_tools
        or binding.reservation != contract.reservation
        or binding.output_contract != contract.output_contract
    ):
        raise PaidExecutionDenied("task-token server binding does not match the complete frozen task scope")


def _capability_hash(provider_id: str, model_id: str, capabilities: frozenset[str], available: bool) -> str:
    return sha256(
        canonical_json_bytes(
            {
                "available": available,
                "capabilities": sorted(capabilities),
                "model_id": model_id,
                "provider_id": provider_id,
            }
        )
    ).hexdigest()


def _capability_set_hash(routes: tuple[VerifiedModelRoute, ...]) -> str:
    return sha256(
        canonical_json_bytes(
            [
                {
                    "capability_sha256": route.capability_sha256,
                    "model_id": route.model_id,
                    "provider_id": route.provider_id,
                }
                for route in routes
            ]
        )
    ).hexdigest()


def _assert_utc(value: datetime, label: str) -> None:
    if value.tzinfo is not timezone.utc:
        raise PaidExecutionDenied(f"{label} must be UTC-aware")


def _assert_nonblank(value: str, label: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip() or any(char.isspace() for char in value):
        raise PaidExecutionDenied(f"{label} must be a non-empty credential-safe reference or identifier")


def _assert_positive_int(value: object, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PaidExecutionDenied(f"{label} must be a positive integer")


def _assert_positive_decimal(value: object, label: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= Decimal("0"):
        raise PaidExecutionDenied(f"{label} must be a finite positive Decimal")
