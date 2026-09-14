"""OpenCode execution adapter for the provider-neutral investigation runtime."""

from __future__ import annotations

import asyncio
import time

from mcp_server.providers.coding_agent.base import (
    ExecutionObserver,
    ExecutionTransport,
    LogCallback,
    ProviderExecutionRequest,
    assert_checkpoint_compatible,
    load_checkpoint,
)
from mcp_server.providers.coding_agent.models import (
    AgentCapabilities,
    InvestigationCheckpoint,
    InvestigationRequest,
    InvestigationResult,
    ResourceCeiling,
    UsageReceipt,
)


class OpenCodeCodingAgent:
    """Run one coherent investigation through an injected OpenCode transport.

    The transport owns CLI/config/event details.  This adapter owns the stable
    lifecycle contract, durable checkpoint envelope and normalization.  It does
    not import the goal-driven domain pipeline or the legacy enterprise runner.
    """

    provider_id = "opencode"

    def __init__(
        self,
        *,
        model: str,
        transport: ExecutionTransport,
        native_delegation: bool = False,
    ) -> None:
        self.model = model
        self._transport = transport
        self._native_delegation = native_delegation

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            session_resume=True,
            native_file_tools=True,
            structured_checkpoints=True,
            cancellation_receipts=True,
            detailed_usage=True,
            native_delegation=self._native_delegation,
        )

    async def start_or_resume(
        self,
        request: InvestigationRequest,
        *,
        checkpoint: InvestigationCheckpoint | None = None,
        log_cb: LogCallback | None = None,
    ) -> InvestigationResult:
        durable = checkpoint or load_checkpoint(request.notebook_path)
        if durable is not None:
            assert_checkpoint_compatible(
                request, durable, provider_id=self.provider_id, model=self.model
            )
            durable.status = "running"
        else:
            durable = InvestigationCheckpoint(
                investigation_id=request.investigation_id,
                provider_id=self.provider_id,
                model=self.model,
                fingerprints=request.fingerprints,
            )
        observer = ExecutionObserver(durable, request.notebook_path)
        reached = request.resource_ceiling.reached_by(durable.usage)
        if reached:
            observer.finish("incomplete")
            return InvestigationResult(
                status="incomplete",
                provider_id=self.provider_id,
                model=self.model,
                checkpoint=observer.checkpoint,
                usage=observer.checkpoint.usage,
                stop_reason="resource_ceiling:" + ",".join(reached),
            )
        execution_request = ProviderExecutionRequest(
            investigation_id=request.investigation_id,
            project_id=request.project_id,
            goal=request.goal,
            expected_outcome=request.expected_outcome,
            workspace=request.authorized_workspace,
            fact_graph=request.fact_graph,
            method_files=request.method_files,
            tool_names=request.tool_names,
            session_id=durable.session_id,
            checkpoint_state=durable.state,
            initial_scope_seed=request.initial_scope_seed,
            provider_options={**request.provider_options, "_checkpoint_revision": durable.revision},
            resource_ceiling=_remaining_ceiling(request.resource_ceiling, durable.usage),
        )
        started = time.monotonic()
        if log_cb is not None:
            await log_cb(
                request.project_id,
                f"OpenCode investigation {'resuming' if durable.session_id else 'starting'}: "
                f"{request.investigation_id}",
                "info",
            )
        try:
            execution = await self._transport(execution_request, observer)
        except asyncio.CancelledError:
            elapsed = int((time.monotonic() - started) * 1000)
            cancellation_usage = UsageReceipt(
                wall_time_ms=elapsed,
                complete=False,
                note="Provider was cancelled before final usage; completed progress is preserved.",
            )
            observer.record_usage(cancellation_usage)
            observer.finish(
                "cancelled",
                in_flight={
                    "session_id": observer.checkpoint.session_id,
                    "state": "cancelled_during_provider_execution",
                },
            )
            if log_cb is not None:
                await log_cb(
                    request.project_id,
                    "OpenCode investigation cancelled; checkpoint preserved",
                    "warning",
                )
            return InvestigationResult(
                status="cancelled",
                provider_id=self.provider_id,
                model=self.model,
                checkpoint=observer.checkpoint,
                usage=observer.checkpoint.usage,
                stop_reason="cancelled",
            )
        except Exception as exc:  # noqa: BLE001 - normalize transport failures
            elapsed = int((time.monotonic() - started) * 1000)
            observer.record_usage(
                UsageReceipt(
                    wall_time_ms=elapsed,
                    complete=False,
                    note="Provider failed before final usage; completed progress is preserved.",
                )
            )
            observer.finish("failed")
            return InvestigationResult(
                status="failed",
                provider_id=self.provider_id,
                model=self.model,
                checkpoint=observer.checkpoint,
                usage=observer.checkpoint.usage,
                stop_reason="provider_error",
                error=f"{type(exc).__name__}: {exc}",
            )

        if execution.session_id:
            observer.checkpoint.session_id = execution.session_id
        if execution.checkpoint_state:
            observer.record_checkpoint(execution.checkpoint_state)
        final_usage = execution.usage.model_copy(
            update={
                "wall_time_ms": execution.usage.wall_time_ms
                or int((time.monotonic() - started) * 1000)
            }
        )
        observer.finalize_usage(final_usage)
        observer.finish(
            execution.status,
            in_flight=(
                None
                if execution.status in {"completed", "failed"}
                else {
                    "session_id": observer.checkpoint.session_id,
                    "state": f"{execution.status}_during_provider_execution",
                    "stop_reason": execution.stop_reason,
                }
            ),
        )
        return InvestigationResult(
            status=execution.status,
            provider_id=self.provider_id,
            model=self.model,
            output=execution.output,
            checkpoint=observer.checkpoint,
            usage=observer.checkpoint.usage,
            stop_reason=execution.stop_reason,
            error=execution.error,
        )


def _remaining_ceiling(ceiling: ResourceCeiling, usage: UsageReceipt) -> ResourceCeiling:
    """Translate a run ceiling into the remaining allowance for this resume."""

    return ResourceCeiling(
        wall_time_seconds=(
            max(1, ceiling.wall_time_seconds - usage.wall_time_ms // 1000)
            if ceiling.wall_time_seconds is not None
            else None
        ),
        processed_tokens=(
            max(1, ceiling.processed_tokens - usage.processed_tokens)
            if ceiling.processed_tokens is not None
            else None
        ),
        cost_usd=(
            max(0.000001, ceiling.cost_usd - usage.cost_usd)
            if ceiling.cost_usd is not None
            else None
        ),
    )
