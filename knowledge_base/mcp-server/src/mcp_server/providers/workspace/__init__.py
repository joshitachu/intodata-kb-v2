"""The ``workspace-v1`` lane: a per-project workspace an agent navigates itself.

Slice 1 is deterministic staging only -- layout plus builder. There is no
provider, no runner and no prompt here yet; Slice 2 adds them.

Deliberately imports nothing from ``mcp_server.providers.custom``. That package's
``__init__`` imports the 7,876-line ``harness.py``, and the plan's risk section
names accidental coupling to it as what made the previous two attempts at this
architecture impossible to remove. This package depends on the standard library
and its own two modules.
"""

from __future__ import annotations

from mcp_server.providers.workspace.builder import (
    BUILDER_VERSION,
    Provenance,
    ScanApprovalError,
    WorkspaceBuildError,
    WorkspaceBuildReceipt,
    build_workspace,
    load_scan_approval,
    read_provenance,
    scan_fingerprint,
    verify_no_silent_truncation,
)
from mcp_server.providers.workspace.layout import (
    WORKSPACE_SCHEMA_VERSION,
    WorkspaceLayout,
    WorkspacePathError,
    workspaces_dir,
)

__all__ = [
    "BUILDER_VERSION",
    "WORKSPACE_SCHEMA_VERSION",
    "Provenance",
    "ScanApprovalError",
    "WorkspaceBuildError",
    "WorkspaceBuildReceipt",
    "WorkspaceLayout",
    "WorkspacePathError",
    "build_workspace",
    "load_scan_approval",
    "read_provenance",
    "scan_fingerprint",
    "verify_no_silent_truncation",
    "workspaces_dir",
]
