"""Add cause_origin + pattern_id to runtime_incidents and rebuild signatures.

Slice 3 changes the deterministic signature algorithm (causal resolution + an
unknown-failure disambiguator) and adds two columns to runtime_incidents:

  * cause_origin  (String(20), NOT NULL, server_default "deterministic") —
    "deterministic" is the only value the builder writes; "llm_fallback" is
    reserved for the Slice 4 LLM fallback.
  * pattern_id    (String(64), nullable) — the matched catalog pattern id.

Because the signature formula changed, stored signature_hash values are stale.
The insight tables are derived from the raw runtime store, so the upgrade rebuilds
every incident's insight rows via builder.rebuild_runtime_insights, recomputing
signatures, confidence, cause_origin, and pattern_id. The rebuild is best-effort:
if the raw store is empty (fresh DB) it is a no-op.

Revision ID: 034
Revises: 033
Create Date: 2026-06-18
"""

from __future__ import annotations

import asyncio
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "034"
down_revision: Union[str, None] = "033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("runtime_incidents") as batch:
        batch.add_column(
            sa.Column(
                "cause_origin",
                sa.String(20),
                nullable=False,
                server_default="deterministic",
            )
        )
        batch.add_column(sa.Column("pattern_id", sa.String(64), nullable=True))

    _rebuild_signatures()


def downgrade() -> None:
    with op.batch_alter_table("runtime_incidents") as batch:
        batch.drop_column("pattern_id")
        batch.drop_column("cause_origin")


def _rebuild_signatures() -> None:
    """Recompute derived insight rows so stale signatures are repaired.

    Runs against the live async session factory. Skipped silently when no raw
    runtime executions exist (nothing to rebuild) so a fresh-DB migration stays a
    pure schema change.
    """
    from src.db.session import async_session_factory
    from src.runtime.insights.builder import rebuild_runtime_insights

    async def _run() -> None:
        async with async_session_factory() as session:
            await rebuild_runtime_insights(session)
            await session.commit()

    try:
        asyncio.run(_run())
    except RuntimeError as exc:
        # An already-running event loop (e.g. migration invoked from async
        # context) prevents asyncio.run. Schedule on the running loop instead.
        if "running event loop" not in str(exc).lower():
            raise
        loop = asyncio.get_event_loop()
        loop.run_until_complete(_run_in_existing_loop())


async def _run_in_existing_loop() -> None:
    from src.db.session import async_session_factory
    from src.runtime.insights.builder import rebuild_runtime_insights

    async with async_session_factory() as session:
        await rebuild_runtime_insights(session)
        await session.commit()
