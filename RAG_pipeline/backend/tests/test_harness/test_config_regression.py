from __future__ import annotations

from pathlib import Path


def test_backend_compose_uses_mcp_service_url_for_docs_provider() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    compose = (repo_root / "docker-compose.yml").read_text()

    assert "MCP_DOCS_BASE_URL: ${MCP_DOCS_BASE_URL:-http://mcp-server:8002/mcp}" in compose
