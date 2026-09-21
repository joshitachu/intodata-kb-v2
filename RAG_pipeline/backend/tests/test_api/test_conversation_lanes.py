"""One conversation list, two lanes, and the column that tells them apart.

The lane used to be encoded twice over: a `[labs] ` prefix glued onto the user's
own title, and `visible_in_chat=False` so the production list would skip the row.
Every test here exists because one of those hacks failed in a way a column does
not:

* the prefix is user-typable, so it could never be a lane test;
* `visible_in_chat` belongs to the topology surface, where it means "the user has
  not promoted this thread" -- reading it as "other lane" merged two unrelated
  ideas into one boolean.

These run against a real (SQLite) session rather than a mocked one on purpose:
the stripping rule only means something if the stored row is provably untouched,
and a MagicMock cannot show that.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.main import app
from src.db.models import Conversation, User
from src.db.models.conversation import ConversationLane

CSRF_HEADERS = {"cookie": "csrf_token=test_csrf", "X-CSRF-Token": "test_csrf"}


@pytest.fixture
async def lane_user(db_session: AsyncSession) -> User:
    """A real owner row: `conversations.user_id` is a foreign key under PRAGMA."""
    user = User(
        username="lane-owner",
        email="lane-owner@example.com",
        password_hash="not-a-real-hash",
    )
    db_session.add(user)
    await db_session.commit()
    return user


@pytest.fixture
async def client(db_session: AsyncSession, lane_user: User):
    from src.api.dependencies import get_current_user, get_db

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: lane_user
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
    app.dependency_overrides.clear()


async def _add_conversation(
    db_session: AsyncSession,
    owner: User,
    *,
    title: str | None,
    lane: str = ConversationLane.V1,
    origin_surface: str = "chat",
    visible_in_chat: bool = True,
) -> Conversation:
    conversation = Conversation(
        user_id=owner.id,
        title=title,
        lane=lane,
        origin_surface=origin_surface,
        visible_in_chat=visible_in_chat,
    )
    db_session.add(conversation)
    await db_session.commit()
    return conversation


async def _stored_title(db_session: AsyncSession, conversation_id: int) -> str | None:
    """Read the title straight from the table, past any ORM identity map."""
    result = await db_session.execute(
        sa.text("SELECT title FROM conversations WHERE id = :id"),
        {"id": conversation_id},
    )
    return result.scalar_one()


class TestUnifiedConversationList:
    async def test_both_lanes_are_listed_and_each_row_carries_its_lane(
        self, client: AsyncClient, db_session: AsyncSession, lane_user: User
    ) -> None:
        v1 = await _add_conversation(db_session, lane_user, title="Quarterly review")
        v2 = await _add_conversation(
            db_session, lane_user, title="Werkorder dig", lane=ConversationLane.V2
        )

        response = await client.get("/api/v1/conversations")

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        lanes = {row["id"]: row["lane"] for row in body["conversations"]}
        assert lanes == {v1.id: "v1", v2.id: "v2"}

    async def test_the_retired_title_marker_is_stripped_on_read_only(
        self, client: AsyncClient, db_session: AsyncSession, lane_user: User
    ) -> None:
        """Display drops the prefix; the row keeps the title the user gave."""
        legacy = await _add_conversation(
            db_session, lane_user, title="[labs] Old thread", lane=ConversationLane.V2
        )

        response = await client.get("/api/v1/conversations")

        assert response.status_code == 200
        listed = {row["id"]: row["title"] for row in response.json()["conversations"]}
        assert listed[legacy.id] == "Old thread"
        assert await _stored_title(db_session, legacy.id) == "[labs] Old thread"

    async def test_a_v1_thread_the_user_named_like_the_old_marker_keeps_its_name(
        self, client: AsyncClient, db_session: AsyncSession, lane_user: User
    ) -> None:
        """The whole reason the prefix was never a lane marker.

        Stripping is conditional on the lane, so a V1 title that merely looks
        like the retired marker is left exactly as typed.
        """
        typed = await _add_conversation(db_session, lane_user, title="[labs] retry")

        response = await client.get("/api/v1/conversations")

        listed = {row["id"]: row for row in response.json()["conversations"]}
        assert listed[typed.id]["title"] == "[labs] retry"
        assert listed[typed.id]["lane"] == "v1"

    async def test_lane_filter_narrows_to_one_lane(
        self, client: AsyncClient, db_session: AsyncSession, lane_user: User
    ) -> None:
        v1 = await _add_conversation(db_session, lane_user, title="Quarterly review")
        v2 = await _add_conversation(
            db_session, lane_user, title="Werkorder dig", lane=ConversationLane.V2
        )

        only_v2 = await client.get("/api/v1/conversations", params={"lane": "v2"})
        only_v1 = await client.get("/api/v1/conversations", params={"lane": "v1"})

        assert [row["id"] for row in only_v2.json()["conversations"]] == [v2.id]
        assert only_v2.json()["total"] == 1
        assert [row["id"] for row in only_v1.json()["conversations"]] == [v1.id]
        assert only_v1.json()["total"] == 1

    async def test_an_unknown_lane_is_rejected_rather_than_ignored(
        self, client: AsyncClient
    ) -> None:
        """A silently ignored filter returns the wrong rows and looks like a bug
        in the list, not in the caller."""
        response = await client.get("/api/v1/conversations", params={"lane": "v3"})

        assert response.status_code == 422

    async def test_topology_visibility_still_means_what_it_meant(
        self, client: AsyncClient, db_session: AsyncSession, lane_user: User
    ) -> None:
        """`visible_in_chat` was not repurposed into a lane test.

        An embedded topology thread stays out of the chat list until the user
        promotes it, and a promoted one comes back on the V1 lane -- unchanged by
        the arrival of the column.
        """
        hidden = await _add_conversation(
            db_session,
            lane_user,
            title="Topology drilldown",
            origin_surface="topology",
            visible_in_chat=False,
        )
        promoted = await _add_conversation(
            db_session,
            lane_user,
            title="Promoted drilldown",
            origin_surface="topology",
            visible_in_chat=True,
        )

        body = (await client.get("/api/v1/conversations")).json()

        listed = {row["id"]: row for row in body["conversations"]}
        assert hidden.id not in listed
        assert listed[promoted.id]["origin_surface"] == "topology"
        assert listed[promoted.id]["lane"] == "v1"


class TestConversationCreationSetsTheLane:
    async def test_creation_defaults_to_v1(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        response = await client.post("/api/v1/conversations", headers=CSRF_HEADERS)

        assert response.status_code == 201
        assert response.json()["lane"] == "v1"
        stored = await db_session.execute(
            sa.text("SELECT lane FROM conversations WHERE id = :id"),
            {"id": response.json()["id"]},
        )
        assert stored.scalar_one() == "v1"

    async def test_creation_accepts_an_explicit_lane(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        response = await client.post(
            "/api/v1/conversations",
            json={"title": "Second opinion", "lane": "v2"},
            headers=CSRF_HEADERS,
        )

        assert response.status_code == 201
        assert response.json()["lane"] == "v2"

    async def test_topology_creation_is_unaffected(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/v1/conversations",
            json={"surface": "topology"},
            headers=CSRF_HEADERS,
        )

        body = response.json()
        assert body["origin_surface"] == "topology"
        assert body["visible_in_chat"] is False
        assert body["lane"] == "v1"

    async def test_the_lane_cannot_be_changed_after_creation(
        self, client: AsyncClient, db_session: AsyncSession, lane_user: User
    ) -> None:
        """No endpoint moves a thread between lanes, and the update schema has no
        field to ask for it: each lane holds history the other has never seen."""
        conversation = await _add_conversation(
            db_session, lane_user, title="Werkorder dig", lane=ConversationLane.V2
        )

        response = await client.patch(
            f"/api/v1/conversations/{conversation.id}",
            json={"lane": "v1", "title": "Renamed"},
            headers=CSRF_HEADERS,
        )

        assert response.status_code == 200
        assert response.json()["lane"] == "v2"
        stored = await db_session.execute(
            sa.text("SELECT lane FROM conversations WHERE id = :id"),
            {"id": conversation.id},
        )
        assert stored.scalar_one() == "v2"


class TestLabsRouteCreatesTheV2Lane:
    async def test_labs_creation_sets_the_lane_and_writes_no_title_marker(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        response = await client.post(
            "/api/v1/labs/conversations",
            json={"title": "Werkorder dig"},
            headers=CSRF_HEADERS,
        )

        assert response.status_code == 201
        body = response.json()
        assert body["lane"] == "v2"
        assert body["title"] == "Werkorder dig"
        row = (
            await db_session.execute(
                sa.text(
                    "SELECT title, lane, visible_in_chat, origin_surface "
                    "FROM conversations WHERE id = :id"
                ),
                {"id": body["id"]},
            )
        ).one()
        assert row.title == "Werkorder dig"
        assert row.lane == "v2"
        # A V2 thread is an ordinary chat-surface thread that OpenCode answers,
        # so it belongs in the one list rather than being hidden out of it.
        assert bool(row.visible_in_chat) is True
        assert row.origin_surface == "chat"

    async def test_a_labs_created_thread_is_listed_by_the_unified_endpoint(
        self, client: AsyncClient
    ) -> None:
        created = await client.post(
            "/api/v1/labs/conversations",
            json={"title": "Werkorder dig"},
            headers=CSRF_HEADERS,
        )

        body = (await client.get("/api/v1/conversations")).json()

        listed = {row["id"]: row for row in body["conversations"]}
        assert listed[created.json()["id"]]["lane"] == "v2"

    async def test_the_labs_lane_reads_only_its_own_threads(
        self, client: AsyncClient, db_session: AsyncSession, lane_user: User
    ) -> None:
        """The lane gate is the column now, not a prefix test on the title."""
        v1 = await _add_conversation(db_session, lane_user, title="Quarterly review")
        v2 = await _add_conversation(
            db_session, lane_user, title="Werkorder dig", lane=ConversationLane.V2
        )

        listed = await client.get("/api/v1/labs/conversations")
        foreign = await client.get(f"/api/v1/labs/conversations/{v1.id}")
        own = await client.get(f"/api/v1/labs/conversations/{v2.id}")

        assert [row["id"] for row in listed.json()["conversations"]] == [v2.id]
        assert foreign.status_code == 404
        assert own.status_code == 200
        assert own.json()["lane"] == "v2"

    async def test_a_legacy_labs_thread_reads_without_its_marker(
        self, client: AsyncClient, db_session: AsyncSession, lane_user: User
    ) -> None:
        legacy = await _add_conversation(
            db_session, lane_user, title="[labs] Old thread", lane=ConversationLane.V2
        )

        response = await client.get(f"/api/v1/labs/conversations/{legacy.id}")

        assert response.json()["title"] == "Old thread"
        assert await _stored_title(db_session, legacy.id) == "[labs] Old thread"


def _load_migration() -> ModuleType:
    """Import revision 104 by path; `alembic/versions` is not a package."""
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "104_conversation_lane.py"
    )
    spec = importlib.util.spec_from_file_location("migration_104", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: (title, origin_surface, visible_in_chat, expected_lane) for every row shape
#: that existed when the column was added.
_ROW_SHAPES: tuple[tuple[str | None, str, int, str], ...] = (
    # Written by the labs route: prefix, chat surface, hidden.
    ("[labs] Werkorder dig", "chat", 0, "v2"),
    # Same, never titled -- `_labs_title(None)` stored the bare prefix.
    ("[labs] ", "chat", 0, "v2"),
    ("Quarterly review", "chat", 1, "v1"),
    # Embedded topology threads (migration 088) are hidden too, and are not V2.
    ("Topology drilldown", "topology", 0, "v1"),
    ("[labs] topology drilldown", "topology", 0, "v1"),
    # A chat thread the user demoted through PATCH: hidden, still V1.
    ("Archived chat", "chat", 0, "v1"),
    # A V1 thread the user happened to name like the marker: visible, still V1.
    ("[labs] retry", "chat", 1, "v1"),
    (None, "chat", 1, "v1"),
)


@pytest.fixture
def pre_migration_rows() -> Any:
    """The `conversations` columns the backfill reads, before `lane` was added.

    Plain sqlite3 rather than the ORM: the point is to run the migration's own
    SQL text, so nothing may re-render it.
    """
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE conversations ("
        " id INTEGER PRIMARY KEY,"
        " title TEXT,"
        " origin_surface TEXT NOT NULL,"
        " visible_in_chat BOOLEAN NOT NULL,"
        " lane TEXT NOT NULL DEFAULT 'v1')"
    )
    connection.executemany(
        "INSERT INTO conversations (id, title, origin_surface, visible_in_chat)"
        " VALUES (?, ?, ?, ?)",
        [
            (index, title, surface, visible)
            for index, (title, surface, visible, _) in enumerate(_ROW_SHAPES, start=1)
        ],
    )
    connection.commit()
    yield connection
    connection.close()


class TestBackfill:
    def test_only_the_rows_the_labs_route_wrote_become_v2(
        self, pre_migration_rows: sqlite3.Connection
    ) -> None:
        migration = _load_migration()

        pre_migration_rows.execute(migration.BACKFILL_SQL)

        classified = {
            row["id"]: row["lane"]
            for row in pre_migration_rows.execute(
                "SELECT id, lane FROM conversations ORDER BY id"
            )
        }
        assert classified == {
            index: expected
            for index, (_, _, _, expected) in enumerate(_ROW_SHAPES, start=1)
        }

    def test_backfilled_threads_are_returned_to_the_chat_list(
        self, pre_migration_rows: sqlite3.Connection
    ) -> None:
        """The hidden flag was the other half of the lane hack, so the upgrade
        undoes it -- and only on the rows it just classified."""
        migration = _load_migration()

        pre_migration_rows.execute(migration.BACKFILL_SQL)

        visible = {
            row["id"]: bool(row["visible_in_chat"])
            for row in pre_migration_rows.execute(
                "SELECT id, visible_in_chat FROM conversations ORDER BY id"
            )
        }
        assert visible == {
            index: (expected == "v2" or bool(stored_visible))
            for index, (_, _, stored_visible, expected) in enumerate(
                _ROW_SHAPES, start=1
            )
        }

    def test_downgrade_re_hides_v2_threads_before_the_column_disappears(
        self, pre_migration_rows: sqlite3.Connection
    ) -> None:
        """Keyed on the column, not on the prefix: threads created after this
        migration carry no prefix at all, and a downgrade must still hide them."""
        migration = _load_migration()
        pre_migration_rows.execute(migration.BACKFILL_SQL)
        pre_migration_rows.execute(
            "INSERT INTO conversations (id, title, origin_surface, visible_in_chat, lane)"
            " VALUES (99, 'No marker at all', 'chat', 1, 'v2')"
        )

        pre_migration_rows.execute(migration.REHIDE_SQL)

        hidden = {
            row["id"]
            for row in pre_migration_rows.execute(
                "SELECT id FROM conversations WHERE visible_in_chat = false"
            )
        }
        assert hidden == {1, 2, 4, 5, 6, 99}

    def test_the_revision_chains_onto_the_labs_session_binding(self) -> None:
        migration = _load_migration()

        assert migration.revision == "104_conversation_lane"
        assert migration.down_revision == "103_labs_session_binding"
