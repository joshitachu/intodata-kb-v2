"""Unit tests for RagBridge content-hash skip (Slice 2 -- vector-store push skip).

All tests use a stub DocRagHashRepository and a mock push_markdown.
No live database is required.
"""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

from mcp_server.services.rag_bridge import RagBridge, rag_push_succeeded


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _make_bridge() -> RagBridge:
    return RagBridge(log_manager=None)


# ---------------------------------------------------------------------------
# Stub repository
# ---------------------------------------------------------------------------


class _StubHashRepo:
    """In-memory stub for DocRagHashRepository. No DB required."""

    def __init__(self, stored: dict[str, str] | None = None) -> None:
        self.stored: dict[str, str] = stored or {}
        self.upserted: dict[str, str] = {}
        self.deleted: list[str] = []
        self.deleted_all: list[str] = []

    async def get_hash(self, project_id: str, doc_filename: str) -> str | None:
        return self.stored.get(doc_filename)

    async def upsert_hash(
        self, project_id: str, doc_filename: str, content_hash: str
    ) -> None:
        self.upserted[doc_filename] = content_hash

    async def delete_hash(self, project_id: str, doc_filename: str) -> None:
        self.deleted.append(doc_filename)

    async def delete_all_for_project(self, project_id: str) -> None:
        self.deleted_all.append(project_id)


def _make_session_factory(stub_repo: _StubHashRepo):
    """Build a mock session factory whose sessions pair with stub_repo.

    Returns (factory_mock, session_mock).
    """
    session = AsyncMock()
    session.commit = AsyncMock()

    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)

    factory = MagicMock(return_value=cm)
    return factory, session


# ---------------------------------------------------------------------------
# 1. Flag off -- all docs pushed, skipped=0
# ---------------------------------------------------------------------------


async def test_flag_off_pushes_all_docs_and_skipped_is_zero(
    tmp_path, monkeypatch
) -> None:
    """When hash skip is disabled, every doc is pushed and result['skipped']==0."""
    from mcp_server.config import config

    monkeypatch.setattr(config, "incremental_hash_skip_enabled", False)

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "01_a.md").write_text("content A")
    (docs_dir / "02_b.md").write_text("content B")

    bridge = _make_bridge()
    with patch.object(bridge, "push_markdown", new_callable=AsyncMock) as mock_push:
        mock_push.return_value = {"status": "ok"}
        result = await bridge.push_docs("proj", output_dir=str(docs_dir))

    assert result["pushed"] == 2
    assert result["skipped"] == 0
    assert result["errors"] == []
    assert mock_push.call_count == 2


# ---------------------------------------------------------------------------
# 2. First push (no stored hash) -- doc pushed and hash recorded
# ---------------------------------------------------------------------------


async def test_first_push_no_stored_hash_pushes_and_records(
    tmp_path, monkeypatch
) -> None:
    """Flag on, no stored hash: doc is pushed and hash is upserted."""
    from mcp_server.config import config

    monkeypatch.setattr(config, "incremental_hash_skip_enabled", True)

    content = "hello world"
    stub = _StubHashRepo(stored={})
    factory, session = _make_session_factory(stub)

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "01_a.md").write_text(content)

    bridge = _make_bridge()
    with (
        patch("mcp_server.services.rag_bridge.async_session_factory", factory),
        patch(
            "mcp_server.services.rag_bridge.DocRagHashRepository",
            return_value=stub,
        ),
        patch.object(bridge, "push_markdown", new_callable=AsyncMock) as mock_push,
    ):
        mock_push.return_value = {"status": "ok"}
        result = await bridge.push_docs("proj", output_dir=str(docs_dir))

    assert result["pushed"] == 1
    assert result["skipped"] == 0
    assert result["errors"] == []
    assert stub.upserted.get("01_a.md") == _sha256(content)
    mock_push.assert_awaited_once()
    session.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# 3. Identical content -- doc is skipped
# ---------------------------------------------------------------------------


async def test_identical_content_doc_is_skipped(tmp_path, monkeypatch) -> None:
    """Flag on, stored hash == current hash: push_markdown is NOT called."""
    from mcp_server.config import config

    monkeypatch.setattr(config, "incremental_hash_skip_enabled", True)

    content = "unchanged content"
    stub = _StubHashRepo(stored={"01_a.md": _sha256(content)})
    factory, session = _make_session_factory(stub)

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "01_a.md").write_text(content)

    bridge = _make_bridge()
    with (
        patch("mcp_server.services.rag_bridge.async_session_factory", factory),
        patch(
            "mcp_server.services.rag_bridge.DocRagHashRepository",
            return_value=stub,
        ),
        patch.object(bridge, "push_markdown", new_callable=AsyncMock) as mock_push,
    ):
        result = await bridge.push_docs("proj", output_dir=str(docs_dir))

    assert result["pushed"] == 0
    assert result["skipped"] == 1
    assert result["errors"] == []
    mock_push.assert_not_awaited()
    # Hash is not re-upserted when skipped
    assert "01_a.md" not in stub.upserted


