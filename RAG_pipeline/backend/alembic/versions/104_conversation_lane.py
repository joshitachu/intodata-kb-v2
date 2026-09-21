"""Record a conversation's chat lane as a column instead of a title prefix.

Two chat lanes answer the same public contract: V1 is the production LangGraph
harness and V2 is the OpenCode lane. Until now nothing in the schema said which
was which. The V2 route marked its threads by writing ``[labs] `` into the
user's own title and by setting ``visible_in_chat = false``, which the V1 list
endpoint already filtered on. Neither is a lane marker. A user who titles a
thread ``[labs] retry`` joins the other lane, the prefix cannot be indexed, and
every read path has to do string surgery to recover a fact the row should simply
carry. ``visible_in_chat`` is worse: it is shared with the topology surface and
is user-controlled, so "hidden" and "V2" were being read as the same thing.

Backfill predicate
------------------
``origin_surface = 'chat' AND visible_in_chat = false AND title LIKE '[labs] %'``
becomes ``lane = 'v2'``; everything else keeps the ``'v1'`` default.

All three conjuncts together are the row shape the V2 route actually wrote --
``Conversation(origin_surface='chat', visible_in_chat=False, title=_labs_title(...))``
-- and none of them identifies the lane on its own:

* the title prefix alone would sweep up a V1 thread a user happened to name
  ``[labs] retry``, and ``_labs_title()`` prefixed unconditionally, so even an
  untitled V2 thread is stored as the bare ``'[labs] '`` -- which
  ``LIKE '[labs] %'`` still matches. (``[`` is not a ``LIKE`` metacharacter in
  PostgreSQL, and the prefix holds no ``%`` or ``_``, so the pattern is literal.)
* ``visible_in_chat = false`` alone means "not listed", never "V2": topology
  threads are stored false too (migration 088), as is any thread the user demoted
  through ``PATCH /conversations/{id}``. Pointing those at the OpenCode runtime,
  which has no history for them, is the worst error available here.
* ``origin_surface = 'chat'`` excludes a topology thread whose auto-generated
  title happens to start with the prefix.

The residual misclassification runs the other way and is accepted: a V2 thread
someone had promoted to ``visible_in_chat = true`` stays on ``'v1'``. No UI
offers that -- only the topology panel calls the visibility PATCH -- and the
alternative conjunction misfiles ordinary chat threads, which is worse.

The upgrade also flips the matched rows back to ``visible_in_chat = true``. The
false was never a statement about the user's intent; it was the second half of
the lane hack. Both lanes are listed together now, so leaving it would hide every
pre-existing V2 thread from the unified list this column exists to enable. The
topology meaning of the flag is untouched: no row with
``origin_surface = 'topology'`` is read or written here.

Titles are NOT rewritten. The stored text is the user's own, a migration that
edits user-visible text cannot be undone by a downgrade, and the API strips the
retired prefix for display instead (``src/schemas.py``), which is one place and
reversible.

Reversible. Upgrade adds one column plus its index and rewrites nothing but
``visible_in_chat`` on rows it can identify again from the column itself.
Downgrade re-hides every ``lane = 'v2'`` thread -- exact for both backfilled and
newly created V2 threads, because it keys on the column rather than on the
prefix that new rows no longer carry -- and then drops the index and the column.
The one detail a downgrade cannot recover is a V2 thread the user had explicitly
promoted with ``visible_in_chat = true`` before this migration ran; it is
re-hidden, and re-promoting it is one click.

Revision ID: 104_conversation_lane
Revises: 103_labs_session_binding
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa

from alembic import op

revision: str = "104_conversation_lane"
down_revision: str | None = "103_labs_session_binding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The rows the V2 route created, expressed exactly as it created them.
#: Module level so the test suite asserts against the predicate that actually
#: runs rather than a retyped copy of it.
LEGACY_LABS_ROW_PREDICATE: Final = (
    "origin_surface = 'chat' AND visible_in_chat = false AND title LIKE '[labs] %'"
)

BACKFILL_SQL: Final = (
    "UPDATE conversations SET lane = 'v2', visible_in_chat = true "
    f"WHERE {LEGACY_LABS_ROW_PREDICATE}"
)

REHIDE_SQL: Final = "UPDATE conversations SET visible_in_chat = false WHERE lane = 'v2'"


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("lane", sa.String(length=16), server_default="v1", nullable=False),
    )
    op.create_index("ix_conversations_lane", "conversations", ["lane"])
    op.execute(BACKFILL_SQL)


def downgrade() -> None:
    # Before the column exists there is no other way to keep V2 threads out of
    # the V1 list, so restore the flag the lane hack used.
    op.execute(REHIDE_SQL)
    op.drop_index("ix_conversations_lane", table_name="conversations")
    op.drop_column("conversations", "lane")
