from __future__ import annotations

import pytest

from mcp_server.experiments.infrastructure.opencode.config_compiler import (
    OpenCodeConfigInput,
    compile_opencode_config,
)


def _spec(**overrides: object) -> OpenCodeConfigInput:
    values: dict[str, object] = {
        "runtime_version": "1.18.4",
        "provider_id": "deepseek",
        "provider_secret_ref": "DEEPSEEK_API_KEY",
        "mcp_server_name": "nexus-experiment",
        "mcp_server_url": "http://nexus-internal/experiment-mcp",
        "mcp_token_secret_ref": "NEXUS_EXPERIMENT_TASK_TOKEN",
        "allowed_mcp_tools": (
            "nexus_experiment_read_evidence",
            "nexus_experiment_get_contract",
        ),
    }
    values.update(overrides)
    return OpenCodeConfigInput(**values)  # type: ignore[arg-type]


def test_config_is_deterministic_secret_reference_only_and_deny_by_default():
    first = compile_opencode_config(_spec())
    second = compile_opencode_config(_spec(allowed_mcp_tools=(
        "nexus_experiment_get_contract",
        "nexus_experiment_read_evidence",
    )))

    assert first.sha256 == second.sha256
    assert first.config["permission"]["*"] == "deny"  # type: ignore[index]
    assert first.config["permission"]["bash"] == "deny"  # type: ignore[index]
    assert first.config["permission"]["mcp"] == {  # type: ignore[index]
        "nexus_experiment_get_contract": "allow",
        "nexus_experiment_read_evidence": "allow",
    }
    assert "credential_secret_ref" in str(first.config)
    assert "DEEPSEEK_API_KEY" in str(first.config)


def test_config_rejects_empty_reference_or_non_nexus_tool():
    with pytest.raises(ValueError, match="secret reference"):
        compile_opencode_config(_spec(provider_secret_ref=""))
    with pytest.raises(ValueError, match="Nexus experiment"):
        compile_opencode_config(_spec(allowed_mcp_tools=("bash",)))
