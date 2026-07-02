"""Unit tests for services/synthesis_impact_mapper.py (Living-Document
Reingestion, Slice 3).

Pure module -- no I/O, no model calls. Tests operate entirely on fixture
strings and FieldChange lists.
"""
from __future__ import annotations

from mcp_server.services.patch_section_map import FieldChange
from mcp_server.services.synthesis_impact_mapper import (
    SynthesisImpactMapper,
    doc_references_group,
    removed_unit_identifiers,
    unit_identifiers,
    verify_removal,
)


def _fc(job: str, unique: str, param: str, old, new) -> FieldChange:
    return FieldChange(
        field_path=f"({job}, {unique}, {param})",
        old_value=old,
        new_value=new,
        component_type="tMap",
    )


_SAMPLE_DOC = (
    "# 09 Executive Summary\n\n"
    "## Executive Risk Summary\n\n"
    "GROUP_A (`tMap_1`) is a stable payroll pipeline with no outstanding risk.\n\n"
    "## Modernisation Priorities\n\n"
    "| Priority | Initiative | Rationale |\n"
    "| --- | --- | --- |\n"
    "| 1 | Migrate GROUP_B to cloud queueing | Reduce latency |\n\n"
    "## Confidence and Limitations\n\n"
    "Scanner coverage is complete across all groups.\n\n"
)


# ---------------------------------------------------------------------------
# unit_identifiers
# ---------------------------------------------------------------------------


class TestUnitIdentifiers:
    def test_includes_group_name(self) -> None:
        ids = unit_identifiers("GROUP_A", [])
        assert ids == frozenset({"GROUP_A"})

    def test_includes_job_and_unique_names_from_field_changes(self) -> None:
        changes = [_fc("Job_Payroll", "tMap_1", "MAPPING", "a", "b")]
        ids = unit_identifiers("GROUP_A", changes)
        assert ids == frozenset({"GROUP_A", "Job_Payroll", "tMap_1"})

    def test_empty_group_and_no_changes_yields_empty(self) -> None:
        assert unit_identifiers("", []) == frozenset()


# ---------------------------------------------------------------------------
# removed_unit_identifiers
# ---------------------------------------------------------------------------


class TestRemovedUnitIdentifiers:
    def test_component_removed_from_surviving_job(self) -> None:
        prev = [{
            "name": "Job_A",
            "components": [
                {"unique_name": "tMap_1", "component_type": "tMap"},
                {"unique_name": "tMap_2", "component_type": "tMap"},
            ],
        }]
        curr = [{
            "name": "Job_A",
            "components": [{"unique_name": "tMap_1", "component_type": "tMap"}],
        }]
        assert removed_unit_identifiers(prev, curr) == frozenset({"tMap_2"})

    def test_whole_job_removed(self) -> None:
        prev = [
            {"name": "Job_A", "components": []},
            {"name": "Job_B", "components": []},
        ]
        curr = [{"name": "Job_A", "components": []}]
        assert removed_unit_identifiers(prev, curr) == frozenset({"Job_B"})

    def test_no_removal_yields_empty(self) -> None:
        jobs = [{"name": "Job_A", "components": [{"unique_name": "tMap_1"}]}]
        assert removed_unit_identifiers(jobs, jobs) == frozenset()

    def test_added_component_is_not_a_removal(self) -> None:
        prev = [{"name": "Job_A", "components": []}]
        curr = [{
            "name": "Job_A",
            "components": [{"unique_name": "tMap_1", "component_type": "tMap"}],
        }]
        assert removed_unit_identifiers(prev, curr) == frozenset()


# ---------------------------------------------------------------------------
# doc_references_group
# ---------------------------------------------------------------------------


class TestDocReferencesGroup:
    def test_true_when_group_name_present(self) -> None:
        assert doc_references_group(_SAMPLE_DOC, "GROUP_A", [])

    def test_true_when_unit_identifier_present(self) -> None:
        changes = [_fc("Job_X", "tMap_1", "MAPPING", "a", "b")]
        assert doc_references_group(_SAMPLE_DOC, "GROUP_A", changes)

    def test_false_when_group_and_units_absent(self) -> None:
        changes = [_fc("Job_Z", "tMap_9", "MAPPING", "a", "b")]
        assert not doc_references_group(_SAMPLE_DOC, "GROUP_Z", changes)


