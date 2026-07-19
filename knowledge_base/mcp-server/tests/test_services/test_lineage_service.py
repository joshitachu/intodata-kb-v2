"""Unit tests for `lineage_service.build_field_lineage` (S4 query-API BFS).

Synthetic entries (raw dicts, same shape as `scan/lineage.json`) exercise the
BFS mechanics directly: direction, depth cap, cycle safety, unresolved/dynamic
edges, and the optional `component` disambiguator. The golden-fixture,
real-scan-output happy path lives in
`tests/test_api/test_lineage_field.py` (route-level, RBAC included).
"""

from __future__ import annotations

import json
import os

import pytest

from mcp_server.services.lineage_service import (
    LineageDataUnavailableError,
    build_field_lineage,
)

PROJECT_ID = "lineageproj"
JOB = "ChainJob"


def _entry(
    *,
    source_table="",
    source_column="",
    source_component="",
    target_table,
    target_column,
    target_component="",
    job_name=JOB,
    confidence="certain",
    resolved=True,
    dynamic_refs=None,
    expression="",
) -> dict:
    return {
        "source_system": "",
        "source_table": source_table,
        "source_column": source_column,
        "source_component": source_component,
        "target_table": target_table,
        "target_column": target_column,
        "target_component": target_component,
        "job_name": job_name,
        "confidence": confidence,
        "resolved": resolved,
        "dynamic_refs": dynamic_refs or [],
        "transformations": [
            {"transformation_type": "direct_map", "expression": expression, "human_explanation": ""}
        ] if expression else [],
    }


def _write_lineage(output_dir, entries: list[dict], project_id: str = PROJECT_ID) -> None:
    scan_dir = output_dir / project_id / "scan"
    scan_dir.mkdir(parents=True, exist_ok=True)
    (scan_dir / "lineage.json").write_text(
        json.dumps({"entries": entries, "unmapped_columns": [], "summary": {}}),
        encoding="utf-8",
    )


class TestNoLineageData:
    def test_missing_lineage_json_raises(self, tmp_path):
        with pytest.raises(LineageDataUnavailableError):
            build_field_lineage(
                PROJECT_ID, tmp_path, job_name=JOB, table="A", column="a", direction="both", max_depth=5
            )

    def test_field_with_no_edges_returns_empty_but_no_error(self, tmp_path):
        _write_lineage(tmp_path, [_entry(source_table="A", source_column="a", target_table="B", target_column="b")])
        result = build_field_lineage(
            PROJECT_ID, tmp_path, job_name=JOB, table="Nowhere", column="x", direction="both", max_depth=5
        )
        assert result.edges == []
        assert result.nodes == [result.start]
        assert result.start.table == "Nowhere"
        assert result.start.column == "x"


class TestDirection:
    """A -> B -> C chain (direct_map, resolved end to end)."""

    @pytest.fixture
    def chain(self, tmp_path):
        entries = [
            _entry(source_table="A", source_column="a", source_component="CompA",
                   target_table="B", target_column="b", target_component="CompB", expression="A.a"),
            _entry(source_table="B", source_column="b", source_component="CompB",
                   target_table="C", target_column="c", target_component="CompC", expression="B.b"),
        ]
        _write_lineage(tmp_path, entries)
        return tmp_path

    def test_down_from_start_follows_source_to_target(self, chain):
        result = build_field_lineage(
            PROJECT_ID, chain, job_name=JOB, table="A", column="a", direction="down", max_depth=10
        )
        assert len(result.edges) == 2
        targets = {(e.target_table, e.target_column) for e in result.edges}
        assert targets == {("B", "b"), ("C", "c")}
        assert result.truncated is False

    def test_up_from_end_follows_target_to_source(self, chain):
        result = build_field_lineage(
            PROJECT_ID, chain, job_name=JOB, table="C", column="c", direction="up", max_depth=10
        )
        assert len(result.edges) == 2
        sources = {(e.source_table, e.source_column) for e in result.edges}
        assert sources == {("A", "a"), ("B", "b")}

    def test_up_direction_does_not_pull_in_downstream_edges(self, chain):
        """Querying 'up' from the middle field must not include the B->C edge."""
        result = build_field_lineage(
            PROJECT_ID, chain, job_name=JOB, table="B", column="b", direction="up", max_depth=10
        )
        assert len(result.edges) == 1
        assert result.edges[0].source_table == "A"

    def test_both_direction_covers_ancestors_and_descendants(self, chain):
        result = build_field_lineage(
            PROJECT_ID, chain, job_name=JOB, table="B", column="b", direction="both", max_depth=10
        )
        assert len(result.edges) == 2
        pairs = {(e.source_table, e.target_table) for e in result.edges}
        assert pairs == {("A", "B"), ("B", "C")}


class TestDepthCap:
    @pytest.fixture
    def long_chain(self, tmp_path):
        # A -> B -> C -> D -> E (4 hops)
        letters = ["A", "B", "C", "D", "E"]
        entries = [
            _entry(source_table=letters[i], source_column="x", target_table=letters[i + 1], target_column="x")
            for i in range(len(letters) - 1)
        ]
        _write_lineage(tmp_path, entries)
        return tmp_path

    def test_max_depth_limits_traversal(self, long_chain):
        result = build_field_lineage(
            PROJECT_ID, long_chain, job_name=JOB, table="A", column="x", direction="down", max_depth=2
        )
        # depth 1: A->B, depth 2: B->C reachable; C->D would be depth 3, excluded.
        reached_targets = {e.target_table for e in result.edges}
        assert reached_targets == {"B", "C"}
        assert "D" not in reached_targets
        assert result.truncated is True

    def test_max_depth_not_truncated_when_graph_is_shorter(self, long_chain):
        result = build_field_lineage(
            PROJECT_ID, long_chain, job_name=JOB, table="A", column="x", direction="down", max_depth=50
        )
        assert {e.target_table for e in result.edges} == {"B", "C", "D", "E"}
        assert result.truncated is False