async def test_force_bypasses_skip_and_re_pushes_matching_hash(
    tmp_path, monkeypatch
) -> None:
    """force=True re-embeds a doc even when its stored hash matches (operator
    recovery path for out-of-band vector-store drift); the hash is refreshed."""
    from mcp_server.config import config

    monkeypatch.setattr(config, "incremental_hash_skip_enabled", True)

    content = "unchanged content"
    stub = _StubHashRepo(stored={"01_a.md": _sha256(content)})
    factory, session = _make_session_factory(stub)

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "01_a.md").write_text(content)

    bridge = _make_bridge()
    with (
        patch("mcp_server.services.rag_bridge.async_session_factory", factory),
        patch(
            "mcp_server.services.rag_bridge.DocRagHashRepository",
            return_value=stub,
        ),
        patch.object(bridge, "push_markdown", new_callable=AsyncMock) as mock_push,
    ):
        result = await bridge.push_docs("proj", output_dir=str(docs_dir), force=True)

    assert result["pushed"] == 1
    assert result["skipped"] == 0
    assert result["errors"] == []
    mock_push.assert_awaited_once()
    # Hash is refreshed after the forced re-push.
    assert "01_a.md" in stub.upserted


# ---------------------------------------------------------------------------
# 4. Changed content -- doc pushed and hash updated
# ---------------------------------------------------------------------------


async def test_changed_content_pushes_and_updates_hash(tmp_path, monkeypatch) -> None:
    """Flag on, stored hash differs from current: doc is pushed, new hash stored."""
    from mcp_server.config import config

    monkeypatch.setattr(config, "incremental_hash_skip_enabled", True)

    old_content = "old content"
    new_content = "new content"
    stub = _StubHashRepo(stored={"01_a.md": _sha256(old_content)})
    factory, session = _make_session_factory(stub)

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "01_a.md").write_text(new_content)

    bridge = _make_bridge()
    with (
        patch("mcp_server.services.rag_bridge.async_session_factory", factory),
        patch(
            "mcp_server.services.rag_bridge.DocRagHashRepository",
            return_value=stub,
        ),
        patch.object(bridge, "push_markdown", new_callable=AsyncMock) as mock_push,
    ):
        mock_push.return_value = {"status": "ok"}
        result = await bridge.push_docs("proj", output_dir=str(docs_dir))

    assert result["pushed"] == 1
    assert result["skipped"] == 0
    assert stub.upserted.get("01_a.md") == _sha256(new_content)
    mock_push.assert_awaited_once()


# ---------------------------------------------------------------------------
# 5. Push failure -- hash NOT recorded
# ---------------------------------------------------------------------------


async def test_push_failure_does_not_record_hash(tmp_path, monkeypatch) -> None:
    """When push_markdown raises, the hash upsert is not called."""
    from mcp_server.config import config

    monkeypatch.setattr(config, "incremental_hash_skip_enabled", True)

    stub = _StubHashRepo(stored={})
    factory, session = _make_session_factory(stub)

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "01_a.md").write_text("some content")

    bridge = _make_bridge()
    with (
        patch("mcp_server.services.rag_bridge.async_session_factory", factory),
        patch(
            "mcp_server.services.rag_bridge.DocRagHashRepository",
            return_value=stub,
        ),
        patch.object(bridge, "push_markdown", new_callable=AsyncMock) as mock_push,
    ):
        mock_push.side_effect = Exception("network timeout")
        result = await bridge.push_docs("proj", output_dir=str(docs_dir))

    assert result["pushed"] == 0
    assert result["skipped"] == 0
    assert len(result["errors"]) == 1
    assert "01_a.md" not in stub.upserted
    session.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# 6. extra_doc_names (pack docs) are eligible for hash skip
# ---------------------------------------------------------------------------


async def test_extra_doc_names_eligible_for_hash_skip(tmp_path, monkeypatch) -> None:
    """Pack docs in extra_doc_names pass through the same skip predicate."""
    from mcp_server.config import config

    monkeypatch.setattr(config, "incremental_hash_skip_enabled", True)

    content = "table inventory data"
    stub = _StubHashRepo(stored={"TABLE_INVENTORY.md": _sha256(content)})
    factory, session = _make_session_factory(stub)

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "TABLE_INVENTORY.md").write_text(content)

    bridge = _make_bridge()
    with (
        patch("mcp_server.services.rag_bridge.async_session_factory", factory),
        patch(
            "mcp_server.services.rag_bridge.DocRagHashRepository",
            return_value=stub,
        ),
        patch.object(bridge, "push_markdown", new_callable=AsyncMock) as mock_push,
    ):
        result = await bridge.push_docs(
            "proj",
            output_dir=str(docs_dir),
            extra_doc_names={"TABLE_INVENTORY.md"},
        )

    assert result["skipped"] == 1
    assert result["pushed"] == 0
    assert result["errors"] == []
    mock_push.assert_not_awaited()


