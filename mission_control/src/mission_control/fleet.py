"""Fleet client registry: who the fleet is, where each instance lives, and how
each one authenticates its heartbeat (ADR-039).

Two sources, merged by ``FleetDirectory``:

* the **registry** (``FleetRegistry``; today ``JsonFleetRegistry``, a JSON file
  in the Mission Control data volume, ``MC_FLEET_REGISTRY``). Writable through
  ``/ops/fleet/clients``. It holds each client's pinned ``mcp_url``, its
  ``control_key`` (plaintext: Mission Control must SEND it as a bearer on every
  push, so it cannot be hashed; the file is 0600 and the key is never returned
  or logged) and a SHA-256 of its telemetry key (never the key itself).
* the legacy, read-only ``clients.json`` (``MC_CLIENTS_CONFIG``, see
  ``clients.py``). Its entries are reported as ``source: "file"`` and cannot be
  changed through the API. A registry entry with the same slug wins.

Why a plain SHA-256 of the telemetry key is enough: the key is 32 random bytes
from ``secrets`` (256 bits of entropy), so there is nothing for a slow,
salted password hash to protect against; a preimage search over 2**256 is not
feasible. A slow hash would only cost CPU on every heartbeat.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from .clients import ClientRegistry, default_registry
from .storage import InterProcessLock, atomic_write_text

logger = logging.getLogger(__name__)

#: Same rule the licence issuer enforces on ``sub`` and deploy-client.sh on
#: ``--client-name``; nginx's heartbeat route uses it too.
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

REGISTRY_VERSION = 1

Source = Literal["registry", "file"]


def valid_slug(slug: str) -> bool:
    return bool(SLUG_PATTERN.match(slug))


def new_telemetry_key() -> str:
    """A fresh telemetry key: 32 random bytes, urlsafe base64 (43 chars)."""
    return secrets.token_urlsafe(32)


def hash_telemetry_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


# -- errors ---------------------------------------------------------------------

class FleetError(Exception):
    """Base for registry refusals; ``code`` is the API's ``detail.code``."""

    code = "fleet_error"


class ClientExists(FleetError):
    code = "client_exists"


class ClientNotFound(FleetError):
    code = "client_not_found"


class ManagedByFile(FleetError):
    """The slug is defined only in the read-only ``clients.json``."""

    code = "managed_by_file"


class RegistryUnavailable(FleetError):
    """The registry file exists but cannot be read or parsed.

    Writes are refused rather than starting from an empty registry, which
    would silently drop every client in the damaged file.
    """

    code = "registry_unavailable"


# -- model ----------------------------------------------------------------------

