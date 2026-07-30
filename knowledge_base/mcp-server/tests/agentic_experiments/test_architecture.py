"""Dependency-direction guardrails for the additive experiment context."""

from __future__ import annotations

import ast
from pathlib import Path


EXPERIMENT_ROOT = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "mcp_server"
    / "experiments"
)

FORBIDDEN_DOMAIN_PREFIXES = (
    "fastapi",
    "sqlalchemy",
    "mcp_server.api",
    "mcp_server.db",
    "mcp_server.services",
)

FORBIDDEN_APPLICATION_PREFIXES = (
    "fastapi",
    "sqlalchemy",
    "mcp_server.api",
    "mcp_server.db",
    "mcp_server.providers",
    "mcp_server.services.atomic_swap",
    "mcp_server.services.rag_bridge",
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _assert_no_forbidden_imports(
    directory: Path,
    forbidden_prefixes: tuple[str, ...],
) -> None:
    if not directory.exists():
        return
    violations: list[str] = []
    for path in sorted(directory.rglob("*.py")):
        for imported in sorted(_imports(path)):
            if imported.startswith(forbidden_prefixes):
                violations.append(
                    f"{path.relative_to(EXPERIMENT_ROOT)} imports {imported}"
                )
    assert violations == []


def test_domain_is_framework_and_infrastructure_free() -> None:
    _assert_no_forbidden_imports(
        EXPERIMENT_ROOT / "domain",
        FORBIDDEN_DOMAIN_PREFIXES,
    )


def test_application_does_not_depend_on_framework_or_legacy_side_effects() -> None:
    _assert_no_forbidden_imports(
        EXPERIMENT_ROOT / "application",
        FORBIDDEN_APPLICATION_PREFIXES,
    )
