"""Tests for GroupDocDependency -- Slice 2 provenance map loader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_server.services.group_doc_dependency import GroupDocDependency


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_manifest(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _minimal_manifest(doc_packs: dict) -> dict:
    return {"source_analyses": [], "doc_packs": doc_packs}


def _pack(included: dict, obligations: list | None = None) -> dict:
    return {
        "path": "_doc_pack_X.md",
        "included": included,
        "omitted": {},
        "unclassified": {},
        "included_obligations": obligations or [],
        "unmatched_obligations": [],
        "evidence_sources": [],
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_missing_manifest_returns_none(tmp_path: Path) -> None:
    result = GroupDocDependency.load(tmp_path / "no_such.json")
    assert result is None


def test_corrupt_manifest_returns_none(tmp_path: Path) -> None:
    bad = tmp_path / "manifest.json"
    bad.write_text("not json {{", encoding="utf-8")
    result = GroupDocDependency.load(bad)
    assert result is None


def test_empty_doc_packs_returns_empty_sets(tmp_path: Path) -> None:
    p = tmp_path / "manifest.json"
    _write_manifest(p, _minimal_manifest({}))
    dep = GroupDocDependency.load(p)
    assert dep is not None
    assert dep.docs_for_group("scripts") == set()
    assert dep.groups_for_doc("01_ARCHITECTURE_MAP") == set()
    assert dep.cross_cutting_docs() == set()


def test_group_name_stripped_of_md_suffix(tmp_path: Path) -> None:
    # Manifest stores group file keys as "scripts.md"; the index key must be "scripts".
    p = tmp_path / "manifest.json"
    _write_manifest(
        p,
        _minimal_manifest({
            "01_ARCH": _pack({"scripts.md": ["GROUP_SUMMARY"]}),
        }),
    )
    dep = GroupDocDependency.load(p)
    assert dep is not None
    assert "01_ARCH" in dep.docs_for_group("scripts")
    assert dep.docs_for_group("scripts.md") == set()


def test_docs_for_group_and_groups_for_doc_invert(tmp_path: Path) -> None:
    # Two docs, three groups. Verify the bi-directional index inverts correctly.
    p = tmp_path / "manifest.json"
    _write_manifest(
        p,
        _minimal_manifest({
            "01_ARCH": _pack({"scripts.md": ["GROUP_SUMMARY"], "config.md": ["RISK_FLAGS"]}),
            "02_COMP": _pack({"scripts.md": ["COMPONENTS"], "sql.md": ["EXECUTION_FLOW"]}),
        }),
    )
    dep = GroupDocDependency.load(p)
    assert dep is not None

    assert dep.docs_for_group("scripts") == {"01_ARCH", "02_COMP"}
    assert dep.docs_for_group("config") == {"01_ARCH"}
    assert dep.docs_for_group("sql") == {"02_COMP"}
    assert dep.docs_for_group("nonexistent") == set()

    assert dep.groups_for_doc("01_ARCH") == {"scripts", "config"}
    assert dep.groups_for_doc("02_COMP") == {"scripts", "sql"}
    assert dep.groups_for_doc("03_MISSING") == set()


def test_cross_cutting_detection(tmp_path: Path) -> None:
    canonical_obligation = {
        "scope": "units",
        "category": "canonical_inventory",
        "terms": ["name", "group"],
        "doc_targets": ["02_COMP", "00_AGENT_BRIEF"],
    }
    other_obligation = {
        "scope": "shared",
        "category": "risk_summary",
        "terms": ["risk"],
        "doc_targets": ["01_ARCH"],
    }
    p = tmp_path / "manifest.json"
    _write_manifest(
        p,
        _minimal_manifest({
            "02_COMP": _pack(
                {"scripts.md": ["COMPONENTS"]},
                obligations=[canonical_obligation],
            ),
            "00_AGENT_BRIEF": _pack(
                {"config.md": ["GROUP_SUMMARY"]},
                obligations=[canonical_obligation],
            ),
            "01_ARCH": _pack(
                {"scripts.md": ["EXECUTION_FLOW"]},
                obligations=[other_obligation],
            ),
        }),
    )
    dep = GroupDocDependency.load(p)
    assert dep is not None

    cross = dep.cross_cutting_docs()
    assert "02_COMP" in cross
    assert "00_AGENT_BRIEF" in cross
    assert "01_ARCH" not in cross


def test_cross_cutting_doc_without_any_included_sections(tmp_path: Path) -> None:
    # A doc may be cross-cutting even if it has no included group sections
    # (it still has the canonical_inventory obligation).
    canonical_obligation = {"scope": "units", "category": "canonical_inventory", "terms": []}
    p = tmp_path / "manifest.json"
    _write_manifest(
        p,
        _minimal_manifest({
            "00_AGENT_BRIEF": _pack({}, obligations=[canonical_obligation]),
        }),
    )
    dep = GroupDocDependency.load(p)
    assert dep is not None
    assert "00_AGENT_BRIEF" in dep.cross_cutting_docs()
    # No group contributed sections, so docs_for_group returns nothing.
    assert dep.docs_for_group("scripts") == set()


def test_missing_doc_packs_key_returns_empty_dependency(tmp_path: Path) -> None:
    # Old manifest format without doc_packs -> no doc_packs dict -> empty index.
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps({"source_analyses": []}), encoding="utf-8")
    dep = GroupDocDependency.load(p)
    assert dep is not None
    assert dep.docs_for_group("x") == set()
    assert dep.cross_cutting_docs() == set()


def test_docs_for_group_returns_copy(tmp_path: Path) -> None:
    # Mutating the returned set must not corrupt the internal index.
    p = tmp_path / "manifest.json"
    _write_manifest(
        p,
        _minimal_manifest({"01_ARCH": _pack({"scripts.md": ["GROUP_SUMMARY"]})}),
    )
    dep = GroupDocDependency.load(p)
    assert dep is not None
    first = dep.docs_for_group("scripts")
    first.add("INJECTED")
    assert "INJECTED" not in dep.docs_for_group("scripts")
