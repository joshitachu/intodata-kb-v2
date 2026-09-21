"""Durable storage for the layered answering guidance stack.

One table holds all three durable levels, discriminated by ``layer``. Three
tables with near-identical columns would have bought nothing: every read the
compiler performs is "give me the ACTIVE rows visible to this (project, user)",
which is one query over one table and three UNIONs over three.

What the split would have bought for free -- that a row cannot claim a scope its
level has no business claiming -- is bought here by
``ck_guidance_scope_matches_layer`` instead. That constraint is an integrity
boundary, not a nicety: an org row carrying a ``project_id`` would render for
that one project and silently vanish for every other, which reads to an operator
as "the house rule stopped working" with nothing in the logs to explain it.

Nothing here is ever deleted. A replaced rule becomes ``superseded`` and its
successor points back at it through ``supersedes_id``, so "why does it answer
this way" stays answerable after the rule that caused it was replaced.
"""

from __future__ import annotations

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base

# The same three shapes the Pydantic contract enforces on the way in, restated
# where the database can enforce them on every writer -- including a migration,
# a psql session, or a future service that bypasses the repository.
_SCOPE_MATCHES_LAYER = (
    "(layer = 'org' AND project_id IS NULL AND user_id IS NULL)"
    " OR (layer = 'project' AND project_id IS NOT NULL AND user_id IS NULL)"
    " OR (layer = 'person' AND project_id IS NOT NULL AND user_id IS NOT NULL)"
)


class GuidanceRuleRow(Base):
    """One guidance statement at one level, with its lifecycle and provenance."""

    __tablename__ = "guidance_rules"
    __table_args__ = (
        CheckConstraint(_SCOPE_MATCHES_LAYER, name="ck_guidance_scope_matches_layer"),
        # The three read paths the compiler actually issues, in the order it
        # issues them. Each covers one layer's scope filter plus the status
        # filter, because every compile reads ACTIVE rows only.
        Index("ix_guidance_rules_layer_status", "layer", "status"),
        Index("ix_guidance_rules_project_status", "project_id", "status"),
        Index("ix_guidance_rules_user_project_status", "user_id", "project_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    layer: Mapped[str] = mapped_column(String(16), nullable=False)
    project_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # A deleted user's preferences are meaningless and would otherwise keep
    # rendering against rows nobody can edit, so they go with the account.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active"
    )
    #: ``admin_ui`` or ``learned:conversation:<id>`` -- provenance for review.
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    # No ``ondelete``: rows are superseded, never removed, so the default
    # restrictive behaviour is the wanted one. A purge that would orphan a
    # supersession chain should fail loudly rather than quietly cut the history
    # that explains a past answer.
    supersedes_id: Mapped[int | None] = mapped_column(
        ForeignKey("guidance_rules.id"), nullable=True
    )
    #: Cached at write time; the budget check sums this rather than re-tokenizing
    #: every sibling row on every write.
    token_estimate: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # SET NULL, not CASCADE: an org rule outlives the admin who wrote it. Losing
    # the author is acceptable; losing the rule is not.
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
