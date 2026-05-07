"""Tests for generated-doc RAG diff helpers."""

from __future__ import annotations

from mcp_server.api.routes.projects import _diff_generated_docs


def test_diff_generated_docs_detects_changed_added_and_removed(tmp_path):
    old_docs = tmp_path / "old"
    new_docs = tmp_path / "new"
    old_docs.mkdir()
    new_docs.mkdir()

    (old_docs / "01_same.md").write_text("# Same", encoding="utf-8")
    (new_docs / "01_same.md").write_text("# Same", encoding="utf-8")

    (old_docs / "02_changed.md").write_text("# Old", encoding="utf-8")
    (new_docs / "02_changed.md").write_text("# New", encoding="utf-8")

    (new_docs / "03_added.md").write_text("# Added", encoding="utf-8")
    (old_docs / "04_removed.md").write_text("# Removed", encoding="utf-8")

    (old_docs / "_internal.md").write_text("# Old internal", encoding="utf-8")
    (new_docs / "_internal.md").write_text("# New internal", encoding="utf-8")

    changed, removed = _diff_generated_docs(old_docs, new_docs)

    assert changed == {"02_changed.md", "03_added.md"}
    assert removed == {"04_removed.md"}
