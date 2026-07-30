from __future__ import annotations

import json
from pathlib import Path

from mcp_server.config import MCPServerConfig, check_agentic_configuration


def _settings(**overrides: object) -> MCPServerConfig:
    values: dict[str, object] = {
        "agentic_experiments_enabled": False,
        "agentic_paid_execution_enabled": False,
        "agentic_task_token_secret": "",
        "agentic_opencode_password": "",
        "agentic_opencode_version": "1.18.4",
        "agentic_max_cost_usd": 0.0,
    }
    values.update(overrides)
    return MCPServerConfig(**values)


def test_disabled_agentic_path_needs_no_new_secrets() -> None:
    assert check_agentic_configuration(_settings()) == []


def test_paid_execution_cannot_be_enabled_without_experiments() -> None:
    warnings = check_agentic_configuration(
        _settings(agentic_paid_execution_enabled=True)
    )

    assert warnings == [
        "agentic paid execution requires MCP_AGENTIC_EXPERIMENTS_ENABLED=true"
    ]


def test_enabled_experiments_fail_closed_without_task_and_runtime_auth() -> None:
    warnings = check_agentic_configuration(
        _settings(agentic_experiments_enabled=True)
    )

    assert warnings == [
        "agentic experiments require a dedicated task-token secret of at least 32 bytes",
        "agentic experiments require authenticated OpenCode access",
    ]


def test_paid_execution_requires_positive_operator_cost_ceiling() -> None:
    warnings = check_agentic_configuration(
        _settings(
            agentic_experiments_enabled=True,
            agentic_paid_execution_enabled=True,
            agentic_task_token_secret="t" * 32,
            agentic_opencode_password="runtime-password-reference",
        )
    )

    assert warnings == [
        "agentic paid execution requires a positive operator cost ceiling"
    ]


def test_enabled_path_rejects_invalid_ttl_and_non_finite_cost() -> None:
    warnings = check_agentic_configuration(
        _settings(
            agentic_experiments_enabled=True,
            agentic_paid_execution_enabled=True,
            agentic_task_token_secret="t" * 32,
            agentic_opencode_password="runtime-password-reference",
            agentic_task_token_ttl_seconds=0,
            agentic_max_cost_usd=float("nan"),
        )
    )

    assert warnings == [
        "agentic experiments require a positive task-token TTL",
        "agentic paid execution requires a positive operator cost ceiling",
    ]


def test_fully_configured_experiment_path_has_no_warning() -> None:
    settings = _settings(
        agentic_experiments_enabled=True,
        agentic_paid_execution_enabled=True,
        agentic_task_token_secret="t" * 32,
        agentic_opencode_password="runtime-password-reference",
        agentic_max_cost_usd=5.0,
    )

    assert check_agentic_configuration(settings) == []


def test_opencode_runtime_only_allows_bounded_documentation_subagents() -> None:
    config_path = (
        Path(__file__).parents[3] / "opencode-runtime" / "opencode.base.json"
    )
    runtime = json.loads(config_path.read_text(encoding="utf-8"))

    assert runtime["permission"]["task"] == "deny"
    assert runtime["subagent_depth"] == 1
    agents = runtime["agent"]
    orchestrator = agents["nexus-doc-orchestrator"]
    assert orchestrator["mode"] == "primary"
    assert orchestrator["permission"]["task"] == {
        "*": "deny",
        "nexus-doc-*": "allow",
    }
    grounded = agents["nexus-doc-grounded-orchestrator"]
    assert grounded["mode"] == "primary"
    assert grounded["permission"]["task"] == {
        "*": "deny",
        "nexus-doc-evidence": "allow",
        "nexus-doc-analyst": "allow",
        "nexus-doc-pack-writer": "allow",
        "nexus-doc-reviewer": "allow",
    }
    child_agents = {
        name: value
        for name, value in agents.items()
        if name not in {"nexus-doc-orchestrator", "nexus-doc-grounded-orchestrator"}
    }
    assert child_agents
    assert all(value["mode"] == "subagent" for value in child_agents.values())
    assert all(value["permission"]["task"] == "deny" for value in child_agents.values())
