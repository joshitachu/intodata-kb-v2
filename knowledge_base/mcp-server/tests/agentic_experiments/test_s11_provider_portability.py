"""Offline portability proof for the provider-neutral OpenCode runtime boundary."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import httpx
import pytest

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
    ModelCapability,
    OutputContract,
    RuntimeBudgets,
    RuntimeTaskRequest,
)
from mcp_server.experiments.domain.sources import RedactionState
from mcp_server.experiments.domain.tasks import CompiledTask
from mcp_server.experiments.infrastructure.opencode.client import OpenCodeHttpClient
from mcp_server.experiments.infrastructure.opencode.config_compiler import (
    OpenCodeConfigInput,
    compile_opencode_config,
)
from mcp_server.experiments.infrastructure.opencode.runtime import (
    PINNED_OPENCODE_VERSION,
    OpenCodeAgentRuntime,
)
from mcp_server.experiments.infrastructure.runtime.fake import FakeAgentRuntime
from mcp_server.experiments.infrastructure.validation.cost import parse_frozen_price_card


NOW = datetime(2026, 7, 27, tzinfo=timezone.utc)
PORTABLE_ROUTES = (
    ("mistral", "mistral-large-3"),
    ("google", "gemini-2.5-pro"),
)


def _request(provider_id: str, model_id: str) -> RuntimeTaskRequest:
    return RuntimeTaskRequest(
        experiment_id="provider-portability",
        task_id=f"task-{provider_id}",
        idempotency_key=f"provider-portability:{provider_id}:{model_id}",
        provider_id=provider_id,
        model_id=model_id,
        semantic_prompt="Produce the bounded artifact.",
        semantic_prompt_sha256="a" * 64,
        context_refs=(),
        output_contract=OutputContract("answer.md", "text/markdown", max_bytes=1024),
        allowed_tool_names=("nexus_experiment_get_contract",),
        budgets=_reservation_to_runtime_budgets(),
        deadline_at=NOW + timedelta(minutes=10),
        required_capabilities=frozenset({"tools"}),
    )


def _reservation_to_runtime_budgets() -> RuntimeBudgets:
    return RuntimeBudgets(max_input_tokens=100, max_output_tokens=100, max_elapsed_ms=1_000)


def _runtime(handler: httpx.MockTransport) -> tuple[OpenCodeAgentRuntime, httpx.AsyncClient]:
    http_client = httpx.AsyncClient(transport=handler)
    client = OpenCodeHttpClient(
        base_url="http://opencode.test",
        username="nexus",
        password="local-test-password-not-a-provider-secret",
        client=http_client,
    )
    return OpenCodeAgentRuntime(client, now=lambda: NOW), http_client


@pytest.mark.asyncio
@pytest.mark.parametrize(("provider_id", "model_id"), PORTABLE_ROUTES)
async def test_opencode_runtime_preserves_distinct_provider_model_routes(
    provider_id: str, model_id: str
) -> None:
    submitted: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"version": PINNED_OPENCODE_VERSION})
        if request.url.path == "/provider":
            return httpx.Response(
                200,
                json={
                    "connected": [provider_id],
                    "all": [
                        {
                            "id": provider_id,
                            "models": {
                                model_id: {"status": "available", "capabilities": ["tools"]}
                            },
                        }
                    ],
                },
            )
        if request.url.path == "/session" and request.method == "POST":
            return httpx.Response(200, json={"id": "session-1"})
        if request.url.path == "/session/session-1/prompt_async":
            submitted.update(json.loads(request.content))
            return httpx.Response(204)
        raise AssertionError(f"unexpected {request.method} {request.url}")

    runtime, http_client = _runtime(httpx.MockTransport(handler))
    try:
        capabilities = await runtime.capabilities()
        await runtime.start_task(_request(provider_id, model_id))
    finally:
        await http_client.aclose()

    assert [(model.provider_id, model.model_id) for model in capabilities.models] == [
        (provider_id, model_id)
    ]
    assert submitted["model"] == {"providerID": provider_id, "modelID": model_id}
    assert "openai" not in str(submitted).lower()
    assert "claude" not in str(submitted).lower()


class _TaskTokenServer:
    def bind_task(self, **scope: object) -> TaskTokenServerBinding:
        return TaskTokenServerBinding(
            mcp_server_url="http://nexus-internal/experiment-mcp/",
            task_token_ref=str(scope["task_token_ref"]),
            source_roles=scope["source_roles"],  # type: ignore[arg-type]
            subject_ids=scope["subject_ids"],  # type: ignore[arg-type]
            allowed_tools=scope["allowed_tools"],  # type: ignore[arg-type]
            reservation=scope["reservation"],  # type: ignore[arg-type]
            output_contract=scope["output_contract"],  # type: ignore[arg-type]
        )


def _reservation() -> RunReservation:
    return RunReservation(
        max_input_tokens=100,
        max_output_tokens=100,
        max_elapsed_ms=1_000,
        max_cost_usd=Decimal("1.00"),
        max_tool_calls=5,
        max_tool_read_bytes=512,
        max_tool_result_bytes=512,
        max_tool_write_bytes=512,
    )


def _price_card(*routes: tuple[str, str]):
    return parse_frozen_price_card(
        {
            "schema_version": "1",
            "price_card_id": "provider-portability-card",
            "version": "2026-07",
            "currency": "USD",
            "rates": [
                {
                    "provider_id": provider_id,
                    "model_id": model_id,
                    "input_usd_per_million": "1",
                    "output_usd_per_million": "2",
                    "cache_read_usd_per_million": "0.5",
                    "cache_write_usd_per_million": "0.5",
                }
                for provider_id, model_id in routes
            ],
        }
    )


def _contract(
    provider_id: str,
    model_id: str,
    identity: VerifiedRuntimeIdentity,
    *,
    price_card_routes: tuple[tuple[str, str], ...] = PORTABLE_ROUTES,
) -> PaidTaskContract:
    tools = ("nexus_experiment_get_contract", "nexus_experiment_submit_task")
    return PaidTaskContract(
        project_id="project-a",
        experiment_id="provider-portability",
        task=CompiledTask(
            node_id="task-a",
            task_type="document",
            role="implementation",
            dependencies=(),
            output_ids=("answer.md",),
            semantic_prompt="Produce the bounded artifact from immutable evidence.",
        ),
        output_contract=OutputContract("answer.md", "text/markdown", max_bytes=1024),
        context_refs=(),
        source_roles=("implementation",),
        subject_ids=("subject-a",),
        recipe_allowed_tools=tools,
        allowed_tools=tools,
        route=PinnedOpenCodeRoute("opencode", PINNED_OPENCODE_VERSION, provider_id, model_id),
        credentials=CredentialReferences(
            task_token_ref="task-token-ref",
            opencode_basic_password_ref="opencode-basic-password-ref",
            provider_credential_ref="provider-credential-ref",
        ),
        runtime_identity=identity,
        price_card=_price_card(*price_card_routes),
        reservation=_reservation(),
        deadline_at=NOW + timedelta(minutes=10),
        expected_mcp_server_url="http://nexus-internal/experiment-mcp/",
        max_runtime_identity_age=timedelta(minutes=1),
        experiment_enabled=True,
        paid_execution_enabled=True,
        redaction_state=RedactionState.APPROVED,
        security_approved=True,
    )


@pytest.mark.asyncio
async def test_preflight_accepts_each_discovered_provider_route_through_the_same_runtime_port() -> None:
    class _OpenCodeFakeRuntime(FakeAgentRuntime):
        runtime_id = "opencode"
        runtime_version = PINNED_OPENCODE_VERSION

    identity = await VerifiedRuntimeIdentity.discover(
        _OpenCodeFakeRuntime(
            models=tuple(
                ModelCapability(provider_id, model_id, frozenset({"tools"}))
                for provider_id, model_id in PORTABLE_ROUTES
            ),
            now=NOW,
        )
    )
    preflight = PaidTaskPreflight(task_token_server=_TaskTokenServer(), now=lambda: NOW)

    prepared = [
        preflight.prepare(
            contract=_contract(provider_id, model_id, identity), execution_id=f"execution-{provider_id}"
        )
        for provider_id, model_id in PORTABLE_ROUTES
    ]

    assert [(item.receipt.provider_id, item.receipt.model_id) for item in prepared] == list(
        PORTABLE_ROUTES
    )
    assert [(item.runtime_request.provider_id, item.runtime_request.model_id) for item in prepared] == list(
        PORTABLE_ROUTES
    )
    assert all("openai" not in str(item.receipt).lower() for item in prepared)
    assert all("claude" not in str(item.receipt).lower() for item in prepared)


@pytest.mark.asyncio
async def test_preflight_fails_closed_for_missing_capability_or_price_card_route() -> None:
    provider_id, model_id = PORTABLE_ROUTES[0]

    class _OpenCodeFakeRuntime(FakeAgentRuntime):
        runtime_id = "opencode"
        runtime_version = PINNED_OPENCODE_VERSION

    missing_capability = await VerifiedRuntimeIdentity.discover(
        _OpenCodeFakeRuntime(models=(ModelCapability(provider_id, model_id, frozenset()),), now=NOW)
    )
    preflight = PaidTaskPreflight(task_token_server=_TaskTokenServer(), now=lambda: NOW)
    with pytest.raises(PaidExecutionDenied, match="capability mismatch"):
        preflight.prepare(
            contract=_contract(provider_id, model_id, missing_capability), execution_id="missing-capability"
        )

    capable_identity = await VerifiedRuntimeIdentity.discover(
        _OpenCodeFakeRuntime(
            models=(ModelCapability(provider_id, model_id, frozenset({"tools"})),), now=NOW
        )
    )
    with pytest.raises(PaidExecutionDenied, match="price-card rate"):
        preflight.prepare(
            contract=_contract(
                provider_id,
                model_id,
                capable_identity,
                price_card_routes=(PORTABLE_ROUTES[1],),
            ),
            execution_id="missing-price-card-route",
        )


def test_provider_secret_values_are_absent_from_compiled_config_and_preflight_receipt() -> None:
    provider_id, model_id = PORTABLE_ROUTES[0]
    provider_secret_value = "mistral-provider-secret-value-must-not-leak"
    config = compile_opencode_config(
        OpenCodeConfigInput(
            runtime_version=PINNED_OPENCODE_VERSION,
            provider_id=provider_id,
            provider_secret_ref="MISTRAL_API_KEY",
            mcp_server_name="nexus-experiment",
            mcp_server_url="http://nexus-internal/experiment-mcp/",
            mcp_token_secret_ref="NEXUS_EXPERIMENT_TASK_TOKEN",
            allowed_mcp_tools=("nexus_experiment_get_contract",),
        )
    )
    identity = VerifiedRuntimeIdentity.from_capabilities(
        runtime_id="opencode",
        runtime_version=PINNED_OPENCODE_VERSION,
        checked_at=NOW,
        models=(ModelCapability(provider_id, model_id, frozenset({"tools"})),),
    )
    prepared = PaidTaskPreflight(task_token_server=_TaskTokenServer(), now=lambda: NOW).prepare(
        contract=_contract(provider_id, model_id, identity, price_card_routes=((provider_id, model_id),)),
        execution_id="secret-free-receipt",
    )

    assert provider_secret_value not in str(config.config)
    assert provider_secret_value not in str(prepared.receipt)
    assert "provider-credential-ref" not in str(prepared.receipt)
    assert "opencode-basic-password-ref" not in str(prepared.receipt)