class TestCycleSafety:
    @pytest.fixture
    def cyclic(self, tmp_path):
        # X -> Y -> X (cycle)
        entries = [
            _entry(source_table="X", source_column="x", target_table="Y", target_column="y"),
            _entry(source_table="Y", source_column="y", target_table="X", target_column="x"),
        ]
        _write_lineage(tmp_path, entries)
        return tmp_path

    def test_cycle_terminates_and_captures_both_edges(self, cyclic):
        result = build_field_lineage(
            PROJECT_ID, cyclic, job_name=JOB, table="X", column="x", direction="down", max_depth=10
        )
        # Must return (not hang) and record both hops of the cycle exactly once.
        assert len(result.edges) == 2
        pairs = {(e.source_table, e.target_table) for e in result.edges}
        assert pairs == {("X", "Y"), ("Y", "X")}

    def test_cycle_in_both_direction_deduplicates_shared_edge(self, cyclic):
        """'both' from X should not double-count the X->Y edge found by both walks."""
        result = build_field_lineage(
            PROJECT_ID, cyclic, job_name=JOB, table="X", column="x", direction="both", max_depth=10
        )
        assert len(result.edges) == 2


class TestUnresolvedAndDynamicEdges:
    def test_constant_source_edge_recorded_with_no_further_node(self, tmp_path):
        entries = [
            _entry(source_table="", source_column="", target_table="Z", target_column="z",
                   resolved=False, confidence="constant" if False else "certain",
                   expression='"fixed"'),
        ]
        _write_lineage(tmp_path, entries)
        result = build_field_lineage(
            PROJECT_ID, tmp_path, job_name=JOB, table="Z", column="z", direction="up", max_depth=10
        )
        assert len(result.edges) == 1
        assert result.edges[0].resolved is False
        assert result.edges[0].source_table == ""
        # No further node beyond the constant -- only the start node.
        assert len(result.nodes) == 1

    def test_dynamic_ref_never_dropped(self, tmp_path):
        entries = [
            _entry(source_table="", source_column="", target_table="Z", target_column="z",
                   resolved=False, confidence="dynamic", dynamic_refs=["context.Setting"]),
        ]
        _write_lineage(tmp_path, entries)
        result = build_field_lineage(
            PROJECT_ID, tmp_path, job_name=JOB, table="Z", column="z", direction="up", max_depth=10
        )
        assert result.edges[0].dynamic_refs == ["context.Setting"]
        assert result.edges[0].confidence == "dynamic"


class TestComponentDisambiguation:
    @pytest.fixture
    def ambiguous(self, tmp_path):
        # Same (job, table, column) reached from two different components --
        # an edge case the `component` query param exists to disambiguate.
        entries = [
            _entry(source_table="T", source_column="x", source_component="CompA",
                   target_table="Out", target_column="o1"),
            _entry(source_table="T", source_column="x", source_component="CompB",
                   target_table="Out", target_column="o2"),
        ]
        _write_lineage(tmp_path, entries)
        return tmp_path

    def test_component_filter_selects_matching_entries_only(self, ambiguous):
        result = build_field_lineage(
            PROJECT_ID, ambiguous, job_name=JOB, table="T", column="x", component="CompA",
            direction="down", max_depth=10,
        )
        assert len(result.edges) == 1
        assert result.edges[0].target_table == "Out"
        assert result.edges[0].target_column == "o1"

    def test_component_filter_with_no_match_returns_empty(self, ambiguous):
        result = build_field_lineage(
            PROJECT_ID, ambiguous, job_name=JOB, table="T", column="x", component="NoSuchComponent",
            direction="down", max_depth=10,
        )
        assert result.edges == []


class TestIndexCachePerMtime:
    def test_cache_invalidates_on_lineage_json_mtime_change(self, tmp_path):
        _write_lineage(tmp_path, [_entry(source_table="A", source_column="a", target_table="B", target_column="b")])
        first = build_field_lineage(
            PROJECT_ID, tmp_path, job_name=JOB, table="A", column="a", direction="down", max_depth=10
        )
        assert len(first.edges) == 1

        # Rewrite with an extra entry -- the on-disk mtime must change, so the
        # cached index is rebuilt rather than served stale. Force the mtime
        # forward explicitly (some filesystems have 1s mtime resolution, so a
        # back-to-back write in the same test could otherwise land on the
        # same timestamp and mask a real cache-invalidation bug).
        _write_lineage(tmp_path, [
            _entry(source_table="A", source_column="a", target_table="B", target_column="b"),
            _entry(source_table="A", source_column="a", target_table="C", target_column="c"),
        ])
        lineage_path = tmp_path / PROJECT_ID / "scan" / "lineage.json"
        new_mtime = lineage_path.stat().st_mtime + 5
        os.utime(lineage_path, (new_mtime, new_mtime))

        second = build_field_lineage(
            PROJECT_ID, tmp_path, job_name=JOB, table="A", column="a", direction="down", max_depth=10
        )
        assert len(second.edges) == 2