# ---------------------------------------------------------------------------
# SynthesisImpactMapper.map_sections -- AC1: single-unit change amends only
# the tied sections.
# ---------------------------------------------------------------------------


class TestMapSectionsBasic:
    def test_only_referencing_heading_is_mapped(self) -> None:
        changes = [_fc("Job_A", "tMap_1", "MAPPING", "a", "b")]
        mapping = SynthesisImpactMapper.map_sections(
            doc_text=_SAMPLE_DOC, group="GROUP_A", field_changes=changes,
        )
        assert mapping.mapped
        assert mapping.headings == frozenset({"Executive Risk Summary"})
        # Unrelated headings (Modernisation Priorities mentions GROUP_B,
        # Confidence and Limitations mentions no group) are NOT mapped.
        assert "Modernisation Priorities" not in mapping.headings
        assert "Confidence and Limitations" not in mapping.headings

    def test_group_name_alone_matches_even_without_unit_tokens(self) -> None:
        mapping = SynthesisImpactMapper.map_sections(
            doc_text=_SAMPLE_DOC, group="GROUP_B", field_changes=[],
        )
        assert mapping.headings == frozenset({"Modernisation Priorities"})

    def test_no_reference_anywhere_yields_unmapped(self) -> None:
        changes = [_fc("Job_Z", "tMap_9", "MAPPING", "a", "b")]
        mapping = SynthesisImpactMapper.map_sections(
            doc_text=_SAMPLE_DOC, group="GROUP_ZZZ", field_changes=changes,
        )
        assert not mapping.mapped
        assert mapping.headings == frozenset()

    def test_reference_outside_any_heading_is_unmapped(self) -> None:
        # Group mentioned only in the H1 title / preamble, not inside any
        # ## Heading section -- AC3: not expressible as a section-level edit.
        doc = (
            "# GROUP_A Executive Summary\n\n"
            "## Executive Risk Summary\n\n"
            "No group-specific content here.\n\n"
        )
        mapping = SynthesisImpactMapper.map_sections(
            doc_text=doc, group="GROUP_A", field_changes=[],
        )
        assert not mapping.mapped


# ---------------------------------------------------------------------------
# SynthesisImpactMapper.map_sections -- AC2: removal detection.
# ---------------------------------------------------------------------------


class TestMapSectionsRemoval:
    def test_removed_identifier_found_in_matched_heading(self) -> None:
        doc = (
            "## Executive Risk Summary\n\n"
            "GROUP_A relies on `tMap_1` and `tMap_2` for payroll transforms.\n\n"
        )
        changes = [_fc("Job_A", "tMap_1", "MAPPING", "a", "b")]
        mapping = SynthesisImpactMapper.map_sections(
            doc_text=doc,
            group="GROUP_A",
            field_changes=changes,
            removed_identifiers=frozenset({"tMap_2"}),
        )
        assert mapping.mapped
        assert mapping.removed_identifiers == frozenset({"tMap_2"})

    def test_removed_identifier_not_present_in_doc_is_not_reported(self) -> None:
        doc = "## Executive Risk Summary\n\nGROUP_A is stable.\n\n"
        changes = [_fc("Job_A", "tMap_1", "MAPPING", "a", "b")]
        mapping = SynthesisImpactMapper.map_sections(
            doc_text=doc,
            group="GROUP_A",
            field_changes=changes,
            removed_identifiers=frozenset({"tMap_9"}),
        )
        assert mapping.removed_identifiers == frozenset()


# ---------------------------------------------------------------------------
# verify_removal -- G6 removal-completeness guard.
# ---------------------------------------------------------------------------


class TestVerifyRemoval:
    def test_empty_removed_set_always_passes(self) -> None:
        assert verify_removal("anything at all", frozenset()) == set()

    def test_residual_identifier_detected(self) -> None:
        text = "GROUP_A now only uses `tMap_1`; `tMap_2` was retired."
        residual = verify_removal(text, frozenset({"tMap_2"}))
        assert residual == {"tMap_2"}

    def test_clean_removal_yields_no_residual(self) -> None:
        text = "GROUP_A now only uses `tMap_1` for payroll transforms."
        residual = verify_removal(text, frozenset({"tMap_2"}))
        assert residual == set()
