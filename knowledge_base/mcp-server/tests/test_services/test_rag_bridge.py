"""Tests for RagBridge service."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcp_server.services.rag_bridge import RagBridge, rag_push_succeeded


def test_rag_push_succeeded_requires_errors_clear_and_expected_count() -> None:
    assert rag_push_succeeded({"pushed": 2, "errors": []}, expected_count=2) is True
    assert rag_push_succeeded({"pushed": 1, "errors": []}, expected_count=2) is False
    assert rag_push_succeeded({"pushed": 2, "errors": ["boom"]}, expected_count=2) is False


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


# --- Pack-declared doc names are authoritative on every push path (D45 / R8) ---


LEGACY_DOC_NAMES = (
    "00_AGENT_BRIEF.md",
    "01_ARCHITECTURE_MAP.md",
    "02_COMPONENT_INVENTORY.md",
    "03_DATA_FLOWS_AND_LINEAGE.md",
    "04_BUSINESS_RULES_AND_TRANSFORMATIONS.md",
    "05_RUNTIME_CONFIG_AND_EXTERNALS.md",
    "06_OPERATIONS_TESTING_AND_DEBUGGING.md",
    "07_QUALITY_SECURITY_RISK_SCORECARD.md",
    "08_CHANGE_PLAYBOOK.md",
    "09_EXECUTIVE_SUMMARY.md",
)
SUPPORT_ARTIFACT_NAMES = ("engineering_facts.md", "dependency_map.md", "sql_usage.md")


def _old_digit_gate_selection(docs_dir, extra_filenames):
    """The selection the removed ``name[0].isdigit()`` gate produced.

    Kept in the test as the explicit regression oracle for the primary paths:
    whatever the new predicate does, the primary paths must still push exactly
    this set.
    """
    extra = extra_filenames or set()
    return {
        p.name for p in sorted(docs_dir.glob("*.md"))
        if p.name[0].isdigit() or p.name in extra
    }


def _write_docs(docs_dir, names):
    docs_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        (docs_dir / name).write_text(f"# {name}\n\nBody.\n", encoding="utf-8")


def test_read_md_files_selects_pack_named_docs_and_excludes_internal(bridge, tmp_path):
    docs_dir = tmp_path / "docs"
    _write_docs(
        docs_dir,
        (
            "00_AGENT_BRIEF.md",
            "INJECTION_FINDINGS.md",
            "TABLE_INVENTORY.md",
            "CROSS_PROJECT_TOPOLOGY.md",
            "_internal.md",
            "engineering_facts.md",
        ),
    )

    selected = {item["filename"] for item in bridge._read_md_files(str(docs_dir))}

    # Pack-declared names are included without any caller passing them, and
    # CROSS_PROJECT_TOPOLOGY.md (pushed deliberately by the partition
    # orchestrator) is not newly excluded.
    assert selected == {
        "00_AGENT_BRIEF.md",
        "INJECTION_FINDINGS.md",
        "TABLE_INVENTORY.md",
        "CROSS_PROJECT_TOPOLOGY.md",
    }
    # Not vacuously true: internal artifacts and engineering-fact support
    # artifacts stay out unless a caller forces them in.
    assert "_internal.md" not in selected
    assert "engineering_facts.md" not in selected

    forced = {item["filename"] for item in bridge._read_md_files(
        str(docs_dir), extra_filenames={"engineering_facts.md"}
    )}
    assert "engineering_facts.md" in forced, "extra_filenames stays an additive superset"
    assert "_internal.md" not in forced


def test_read_md_files_primary_path_set_is_unchanged_for_legacy_docs_dir(bridge, tmp_path):
    """Regression guard: the primary paths' pushed set must not change."""
    docs_dir = tmp_path / "docs"
    _write_docs(docs_dir, LEGACY_DOC_NAMES + SUPPORT_ARTIFACT_NAMES + ("_internal.md",))

    # Generation-completion push (api/routes/projects.py): extra_doc_names are
    # the pack-declared non-numeric names -- none for a legacy run.
    generation = {item["filename"] for item in bridge._read_md_files(str(docs_dir))}
    assert generation == _old_digit_gate_selection(docs_dir, None)
    assert generation == set(LEGACY_DOC_NAMES)

    # Manual re-push: every non-numeric, non-underscore *.md is passed in.
    manual_extras = {
        p.name for p in docs_dir.glob("*.md")
        if not p.name[0].isdigit() and not p.name.startswith("_")
    }
    manual = {item["filename"] for item in bridge._read_md_files(
        str(docs_dir), extra_filenames=manual_extras
    )}
    assert manual == _old_digit_gate_selection(docs_dir, manual_extras)
    assert manual == set(LEGACY_DOC_NAMES) | set(SUPPORT_ARTIFACT_NAMES)


async def test_push_docs_secondary_path_pushes_pack_named_doc(bridge, tmp_path):
    """The five secondary paths pass output_dir + filenames, never extra_doc_names."""
    docs_dir = tmp_path / "docs"
    _write_docs(docs_dir, ("INJECTION_FINDINGS.md", "_internal.md"))

    with patch.object(bridge, "push_markdown", new_callable=AsyncMock) as mock_push:
        mock_push.return_value = {"status": "ok"}

        result = await bridge.push_docs(
            "proj",
            output_dir=str(docs_dir),
            filenames={"INJECTION_FINDINGS.md", "_internal.md"},
        )

    assert result["pushed"] == 1
    assert result["errors"] == []
    pushed = {call.kwargs["filename"] for call in mock_push.await_args_list}
    assert pushed == {"INJECTION_FINDINGS.md"}


async def test_evidence_doc_update_pushes_pack_named_doc(log_manager, monkeypatch, tmp_path):
    """End-to-end on one previously-dropping path (evidence_doc_update.py)."""
    from mcp_server.config import config
    from mcp_server.services.evidence_doc_update import EvidenceDocUpdateService

    monkeypatch.setattr(config, "project_root", tmp_path)
    monkeypatch.setattr(config, "rag_base_url", "http://localhost:9999")
    monkeypatch.setattr(config, "incremental_hash_skip_enabled", False)

    docs_dir = tmp_path / "output" / "proj" / "docs"
    _write_docs(docs_dir, ("INJECTION_FINDINGS.md", "_internal.md"))

    with patch(
        "mcp_server.services.rag_bridge.RagBridge.push_markdown",
        new_callable=AsyncMock,
    ) as mock_push:
        mock_push.return_value = {"status": "ok"}

        result = await EvidenceDocUpdateService(log_manager=log_manager).update_from_evidence(
            project_id="proj",
            filename="INJECTION_FINDINGS.md",
            evidence_text="Injection sink confirmed in the order intake handler.",
        )

    assert result.status == "updated"
    assert result.rag_result["pushed"] == 1
    pushed = {call.kwargs["filename"] for call in mock_push.await_args_list}
    assert pushed == {"INJECTION_FINDINGS.md"}
    assert "_internal.md" not in pushed
