"""Operational scheduler for runtime source sync cycles."""

from __future__ import annotations

import asyncio
import datetime
import logging
from collections.abc import Callable, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from src.runtime.sync.service import (
    RuntimeSyncAdapter,
    RuntimeSyncCycleResult,
    run_runtime_source_sync_cycle,
    run_runtime_sync_cycle,
)
from src.runtime.insights.semantic import RuntimeSemanticIndexer

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AsyncSession]
AdapterFactory = Callable[[], RuntimeSyncAdapter]
NowFactory = Callable[[], datetime.datetime]


class RuntimeSyncScheduler:
    """Runs configured runtime adapter sync cycles on a fixed interval."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        adapter_factory: AdapterFactory,
        source_key: str,
        project_ids: Sequence[str],
        interval_seconds: int = 60,
        semantic_indexer: RuntimeSemanticIndexer | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._adapter_factory = adapter_factory
        self._source_key = source_key
        self._project_ids = list(project_ids)
        self._interval_seconds = max(1, interval_seconds)
        self._semantic_indexer = semantic_indexer
        self._stop = asyncio.Event()
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        return self._running

    async def run_once(self) -> list[RuntimeSyncCycleResult]:
        """Run one non-overlapping sync pass for every configured project."""
        if self._lock.locked():
            logger.info("Runtime sync skipped because previous cycle is still running")
            return []
        async with self._lock:
            results: list[RuntimeSyncCycleResult] = []
            for project_id in self._project_ids:
                adapter = self._adapter_factory()
                async with self._session_factory() as session:
                    try:
                        result = await run_runtime_sync_cycle(
                            session,
                            adapter=adapter,
                            source_key=self._source_key,
                            project_id=project_id,
                            semantic_indexer=self._semantic_indexer,
                        )
                        await session.commit()
                    except Exception:
                        await session.rollback()
                        logger.exception(
                            "Runtime sync cycle crashed for source=%s project=%s",
                            self._source_key,
                            project_id,
                        )
                        continue
                    results.append(result)
                    logger.info(
                        "Runtime sync cycle finished source=%s project=%s status=%s",
                        self._source_key,
                        project_id,
                        result.status.value,
                    )
            return results

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop.clear()
        while not self._stop.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_seconds)
            except TimeoutError:
                continue
        self._running = False

    def start_background(self) -> asyncio.Task[None]:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.start())
        return self._task

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task


def parse_project_ids(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


class RuntimeSourcePollingScheduler:
    """Polls every enabled runtime source config on its own configured interval.

    Unlike :class:`RuntimeSyncScheduler` (which fetches through a fixed adapter
    with deploy-level credentials), this scheduler discovers registered sources
    from the store each tick and runs the source-configured sync cycle. Credentials
    therefore come from each source's stored record, so automatic polling uses the
    exact same path as the manual "Sync now" action and honours the per-source
    ``enabled`` flag and ``sync_interval_seconds`` set from the UI.
    """

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        tick_seconds: int = 30,
        semantic_indexer: RuntimeSemanticIndexer | None = None,
        now_factory: NowFactory = _utc_now,
    ) -> None:
        self._session_factory = session_factory
        # The tick is the polling granularity; individual sources are only synced
        # once their own interval has elapsed. Bounded below by the minimum source
        # interval (30s) so short intervals are still honoured.
        self._tick_seconds = max(5, min(tick_seconds, 30))
        self._semantic_indexer = semantic_indexer
        self._now = now_factory
        self._stop = asyncio.Event()
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        return self._running

    def _is_due(self, source, now: datetime.datetime) -> bool:
        last_attempt = source.health.last_attempt_at if source.health else None
        if last_attempt is None:
            return True
        if last_attempt.tzinfo is None:
            last_attempt = last_attempt.replace(tzinfo=datetime.UTC)
        return (now - last_attempt).total_seconds() >= source.sync_interval_seconds

    async def run_once(self) -> list[RuntimeSyncCycleResult]:
        """Sync every enabled source whose interval has elapsed (non-overlapping)."""
        if self._lock.locked():
            logger.info("Runtime source polling skipped because previous tick is still running")
            return []
        async with self._lock:
            from src.runtime.store.repository import RuntimeStoreRepository

            now = self._now()
            async with self._session_factory() as session:
                repo = RuntimeStoreRepository(session)
                # Only enabled sources with stored credentials — never the
                # discovered/mirror rows written by sync batches.
                sources = await repo.list_pollable_sources()
            due = [source for source in sources if self._is_due(source, now)]

            results: list[RuntimeSyncCycleResult] = []
            for source in due:
                async with self._session_factory() as session:
                    try:
                        result = await run_runtime_source_sync_cycle(
                            session,
                            source_id=source.id,
                            now=now,
                            semantic_indexer=self._semantic_indexer,
                        )
                        await session.commit()
                    except Exception:
                        await session.rollback()
                        logger.exception(
                            "Runtime source sync crashed for source_id=%s estate=%s",
                            source.id,
                            source.estate_key,
                        )
                        continue
                    results.append(result)
                    logger.info(
                        "Runtime source sync finished source_id=%s estate=%s status=%s",
                        source.id,
                        source.estate_key,
                        result.status.value,
                    )
            return results

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop.clear()
        while not self._stop.is_set():
            try:
                await self.run_once()
            except Exception:
                logger.exception("Runtime source polling tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._tick_seconds)
            except TimeoutError:
                continue
        self._running = False

    def start_background(self) -> asyncio.Task[None]:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.start())
        return self._task

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
