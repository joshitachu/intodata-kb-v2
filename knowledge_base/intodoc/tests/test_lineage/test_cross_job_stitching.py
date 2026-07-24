"""Golden-file tests for the S5 cross-job stitching increment in
`intodoc.lineage` (architecture doc sec 2.2 step 5 / sec 5 S5):

(a) trunjob context-param passing -- a parent job's `trunjob_refs`
    context-param override, consumed as `context.<name>` in the child
    job's tMap expressions.
(b) table identity -- an output component in one job and an input
    component in another job that share a resolved connection ref and
    table param.

Neither mechanism has real usage in the available scan fixtures/output
(every real `trunjob_refs[].context_params` list scanned to date is empty,
and no two real jobs share both a resolved `metadata_connection_ref` and
matching `TABLE` param -- verified against alitalend/reverse_engineer_VW/
cdc scan output before writing this test), so these are constructed
minimal fixtures that exercise the logic directly, following the same
`ParsedJob`-construction pattern as `test_build_lineage_matrix.py`.
"""

from intodoc.lineage import build_lineage_matrix
from intodoc.models import (
    Component,
    ComponentParam,
    LineageConfidence,
    ParsedJob,
    ScanResult,
    TMapData,
    TMapEntry,
    TRunJobRef,
)
from intodoc.scanner.credential_filter import CredentialFilter


def _matrix(*jobs):
    return build_lineage_matrix(
        ScanResult(platform="talend", repo_root="/fake", jobs=list(jobs)),
        CredentialFilter(),
    )


# ── (a) trunjob context-param passing ────────────────────────────────────


def _child_job_referencing_context(param_name: str, name: str = "ChildJob") -> ParsedJob:
    tmap = TMapData(
        input_tables=["row1"],
        output_tables=["out1"],
        entries=[TMapEntry(table_name="out1", column_name="fileName", expression=f"context.{param_name}")],
    )
    return ParsedJob(name=name, path="p", tmap_data=[tmap])


def _parent_job(child_job: str, context_params=None, name: str = "ParentJob") -> ParsedJob:
    return ParsedJob(
        name=name,
        path="p",
        trunjob_refs=[TRunJobRef(child_job=child_job, context_params=context_params or [])],
    )


class TestTrunjobContextParamStitching:
    def test_context_param_used_in_child_creates_inferred_edge(self):
        child = _child_job_referencing_context("SourceFileName")
        parent = _parent_job(
            "ChildJob",
            context_params=[ComponentParam(name="SourceFileName", value="context.ParentSourceFile")],
        )
        matrix = _matrix(parent, child)

        # The S4 intra-job pass still emits its own dynamic, unresolved entry
        # for the child's expression -- never dropped just because a
        # cross-job edge was also inferred for it.
        intra_job = [e for e in matrix.entries if e.job_name == "ChildJob" and e.source_job_name == ""]
        assert len(intra_job) == 1
        assert intra_job[0].confidence == LineageConfidence.DYNAMIC
        assert intra_job[0].dynamic_refs == ["context.SourceFileName"]

        cross_job = [e for e in matrix.entries if e.source_job_name != ""]
        assert len(cross_job) == 1
        edge = cross_job[0]
        assert edge.confidence == LineageConfidence.INFERRED
        assert edge.source_job_name == "ParentJob"
        assert edge.job_name == "ChildJob"
        assert edge.source_column == "SourceFileName"
        assert edge.target_table == "out1"
        assert edge.target_column == "fileName"
        assert edge.resolved is True
        assert edge.transformations[0].transformation_type == "trunjob_context_param"
        assert matrix.summary["cross_job_entries"] == 1

    def test_empty_context_params_matches_real_world_scan_output_and_never_stitches(self):
        """Verified real-world state (alitalend/reverse_engineer_VW/cdc): every
        scanned `trunjob_refs[].context_params` is `[]` today. Must not crash
        and must never fabricate an edge from nothing."""
        child = _child_job_referencing_context("SourceFileName")
        parent = _parent_job("ChildJob", context_params=[])
        matrix = _matrix(parent, child)
        assert all(e.source_job_name == "" for e in matrix.entries)
        assert matrix.summary["cross_job_entries"] == 0

    def test_composite_resolved_id_stitches_the_intended_cross_root_child(self):
        child = _child_job_referencing_context("SourceFileName", name="ChildJob")
        child.job_id = "root_b_ChildJob-abcdef123456"
        child.source_root = "root-b"
        parent = _parent_job(
            "ChildJob",
            context_params=[ComponentParam(name="SourceFileName", value="value")],
        )
        parent.job_id = "root_a_ParentJob-fedcba654321"
        parent.source_root = "root-a"
        parent.trunjob_refs[0].resolved_job_id = child.job_id
        parent.trunjob_refs[0].resolution = "global"

        matrix = _matrix(parent, child)
        edges = [edge for edge in matrix.entries if edge.source_job_name == parent.job_id]
        assert len(edges) == 1
        assert edges[0].job_name == child.job_id

    def test_ambiguous_composite_reference_does_not_stitch(self):
        one = _child_job_referencing_context("SourceFileName")
        one.job_id = "one::ChildJob"
        two = _child_job_referencing_context("SourceFileName")
        two.job_id = "two::ChildJob"
        parent = _parent_job(
            "ChildJob",
            context_params=[ComponentParam(name="SourceFileName", value="value")],
        )
        parent.trunjob_refs[0].resolution = "ambiguous"

        matrix = _matrix(parent, one, two)
        assert not [edge for edge in matrix.entries if edge.source_job_name == "ParentJob"]

    def test_context_param_not_referenced_in_child_no_edge(self):
        child = _child_job_referencing_context("SomeOtherParam")
        parent = _parent_job(
            "ChildJob",
            context_params=[ComponentParam(name="SourceFileName", value="context.ParentSourceFile")],
        )
        matrix = _matrix(parent, child)
        assert all(e.source_job_name == "" for e in matrix.entries)

    def test_child_job_not_in_scan_surfaces_as_unmapped_not_fabricated(self):
        parent = _parent_job(
            "MissingChild",
            context_params=[ComponentParam(name="SourceFileName", value="context.ParentSourceFile")],
        )
        matrix = _matrix(parent)
        assert all(e.source_job_name == "" for e in matrix.entries)
        reasons = {u["reason"] for u in matrix.unmapped_columns}
        assert "trunjob child job not found in scan for context-param stitching" in reasons