@dataclass(frozen=True)
class FleetClient:
    slug: str
    mcp_url: str | None
    control_key: str | None
    telemetry_key_sha256: str | None
    source: Source
    created_at: str | None = None
    updated_at: str | None = None

    def __repr__(self) -> str:  # never print key material
        return (f"FleetClient(slug={self.slug!r}, mcp_url={self.mcp_url!r}, "
                f"source={self.source!r}, control_key=***, telemetry_key_sha256=***)")

    @property
    def has_control_key(self) -> bool:
        return bool(self.control_key)

    @property
    def heartbeat_auth(self) -> Literal["client_key", "shared_key_only"]:
        return "client_key" if self.telemetry_key_sha256 else "shared_key_only"

    def control_entry(self) -> dict[str, Any]:
        """The shape ``InstanceControlClient.resolve`` reads."""
        return {"control_key": self.control_key, "mcp_url": self.mcp_url}

    def telemetry_key_matches(self, presented: str) -> bool:
        if not self.telemetry_key_sha256:
            return False
        return hmac.compare_digest(hash_telemetry_key(presented).encode("ascii"),
                                   self.telemetry_key_sha256.encode("ascii"))

    def to_record(self) -> dict[str, Any]:
        return {
            "mcp_url": self.mcp_url,
            "control_key": self.control_key,
            "telemetry_key_sha256": self.telemetry_key_sha256,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_record(cls, slug: str, raw: dict[str, Any]) -> FleetClient:
        def text(name: str) -> str | None:
            value = raw.get(name)
            return value if isinstance(value, str) and value else None

        return cls(slug=slug, mcp_url=text("mcp_url"), control_key=text("control_key"),
                   telemetry_key_sha256=text("telemetry_key_sha256"), source="registry",
                   created_at=text("created_at"), updated_at=text("updated_at"))


class _Unset:
    """Sentinel for "field not given" in ``update`` (``None`` means "clear")."""


UNSET: Any = _Unset()


# -- repository -----------------------------------------------------------------

@runtime_checkable
class FleetRegistry(Protocol):
    """The writable client registry. Every method may raise ``RegistryUnavailable``."""

    def list(self) -> list[FleetClient]: ...

    def get(self, slug: str) -> FleetClient | None: ...

    def create(self, slug: str, *, mcp_url: str, control_key: str | None,
               telemetry_key_sha256: str) -> FleetClient:
        """Raise ``ClientExists`` if the slug is already registered."""
        ...

    def update(self, slug: str, *, mcp_url: Any = UNSET, control_key: Any = UNSET,
               telemetry_key_sha256: Any = UNSET) -> FleetClient:
        """Raise ``ClientNotFound`` if the slug is not registered."""
        ...

    def delete(self, slug: str) -> None:
        """Raise ``ClientNotFound`` if the slug is not registered."""
        ...


class JsonFleetRegistry:
    """``FleetRegistry`` over one JSON file.

    Format: ``{"version": 1, "clients": {"<slug>": {mcp_url, control_key,
    telemetry_key_sha256, created_at, updated_at}}}``. Every mutation is a
    read-modify-write under an exclusive lock (thread lock + ``flock`` on
    ``<file>.lock``, so several uvicorn workers are safe) and is written
    atomically with mode 0600. Reads need no lock: ``os.replace`` means a
    reader sees one complete version.
    """

    def __init__(self, path: Path | Callable[[], Path]) -> None:
        self._path = path
        self._locks: dict[Path, InterProcessLock] = {}
        self._locks_guard = threading.Lock()
        # Read cache for list/get, keyed by the file's identity. Heartbeats
        # with an unknown bearer look up the registry on every request, so an
        # unchanged file is parsed once. ``os.replace`` gives every write a new
        # inode, so the stamp changes even when mtime resolution is coarse;
        # our own writes also drop the cache explicitly.
        self._cache_guard = threading.Lock()
        self._cache_stamp: tuple[str, int, int, int] | None = None
        self._cache_data: dict[str, dict[str, Any]] = {}
        self._cache_error: RegistryUnavailable | None = None

    def path(self) -> Path:
        return self._path() if callable(self._path) else self._path

    def _lock(self, path: Path) -> InterProcessLock:
        with self._locks_guard:
            lock = self._locks.get(path)
            if lock is None:
                lock = self._locks[path] = InterProcessLock(path.with_name(path.name + ".lock"))
            return lock

    # -- file I/O -----------------------------------------------------------------

    def _read(self, path: Path) -> dict[str, dict[str, Any]]:
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            logger.error("fleet registry %s not readable: %s", path, type(exc).__name__)
            raise RegistryUnavailable(
                f"fleet registry not readable ({type(exc).__name__})") from None
        try:
            data = json.loads(text)
            clients = data["clients"]
            if not isinstance(clients, dict):
                raise TypeError("clients must be an object")
        except (ValueError, KeyError, TypeError) as exc:
            # Never log the content: it holds control keys.
            logger.error("fleet registry %s is malformed: %s", path, type(exc).__name__)
            raise RegistryUnavailable("fleet registry is malformed") from None
        return {slug: raw for slug, raw in clients.items()
                if isinstance(slug, str) and isinstance(raw, dict)}

    def _write(self, path: Path, clients: dict[str, dict[str, Any]]) -> None:
        body = {"version": REGISTRY_VERSION, "clients": dict(sorted(clients.items()))}
        try:
            atomic_write_text(path, json.dumps(body, indent=2) + "\n", mode=0o600)
        finally:
            self._invalidate()

    def _invalidate(self) -> None:
        with self._cache_guard:
            self._cache_stamp, self._cache_data, self._cache_error = None, {}, None

    def _read_cached(self, path: Path) -> dict[str, dict[str, Any]]:
        """``_read`` for the read-only paths, cached by (path, mtime, size, inode).

        A damaged file is cached too, so it is logged once per version rather
        than on every heartbeat. Mutations never use this: they re-read under
        the lock. Callers must not modify the returned mapping.
        """
        try:
            st = path.stat()
        except FileNotFoundError:
            return {}
        except OSError:
            return self._read(path)  # let _read classify and log it
        stamp = (str(path), st.st_mtime_ns, st.st_size, st.st_ino)
        with self._cache_guard:
            if stamp == self._cache_stamp:
                if self._cache_error is not None:
                    raise RegistryUnavailable(str(self._cache_error))
                return self._cache_data
            try:
                data = self._read(path)
            except RegistryUnavailable as exc:
                self._cache_stamp, self._cache_data, self._cache_error = stamp, {}, exc
                raise
            self._cache_stamp, self._cache_data, self._cache_error = stamp, data, None
            return data

    # -- FleetRegistry --------------------------------------------------------------

    def list(self) -> list[FleetClient]:
        return [FleetClient.from_record(slug, raw)
                for slug, raw in sorted(self._read_cached(self.path()).items())]

    def get(self, slug: str) -> FleetClient | None:
        raw = self._read_cached(self.path()).get(slug)
        return FleetClient.from_record(slug, raw) if raw is not None else None

    def create(self, slug: str, *, mcp_url: str, control_key: str | None,
               telemetry_key_sha256: str) -> FleetClient:
        path = self.path()
        with self._lock(path).hold():
            clients = self._read(path)
            if slug in clients:
                raise ClientExists(f"client '{slug}' is already registered")
            now = _now()
            client = FleetClient(slug=slug, mcp_url=mcp_url, control_key=control_key or None,
                                 telemetry_key_sha256=telemetry_key_sha256,
                                 source="registry", created_at=now, updated_at=now)
            clients[slug] = client.to_record()
            self._write(path, clients)
            return client

    def update(self, slug: str, *, mcp_url: Any = UNSET, control_key: Any = UNSET,
               telemetry_key_sha256: Any = UNSET) -> FleetClient:
        path = self.path()
        with self._lock(path).hold():
            clients = self._read(path)
            raw = clients.get(slug)
            if raw is None:
                raise ClientNotFound(f"client '{slug}' is not registered")
            client = FleetClient.from_record(slug, raw)
            changes: dict[str, Any] = {}
            if mcp_url is not UNSET:
                changes["mcp_url"] = mcp_url
            if control_key is not UNSET:
                changes["control_key"] = control_key or None
            if telemetry_key_sha256 is not UNSET:
                changes["telemetry_key_sha256"] = telemetry_key_sha256
            client = replace(client, **changes, updated_at=_now())
            clients[slug] = client.to_record()
            self._write(path, clients)
            return client

    def delete(self, slug: str) -> None:
        path = self.path()
        with self._lock(path).hold():
            clients = self._read(path)
            if clients.pop(slug, None) is None:
                raise ClientNotFound(f"client '{slug}' is not registered")
            self._write(path, clients)


# -- merged view ----------------------------------------------------------------

@dataclass(frozen=True)
class HeartbeatLookup:
    client: FleetClient | None
    registry_available: bool

    @property
    def has_telemetry_key(self) -> bool:
        return self.client is not None and bool(self.client.telemetry_key_sha256)


@dataclass(frozen=True)
class FleetListing:
    clients: list[FleetClient]
    registry_unavailable: bool

class FleetDirectory:
    """Registry entries plus legacy ``clients.json`` entries, registry first.

    ``get`` has the duck-typed shape ``InstanceControlClient`` has always used
    (a dict with ``control_key`` / ``mcp_url``, or ``None``), so push and
    trigger resolution see both sources through one call.
    """

    def __init__(self, registry: FleetRegistry, file_source: ClientRegistry) -> None:
        self.registry = registry
        self.file_source = file_source

    def _file_client(self, slug: str, raw: Any) -> FleetClient | None:
        if not isinstance(raw, dict):
            return None

        def text(name: str) -> str | None:
            value = raw.get(name)
            return value if isinstance(value, str) and value.strip() else None

        return FleetClient(slug=slug, mcp_url=text("mcp_url"), control_key=text("control_key"),
                           telemetry_key_sha256=None, source="file")

    def _registry_get(self, slug: str) -> FleetClient | None:
        """Registry lookup for the READ paths: a damaged registry is "no entry"
        (logged by the registry) so file-defined clients keep working."""
        try:
            return self.registry.get(slug)
        except RegistryUnavailable:
            return None

    def entry(self, slug: str) -> FleetClient | None:
        client = self._registry_get(slug)
        if client is not None:
            return client
        return self._file_client(slug, self.file_source.get(slug))

    def heartbeat_lookup(self, slug: str) -> HeartbeatLookup:
        """The slug's REGISTRY entry for heartbeat auth, and whether the registry
        could be read at all. Only registry entries carry telemetry keys; the
        caller needs ``registry_available`` to fail closed when it cannot tell
        whether a slug has one."""
        try:
            return HeartbeatLookup(self.registry.get(slug), registry_available=True)
        except RegistryUnavailable:
            return HeartbeatLookup(None, registry_available=False)

    def listing(self) -> FleetListing:
        """Every client, plus whether the registry was unreadable (then only
        file entries are in ``clients``)."""
        try:
            registered = {c.slug: c for c in self.registry.list()}
            unavailable = False
        except RegistryUnavailable:
            registered, unavailable = {}, True
        merged = dict(registered)
        for slug, raw in self.file_source.all().items():
            if slug in merged:
                continue
            if not valid_slug(slug):
                logger.warning("clients.json: skipping entry with an invalid slug")
                continue
            client = self._file_client(slug, raw)
            if client is not None:
                merged[slug] = client
        return FleetListing([merged[slug] for slug in sorted(merged)],
                            registry_unavailable=unavailable)

    def entries(self) -> list[FleetClient]:
        return self.listing().clients

    def get(self, slug: str) -> dict[str, Any] | None:
        client = self._registry_get(slug)
        if client is not None:
            return client.control_entry()
        return self.file_source.get(slug)


def _default_registry_path() -> Path:
    from .config import settings

    return settings.fleet_registry_path


default_fleet_registry = JsonFleetRegistry(_default_registry_path)
default_directory = FleetDirectory(default_fleet_registry, default_registry)


def get_fleet_directory() -> FleetDirectory:
    """FastAPI dependency; tests may override it."""
    return default_directory
