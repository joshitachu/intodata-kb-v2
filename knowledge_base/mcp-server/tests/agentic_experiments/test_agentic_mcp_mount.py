from __future__ import annotations

from fastapi.responses import JSONResponse
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from mcp_server.api.app import _AgenticMcpFeatureGate, app
from mcp_server.config import config


async def _ok(_request):
    return JSONResponse({"status": "reachable"})


def test_application_mounts_internal_agentic_mcp_before_any_spa_catch_all() -> None:
    matching = [
        (index, route)
        for index, route in enumerate(app.routes)
        if getattr(route, "path", None) == "/internal/agentic-mcp"
    ]

    assert len(matching) == 1
    index, route = matching[0]
    catch_all_indexes = [
        candidate_index
        for candidate_index, candidate in enumerate(app.routes)
        if getattr(candidate, "path", None) == "/{full_path:path}"
    ]
    assert not catch_all_indexes or index < min(catch_all_indexes)
    assert route.name == "agentic-experiment-mcp"


def test_internal_agentic_mcp_gate_is_hidden_when_feature_is_off(
    monkeypatch,
) -> None:
    monkeypatch.setattr(config, "agentic_experiments_enabled", False)
    gate = _AgenticMcpFeatureGate(Starlette(routes=[Route("/", _ok)]))

    with TestClient(gate) as client:
        response = client.get("/")

    assert response.status_code == 404


def test_internal_agentic_mcp_gate_fails_closed_without_signing_secret(
    monkeypatch,
) -> None:
    monkeypatch.setattr(config, "agentic_experiments_enabled", True)
    monkeypatch.setattr(config, "agentic_task_token_secret", "")
    gate = _AgenticMcpFeatureGate(Starlette(routes=[Route("/", _ok)]))

    with TestClient(gate) as client:
        response = client.get("/")

    assert response.status_code == 503


def test_internal_agentic_mcp_gate_passes_only_after_both_controls(
    monkeypatch,
) -> None:
    monkeypatch.setattr(config, "agentic_experiments_enabled", True)
    monkeypatch.setattr(config, "agentic_task_token_secret", "t" * 32)
    monkeypatch.setattr(config, "agentic_opencode_password", "runtime-password")
    gate = _AgenticMcpFeatureGate(Starlette(routes=[Route("/", _ok)]))

    with TestClient(gate) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {"status": "reachable"}