# ── (b) table identity ───────────────────────────────────────────────────


def _table_component(unique_name: str, component_type: str, connection_ref: str, table: str) -> Component:
    return Component(
        unique_name=unique_name,
        component_type=component_type,
        metadata_connection_ref=connection_ref,
        params=[ComponentParam(name="TABLE", value=f'"{table}"')],
    )


class TestTableIdentityStitching:
    def test_matching_connection_ref_and_table_creates_inferred_edge(self):
        job_a = ParsedJob(
            name="JobA", path="p",
            components=[_table_component("tMSSqlOutput_1", "tMSSqlOutput", "SharedDB", "Orders")],
        )
        job_b = ParsedJob(
            name="JobB", path="p",
            components=[_table_component("tMSSqlInput_1", "tMSSqlInput", "SharedDB", "Orders")],
        )
        matrix = _matrix(job_a, job_b)

        assert len(matrix.entries) == 1
        edge = matrix.entries[0]
        assert edge.confidence == LineageConfidence.INFERRED
        assert edge.source_job_name == "JobA"
        assert edge.job_name == "JobB"
        assert edge.source_component == "tMSSqlOutput_1"
        assert edge.target_component == "tMSSqlInput_1"
        assert edge.source_table == "Orders"
        assert edge.target_table == "Orders"
        assert edge.transformations[0].transformation_type == "table_identity"
        assert matrix.summary["cross_job_entries"] == 1

    def test_mismatched_connection_ref_no_edge(self):
        job_a = ParsedJob(
            name="JobA", path="p",
            components=[_table_component("tMSSqlOutput_1", "tMSSqlOutput", "DBOne", "Orders")],
        )
        job_b = ParsedJob(
            name="JobB", path="p",
            components=[_table_component("tMSSqlInput_1", "tMSSqlInput", "DBTwo", "Orders")],
        )
        matrix = _matrix(job_a, job_b)
        assert matrix.entries == []

    def test_empty_connection_ref_never_spuriously_matches(self):
        """Two components with equally-empty refs must NOT be treated as a
        match -- that would fabricate edges between every unresolved
        component in the project (see _table_endpoints)."""
        job_a = ParsedJob(
            name="JobA", path="p",
            components=[_table_component("tMSSqlOutput_1", "tMSSqlOutput", "", "Orders")],
        )
        job_b = ParsedJob(
            name="JobB", path="p",
            components=[_table_component("tMSSqlInput_1", "tMSSqlInput", "", "Orders")],
        )
        matrix = _matrix(job_a, job_b)
        assert matrix.entries == []

    def test_same_job_write_read_is_not_a_job_boundary_edge(self):
        job = ParsedJob(
            name="JobA", path="p",
            components=[
                _table_component("tMSSqlOutput_1", "tMSSqlOutput", "SharedDB", "Orders"),
                _table_component("tMSSqlInput_1", "tMSSqlInput", "SharedDB", "Orders"),
            ],
        )
        matrix = _matrix(job)
        assert matrix.entries == []


# ── Combined: both mechanisms distinguishable from S4 intra-job edges ────


class TestCrossJobEdgesDistinguishableFromIntraJobEdges:
    def test_inferred_confidence_only_ever_appears_on_cross_job_edges(self):
        child = _child_job_referencing_context("SourceFileName")
        parent = _parent_job(
            "ChildJob",
            context_params=[ComponentParam(name="SourceFileName", value="context.ParentSourceFile")],
        )
        job_a = ParsedJob(
            name="JobA", path="p",
            components=[_table_component("tMSSqlOutput_1", "tMSSqlOutput", "SharedDB", "Orders")],
            tmap_data=[TMapData(
                input_tables=["row1"], output_tables=["out1"],
                entries=[TMapEntry(table_name="out1", column_name="x", expression="row1.x")],
            )],
        )
        job_b = ParsedJob(
            name="JobB", path="p",
            components=[_table_component("tMSSqlInput_1", "tMSSqlInput", "SharedDB", "Orders")],
        )
        matrix = _matrix(parent, child, job_a, job_b)

        assert matrix.summary["cross_job_entries"] == 2
        inferred = [e for e in matrix.entries if e.confidence == LineageConfidence.INFERRED]
        non_inferred = [e for e in matrix.entries if e.confidence != LineageConfidence.INFERRED]
        assert len(inferred) == 2
        assert all(e.source_job_name != "" for e in inferred)
        assert all(e.source_job_name == "" for e in non_inferred)
        # The intra-job direct_map (JobA.row1.x -> out1.x) keeps its S4 tier.
        assert any(e.confidence == LineageConfidence.CERTAIN for e in non_inferred)
