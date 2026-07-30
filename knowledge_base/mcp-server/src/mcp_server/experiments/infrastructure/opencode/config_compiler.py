"""Deterministic, secret-reference-only OpenCode configuration receipts.

This module produces the trusted configuration snapshot used by the runtime
adapter.  It never reads environment variables and intentionally does not
perform the S5 server-side permission enforcement work.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from mcp_server.experiments.infrastructure.storage.canonical_json import canonical_json_bytes


_DENIED_BUILTINS = (
    "bash",
    "edit",
    "read",
    "glob",
    "grep",
    "external_directory",
    "webfetch",
    "websearch",
    "task",
    "skill",
    "lsp",
    "question",
)


@dataclass(frozen=True, slots=True)
class OpenCodeConfigInput:
    runtime_version: str
    provider_id: str
    provider_secret_ref: str
    mcp_server_name: str
    mcp_server_url: str
    mcp_token_secret_ref: str
    allowed_mcp_tools: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CompiledOpenCodeConfig:
    config: dict[str, object]
    sha256: str


def compile_opencode_config(spec: OpenCodeConfigInput) -> CompiledOpenCodeConfig:
    """Compile a stable deny-by-default config without accepting secret values."""

    _require_reference_name(spec.provider_secret_ref, "provider_secret_ref")
    _require_reference_name(spec.mcp_token_secret_ref, "mcp_token_secret_ref")
    allowed_tools = tuple(sorted(set(spec.allowed_mcp_tools)))
    if not allowed_tools:
        raise ValueError("at least one explicit Nexus MCP tool is required")
    if any(not tool.startswith("nexus_experiment_") for tool in allowed_tools):
        raise ValueError("only Nexus experiment MCP tool names may be allowed")

    permissions: dict[str, object] = {"*": "deny"}
    permissions.update({tool: "deny" for tool in _DENIED_BUILTINS})
    permissions["mcp"] = {tool: "allow" for tool in allowed_tools}
    config: dict[str, object] = {
        "schema_version": 1,
        "runtime": {"name": "opencode", "version": spec.runtime_version},
        "provider": {
            "id": spec.provider_id,
            "credential_secret_ref": spec.provider_secret_ref,
        },
        "permission": permissions,
        "mcp": {
            spec.mcp_server_name: {
                "type": "remote",
                "url": spec.mcp_server_url,
                "auth_secret_ref": spec.mcp_token_secret_ref,
                "allowed_tools": list(allowed_tools),
            }
        },
    }
    digest = sha256(canonical_json_bytes(config)).hexdigest()
    return CompiledOpenCodeConfig(config=config, sha256=digest)


def _require_reference_name(value: str, field_name: str) -> None:
    if not value or value != value.strip() or any(char.isspace() for char in value):
        raise ValueError(f"{field_name} must be a non-empty secret reference name")
