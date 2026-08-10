"""Default-off application composition for the OpenAI Agents Grounded route."""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import json
import math
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from mcp_server.config import MCPServerConfig
from mcp_server.experiments.application.grounded_runtime import (
    BOUNDED_BUDGET_MONITORING_CAPABILITY,
)
from mcp_server.experiments.infrastructure.openai_agents.grounded import (
    OpenAIAgentsGroundedTaskBuilder,
)
from mcp_server.experiments.infrastructure.openai_agents.preflight import (
    VerifiedOpenAIAgentsRoute,
    verify_openai_agents_route,
)
from mcp_server.experiments.infrastructure.openai_agents.runtime import (
    OpenAIAgentModel,
    OpenAIAgentsRuntime,
    OpenAIAgentsRuntimeConfig,
)
from mcp_server.experiments.infrastructure.openai_agents.state_store import (
    FileRuntimeTaskStateStore,
)


OPENAI_AGENTS_GROUNDED_RUNTIME_ID = "openai-agents"
OPENCODE_GROUNDED_RUNTIME_ID = "opencode"


@dataclass(frozen=True, slots=True)
class ConfiguredOpenAIAgentsGroundedRuntime:
    runtime: OpenAIAgentsRuntime
    task_builder: OpenAIAgentsGroundedTaskBuilder
    verified_route: VerifiedOpenAIAgentsRoute


@dataclass(frozen=True, slots=True)
class OpenAIAgentsProviderRoute:
    provider_id: str
    model_id: str
    display_name: str
    api_key_env_var: str
    base_url: str | None
    input_usd_per_million: float
    output_usd_per_million: float


async def compose_openai_agents_grounded_runtime(
    *,
    settings: MCPServerConfig,
    provider_id: str,
    model_id: str,
    workspace_root: Path,
    sdk: Any | None = None,
) -> ConfiguredOpenAIAgentsGroundedRuntime:
    """Build and statically preflight the explicitly selected optional route."""

    if not settings.agentic_openai_agents_enabled:
        raise ValueError("OpenAI Agents Grounded runtime is disabled")
    route = next(
        (
            candidate
            for candidate in configured_openai_agents_routes(settings)
            if candidate.provider_id == provider_id and candidate.model_id == model_id
        ),
        None,
    )
    if route is None:
        raise ValueError("OpenAI Agents provider/model route is not operator-allowed")
    sdk_module = sdk or importlib.import_module("agents")
    api_key = os.environ.get(route.api_key_env_var)
    if not api_key:
        raise RuntimeError("OpenAI-compatible provider credential is unavailable")
    provider = sdk_module.OpenAIProvider(
        api_key=api_key,
        base_url=route.base_url,
        use_responses=route.base_url is None,
        buffer_streamed_tool_calls=route.base_url is not None,
    )
    resolved_model = provider.get_model(route.model_id)
    builder = OpenAIAgentsGroundedTaskBuilder(sdk=sdk_module)
    runtime = OpenAIAgentsRuntime(
        config=OpenAIAgentsRuntimeConfig(
            enabled=True,
            provider_id=route.provider_id,
            models=(OpenAIAgentModel(model_id=model_id),),
            api_key_env_var=route.api_key_env_var,
            max_turns_per_role=settings.agentic_openai_agents_max_turns_per_role,
        ),
        tool_factory=builder,
        output_reference_resolver=builder.resolve_output_references,
        task_state_store=FileRuntimeTaskStateStore(
            Path(workspace_root) / ".openai-agents-task-state"
        ),
        cost_estimator=_route_cost_estimator(route),
        model_resolver=lambda requested_model: (
            resolved_model
            if requested_model == route.model_id
            else _raise_unconfigured_model()
        ),
        sdk=sdk_module,
    )
    verified = await verify_openai_agents_route(
        runtime,
        provider_id=provider_id,
        model_id=model_id,
        required_capabilities=frozenset({"tools", BOUNDED_BUDGET_MONITORING_CAPABILITY}),
    )
    return ConfiguredOpenAIAgentsGroundedRuntime(runtime, builder, verified)


