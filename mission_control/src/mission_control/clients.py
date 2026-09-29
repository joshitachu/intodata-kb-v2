"""Legacy, read-only per-client config file (``clients.json``).

Since ADR-039 clients are normally registered through Mission Control
(``/ops/fleet/clients``, stored by ``fleet.JsonFleetRegistry``). This file is
still read, merged behind the registry by ``fleet.FleetDirectory``: its entries
are reported as ``source: "file"`` and cannot be changed through the API.

Config file: ``MC_CLIENTS_CONFIG`` (default ``clients.json`` next to the
snapshots directory; see ``Settings.clients_config_path``).
Format::

    {
      "acme": {
        "control_key": "some-secret-control-key",
        "mcp_url": "https://acme.nexus.example"
      }
    }

Both fields are required for any control call (licence push, scan/generate
triggers). ``mcp_url`` must be pinned here: the URL a heartbeat reports is never
used for addressing, because a heartbeat may still arrive on the shared
``MC_API_KEY`` and a forged one could redirect the control key. It must be ``https://`` (plain ``http://``
only for localhost) and carry no credentials. The control_key must match
MCP_OPS_CONTROL_KEY on the target VPS.

The file is re-read when its mtime or size changes, so an operator can add a
client without restarting Mission Control.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: The pre-settings default, derived from ``__file__`` (four levels up: the repo
#: root in a source checkout, somewhere under site-packages when installed).
#: Kept only as a fallback when ``MC_CLIENTS_CONFIG`` is unset and the data-dir
#: default does not exist, so an existing file there keeps working.
_LEGACY_PATH = Path(__file__).resolve().parents[3] / "clients.json"


class ClientRegistry:
    """Reads ``clients.json`` and caches it by (mtime, size)."""

    def __init__(self, path: Path | Callable[[], Path]) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._stamp: tuple[str, int, int] | None = None
        self._data: dict[str, Any] = {}

    def path(self) -> Path:
        return self._path() if callable(self._path) else self._path

    def _load(self) -> dict[str, Any]:
        path = self.path()
        try:
            stat = path.stat()
        except FileNotFoundError:
            return {}
        if path.is_dir():
            # The operator compose files bind-mount this file optionally; when
            # the host file is absent Docker creates an empty directory in its
            # place. That means "no file clients", not a malformed file.
            return {}
        stamp = (str(path), stat.st_mtime_ns, stat.st_size)
        with self._lock:
            if stamp == self._stamp:
                return self._data
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise TypeError("top level must be an object keyed by client slug")
            except (OSError, ValueError, TypeError) as exc:  # malformed: "no clients", loudly
                # Never log the content: it holds control keys.
                logger.warning("Failed to read clients config %s: %s", path, type(exc).__name__)
                return {}
            self._stamp, self._data = stamp, data
            return data

    def get(self, slug: str) -> dict[str, Any] | None:
        """The client's entry, or ``None`` when the slug is not configured."""
        entry = self._load().get(slug)
        return entry if isinstance(entry, dict) else None

    def all(self) -> dict[str, dict[str, Any]]:
        """Every well-formed entry, keyed by slug (a copy; the cache stays private)."""
        return {slug: dict(entry) for slug, entry in self._load().items()
                if isinstance(slug, str) and isinstance(entry, dict)}


def _default_path() -> Path:
    from .config import settings

    if settings.clients_config is not None:
        return settings.clients_config
    path = settings.clients_config_path
    if not path.exists() and _LEGACY_PATH.exists():
        return _LEGACY_PATH
    return path


default_registry = ClientRegistry(_default_path)


def load_client_config(slug: str) -> dict[str, Any]:
    """Backwards-compatible accessor: the client's control entry (registry first,
    then this file), or ``{}``."""
    from .fleet import default_directory

    return default_directory.get(slug) or {}
