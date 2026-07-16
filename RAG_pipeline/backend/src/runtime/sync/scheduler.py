"""Operational scheduler for runtime source sync cycles."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from src.runtime.sync.service import RuntimeSyncAdapter, RuntimeSyncCycleResult, run_runtime_sync_cycle
from src.runtime.insights.semantic import RuntimeSemanticIndexer

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AsyncSession]
AdapterFactory = Callable[[], RuntimeSyncAdapter]


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