def _configured_model_ids(value: str) -> frozenset[str]:
    # An empty allowlist is a normal, benign "not configured yet" state, not a
    # malformed one -- callers (e.g. capabilities reporting) surface it as
    # "model allowlist is empty" rather than a route-parse failure.
    return frozenset(item.strip() for item in value.split(",") if item.strip())


def configured_openai_agents_routes(
    settings: MCPServerConfig,
) -> tuple[OpenAIAgentsProviderRoute, ...]:
    """Parse the operator allowlist without reading or returning secret values."""

    raw = settings.agentic_openai_agents_routes_json.strip()
    if raw:
        try:
            values = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("OpenAI Agents provider route allowlist is invalid JSON") from exc
        if not isinstance(values, list) or not values:
            raise ValueError("OpenAI Agents provider route allowlist must be a non-empty array")
        routes = tuple(_parse_route(value) for value in values)
    else:
        models = _configured_model_ids(settings.agentic_openai_agents_model_ids)
        routes = tuple(
            OpenAIAgentsProviderRoute(
                provider_id=settings.agentic_openai_agents_provider_id.strip(),
                model_id=model,
                display_name=model,
                api_key_env_var=settings.agentic_openai_agents_api_key_env_var.strip(),
                base_url=None,
                input_usd_per_million=_legacy_rate(settings, model, "input"),
                output_usd_per_million=_legacy_rate(settings, model, "output"),
            )
            for model in sorted(models)
        )
    identities = {(route.provider_id, route.model_id) for route in routes}
    if len(identities) != len(routes):
        raise ValueError("OpenAI Agents provider routes must be unique")
    return routes


def _parse_route(value: object) -> OpenAIAgentsProviderRoute:
    if not isinstance(value, dict):
        raise ValueError("OpenAI Agents provider route must be an object")
    provider_id = _required_string(value, "provider_id")
    model_id = _required_string(value, "model_id")
    api_key_env_var = _required_string(value, "api_key_env_var")
    display_name = str(value.get("display_name") or model_id).strip()
    base_value = value.get("base_url")
    base_url = None if base_value is None else str(base_value).strip().rstrip("/")
    if base_url:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("OpenAI-compatible provider base URL must be a safe HTTPS origin/path")
    return OpenAIAgentsProviderRoute(
        provider_id=provider_id,
        model_id=model_id,
        display_name=display_name,
        api_key_env_var=api_key_env_var,
        base_url=base_url,
        input_usd_per_million=_required_rate(value, "input_usd_per_million"),
        output_usd_per_million=_required_rate(value, "output_usd_per_million"),
    )


def _required_string(value: dict[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"OpenAI Agents provider route requires {key}")
    return item.strip()


def _required_rate(value: dict[str, object], key: str) -> float:
    try:
        rate = float(value[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"OpenAI Agents provider route requires {key}") from exc
    if not math.isfinite(rate) or rate < 0:
        raise ValueError(f"OpenAI Agents provider route {key} must be finite and non-negative")
    return rate


def _legacy_rate(settings: MCPServerConfig, model_id: str, direction: str) -> float:
    try:
        pricing = json.loads(settings.model_pricing_json or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("model pricing configuration is invalid") from exc
    route = pricing.get(f"{settings.agentic_openai_agents_provider_id}/{model_id}")
    if route is None:
        route = pricing.get(model_id)
    if not isinstance(route, dict):
        raise ValueError("OpenAI Agents allowlisted route requires frozen model pricing")
    for key in (direction, f"{direction}_per_million", f"{direction}_usd_per_million"):
        if key in route:
            return _required_rate(route, key)
    raise ValueError("OpenAI Agents allowlisted route requires frozen model pricing")


def _route_cost_estimator(route: OpenAIAgentsProviderRoute):
    def estimate(request: Any, usage: Any) -> float | None:
        if request.provider_id != route.provider_id or request.model_id != route.model_id:
            return None
        if usage.input_tokens is None or usage.output_tokens is None:
            return None
        return (
            usage.input_tokens * route.input_usd_per_million
            + usage.output_tokens * route.output_usd_per_million
        ) / 1_000_000

    return estimate


def _raise_unconfigured_model() -> Any:
    raise ValueError("OpenAI Agents model route changed after preflight")
