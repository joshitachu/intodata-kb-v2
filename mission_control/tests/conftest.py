from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

# mission_control.config builds its Settings at import and requires MC_API_KEY.
os.environ.setdefault("MC_API_KEY", "test-key")

API_KEY = os.environ["MC_API_KEY"]
AUTH = {"Authorization": f"Bearer {API_KEY}"}


@pytest.fixture
def mc_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Point Mission Control's settings at a throwaway data dir."""
    from mission_control.config import settings

    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    clients = tmp_path / "clients.json"
    monkeypatch.setattr(settings, "api_key", API_KEY)
    monkeypatch.setattr(settings, "snapshots_dir", snapshots)
    monkeypatch.setattr(settings, "clients_config", clients)
    monkeypatch.setattr(settings, "licence_ledger_dir", tmp_path / "licences")
    monkeypatch.setattr(settings, "licence_signing_key_file", None)
    monkeypatch.setattr(settings, "licence_signing_kid", None)

    def add_client(slug: str, *, mcp_url: str | None = "https://acme.example",
                   control_key: str | None = "ctl-key", snapshot: dict | None = None) -> None:
        """Configure a client the way an operator does: ``mcp_url`` and
        ``control_key`` pinned in clients.json. ``snapshot`` (optional) writes
        a heartbeat, which must never influence addressing. With both
        ``mcp_url`` and ``control_key`` ``None`` no clients.json entry is made."""
        if snapshot is not None:
            (snapshots / f"{slug}.json").write_text(json.dumps(snapshot))
        entry = {k: v for k, v in (("control_key", control_key), ("mcp_url", mcp_url))
                 if v is not None}
        if entry:
            data = json.loads(clients.read_text()) if clients.exists() else {}
            data[slug] = entry
            clients.write_text(json.dumps(data))

    return {"tmp": tmp_path, "snapshots": snapshots, "clients": clients, "add_client": add_client}


@pytest.fixture
def instance(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Route every outbound ``httpx.AsyncClient`` through a mock instance.

    Set ``state["handler"]`` to a ``request -> httpx.Response`` callable; every
    request seen is appended to ``state["requests"]``.
    """
    state: dict[str, Any] = {"requests": [], "handler": lambda r: httpx.Response(200, json={})}
    real = httpx.AsyncClient

    def _handle(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        handler: Callable[[httpx.Request], httpx.Response] = state["handler"]
        return handler(request)

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(_handle)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return state


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from mission_control.main import app

    with TestClient(app) as c:
        yield c
