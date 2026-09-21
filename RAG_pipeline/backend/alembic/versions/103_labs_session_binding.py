"""Give a conversation a durable OpenCode session id.

The labs lane runs its reasoning loop inside an OpenCode session, and the map
from conversation to session lived only in a module-level dict in
``src/labs/runtime.py``. That dict does not survive a process restart, and the
backend restarts constantly: ``uvicorn --reload --reload-dir /app/src`` reloaded
39 times in one working session, and a production deploy does the same thing
less often.

Every restart therefore made the next turn create a SECOND top-level OpenCode
session for the same conversation. Verified on two live conversations, each left
holding two sessions with ``parentID=None``: the conversation's history on the
OpenCode side was split in two, and the model silently lost everything said
before the restart while the Nexus-side message list still looked complete.

The session id is the only part of that binding worth persisting. The MCP bearer
token minted alongside it is deliberately NOT stored: it is a credential with
its own expiry, it is re-mintable by design, and it belongs in memory and in the
workspace's ``opencode.json``, which only the backend writes. A restart should
re-mint the token -- that is correct for a credential -- and reuse the session.

Nullable with no default, because that is the truth for the rows that exist:
every production ``/chat`` conversation has no OpenCode session and never will,
and a labs conversation that has not run a turn since this column existed has
none either. Both read correctly as NULL, so no data migration is needed.

Reversible. Upgrade adds one nullable column; downgrade drops it, after which
the binding falls back to being in-process only and a restart splits a
conversation's session again. No other column is read, rewritten or deleted in
either direction.

Revision ID: 103_labs_session_binding
Revises: 102_document_text_provenance
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "103_labs_session_binding"
down_revision: str | None = "102_document_text_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("opencode_session_id", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversations", "opencode_session_id")
