"""Unit tests for services/patch_section_map.py (Slice 4)."""
from __future__ import annotations

import pytest

from mcp_server.services.patch_section_map import (
    ALWAYS_REWRITE,
    EVIDENCE_REGISTER_HEADING,
    FieldChange,
    docs_for_sections,
    sections_for_delta,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fc(component_type: str, field_path: str = "job.comp.param") -> FieldChange:
    return FieldChange(
        field_path=field_path,
        old_value="old",
        new_value="new",
        component_type=component_type,
    )


# ---------------------------------------------------------------------------
# sections_for_delta
# ---------------------------------------------------------------------------


class TestSectionsForDelta:
    def test_tmap_talend(self) -> None:
        changes = [_fc("tMap")]
        sections = sections_for_delta("talend", changes)
        assert "TRANSFORMATION_DETAIL" in sections
        assert "DATA_FLOWS" in sections
        assert "BUSINESS_RULES" in sections
        assert "GROUP_SUMMARY" in sections  # always included

    def test_unknown_component_type_falls_to_default(self) -> None:
        changes = [_fc("tUnknownXYZ")]
        sections = sections_for_delta("talend", changes)
        assert "COMPONENTS" in sections
        assert "GROUP_SUMMARY" in sections
        # Should NOT include tMap sections
        assert "TRANSFORMATION_DETAIL" not in sections

    def test_workato_trigger(self) -> None:
        changes = [_fc("trigger")]
        sections = sections_for_delta("workato", changes)
        assert "SCHEDULING" in sections
        assert "EXTERNAL_SYSTEMS" in sections
        assert "GROUP_SUMMARY" in sections

    def test_workato_default_platform_fallback(self) -> None:
        """Unknown platform falls back to Talend map."""
        changes = [_fc("tMSSqlInput")]
        sections = sections_for_delta("generic", changes)
        assert "EXTERNAL_SYSTEMS" in sections
        assert "SOURCE_SQL" in sections

    def test_multiple_component_types_union(self) -> None:
        changes = [_fc("tMap"), _fc("tMSSqlInput")]
        sections = sections_for_delta("talend", changes)
        assert "TRANSFORMATION_DETAIL" in sections  # from tMap
        assert "SOURCE_SQL" in sections  # from tMSSqlInput
        assert "DATA_FLOWS" in sections  # from both
        assert "GROUP_SUMMARY" in sections

    def test_always_rewrite_included_even_for_empty_changes(self) -> None:
        sections = sections_for_delta("talend", [_fc("tScheduler")])
        for s in ALWAYS_REWRITE:
            assert s in sections

    def test_returns_frozenset(self) -> None:
        result = sections_for_delta("talend", [_fc("tMap")])
        assert isinstance(result, frozenset)

    def test_none_component_type_uses_default(self) -> None:
        fc = FieldChange(
            field_path="job.comp.param",
            old_value="a",
            new_value="b",
            component_type=None,
        )
        sections = sections_for_delta("talend", [fc])
        assert "COMPONENTS" in sections
        assert "GROUP_SUMMARY" in sections


# ---------------------------------------------------------------------------
# docs_for_sections
# ---------------------------------------------------------------------------


class TestDocsForSections:
    def test_data_flows_section_maps_to_data_flows_doc(self) -> None:
        result = docs_for_sections(frozenset({"DATA_FLOWS"}))
        # The doc with DATA_FLOWS in its phase2_sections should be included.
        # Based on doc_catalog.yaml, 03_DATA_FLOWS_AND_LINEAGE has DATA_FLOWS.
        names = [d.lower() for d in result]
        assert any("data_flow" in n or "lineage" in n for n in names), (
            f"Expected a DATA_FLOWS doc in result, got {result}"
        )

    def test_synthesis_docs_excluded(self) -> None:
        # Synthesis docs like 09_EXECUTIVE_SUMMARY have phase2_sections: []
        # They should never be returned by docs_for_sections.
        result = docs_for_sections(frozenset({"GROUP_SUMMARY", "COMPONENTS"}))
        for name in result:
            assert "executive" not in name.lower(), (
                f"Synthesis doc {name!r} should not be in docs_for_sections result"
            )

    def test_no_matching_sections_returns_empty(self) -> None:
        result = docs_for_sections(frozenset({"NONEXISTENT_SECTION_XYZ"}))
        assert result == []

    def test_empty_sections_returns_empty(self) -> None:
        result = docs_for_sections(frozenset())
        assert result == []

    def test_returns_list(self) -> None:
        result = docs_for_sections(frozenset({"DATA_FLOWS"}))
        assert isinstance(result, list)

    def test_architecture_map_included_for_data_flows(self) -> None:
        result = docs_for_sections(frozenset({"DATA_FLOWS"}))
        names = [d.lower() for d in result]
        # 01_ARCHITECTURE_MAP maps DATA_FLOWS in most catalogs
        assert any("architecture" in n for n in names), (
            f"Expected architecture doc in result for DATA_FLOWS, got {result}"
        )

    def test_multiple_sections_expands_doc_set(self) -> None:
        single = docs_for_sections(frozenset({"TRANSFORMATION_DETAIL"}))
        multi = docs_for_sections(frozenset({"TRANSFORMATION_DETAIL", "EXTERNAL_SYSTEMS"}))
        # multi should be a superset
        assert set(single).issubset(set(multi))


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_evidence_register_heading_value() -> None:
    assert EVIDENCE_REGISTER_HEADING == "Evidence Register"


def test_always_rewrite_contains_group_summary() -> None:
    assert "GROUP_SUMMARY" in ALWAYS_REWRITE
