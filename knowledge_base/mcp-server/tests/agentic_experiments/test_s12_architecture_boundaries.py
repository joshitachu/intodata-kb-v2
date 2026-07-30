"""Wave 7 regression guards for the additive experiment isolation boundary."""

from __future__ import annotations

import ast
from dataclasses import fields
from pathlib import Path

from mcp_server.experiments.application.preflight import ImmutablePreflightReceipt
from mcp_server.experiments.domain.evaluation import (
    ComparabilityReceipt,
    EvaluationReceipt,
    PriceCardReceipt,
)
from mcp_server.experiments.domain.runtime import (
    CancelResult,
    RuntimeTaskEvent,
    RuntimeTaskResult,
    redact_payload,
)
from mcp_server.experiments.infrastructure.opencode.runtime import PINNED_OPENCODE_VERSION
from mcp_server.experiments.infrastructure.storage.experiment_retention import (
    RestoreReceipt,
    RetentionReceipt,
)
from mcp_server.experiments.mcp_tools.config_compiler import NEXUS_EXPERIMENT_TOOLS
from mcp_server.experiments.mcp_tools.ledger import ToolAuditLedger


MCP_SERVER_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = MCP_SERVER_ROOT / "src" / "mcp_server"
EXPERIMENT_ROOT = SOURCE_ROOT / "experiments"
EXPERIMENT_ROUTE = SOURCE_ROOT / "api" / "routes" / "agentic_experiments.py"
APP_PATH = SOURCE_ROOT / "api" / "app.py"
CONFIG_PATH = SOURCE_ROOT / "config.py"
OPENCODE_DOCKERFILE = MCP_SERVER_ROOT.parent / "opencode-runtime" / "Dockerfile"
OPENCODE_HEALTHCHECK = MCP_SERVER_ROOT.parent / "opencode-runtime" / "healthcheck.mjs"
TRANSPORT_PATH = EXPERIMENT_ROOT / "infrastructure" / "mcp_transport" / "app.py"

APPROVED_NEXUS_TOOLS = (
    "nexus_experiment_get_contract",
    "nexus_experiment_list_evidence",
    "nexus_experiment_search_evidence",
    "nexus_experiment_read_evidence",
    "nexus_experiment_traverse_relationships",
    "nexus_experiment_write_artifact",
    "nexus_experiment_submit_task",
)

# These are the legacy paths that can scan/generate/publish documentation or
# hand it to RAG.  Experiment baseline *reading* is deliberately represented by
# its own immutable adapters, rather than importing any of these services.
FORBIDDEN_LEGACY_PREFIXES = (
    "mcp_server.api.routes.generate_runs",
    "mcp_server.providers.custom",
    "mcp_server.services.atomic_swap",
    "mcp_server.services.rag_bridge",
    "mcp_server.services.scanner_service",
)

_SECRET_FIELD_PARTS = frozenset(
    {"api_key", "authorization", "credential", "password", "secret", "token"}
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imported_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "importlib"
            and node.func.attr == "import_module"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            modules.add(node.args[0].value)
    return modules


def _python_paths(*roots: Path) -> tuple[Path, ...]:
    return tuple(
        path
        for root in roots
        for path in (sorted(root.rglob("*.py")) if root.is_dir() else (root,))
    )


def _class_defaults(path: Path, class_name: str) -> dict[str, object]:
    for node in _tree(path).body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            defaults: dict[str, object] = {}
            for statement in node.body:
                if not (
                    isinstance(statement, ast.AnnAssign)
                    and isinstance(statement.target, ast.Name)
                    and statement.value is not None
                ):
                    continue
                try:
                    defaults[statement.target.id] = ast.literal_eval(statement.value)
                except ValueError:
                    continue
            return defaults
    raise AssertionError(f"{class_name} not found in {path}")


def _registered_transport_tool_names() -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_tree(TRANSPORT_PATH)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "tool":
            continue
        for keyword in node.keywords:
            if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                assert isinstance(keyword.value.value, str)
                names.add(keyword.value.value)
    return names


def test_experiment_api_and_runtime_do_not_import_legacy_publication_internals() -> None:
    violations = [
        f"{path.relative_to(SOURCE_ROOT)} imports {module}"
        for path in _python_paths(EXPERIMENT_ROOT, EXPERIMENT_ROUTE)
        for module in sorted(_imported_modules(path))
        if module.startswith(FORBIDDEN_LEGACY_PREFIXES)
    ]

    assert violations == []


def test_experiment_mount_is_feature_gated_and_never_reuses_docs_publish_route() -> None:
    app = _tree(APP_PATH)
    mounted_paths = {
        node.args[0].value
        for node in ast.walk(app)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "mount"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }

    assert "/internal/agentic-mcp" in mounted_paths
    assert "/mcp/generate" not in mounted_paths
    assert "_AgenticMcpFeatureGate" in APP_PATH.read_text(encoding="utf-8")


def test_agentic_feature_flags_are_disabled_by_default() -> None:
    defaults = _class_defaults(CONFIG_PATH, "MCPServerConfig")

    assert defaults["agentic_experiments_enabled"] is False
    assert defaults["agentic_paid_execution_enabled"] is False
    assert defaults["agentic_max_cost_usd"] == 0.0


def test_exact_approved_nexus_mcp_tool_surface_is_registered() -> None:
    assert NEXUS_EXPERIMENT_TOOLS == APPROVED_NEXUS_TOOLS
    assert _registered_transport_tool_names() == set(APPROVED_NEXUS_TOOLS)


def test_opencode_pin_is_consistent_across_runtime_config_and_container() -> None:
    config_defaults = _class_defaults(CONFIG_PATH, "MCPServerConfig")
    dockerfile = OPENCODE_DOCKERFILE.read_text(encoding="utf-8")
    healthcheck = OPENCODE_HEALTHCHECK.read_text(encoding="utf-8")

    assert PINNED_OPENCODE_VERSION == "1.18.4"
    assert config_defaults["agentic_opencode_version"] == PINNED_OPENCODE_VERSION
    assert "ARG OPENCODE_VERSION=1.18.4" in dockerfile
    assert "NEXUS_EXPECTED_OPENCODE_VERSION=1.18.4" in dockerfile
    assert '"1.18.4"' in healthcheck


def test_safe_receipts_and_runtime_outputs_have_no_secret_value_fields() -> None:
    safe_types = (
        ImmutablePreflightReceipt,
        PriceCardReceipt,
        ComparabilityReceipt,
        EvaluationReceipt,
        RetentionReceipt,
        RestoreReceipt,
        RuntimeTaskEvent,
        RuntimeTaskResult,
        CancelResult,
    )
    exposed = {
        f"{schema.__name__}.{field.name}"
        for schema in safe_types
        for field in fields(schema)
        if any(part in field.name.casefold() for part in _SECRET_FIELD_PARTS)
    }

    assert exposed == set()


def test_safe_receipt_and_audit_payloads_redact_secret_values_recursively() -> None:
    payload = {
        "password": "not-for-output",
        "nested": {"authorization": "Bearer not-for-output"},
        "items": [{"api_key": "not-for-output"}],
    }

    assert redact_payload(payload) == {
        "password": "[REDACTED]",
        "nested": {"authorization": "[REDACTED]"},
        "items": [{"api_key": "[REDACTED]"}],
    }
    ledger = ToolAuditLedger()
    record = ledger.append(
        tool_name="nexus_experiment_get_contract",
        outcome="denied",
        operation_id="operation-1",
        detail=payload,
    )
    assert record.detail == redact_payload(payload)
