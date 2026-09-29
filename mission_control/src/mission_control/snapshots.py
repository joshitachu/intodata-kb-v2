"""Read access to the heartbeat snapshots written by the telemetry receiver."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import settings


def snapshot_path(client_slug: str) -> Path:
    return settings.snapshots_dir / f"{client_slug}.json"


def load_snapshot(client_slug: str) -> dict[str, Any] | None:
    """The stored snapshot, or ``None`` when there is none for this client."""
    path = snapshot_path(client_slug)
    if not path.exists():
        return None
    return json.loads(path.read_text())
