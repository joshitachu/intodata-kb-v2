"""Runtime-port execution of a preflighted paid experiment task."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from mcp_server.experiments.application.preflight import (
    ImmutablePreflightReceipt,
    PaidExecutionDenied,
    PaidTaskContract,
    PaidTaskPreflight,
)
from mcp_server.experiments.domain.evaluation import UsageMetrics
from mcp_server.experiments.domain.runtime import (
    AgentRuntime,
    ArtifactReference,
    CancelRequest,
    CancelResult,
    RuntimeTaskEvent,
    RuntimeTaskHandle,
    RuntimeTaskResult,
    RuntimeTaskStatus,
)
from mcp_server.experiments.infrastructure.validation.cost import calculate_costs


_TERMINAL_STATUSES = frozenset(
    {RuntimeTaskStatus.SUCCEEDED, RuntimeTaskStatus.FAILED, RuntimeTaskStatus.CANCELLED}
)


@dataclass(frozen=True, slots=True)
class NormalizedTaskSession:
    runtime_id: str
    session_id: str
    message_id: str | None
    experiment_id: str
    task_id: str


@dataclass(frozen=True, slots=True)
class NormalizedTaskUsage:
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_write_tokens: int | None
    billed_cost_usd: Decimal | None
    billed_cost_known: bool
    normalized_cost_usd: Decimal | None
    elapsed_ms: int | None
    reservation_state: str


@dataclass(frozen=True, slots=True)
class ExecutedTask:
    preflight: ImmutablePreflightReceipt
    session: NormalizedTaskSession
    events: tuple[RuntimeTaskEvent, ...]
    result: RuntimeTaskResult
    usage: NormalizedTaskUsage


@dataclass(frozen=True, slots=True)
class NormalizedCancellation:
    session_id: str
    accepted: bool
    status: str
    detail: dict[str, object]


class TaskExecutionService:
    """Starts a generic runtime exactly once; reconciliation is read-only afterwards."""

    def __init__(self, *, runtime: AgentRuntime, preflight: PaidTaskPreflight) -> None:
        self._runtime = runtime
        self._preflight = preflight

    async def execute(self, *, contract: PaidTaskContract, execution_id: str) -> ExecutedTask:
        prepared = self._preflight.prepare(contract=contract, execution_id=execution_id)
        # There is intentionally no retry path here.  The runtime receives one
        # idempotency key and is responsible only for safe reconciliation.
        handle = await self._runtime.start_task(prepared.runtime_request)
        _assert_handle(handle, prepared.receipt, prepared.runtime_request.idempotency_key)
        events = tuple([event async for event in self._runtime.observe(handle)])
        result = await self._runtime.result(handle)
        _assert_result_identity(result, handle, prepared.receipt)
        _assert_events(events, prepared.receipt, result.status)
        _assert_output_refs(result, contract)
        usage = _normalize_usage(result, contract)
        return ExecutedTask(
            preflight=prepared.receipt,
            session=NormalizedTaskSession(
                runtime_id=handle.runtime_id,
                session_id=handle.session_id,
                message_id=handle.message_id,
                experiment_id=handle.experiment_id,
                task_id=handle.task_id,
            ),
            events=events,
            result=result,
            usage=usage,
        )

    async def cancel(self, *, executed: ExecutedTask, reason: str) -> NormalizedCancellation:
        if not reason or not reason.strip():
            raise PaidExecutionDenied("cancellation reason must be non-empty")
        result = await self._runtime.cancel(
            CancelRequest(
                experiment_id=executed.session.experiment_id,
                task_id=executed.session.task_id,
                session_id=executed.session.session_id,
                reason=reason,
            )
        )
        return _normalize_cancel(result, expected_session_id=executed.session.session_id)


def _assert_handle(
    handle: RuntimeTaskHandle,
    receipt: ImmutablePreflightReceipt,
    expected_idempotency_key: str,
) -> None:
    if (
        handle.runtime_id != receipt.runtime_id
        or
        handle.experiment_id != receipt.experiment_id
        or handle.task_id != receipt.task_id
        or not handle.session_id
        or handle.idempotency_key != expected_idempotency_key
    ):
        raise PaidExecutionDenied("runtime returned a task handle outside the preflight scope")


def _assert_events(
    events: tuple[RuntimeTaskEvent, ...],
    receipt: ImmutablePreflightReceipt,
    result_status: RuntimeTaskStatus,
) -> None:
    sequences = [event.sequence for event in events]
    if not sequences or sequences != list(range(1, len(sequences) + 1)):
        raise PaidExecutionDenied("runtime events must be non-empty and contiguous from sequence one")
    event_ids = [event.event_id for event in events]
    if any(not isinstance(event_id, str) or not event_id.strip() for event_id in event_ids) or len(
        event_ids
    ) != len(set(event_ids)):
        raise PaidExecutionDenied("runtime event ids must be unique and non-blank")
    terminal_events = [event for event in events if event.task_status in _TERMINAL_STATUSES]
    if len(terminal_events) != 1 or events[-1] != terminal_events[0]:
        raise PaidExecutionDenied("runtime events require exactly one final terminal event")
    for event in events:
        task_id = event.payload.get("task_id")
        experiment_id = event.payload.get("experiment_id")
        if task_id is not None and task_id != receipt.task_id:
            raise PaidExecutionDenied("runtime event task identity is outside the preflight scope")
        if experiment_id is not None and experiment_id != receipt.experiment_id:
            raise PaidExecutionDenied("runtime event experiment identity is outside the preflight scope")
        if event.task_status in _TERMINAL_STATUSES and event.task_status != result_status:
            raise PaidExecutionDenied("runtime terminal event does not match the task result")


def _assert_result_identity(
    result: RuntimeTaskResult,
    handle: RuntimeTaskHandle,
    receipt: ImmutablePreflightReceipt,
) -> None:
    if (
        result.runtime_id != receipt.runtime_id
        or result.runtime_version != receipt.runtime_version
        or
        result.session_id != handle.session_id
        or result.message_id != handle.message_id
        or result.provider_id != receipt.provider_id
        or result.model_id != receipt.model_id
    ):
        raise PaidExecutionDenied("runtime result does not match the pinned paid-run identity")
    if result.status not in _TERMINAL_STATUSES:
        raise PaidExecutionDenied("runtime result is nonterminal or unknown")
    if result.retry_count != 0:
        raise PaidExecutionDenied("paid task execution does not permit silent runtime retries")
    _assert_nonnegative_int(result.retry_count, "runtime retry count")
    _assert_nonnegative_int(result.tool_calls, "runtime tool calls")
    _assert_nonnegative_int(result.tool_bytes, "runtime tool bytes")
    for value, label in (
        (result.timings.elapsed_ms, "runtime elapsed time"),
        (result.timings.provider_latency_ms, "runtime provider latency"),
    ):
        if value is not None:
            _assert_nonnegative_int(value, label)


def _assert_output_refs(result: RuntimeTaskResult, contract: PaidTaskContract) -> None:
    refs = result.output_refs
    names = [reference.logical_name for reference in refs]
    if len(names) != len(set(names)):
        raise PaidExecutionDenied("runtime output references must not be duplicated")
    for reference in refs:
        _assert_reference_shape(reference)
    output = contract.output_contract
    matching = [reference for reference in refs if reference.logical_name == output.artifact_name]
    if result.status == RuntimeTaskStatus.SUCCEEDED:
        if output.required and len(matching) != 1:
            raise PaidExecutionDenied("successful task is missing its declared required output artifact")
        if any(reference.logical_name != output.artifact_name for reference in refs):
            raise PaidExecutionDenied("runtime emitted an undeclared output artifact")
        if matching:
            reference = matching[0]
            if reference.media_type != output.media_type:
                raise PaidExecutionDenied("runtime output media type differs from the declared artifact")
            if output.max_bytes is not None and (
                reference.byte_size is None or reference.byte_size > output.max_bytes
            ):
                raise PaidExecutionDenied("runtime output exceeds or omits the declared artifact byte bound")
    elif refs:
        raise PaidExecutionDenied("failed or cancelled task must not report output artifacts")


def _assert_reference_shape(reference: ArtifactReference) -> None:
    if (
        not reference.logical_name
        or not reference.media_type
        or len(reference.sha256) != 64
        or any(character not in "0123456789abcdef" for character in reference.sha256)
        or (
            reference.byte_size is not None
            and (isinstance(reference.byte_size, bool) or not isinstance(reference.byte_size, int) or reference.byte_size < 0)
        )
    ):
        raise PaidExecutionDenied("runtime output reference has an invalid declared artifact shape")


def _normalize_usage(result: RuntimeTaskResult, contract: PaidTaskContract) -> NormalizedTaskUsage:
    raw = result.usage
    token_values = (raw.input_tokens, raw.output_tokens, raw.cache_read_tokens, raw.cache_write_tokens)
    labels = (
        "input token usage",
        "output token usage",
        "cache-read token usage",
        "cache-write token usage",
    )
    for value, label in zip(token_values, labels, strict=True):
        if value is not None:
            _assert_nonnegative_int(value, label)
    elapsed_ms = result.timings.elapsed_ms
    if elapsed_ms is not None:
        _assert_nonnegative_int(elapsed_ms, "runtime elapsed time")
    complete = all(value is not None for value in token_values) and elapsed_ms is not None
    if not complete:
        if result.status == RuntimeTaskStatus.SUCCEEDED:
            raise PaidExecutionDenied("successful paid execution requires complete token and elapsed-time usage")
        billed = _decimal_or_none(raw.billed_cost_usd, "provider billed cost")
        return NormalizedTaskUsage(
            input_tokens=token_values[0],
            output_tokens=token_values[1],
            cache_read_tokens=token_values[2],
            cache_write_tokens=token_values[3],
            billed_cost_usd=billed,
            billed_cost_known=billed is not None,
            normalized_cost_usd=None,
            elapsed_ms=elapsed_ms,
            reservation_state="ambiguous_incomplete_terminal_usage",
        )
    for value, label in zip(
        token_values,
        labels,
        strict=True,
    ):
        _assert_nonnegative_int(value, label)
    input_tokens, output_tokens, cache_read_tokens, cache_write_tokens = token_values
    reservation = contract.reservation
    if (
        input_tokens > reservation.max_input_tokens
        or output_tokens > reservation.max_output_tokens
        or elapsed_ms > reservation.max_elapsed_ms
        or result.tool_calls > reservation.max_tool_calls
        or result.tool_bytes > reservation.max_tool_read_bytes
    ):
        raise PaidExecutionDenied("runtime usage exceeded the frozen paid-run reservation")
    billed = _decimal_or_none(raw.billed_cost_usd, "provider billed cost")
    if billed is not None and billed > reservation.max_cost_usd:
        raise PaidExecutionDenied("reported provider billed cost exceeded the frozen reservation")
    costs = calculate_costs(
        UsageMetrics(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            provider_calls=1,
            tool_calls=result.tool_calls,
            tool_bytes=result.tool_bytes,
            retries=result.retry_count,
        ),
        provider_id=contract.route.provider_id,
        model_id=contract.route.model_id,
        price_card=contract.price_card,
        billed_cost_usd=billed,
    )
    cost = costs.metrics.normalized_cost_usd
    if cost is None or cost > reservation.max_cost_usd:
        raise PaidExecutionDenied("runtime cost exceeded or cannot be priced from the frozen reservation")
    return NormalizedTaskUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        billed_cost_usd=billed,
        billed_cost_known=billed is not None,
        normalized_cost_usd=cost,
        elapsed_ms=elapsed_ms,
        reservation_state=(
            "proved" if billed is not None or result.status == RuntimeTaskStatus.SUCCEEDED else "normalized_unknown_billing"
        ),
    )


def _decimal_or_none(value: float | None, label: str) -> Decimal | None:
    if value is None:
        return None
    decimal = Decimal(str(value))
    if not decimal.is_finite() or decimal < Decimal("0"):
        raise PaidExecutionDenied(f"{label} must be finite and non-negative")
    return decimal


def _assert_nonnegative_int(value: object, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PaidExecutionDenied(f"{label} must be a non-negative integer")


def _normalize_cancel(result: CancelResult, *, expected_session_id: str) -> NormalizedCancellation:
    if result.session_id != expected_session_id:
        raise PaidExecutionDenied("runtime cancellation result does not match the task session")
    return NormalizedCancellation(
        session_id=result.session_id,
        accepted=result.accepted,
        status=result.status.value,
        detail=dict(result.detail),
    )
