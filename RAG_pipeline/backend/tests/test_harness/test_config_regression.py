from __future__ import annotations

import ast
from pathlib import Path


def test_backend_compose_uses_mcp_service_url_for_docs_provider() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    compose = (repo_root / "docker-compose.yml").read_text()

    assert "MCP_DOCS_BASE_URL: ${MCP_DOCS_BASE_URL:-http://mcp-server:8002/mcp}" in compose


def test_alembic_revision_ids_fit_version_table_column() -> None:
    backend_root = Path(__file__).resolve().parents[2]
    for migration in (backend_root / "alembic" / "versions").glob("*.py"):
        tree = ast.parse(migration.read_text(), filename=str(migration))
        revision = next(
            (
                value.value
                for node in tree.body
                for target, value in [
                    (node.target, node.value)
                    if isinstance(node, ast.AnnAssign)
                    else (
                        node.targets[0],
                        node.value,
                    )
                    if isinstance(node, ast.Assign) and len(node.targets) == 1
                    else (None, None)
                ]
                if isinstance(target, ast.Name)
                and target.id == "revision"
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
            ),
            None,
        )
        assert revision is not None, f"Missing revision identifier in {migration.name}"
        assert len(revision) <= 32, f"Revision identifier exceeds varchar(32): {revision}"
