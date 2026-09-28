"""Record which runtime a deployment targeted, and which version it deployed.

Revision ID: 107_runtime_cluster_and_version
Revises: 106_job_scan_map_provenance

Three facts the mirror was throwing away, all of them present in the payloads
TMC already returns.

``runtime_job_runs.deployment_target`` -- a Talend task deploys either to a
single Remote Engine or to a Remote Engine Cluster, and the execution payload
says which (``runtime.type``: ``REMOTE_ENGINE`` vs ``REMOTE_ENGINE_CLUSTER``).
Without it, one route deployed to a two-node cluster and the same route
deployed twice by hand are indistinguishable downstream, so the read side
cannot decide whether two rows are one logical deployment or two.

``runtime_job_runs.artifact_version`` -- ``taskVersion`` from the same payload.
Verified on a live estate: ``processor_inther_product_jde_rest_p001`` has a V1
deployment on the cluster AND a V2 deployment on each engine at the same time.
Keying on (task, engine) alone collapses those to whichever started last, so a
superseded version can silently present itself as the running one.

``runtime_engines.cluster_id`` / ``cluster_name`` -- membership, read from
``/processing/runtimes/remote-engines/{id}`` and
``/processing/runtimes/remote-engine-clusters``. ``deployment_target`` says a
row belongs to *a* cluster; only this says *which*, which is what lets sibling
members be grouped into one row and named.

All five columns are nullable with no default. An existing row genuinely does
not know its target, version or cluster -- it was mirrored before these were
captured -- and NULL is that statement. Backfilling a guess would be worse than
the gap: consumers must be able to tell "deployed to a single engine" from "we
never recorded it", and a default makes those two the same value forever.

Reversible; downgrade drops the columns and loses only these distinctions.
No run, engine or telemetry row is read, rewritten or deleted either way.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "107_runtime_cluster_and_version"
down_revision: str | None = "106_job_scan_map_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS: tuple[tuple[str, str, int], ...] = (
    ("runtime_job_runs", "deployment_target", 64),
    ("runtime_job_runs", "artifact_version", 120),
    ("runtime_engines", "cluster_id", 255),
    ("runtime_engines", "cluster_name", 255),
)


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(item["name"] == column for item in inspector.get_columns(table))


def upgrade() -> None:
    for table, column, length in _COLUMNS:
        if _has_column(table, column):
            continue
        op.add_column(table, sa.Column(column, sa.String(length), nullable=True))
    # Grouping a route's cluster members is a per-cluster lookup on every
    # deployment read; without this it is a sequential scan of the engine
    # inventory once per route.
    op.create_index(
        "ix_runtime_engines_cluster_id",
        "runtime_engines",
        ["cluster_id"],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_runtime_engines_cluster_id",
        table_name="runtime_engines",
        if_exists=True,
    )
    for table, column, _length in _COLUMNS:
        if _has_column(table, column):
            op.drop_column(table, column)
