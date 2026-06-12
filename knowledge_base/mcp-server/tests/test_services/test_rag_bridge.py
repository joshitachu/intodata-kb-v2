"""Tests for RagBridge service."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcp_server.services.rag_bridge import RagBridge


@pytest.fixture
def bridge(log_manager, monkeypatch):
    from mcp_server.config import config
    monkeypatch.setattr(config, "rag_base_url", "http://localhost:9999")
    monkeypatch.setattr(config, "ingest_api_key", "test-key")
    return RagBridge(log_manager=log_manager)


async def test_push_markdown_sends_request(bridge):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"status": "ok"}

    with patch("mcp_server.services.rag_bridge.httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_client

        result = await bridge.push_markdown("proj", "test.md", "# Hello")
        assert result == {"status": "ok"}

        # Verify auth header was sent
        call_kwargs = mock_client.post.call_args
        assert call_kwargs.kwargs["headers"]["Authorization"] == "Bearer test-key"


async def test_push_docs_from_output_dir(bridge, tmp_path):
    # Create some markdown files
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "01_file1.md").write_text("# File 1")
    (docs_dir / "02_file2.md").write_text("# File 2")

    with patch.object(bridge, "push_markdown", new_callable=AsyncMock) as mock_push:
        mock_push.return_value = {"status": "ok"}

        result = await bridge.push_docs("proj", output_dir=str(docs_dir))
        assert result["pushed"] == 2
        assert len(result["errors"]) == 0
        assert mock_push.call_count == 2


async def test_push_docs_from_output_dir_with_filename_filter(bridge, tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "01_file1.md").write_text("# File 1")
    (docs_dir / "02_file2.md").write_text("# File 2")

    with patch.object(bridge, "push_markdown", new_callable=AsyncMock) as mock_push:
        mock_push.return_value = {"status": "ok"}

        result = await bridge.push_docs(
            "proj",
            output_dir=str(docs_dir),
            filenames={"02_file2.md"},
        )
        assert result["pushed"] == 1
        assert len(result["errors"]) == 0
        mock_push.assert_awaited_once()
        assert mock_push.await_args.kwargs["filename"] == "02_file2.md"


async def test_push_docs_ignores_nested_rag_support(bridge, tmp_path):
    docs_dir = tmp_path / "docs"
    package_dir = docs_dir / "_rag_support" / "phase2_analysis"
    package_dir.mkdir(parents=True)
    (docs_dir / "01_OVERVIEW.md").write_text("# Overview")
    (package_dir / "A.md").write_text("# Internal Phase 2 analysis")
    (docs_dir / "_rag_support" / "manifest.json").write_text("{}")

    with patch.object(bridge, "push_markdown", new_callable=AsyncMock) as mock_push:
        mock_push.return_value = {"status": "ok"}

        result = await bridge.push_docs("proj", output_dir=str(docs_dir))
        assert result["pushed"] == 1
        mock_push.assert_awaited_once()
        assert mock_push.await_args.kwargs["filename"] == "01_OVERVIEW.md"


async def test_delete_document_sends_scoped_request(bridge):
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"deleted": True}

    with patch("mcp_server.services.rag_bridge.httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.delete.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_client

        result = await bridge.delete_document("proj", "01_file.md")

        assert result == {"deleted": True}
        call = mock_client.delete.call_args
        assert call.args[0] == "http://localhost:9999/api/v1/ingest/by-project/proj/document/01_file.md"
        assert call.kwargs["headers"]["Authorization"] == "Bearer test-key"


async def test_push_docs_handles_error(bridge, tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "01_file1.md").write_text("# File 1")

    with patch.object(bridge, "push_markdown", new_callable=AsyncMock) as mock_push:
        mock_push.side_effect = Exception("Connection refused")

        result = await bridge.push_docs("proj", output_dir=str(docs_dir))
        assert result["pushed"] == 0
        assert len(result["errors"]) == 1
        assert "Connection refused" in result["errors"][0]


async def test_push_docs_no_input(bridge):
    result = await bridge.push_docs("proj")
    assert result["pushed"] == 0
    assert "No docs or output_dir provided" in result["errors"]


async def test_push_docs_from_list(bridge):
    docs = [
        {"filename": "a.md", "content": "# A"},
        {"filename": "b.md", "content": "# B"},
    ]

    with patch.object(bridge, "push_markdown", new_callable=AsyncMock) as mock_push:
        mock_push.return_value = {"status": "ok"}

        result = await bridge.push_docs("proj", docs=docs)
        assert result["pushed"] == 2


def _make_internal_docs_tree(tmp_path):
    docs = tmp_path / "docs"
    pkg = docs / "_rag_support" / "phase2_analysis"
    pkg.mkdir(parents=True)
    (pkg / "Item.md").write_text("# Item analysis")
    facts = docs / "_facts"
    facts.mkdir()
    (facts / "entity_index.csv").write_text(
        "id,entity_type,name,path,unit_name,confidence,evidence\n"
        'e1,table,SALESTABLE,Item/job.item,sjItem,1.0,"[]"\n'
    )
    (docs / "01_OVERVIEW.md").write_text("# Visible")
    return docs


async def test_sync_internal_layers_deletes_then_pushes_with_metadata(bridge, tmp_path):
    docs = _make_internal_docs_tree(tmp_path)
    calls: list[str] = []

    with (
        patch.object(bridge, "delete_internal_documents", new_callable=AsyncMock) as mock_delete,
        patch.object(bridge, "push_markdown", new_callable=AsyncMock) as mock_push,
        patch.object(bridge, "post_entity_aliases", new_callable=AsyncMock) as mock_aliases,
    ):
        mock_delete.side_effect = lambda *a, **k: calls.append("delete")
        mock_push.side_effect = lambda *a, **k: calls.append("push")
        mock_aliases.return_value = {"inserted": 1}

        result = await bridge.sync_internal_layers("proj", docs, run_id=209)

    assert result["pushed"] == 1
    assert result["aliases"] == 1
    assert result["errors"] == []
    assert calls[0] == "delete", "previous internal set must be deleted before pushing"
    push_kwargs = mock_push.await_args.kwargs
    assert push_kwargs["filename"] == "_internal/technical_analysis/Item.md"
    assert push_kwargs["metadata"]["source_layer"] == "technical_analysis"
    assert push_kwargs["metadata"]["visibility"] == "internal"
    assert push_kwargs["metadata"]["run_id"] == 209
    alias_args = mock_aliases.await_args
    assert alias_args.args[0] == "proj"
    assert alias_args.args[1][0]["alias"] == "SALESTABLE"


async def test_sync_internal_layers_aborts_on_delete_failure(bridge, tmp_path):
    docs = _make_internal_docs_tree(tmp_path)

    with (
        patch.object(bridge, "delete_internal_documents", new_callable=AsyncMock) as mock_delete,
        patch.object(bridge, "push_markdown", new_callable=AsyncMock) as mock_push,
        patch.object(bridge, "post_entity_aliases", new_callable=AsyncMock) as mock_aliases,
    ):
        mock_delete.side_effect = Exception("Connection refused")

        result = await bridge.sync_internal_layers("proj", docs)

    assert result["pushed"] == 0
    assert len(result["errors"]) == 1
    assert "Internal-layer delete failed" in result["errors"][0]
    mock_push.assert_not_awaited()
    mock_aliases.assert_not_awaited()


async def test_sync_internal_layers_push_error_continues(bridge, tmp_path):
    docs = _make_internal_docs_tree(tmp_path)
    (docs / "_assessment").mkdir()
    (docs / "_assessment" / "scorecard.md").write_text("# Scorecard")

    with (
        patch.object(bridge, "delete_internal_documents", new_callable=AsyncMock),
        patch.object(bridge, "push_markdown", new_callable=AsyncMock) as mock_push,
        patch.object(bridge, "post_entity_aliases", new_callable=AsyncMock) as mock_aliases,
    ):
        mock_push.side_effect = [Exception("boom"), {"status": "ok"}]
        mock_aliases.return_value = {"inserted": 1}

        result = await bridge.sync_internal_layers("proj", docs)

    assert result["pushed"] == 1
    assert len(result["errors"]) == 1
    assert mock_push.await_count == 2
    mock_aliases.assert_awaited_once()


async def test_sync_internal_layers_empty_docs_tree_is_noop(bridge, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()

    with (
        patch.object(bridge, "delete_internal_documents", new_callable=AsyncMock) as mock_delete,
        patch.object(bridge, "push_markdown", new_callable=AsyncMock) as mock_push,
        patch.object(bridge, "post_entity_aliases", new_callable=AsyncMock) as mock_aliases,
    ):
        result = await bridge.sync_internal_layers("proj", docs)

    assert result == {"pushed": 0, "aliases": 0, "errors": []}
    mock_delete.assert_awaited_once(), "delete still runs so stale internal docs are removed"
    mock_push.assert_not_awaited()
    mock_aliases.assert_not_awaited()


async def test_push_docs_skips_empty(bridge):
    docs = [
        {"filename": "", "content": "# A"},
        {"filename": "b.md", "content": ""},
    ]

    with patch.object(bridge, "push_markdown", new_callable=AsyncMock) as mock_push:
        mock_push.return_value = {"status": "ok"}

        result = await bridge.push_docs("proj", docs=docs)
        assert result["pushed"] == 0
        assert len(result["errors"]) == 2
