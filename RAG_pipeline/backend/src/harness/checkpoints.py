"""Repository-native durable checkpointer for small Nexus LangGraph workflows.

The graph state is intentionally narrow. This saver persists the LangGraph
checkpoint protocol, but removes messages, hidden reasoning, and credential-like
values before serialization. Providers are always re-created with fresh Nexus
authorization on resume, so no provider clients or secrets need checkpointing.
"""

from __future__ import annotations

import asyncio
import copy
import random
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.harness import (
    HarnessWorkflowCheckpointBlobRow,
    HarnessWorkflowCheckpointRow,
    HarnessWorkflowCheckpointWriteRow,
)
from src.db.session import async_session_factory

_SENSITIVE_STATE_KEYS = frozenset(
    {
        "message",
        "reasoning",
        "thought",
        "chain_of_thought",
        "access_token",
        "refresh_token",
        "api_key",
        "secret",
        "password",
        "authorization",
        "evidence_text",
    }
)
_REDACTED = "[REDACTED]"

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class DurableWorkflowCheckpointSaver(BaseCheckpointSaver[str]):
    """Async LangGraph saver using Nexus's existing relational database.

    It is deliberately a compact implementation of the checkpointer protocol
    required by the harness graph. Checkpoint writes are independently committed
    so a request/backend restart cannot discard an interrupt checkpoint.
    """

    def __init__(self, *, session_factory: SessionFactory = async_session_factory) -> None:
        super().__init__()
        self._session_factory = session_factory
        # LangGraph may flush a checkpoint and its pending writes concurrently.
        # Serializing this saver instance keeps SQLite tests correct and avoids
        # interleaving a thread's persisted transition on a shared connection.
        self._write_lock = asyncio.Lock()

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        configurable = config["configurable"]
        thread_id = str(configurable["thread_id"])
        namespace = str(configurable.get("checkpoint_ns", ""))
        checkpoint_id = get_checkpoint_id(config)
        async with self._session_factory() as session:
            stmt = select(HarnessWorkflowCheckpointRow).where(
                HarnessWorkflowCheckpointRow.thread_id == thread_id,
                HarnessWorkflowCheckpointRow.checkpoint_ns == namespace,
            )
            if checkpoint_id:
                stmt = stmt.where(HarnessWorkflowCheckpointRow.checkpoint_id == str(checkpoint_id))
            else:
                stmt = stmt.order_by(HarnessWorkflowCheckpointRow.checkpoint_id.desc())
            row = (await session.execute(stmt.limit(1))).scalar_one_or_none()
            if row is None:
                return None
            return await self._tuple_from_row(session, row)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        tuples: list[CheckpointTuple] = []
        async with self._session_factory() as session:
            stmt = select(HarnessWorkflowCheckpointRow).order_by(
                HarnessWorkflowCheckpointRow.checkpoint_id.desc()
            )
            if config is not None:
                configurable = config["configurable"]
                stmt = stmt.where(
                    HarnessWorkflowCheckpointRow.thread_id == str(configurable["thread_id"])
                )
                if "checkpoint_ns" in configurable:
                    stmt = stmt.where(
                        HarnessWorkflowCheckpointRow.checkpoint_ns
                        == str(configurable.get("checkpoint_ns", ""))
                    )
                if checkpoint_id := get_checkpoint_id(config):
                    stmt = stmt.where(HarnessWorkflowCheckpointRow.checkpoint_id == str(checkpoint_id))
            if before is not None and (before_id := get_checkpoint_id(before)):
                stmt = stmt.where(HarnessWorkflowCheckpointRow.checkpoint_id < str(before_id))
            if limit is not None:
                stmt = stmt.limit(limit)
            rows = (await session.execute(stmt)).scalars().all()
            for row in rows:
                tuple_ = await self._tuple_from_row(session, row)
                if filter and not all(filter.get(key) == tuple_.metadata.get(key) for key in filter):
                    continue
                tuples.append(tuple_)
        for tuple_ in tuples:
            yield tuple_

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: dict[str, str],
    ) -> RunnableConfig:
        async with self._write_lock:
            return await self._aput_unlocked(config, checkpoint, metadata, new_versions)

    async def _aput_unlocked(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: dict[str, str],
    ) -> RunnableConfig:
        configurable = config["configurable"]
        thread_id = str(configurable["thread_id"])
        namespace = str(configurable.get("checkpoint_ns", ""))
        checkpoint_id = str(checkpoint["id"])
        sanitized = _sanitize_checkpoint(checkpoint)
        payload = dict(sanitized)
        values = payload.pop("channel_values", {})
        checkpoint_type, checkpoint_data = self.serde.dumps_typed(payload)
        metadata_type, metadata_data = self.serde.dumps_typed(get_checkpoint_metadata(config, metadata))

        async with self._session_factory() as session:
            async with session.begin():
                row = (
                    await session.execute(
                        select(HarnessWorkflowCheckpointRow).where(
                            HarnessWorkflowCheckpointRow.thread_id == thread_id,
                            HarnessWorkflowCheckpointRow.checkpoint_ns == namespace,
                            HarnessWorkflowCheckpointRow.checkpoint_id == checkpoint_id,
                        )
                    )
                ).scalar_one_or_none()
                if row is None:
                    session.add(
                        HarnessWorkflowCheckpointRow(
                            thread_id=thread_id,
                            checkpoint_ns=namespace,
                            checkpoint_id=checkpoint_id,
                            parent_checkpoint_id=_optional_text(configurable.get("checkpoint_id")),
                            checkpoint_type=checkpoint_type,
                            checkpoint_data=checkpoint_data,
                            metadata_type=metadata_type,
                            metadata_data=metadata_data,
                        )
                    )
                else:
                    row.parent_checkpoint_id = _optional_text(configurable.get("checkpoint_id"))
                    row.checkpoint_type = checkpoint_type
                    row.checkpoint_data = checkpoint_data
                    row.metadata_type = metadata_type
                    row.metadata_data = metadata_data
                for channel, version in new_versions.items():
                    encoded = (
                        self.serde.dumps_typed(values[channel])
                        if channel in values
                        else ("empty", b"")
                    )
                    blob = (
                        await session.execute(
                            select(HarnessWorkflowCheckpointBlobRow).where(
                                HarnessWorkflowCheckpointBlobRow.thread_id == thread_id,
                                HarnessWorkflowCheckpointBlobRow.checkpoint_ns == namespace,
                                HarnessWorkflowCheckpointBlobRow.channel == str(channel),
                                HarnessWorkflowCheckpointBlobRow.channel_version == str(version),
                            )
                        )
                    ).scalar_one_or_none()
                    if blob is None:
                        session.add(
                            HarnessWorkflowCheckpointBlobRow(
                                thread_id=thread_id,
                                checkpoint_ns=namespace,
                                channel=str(channel),
                                channel_version=str(version),
                                value_type=encoded[0],
                                value_data=encoded[1],
                            )
                        )
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": namespace,
                "checkpoint_id": checkpoint_id,
            }
        }

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        async with self._write_lock:
            await self._aput_writes_unlocked(config, writes, task_id, task_path)

    async def _aput_writes_unlocked(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        configurable = config["configurable"]
        thread_id = str(configurable["thread_id"])
        namespace = str(configurable.get("checkpoint_ns", ""))
        checkpoint_id = str(configurable["checkpoint_id"])
        async with self._session_factory() as session:
            async with session.begin():
                for index, (channel, value) in enumerate(writes):
                    write_index = WRITES_IDX_MAP.get(channel, index)
                    existing = (
                        await session.execute(
                            select(HarnessWorkflowCheckpointWriteRow.id).where(
                                HarnessWorkflowCheckpointWriteRow.thread_id == thread_id,
                                HarnessWorkflowCheckpointWriteRow.checkpoint_ns == namespace,
                                HarnessWorkflowCheckpointWriteRow.checkpoint_id == checkpoint_id,
                                HarnessWorkflowCheckpointWriteRow.task_id == task_id,
                                HarnessWorkflowCheckpointWriteRow.write_index == write_index,
                            )
                        )
                    ).scalar_one_or_none()
                    if existing is not None:
                        continue
                    value_type, value_data = self.serde.dumps_typed(_sanitize_value(value))
                    session.add(
                        HarnessWorkflowCheckpointWriteRow(
                            thread_id=thread_id,
                            checkpoint_ns=namespace,
                            checkpoint_id=checkpoint_id,
                            task_id=task_id,
                            write_index=write_index,
                            channel=str(channel),
                            value_type=value_type,
                            value_data=value_data,
                            task_path=task_path,
                        )
                    )

    async def adelete_thread(self, thread_id: str) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                for model in (
                    HarnessWorkflowCheckpointWriteRow,
                    HarnessWorkflowCheckpointBlobRow,
                    HarnessWorkflowCheckpointRow,
                ):
                    await session.execute(delete(model).where(model.thread_id == thread_id))

    def get_next_version(self, current: str | None, channel: None) -> str:
        current_value = 0 if current is None else int(str(current).split(".")[0])
        return f"{current_value + 1:032}.{random.random():016}"

    async def _tuple_from_row(
        self,
        session: AsyncSession,
        row: HarnessWorkflowCheckpointRow,
    ) -> CheckpointTuple:
        checkpoint = self.serde.loads_typed((row.checkpoint_type, row.checkpoint_data))
        versions = checkpoint.get("channel_versions", {})
        values: dict[str, Any] = {}
        if versions:
            blobs = (
                await session.execute(
                    select(HarnessWorkflowCheckpointBlobRow).where(
                        HarnessWorkflowCheckpointBlobRow.thread_id == row.thread_id,
                        HarnessWorkflowCheckpointBlobRow.checkpoint_ns == row.checkpoint_ns,
                        HarnessWorkflowCheckpointBlobRow.channel.in_(list(versions)),
                    )
                )
            ).scalars().all()
            by_key = {(blob.channel, blob.channel_version): blob for blob in blobs}
            for channel, version in versions.items():
                blob = by_key.get((str(channel), str(version)))
                if blob is not None and blob.value_type != "empty":
                    values[str(channel)] = self.serde.loads_typed((blob.value_type, blob.value_data))
        writes = (
            await session.execute(
                select(HarnessWorkflowCheckpointWriteRow)
                .where(
                    HarnessWorkflowCheckpointWriteRow.thread_id == row.thread_id,
                    HarnessWorkflowCheckpointWriteRow.checkpoint_ns == row.checkpoint_ns,
                    HarnessWorkflowCheckpointWriteRow.checkpoint_id == row.checkpoint_id,
                )
                .order_by(HarnessWorkflowCheckpointWriteRow.id.asc())
            )
        ).scalars().all()
        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": row.thread_id,
                    "checkpoint_ns": row.checkpoint_ns,
                    "checkpoint_id": row.checkpoint_id,
                }
            },
            checkpoint={**checkpoint, "channel_values": values},
            metadata=self.serde.loads_typed((row.metadata_type, row.metadata_data)),
            parent_config=(
                {
                    "configurable": {
                        "thread_id": row.thread_id,
                        "checkpoint_ns": row.checkpoint_ns,
                        "checkpoint_id": row.parent_checkpoint_id,
                    }
                }
                if row.parent_checkpoint_id
                else None
            ),
            pending_writes=[
                (write.task_id, write.channel, self.serde.loads_typed((write.value_type, write.value_data)))
                for write in writes
            ],
        )


def _sanitize_checkpoint(checkpoint: Checkpoint) -> Checkpoint:
    sanitized = copy.deepcopy(checkpoint)
    values = sanitized.get("channel_values")
    if isinstance(values, dict):
        sanitized["channel_values"] = _sanitize_value(values)
    return sanitized


def _sanitize_value(value: Any, *, key: str | None = None) -> Any:
    if key is not None and key.lower() in _SENSITIVE_STATE_KEYS:
        return _REDACTED
    if isinstance(value, dict):
        return {
            str(item_key): _sanitize_value(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_value(item) for item in value)
    return value


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None