# ---------------------------------------------------------------------------
# 7. sync_internal_layers does NOT apply skip predicate to its push loop
# ---------------------------------------------------------------------------


async def test_sync_internal_layers_pushes_all_artifacts_unconditionally(
    tmp_path, monkeypatch
) -> None:
    """Internal-layer push loop bypasses the hash skip predicate entirely."""
    from mcp_server.config import config

    monkeypatch.setattr(config, "incremental_hash_skip_enabled", True)

    artifact1 = {
        "filename": "_rag_support/phase2.md",
        "content": "phase 2 data",
        "metadata": {},
    }
    artifact2 = {
        "filename": "_rag_support/scorecard.md",
        "content": "scorecard data",
        "metadata": {},
    }

    stub = _StubHashRepo()
    factory, session = _make_session_factory(stub)

    bridge = _make_bridge()
    with (
        patch(
            "mcp_server.services.rag_bridge.collect_internal_artifacts",
            return_value=[artifact1, artifact2],
        ),
        patch(
            "mcp_server.services.rag_bridge.build_entity_aliases",
            return_value=[],
        ),
        patch("mcp_server.services.rag_bridge.async_session_factory", factory),
        patch(
            "mcp_server.services.rag_bridge.DocRagHashRepository",
            return_value=stub,
        ),
        patch.object(
            bridge,
            "delete_internal_documents",
            new_callable=AsyncMock,
            return_value={},
        ),
        patch.object(
            bridge,
            "push_markdown",
            new_callable=AsyncMock,
            return_value={},
        ) as mock_push,
    ):
        result = await bridge.sync_internal_layers("proj", tmp_path)

    # Both internal artifacts pushed unconditionally (no skip)
    assert result["pushed"] == 2
    assert mock_push.call_count == 2
    # No upsert_hash calls in the internal push path
    assert len(stub.upserted) == 0
    # get_hash is never called in the internal push path
    assert len(stub.stored) == 0


# ---------------------------------------------------------------------------
# 8. sync_internal_layers invalidates hash rows after successful delete
# ---------------------------------------------------------------------------


async def test_sync_internal_layers_invalidates_hash_rows(
    tmp_path, monkeypatch
) -> None:
    """After a successful internal delete, delete_all_for_project is called."""
    from mcp_server.config import config

    monkeypatch.setattr(config, "incremental_hash_skip_enabled", True)

    stub = _StubHashRepo()
    factory, session = _make_session_factory(stub)

    bridge = _make_bridge()
    with (
        patch(
            "mcp_server.services.rag_bridge.collect_internal_artifacts",
            return_value=[],
        ),
        patch(
            "mcp_server.services.rag_bridge.build_entity_aliases",
            return_value=[],
        ),
        patch("mcp_server.services.rag_bridge.async_session_factory", factory),
        patch(
            "mcp_server.services.rag_bridge.DocRagHashRepository",
            return_value=stub,
        ),
        patch.object(
            bridge,
            "delete_internal_documents",
            new_callable=AsyncMock,
            return_value={},
        ),
        patch.object(
            bridge,
            "push_markdown",
            new_callable=AsyncMock,
            return_value={},
        ),
    ):
        await bridge.sync_internal_layers("proj", tmp_path)

    # delete_all_for_project called exactly once with the project_id
    assert stub.deleted_all == ["proj"]
    session.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# 9. rag_push_succeeded treats pushed + skipped >= expected_count as success
# ---------------------------------------------------------------------------


def test_rag_push_succeeded_with_skipped_count() -> None:
    """pushed + skipped >= expected_count returns True; under-count returns False."""
    # Exact match via skipped only
    assert (
        rag_push_succeeded({"pushed": 0, "skipped": 5, "errors": []}, expected_count=5)
        is True
    )
    # Mix of pushed + skipped
    assert (
        rag_push_succeeded({"pushed": 2, "skipped": 3, "errors": []}, expected_count=5)
        is True
    )
    # Under-count even with skipped
    assert (
        rag_push_succeeded({"pushed": 2, "skipped": 2, "errors": []}, expected_count=5)
        is False
    )
    # Errors always fail regardless of counts
    assert (
        rag_push_succeeded(
            {"pushed": 2, "skipped": 3, "errors": ["boom"]}, expected_count=5
        )
        is False
    )
    # Backward compat: no skipped key (old callers)
    assert (
        rag_push_succeeded({"pushed": 5, "errors": []}, expected_count=5)
        is True
    )
    assert (
        rag_push_succeeded({"pushed": 3, "errors": []}, expected_count=5)
        is False
    )
