"""retired: projects and scan_runs are owned by the MCP server

Revision ID: 003
Revises: 002
Create Date: 2026-02-18

Originally this revision created ``projects`` and ``scan_runs`` "for the MCP
server", guarded by ``if "projects" not in existing`` on the assumption that
whichever service reached the shared database first would win the race.

There is no race. ``docker-compose.yml`` makes ``mcp-server`` wait for
``backend`` to become healthy, so the backend's alembic run ALWAYS executed
first and always created this copy of the table. The copy is a snapshot of the
MCP model as it stood in Feb 2026 -- 17 columns against the model's 34, missing
``connection_kind``, ``github_installation_id``, ``plugin_pack_id``,
``azure_devops_connection_id`` and twelve others. Because ``create_all`` never
alters an existing table, the MCP server could never repair it, and startup
died on ``ALTER TABLE projects ALTER COLUMN connection_kind`` against a column
that was never created. Every clean deployment hit this.

Both tables are declared as models in ``mcp_server.db.models`` (``Project``,
``ScanRun``). That is the single owner, and its ``create_all`` builds them in
full whichever service starts first. Duplicating the DDL here is what allowed
the two definitions to drift, so the duplicate is removed rather than
resynchronised.

Kept as a no-op revision so that databases which already ran it keep their
tables and their alembic history stays intact. No other RAG revision references
either table, so nothing downstream depends on this one creating them.
"""

from typing import Sequence, Union

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No-op: ``projects`` and ``scan_runs`` belong to mcp_server.db.models."""


def downgrade() -> None:
    """No-op: this revision no longer owns either table, so it drops neither."""
