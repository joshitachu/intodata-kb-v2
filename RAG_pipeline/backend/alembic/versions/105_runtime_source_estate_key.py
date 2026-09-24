"""Runtime sources lose their owning project and gain an estate key.

Revision ID: 105_runtime_source_estate_key
Revises: 104_conversation_lane

`runtime_sources.project_id` was doing three jobs at once: who holds the
credential and pays the poll, what every mirrored row is stamped with, and which
projects may read the result. A vendor tenant therefore could not be registered
before a project existed, `uq_runtime_sources_project_tmc` stopped a project
from connecting a SECOND, different tenant, and deleting the owning project
orphaned the source.

This revision splits those three apart:

* `estate_key` keeps the STAMP job, and is backfilled to each source's current
  `project_id`. That is the crux of the design: not one of the ~868 MB of rows
  in `runtime_executions`, `runtime_failures` or `runtime_log_events` is
  re-stamped, their `(project_id, execution_id)` uniqueness is untouched, and no
  index is rebuilt. A source that served one project before this migration emits
  the identical predicate afterwards.
* `runtime_source_read_grants` keeps the ACCESS job and becomes the complete
  answer to it. Every project that could reach a source before gets a row, from
  all three paths that granted access (see `_backfill_attachments`), so nobody
  loses access silently.
* nothing keeps the OWNERSHIP job, because it was never a real relation --
  `physical_identity` is what actually guarantees one tenant, one mirror, one
  poll, and it stays exactly as it is.

Rerunnable: every statement is guarded, so upgrade -> downgrade -> upgrade
leaves the same rows.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "105_runtime_source_estate_key"
down_revision: str | None = "104_conversation_lane"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: Stamped on every attachment row this migration creates, so `downgrade()` can
#: remove exactly those and leave a genuine, operator-created read grant alone.
#: Without it the two would be indistinguishable and a downgrade would either
#: strand rows the old code forbids (the owner's own) or delete grants nobody
#: asked it to.
_MIGRATION_MARKER = "migration:105_runtime_source_estate_key"

#: Must match `ESTATE_KEY_PREFIX` in src/db/models/runtime_data.py.
_ESTATE_KEY_PREFIX = "estate:"


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(item["name"] == column for item in inspector.get_columns(table))


def _has_index(table: str, name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(item["name"] == name for item in inspector.get_indexes(table))


def _has_unique_constraint(table: str, name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(
        item["name"] == name for item in inspector.get_unique_constraints(table)
    )


def _backfill_attachments(*, include_former_owner: bool) -> None:
    """One attachment row per access path that existed before this revision.

    Three of them, and leaving any one out would revoke access from somebody
    without telling them:

    1. the source's own `project_id` -- the former owner, whose read was implied
       by ownership and is now a row like everyone else's;
    2. every existing read-grant row -- already in this table, so nothing to do
       beyond not disturbing it;
    3. every distinct project in `runtime_workspace_project_map` for the source
       -- those containers' rows are stamped with the MAPPED project, so that
       project was reading this source's mirror all along.

    `NOT EXISTS` on `(source_id, project_id)` makes each insert idempotent and
    makes an existing grant win: a real grant may carry `environment_ids` or
    `window_days`, and an unconditional insert would widen it to everything.

    `include_former_owner` is False on a re-run over an already-migrated schema,
    where the column that path reads no longer exists. The workspace-map path is
    unconditional: it reads its own table, which this revision does not touch.
    """
    if include_former_owner:
        op.execute(
            sa.text(
                """
                INSERT INTO runtime_source_read_grants
                    (source_id, project_id, environment_ids, window_days, created_at, created_by)
                SELECT s.id, s.project_id, NULL, NULL, NOW(), :marker
                FROM runtime_sources s
                WHERE NOT EXISTS (
                    SELECT 1 FROM runtime_source_read_grants g
                    WHERE g.source_id = s.id AND g.project_id = s.project_id
                )
                """
            ).bindparams(marker=_MIGRATION_MARKER)
        )
    op.execute(
        sa.text(
            """
            INSERT INTO runtime_source_read_grants
                (source_id, project_id, environment_ids, window_days, created_at, created_by)
            SELECT m.source_id, m.project_id, NULL, NULL, NOW(), :marker
            FROM (
                SELECT DISTINCT source_id, project_id FROM runtime_workspace_project_map
            ) AS m
            WHERE m.project_id IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM runtime_source_read_grants g
                WHERE g.source_id = m.source_id AND g.project_id = m.project_id
            )
            """
        ).bindparams(marker=_MIGRATION_MARKER)
    )


def upgrade() -> None:
    # Everything below is conditioned on this rather than assumed, so running
    # the revision a second time over an already-migrated schema is a no-op
    # instead of an error on a column that is no longer there.
    has_legacy_owner = _has_column("runtime_sources", "project_id")

    if not _has_column("runtime_sources", "estate_key"):
        op.add_column(
            "runtime_sources", sa.Column("estate_key", sa.String(255), nullable=True)
        )
    if has_legacy_owner:
        # Backfilled from the column it replaces, which is what makes this
        # migration data-free for every mirrored table: the stamp does not move.
        op.execute(
            sa.text(
                "UPDATE runtime_sources SET estate_key = project_id WHERE estate_key IS NULL"
            )
        )
    op.alter_column("runtime_sources", "estate_key", nullable=False)

    # Attachments are written BEFORE `project_id` goes, since one of the three
    # paths is derived from it.
    _backfill_attachments(include_former_owner=has_legacy_owner)

    if not _has_index("runtime_sources", "ix_runtime_sources_estate_key"):
        op.create_index("ix_runtime_sources_estate_key", "runtime_sources", ["estate_key"])
    if not _has_unique_constraint("runtime_sources", "uq_runtime_sources_estate_source"):
        op.create_unique_constraint(
            "uq_runtime_sources_estate_source", "runtime_sources", ["estate_key", "source_key"]
        )

    # Meaningless without an owning project, and the thing that refused a
    # project's second tenant. `ix_runtime_sources_physical_identity` is the
    # duplicate guard that matters and is deliberately untouched.
    if _has_index("runtime_sources", "uq_runtime_sources_project_tmc"):
        op.drop_index("uq_runtime_sources_project_tmc", table_name="runtime_sources")
    if _has_unique_constraint("runtime_sources", "uq_runtime_sources_project_source"):
        op.drop_constraint(
            "uq_runtime_sources_project_source", "runtime_sources", type_="unique"
        )
    if _has_index("runtime_sources", "ix_runtime_sources_project_id"):
        op.drop_index("ix_runtime_sources_project_id", table_name="runtime_sources")
    if _has_column("runtime_sources", "project_id"):
        op.drop_column("runtime_sources", "project_id")


def downgrade() -> None:
    """Restore the owning project, and refuse to lie if it cannot be restored.

    Two honest limits, both deliberate:

    * a source registered with NO project has no owner to put back. It takes the
      lowest-sorting project attached to it, and its own estate key if nothing is
      attached -- the old code will simply match no rows for that string, which
      is a visible, diagnosable state rather than a wrong owner.
    * recreating `uq_runtime_sources_project_tmc` FAILS, loudly and inside the
      transaction, if a project has picked up a second TMC source in the
      meantime. That is the point: the old schema cannot represent that estate,
      and quietly dropping one of the two sources to fit would destroy a mirror.
    """
    has_estate_key = _has_column("runtime_sources", "estate_key")
    if not _has_column("runtime_sources", "project_id"):
        op.add_column(
            "runtime_sources", sa.Column("project_id", sa.String(255), nullable=True)
        )
    if has_estate_key:
        op.execute(
            sa.text(
                """
            UPDATE runtime_sources AS s
            SET project_id = CASE
                WHEN s.estate_key NOT LIKE :prefix THEN s.estate_key
                ELSE COALESCE(
                    (SELECT MIN(g.project_id) FROM runtime_source_read_grants g
                     WHERE g.source_id = s.id),
                    s.estate_key
                )
            END
            WHERE s.project_id IS NULL
            """
            ).bindparams(prefix=f"{_ESTATE_KEY_PREFIX}%")
        )
    op.alter_column("runtime_sources", "project_id", nullable=False)

    # Exactly the rows `upgrade()` created; an operator's own grant is left
    # alone, and so is a grant that happened to already exist for a project this
    # migration would otherwise have inserted.
    op.execute(
        sa.text(
            "DELETE FROM runtime_source_read_grants WHERE created_by = :marker"
        ).bindparams(marker=_MIGRATION_MARKER)
    )
    # The owner is never a grant row in the old model, and one restored above may
    # now collide with a grant this migration did not create.
    op.execute(
        sa.text(
            """
            DELETE FROM runtime_source_read_grants g
            USING runtime_sources s
            WHERE g.source_id = s.id AND g.project_id = s.project_id
            """
        )
    )

    if _has_unique_constraint("runtime_sources", "uq_runtime_sources_estate_source"):
        op.drop_constraint(
            "uq_runtime_sources_estate_source", "runtime_sources", type_="unique"
        )
    if _has_index("runtime_sources", "ix_runtime_sources_estate_key"):
        op.drop_index("ix_runtime_sources_estate_key", table_name="runtime_sources")
    if _has_column("runtime_sources", "estate_key"):
        op.drop_column("runtime_sources", "estate_key")

    if not _has_index("runtime_sources", "ix_runtime_sources_project_id"):
        op.create_index("ix_runtime_sources_project_id", "runtime_sources", ["project_id"])
    if not _has_unique_constraint("runtime_sources", "uq_runtime_sources_project_source"):
        op.create_unique_constraint(
            "uq_runtime_sources_project_source", "runtime_sources", ["project_id", "source_key"]
        )
    if not _has_index("runtime_sources", "uq_runtime_sources_project_tmc"):
        op.create_index(
            "uq_runtime_sources_project_tmc",
            "runtime_sources",
            ["project_id"],
            unique=True,
            postgresql_where=sa.text("source_type = 'tmc'"),
        )
