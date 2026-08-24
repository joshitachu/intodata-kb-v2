"""Add connector_instances.account_key and enforce one connection per account.

A connector instance points at an account: an Elastic cluster endpoint, a TMC
region. Without a key, the same cluster can be registered twice under two
display names, and a reader of the connectors directory cannot tell that they
are the same thing.

MUST BE IDEMPOTENT. The mcp-server container boots with ``create_all`` ->
``alembic upgrade head`` -> serve (see docker-compose.yml), so by the time
alembic runs, ``create_all`` has ALREADY added any column declared in
``db/models.py``. A plain ``op.add_column`` therefore dies with
DuplicateColumnError, stranding ``kb_alembic_version`` on the previous revision
and blocking every later revision.

BACKFILL AND DEDUPE. Existing rows have no key, so it is derived here from the
same ``safe_config`` field the manifest names (``endpoint`` for Elastic). Rows
that collide keep the OLDEST instance keyed and leave newer duplicates NULL
rather than deleting anything: an operator must decide which duplicate is real,
and NULLs stay distinct under the unique index, so the constraint still applies.

Revision ID: 030_add_connector_account_key
Revises: 029_add_azure_devops_connections
Create Date: 2026-08-24

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "030_add_connector_account_key"
down_revision = "029_add_azure_devops_connections"
branch_labels = None
depends_on = None

_TABLE = "connector_instances"
_COLUMN = "account_key"
_INDEX = "uq_connector_instance_account"


def _columns(bind) -> set[str]:
    return {row["name"] for row in sa.inspect(bind).get_columns(_TABLE)}


def _constraints(bind) -> set[str]:
    inspector = sa.inspect(bind)
    names = {c["name"] for c in inspector.get_unique_constraints(_TABLE)}
    names |= {i["name"] for i in inspector.get_indexes(_TABLE)}
    return {name for name in names if name}


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(_TABLE):
        return
    if _COLUMN not in _columns(bind):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(255), nullable=True))

    # Backfill from the Elastic manifest's declared account field. Normalization
    # must match ``connectors.domain.account_key_for``: lowercase, no scheme, no
    # trailing slash.
    if bind.dialect.name == "postgresql":
        bind.execute(
            sa.text(
                """
                UPDATE connector_instances
                   SET account_key = regexp_replace(
                           regexp_replace(lower(safe_config->>'endpoint'), '^https?://', ''),
                           '/+$', '')
                 WHERE account_key IS NULL
                   AND safe_config ? 'endpoint'
                   AND coalesce(trim(safe_config->>'endpoint'), '') <> ''
                """
            )
        )
        # Keep the oldest row of each collision keyed; newer duplicates go back
        # to NULL so a human can retire the right one.
        bind.execute(
            sa.text(
                """
                UPDATE connector_instances
                   SET account_key = NULL
                 WHERE id IN (
                       SELECT id FROM (
                           SELECT id, row_number() OVER (
                                      PARTITION BY provider_id, account_key
                                      ORDER BY created_at, id
                                  ) AS rn
                             FROM connector_instances
                            WHERE account_key IS NOT NULL
                       ) ranked
                        WHERE ranked.rn > 1
                 )
                """
            )
        )

    if _INDEX not in _constraints(bind):
        op.create_unique_constraint(_INDEX, _TABLE, ["provider_id", _COLUMN])


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(_TABLE):
        return
    if _INDEX in _constraints(bind):
        op.drop_constraint(_INDEX, _TABLE, type_="unique")
    if _COLUMN in _columns(bind):
        op.drop_column(_TABLE, _COLUMN)
