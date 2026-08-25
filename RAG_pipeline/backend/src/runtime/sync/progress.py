"""In-flight state for runtime source syncs.

Why in memory: a sync cycle is one transaction that commits at the end, so
nothing it writes is visible until it finishes. A progress row in the same
transaction would be invisible for exactly the period it is needed. This keeps
the live state beside the process running the cycle instead, which is also the
right lifetime -- if the process dies the sync died with it, and a restart
correctly reports nothing running.

It doubles as the concurrency guard. ``begin`` refuses a second start for a
source already syncing, which is what stops a repeatedly-clicked "Sync now" from
stacking several full imports of the same tenant on top of each other.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field


@dataclass
class SyncProgress:
    """What a running cycle is doing right now."""

    source_id: int
    started_at: datetime.datetime
    # Human-readable, shown verbatim in the UI: "Fetching from the source",
    # "Writing 14,823 records". Phases are coarse on purpose -- the provider
    # fan-out is concurrent, so a percentage would be a guess.
    phase: str = "Starting"
    records: int = 0
    scope: str | None = None


@dataclass
class _Registry:
    running: dict[int, SyncProgress] = field(default_factory=dict)

    def begin(
        self,
        source_id: int,
        *,
        now: datetime.datetime,
        scope: str | None = None,
    ) -> bool:
        """Claim a source for syncing. False when one is already in flight."""
        if source_id in self.running:
            return False
        self.running[source_id] = SyncProgress(
            source_id=source_id, started_at=now, scope=scope
        )
        return True

    def update(self, source_id: int, *, phase: str | None = None, records: int | None = None) -> None:
        current = self.running.get(source_id)
        if current is None:
            return
        if phase is not None:
            current.phase = phase
        if records is not None:
            current.records = records

    def finish(self, source_id: int) -> None:
        self.running.pop(source_id, None)

    def get(self, source_id: int) -> SyncProgress | None:
        return self.running.get(source_id)

    def snapshot(self) -> list[SyncProgress]:
        return list(self.running.values())


sync_progress = _Registry()
