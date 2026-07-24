from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.config import Settings
from src.harness.agent_loop import AgentLoopLimits
from src.harness.run_budgets import LayeredBudgetLimits


def test_backend_compose_uses_mcp_service_url_for_docs_provider() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    compose = (repo_root / "docker-compose.yml").read_text()

    assert "MCP_DOCS_BASE_URL: ${MCP_DOCS_BASE_URL:-http://mcp-server:8002/mcp}" in compose
    assert "AGENT_RUNTIME_V2_CANARY_STAGE: ${AGENT_RUNTIME_V2_CANARY_STAGE:-off}" in compose
    assert "AGENT_RUNTIME_V2_SYNTHESIS_RESERVE_CALLS: ${AGENT_RUNTIME_V2_SYNTHESIS_RESERVE_CALLS:-2}" in compose
    assert "AGENT_RUNTIME_V2_PROVIDER_MAX_RETRIES: ${AGENT_RUNTIME_V2_PROVIDER_MAX_RETRIES:-1}" in compose


def test_standard_runtime_keeps_two_synthesis_calls_and_one_transport_retry() -> None:
    runtime_settings = Settings(_env_file=None)

    assert runtime_settings.agent_runtime_v2_synthesis_reserve_calls == 2
    assert runtime_settings.agent_runtime_v2_provider_max_retries == 1


def test_configured_tool_call_ceiling_is_accepted_by_the_runtime_contract() -> None:
    runtime_settings = Settings(_env_file=None)

    limits = AgentLoopLimits(
        max_tool_calls=runtime_settings.agent_chat_max_tool_calls
    )
    layered = LayeredBudgetLimits(
        max_model_calls=runtime_settings.agent_runtime_v2_max_model_calls,
        max_tool_calls=runtime_settings.agent_chat_max_tool_calls,
    )

    assert limits.max_tool_calls == runtime_settings.agent_chat_max_tool_calls
    assert layered.max_model_calls == runtime_settings.agent_runtime_v2_max_model_calls
    assert layered.max_tool_calls == runtime_settings.agent_chat_max_tool_calls


def test_canary_settings_reject_non_monotonic_stage_configuration() -> None:
    with pytest.raises(ValidationError, match="planning stage requires planning"):
        Settings(
            _env_file=None,
            agent_runtime_v2_canary_stage="planning",
            agent_runtime_v2_enabled=True,
            agent_runtime_v2_readonly_percent=10,
        )


def test_full_standard_settings_require_complete_live_transport_rollout() -> None:
    settings = Settings(
        _env_file=None,
        agent_runtime_v2_canary_stage="full_standard",
        agent_runtime_v2_enabled=True,
        agent_runtime_v2_readonly_percent=100,
        agent_runtime_v2_planning_enabled=True,
        agent_runtime_v2_planning_percent=100,
        agent_runtime_v2_effects_enabled=True,
        agent_runtime_v2_effect_percent=100,
        agent_runtime_v2_live_streaming_enabled=True,
        agent_runtime_v2_live_streaming_percent=100,
    )
    assert settings.agent_runtime_v2_canary_stage == "full_standard"


def test_alembic_revision_ids_fit_version_table_column() -> None:
    backend_root = Path(__file__).resolve().parents[2]
    for migration in (backend_root / "alembic" / "versions").glob("*.py"):
        tree = ast.parse(migration.read_text(), filename=str(migration))
        revision = next(
            (
                value.value
                for node in tree.body
                for target, value in [
                    (node.target, node.value)
                    if isinstance(node, ast.AnnAssign)
                    else (
                        node.targets[0],
                        node.value,
                    )
                    if isinstance(node, ast.Assign) and len(node.targets) == 1
                    else (None, None)
                ]
                if isinstance(target, ast.Name)
                and target.id == "revision"
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
            ),
            None,
        )
        assert revision is not None, f"Missing revision identifier in {migration.name}"
        assert len(revision) <= 32, f"Revision identifier exceeds varchar(32): {revision}"
