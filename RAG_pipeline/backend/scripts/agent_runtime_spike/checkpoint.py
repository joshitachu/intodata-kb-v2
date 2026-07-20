"""Strict checkpoint adapter for agent-message restart tests.

The repository saver is reused only against an isolated SQLite test database.
This adapter disables pickle and permissive msgpack reconstruction, strips
provider reasoning fields, and redacts secret-shaped strings before persistence.
"""

from __future__ import annotations

import copy
from typing import Any

from langchain_core.messages import BaseMessage
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.types import Command

from scripts.agent_runtime_eval.security import SECRET_PATTERNS
from src.harness.checkpoints import DurableWorkflowCheckpointSaver


_PRIVATE_KEYS = frozenset(
    {
        "reasoning",
        "thinking",
        "thought",
        "chain_of_thought",
        "reasoning_content",
        "encrypted_reasoning",
        "api_key",
        "authorization",
        "access_token",
        "refresh_token",
        "password",
        "secret",
    }
)
_REDACTED = "[REDACTED]"


def _redact_string(value: str) -> str:
    redacted = value
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub(_REDACTED, redacted)
    return redacted


def sanitize_agent_checkpoint(value: Any, *, key: str | None = None) -> Any:
    if key is not None and key.lower() in _PRIVATE_KEYS:
        return _REDACTED
    if isinstance(value, str):
        return _redact_string(value)
    if isinstance(value, BaseMessage):
        content = sanitize_agent_checkpoint(value.content, key="content")
        if isinstance(content, list):
            content = [
                block
                for block in content
                if not (isinstance(block, dict) and block.get("type") in {"reasoning", "thinking"})
            ]
        return value.model_copy(
            update={
                "content": content,
                "additional_kwargs": sanitize_agent_checkpoint(value.additional_kwargs),
                "response_metadata": sanitize_agent_checkpoint(value.response_metadata),
            }
        )
    if isinstance(value, dict):
        return {
            str(item_key): sanitize_agent_checkpoint(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [sanitize_agent_checkpoint(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_agent_checkpoint(item) for item in value)
    return copy.deepcopy(value)


class SafeAgentCheckpointSaver(DurableWorkflowCheckpointSaver):
    """Repository checkpointer with a strict, non-executable trust boundary."""

    def __init__(self, *, session_factory) -> None:  # noqa: ANN001
        super().__init__(session_factory=session_factory)
        self.serde = JsonPlusSerializer(
            pickle_fallback=False,
            allowed_json_modules=None,
            allowed_msgpack_modules=None,
        )

    async def aput(self, config, checkpoint, metadata, new_versions):  # noqa: ANN001, ANN201
        return await super().aput(
            config,
            sanitize_agent_checkpoint(checkpoint),
            sanitize_agent_checkpoint(metadata),
            new_versions,
        )

    async def aput_writes(self, config, writes, task_id, task_path=""):  # noqa: ANN001, ANN201
        safe_writes = [(channel, sanitize_agent_checkpoint(value)) for channel, value in writes]
        await super().aput_writes(config, safe_writes, task_id, task_path)


def resume_command() -> Command:
    return Command(resume=True)
