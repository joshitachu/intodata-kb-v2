"""One-task OpenCode security config for the disposable experiment sidecar."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from mcp_server.experiments.infrastructure.storage.canonical_json import canonical_json_bytes


NEXUS_EXPERIMENT_TOOLS = (
    "nexus_experiment_get_contract",
    "nexus_experiment_list_evidence",
    "nexus_experiment_search_evidence",
    "nexus_experiment_read_evidence",
    "nexus_experiment_traverse_relationships",
    "nexus_experiment_write_artifact",
    "nexus_experiment_submit_task",
)

OPENCODE_MCP_SERVER_NAME = "nexus"
OPENCODE_TASK_AGENT_NAME = "nexus-task"
OPENCODE_MCP_TIMEOUT_MS = 300_000

_DENIED_BUILTINS = (
    "bash", "edit", "read", "glob", "grep", "external_directory", "webfetch", "websearch",
    "task", "skill", "lsp", "question",
)


@dataclass(frozen=True, slots=True)
class OneTaskOpenCodeConfigInput:
    mcp_server_url: str
    allowed_tools: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OneTaskOpenCodeConfig:
    config: dict[str, object]
    sha256: str


def native_to_opencode_tool_id(native_tool_name: str) -> str:
    if native_tool_name not in NEXUS_EXPERIMENT_TOOLS:
        raise ValueError("unknown native Nexus experiment tool")
    return f"{OPENCODE_MCP_SERVER_NAME}_{native_tool_name}"


def opencode_to_native_tool_id(opencode_tool_id: str) -> str:
    prefix = f"{OPENCODE_MCP_SERVER_NAME}_"
    native = opencode_tool_id.removeprefix(prefix)
    if native == opencode_tool_id or native not in NEXUS_EXPERIMENT_TOOLS:
        raise ValueError("unknown OpenCode experiment tool id")
    return native


def compile_one_task_opencode_config(spec: OneTaskOpenCodeConfigInput) -> OneTaskOpenCodeConfig:
    tools = tuple(sorted(set(spec.allowed_tools)))
    if not spec.mcp_server_url or not tools or any(tool not in NEXUS_EXPERIMENT_TOOLS for tool in tools):
        raise ValueError("one-task OpenCode config requires only exact Nexus MCP tool names")
    final_tool_ids = tuple(native_to_opencode_tool_id(tool) for tool in tools)
    permission: dict[str, object] = {
        "*": "deny",
        **{tool: "deny" for tool in _DENIED_BUILTINS},
        **{tool_id: "allow" for tool_id in final_tool_ids},
    }
    config: dict[str, object] = {
        "autoupdate": False,
        "mcp": {
            OPENCODE_MCP_SERVER_NAME: {
                "headers": {"Authorization": "Bearer {env:NEXUS_EXPERIMENT_TASK_TOKEN}"},
                "oauth": False,
                "timeout": OPENCODE_MCP_TIMEOUT_MS,
                "type": "remote",
                "url": spec.mcp_server_url,
            }
        },
        "agent": {
            OPENCODE_TASK_AGENT_NAME: {
                "mode": "primary",
                "permission": dict(permission),
            }
        },
        "permission": permission,
        "share": "disabled",
        "snapshot": False,
        "subagent_depth": 0,
    }
    return OneTaskOpenCodeConfig(config=config, sha256=sha256(canonical_json_bytes(config)).hexdigest())
