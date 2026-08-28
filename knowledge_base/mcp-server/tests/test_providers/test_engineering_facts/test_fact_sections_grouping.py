"""dependency_map.md is grouped into ``##`` sections per dependency_type (WI-2).

The map had exactly one ``#`` heading and one flat table, so
``phase3_slices.segment_markdown`` had no H2 boundary to split on and fell
back to blind ~75 KB line blocks that can never fit a slice -- the whole file
was dropped. Grouping gives the splitter real section boundaries.

Grouping is opt-in per artifact (``ArtifactSpec.group_by``) and only
``dependency_map`` uses it, so the consumers below keep seeing the exact rows
they saw before, with headings inserted between them. Each consumer named in
the work item is exercised against a grouped file here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_server.providers.custom.engineering_facts.builder import EngineeringFactsBuilder
from mcp_server.providers.custom.engineering_facts.writers.markdown_writer import (
    MARKDOWN_ARTIFACTS,
    MarkdownFactWriter,
)
from mcp_server.providers.custom.phase2_context import _write_scoped_artifact
from mcp_server.providers.custom.phase3_slices import segment_markdown
from mcp_server.providers.custom.target_evidence import filter_fact_table
from mcp_server.services.doc_quality_scorer import (
    is_product_doc_filename,
    is_published_doc_filename,
    is_support_artifact_filename,
)

UNIT = "AssetCheck_GetLocation"
SIBLING = "AssetCheck_GetLocations"


@pytest.fixture()
def staging(tmp_path: Path) -> Path:
    builder = EngineeringFactsBuilder("p", "talend")
    for i in range(40):
        builder.add_dependency(source=UNIT, target=f"Child_{i:02d}", dependency_type="calls_job")
        builder.add_dependency(source=SIBLING, target=f"Other_{i:02d}", dependency_type="calls_job")
        builder.add_dependency(source=UNIT, target=f"System_{i % 7}", dependency_type="external_system")
        builder.add_dependency(source=UNIT, target=f"conn_{i:02d}", dependency_type="uses_connection")
        builder.add_dependency(source=SIBLING, target=f"routine_{i:02d}", dependency_type="uses_routine")
        builder.add_impact(
            changed_entity=f"Child_{i:02d}",
            impacted_entity=UNIT,
            check="Review parent orchestration and context passing.",
        )
    out = tmp_path / "staging"
    MarkdownFactWriter().write(builder.build(), out)
    return out


def _headings(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("#")]


def test_only_dependency_map_is_grouped():
    grouped = {name for name, spec in MARKDOWN_ARTIFACTS.items() if spec.group_by}
    assert grouped == {"dependency_map"}
    # change_impact_matrix must stay flat: phase3_context._markdown_table_rows
    # treats the first pipe line as the header and everything after the
    # separator as data, so repeated per-section headers would parse as rows.
    assert MARKDOWN_ARTIFACTS["change_impact_matrix"].group_by is None


def test_sections_are_calls_job_first_then_alphabetical(staging: Path):
    text = (staging / "dependency_map.md").read_text(encoding="utf-8")
    assert _headings(text) == [
        "# Dependency Map",
        "## calls_job",
        "## external_system",
        "## uses_connection",
        "## uses_routine",
    ]


def test_grouping_is_order_independent(tmp_path: Path):
    rows = [
        ("b", "1", "uses_routine"),
        ("a", "2", "calls_job"),
        ("c", "3", "external_system"),
        ("d", "4", "calls_job"),
    ]

    def render(ordered: list[tuple[str, str, str]]) -> str:
        builder = EngineeringFactsBuilder("p", "talend")
        for source, target, dependency_type in ordered:
            builder.add_dependency(source=source, target=target, dependency_type=dependency_type)
        out = tmp_path / f"o{len(list(tmp_path.iterdir()))}"
        MarkdownFactWriter().write(builder.build(), out)
        return (out / "dependency_map.md").read_text(encoding="utf-8")

    assert _headings(render(rows)) == _headings(render(list(reversed(rows))))


def test_every_row_survives_grouping(staging: Path):
    text = (staging / "dependency_map.md").read_text(encoding="utf-8")
    data_rows = [
        line
        for line in text.splitlines()
        if line.startswith("|") and not line.startswith("|source|") and "---" not in line
    ]
    # 40 calls_job per job + 7 distinct external systems + 40 connections
    # + 40 routines; the builder deduplicates on stable id.
    assert len(data_rows) == 167
    section_counts = {
        line.removeprefix("## "): int(text.split(line, 1)[1].split(" rows.", 1)[0].strip())
        for line in _headings(text)
        if line.startswith("## ")
    }
    assert sum(section_counts.values()) == len(data_rows)
    assert section_counts["calls_job"] == 80
    assert f"|{UNIT}|Child_00|calls_job|1.0||" in text


def test_segment_markdown_yields_per_type_segments(staging: Path):
    text = (staging / "dependency_map.md").read_text(encoding="utf-8")
    segments = segment_markdown(text, 8_000)

    assert len(segments) >= 5  # preamble + one per dependency_type
    assert "".join(segments) == text, "section text must stay verbatim"
    starts = [s.splitlines()[0] for s in segments]
    assert "## calls_job" in starts
    assert "## uses_routine" in starts


def test_segment_markdown_had_no_boundary_before_grouping():
    """Regression anchor: an ungrouped map degrades to line splitting."""
    flat = "# Dependency Map\n\n|a|b|\n|---|---|\n" + "|x|y|\n" * 4000
    segments = segment_markdown(flat, 8_000)
    assert not any(s.startswith("## ") for s in segments)


# --------------------------------------------------------------------------
# Consumers named in WI-2: each must still parse the grouped file.
# --------------------------------------------------------------------------


def test_target_evidence_filter_fact_table_still_filters(staging: Path, tmp_path: Path):
    filtered = filter_fact_table(
        staging / "dependency_map.md", UNIT, tmp_path / "filtered", 200_000
    )
    assert filtered is not None
    text = filtered.read_text(encoding="utf-8")

    # Headings and per-section table headers are preserved.
    assert "# Dependency Map" in text
    assert "## calls_job" in text
    assert "|source|target|dependency_type|confidence|evidence|" in text
    # Boundary-aware: the sibling job is not pulled in.
    assert SIBLING not in text
    assert len([line for line in text.splitlines() if line.startswith(f"|{UNIT}|")]) == 87


def test_phase2_context_scoped_artifact_still_filters(staging: Path, tmp_path: Path):
    scoped = _write_scoped_artifact(
        source=staging / "dependency_map.md",
        destination=tmp_path / "scoped_dependency_map.md",
        terms=(UNIT,),
        max_bytes=200_000,
    )
    assert scoped is not None
    text = scoped.read_text(encoding="utf-8")

    assert "# Dependency Map" in text
    assert "## calls_job" in text
    assert "## uses_connection" in text
    assert f"|{UNIT}|Child_00|calls_job|1.0||" in text
    assert not [line for line in text.splitlines() if line.startswith(f"|{SIBLING}|")]


def test_filename_predicates_are_unaffected_by_grouping():
    # doc_quality_scorer / rag_bridge classify these artifacts by FILENAME
    # only; they never parse the table. Pinned so a future grouping change
    # cannot quietly promote a fact map into the published document set.
    for name in ("dependency_map.md", "engineering_facts.md", "known_risks.md"):
        assert is_support_artifact_filename(name)
        assert not is_published_doc_filename(name)
        assert not is_product_doc_filename(name)


def test_rag_bridge_selection_delegates_to_the_shared_predicate():
    """rag_bridge._read_md_files selects by name, never by table shape."""
    import inspect

    from mcp_server.services.rag_bridge import RagBridge

    source = inspect.getsource(RagBridge._read_md_files)
    assert "is_published_doc_filename" in source
    assert "|" not in source.split('"""')[-1], "selection must not parse table rows"
