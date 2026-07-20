"""Tests for `intodoc.lineage.build_lineage_matrix` -- classification, Var
chaining, row->component resolution, and credential filtering.

All secret-shaped values in fixtures are FAKE (same convention as
test_redaction_verifier.py) -- never real credentials.
"""

from intodoc.models import (
    Component,
    Connection,
    LineageConfidence,
    ParsedJob,
    ScanResult,
    TMapData,
    TMapEntry,
)
from intodoc.lineage import build_lineage_matrix
from intodoc.scanner.credential_filter import CredentialFilter


def _job(
    entries,
    var_entries=None,
    input_tables=("row1",),
    output_tables=("out1",),
    connections=None,
    components=None,
    name="J",
) -> ParsedJob:
    tmap = TMapData(
        input_tables=list(input_tables),
        output_tables=list(output_tables),
        entries=entries,
        var_entries=var_entries or [],
    )
    return ParsedJob(
        name=name,
        path="p",
        tmap_data=[tmap],
        connections=connections or [],
        components=components or [],
    )


def _matrix(job, cred_filter=None):
    return build_lineage_matrix(
        ScanResult(platform="talend", repo_root="/fake", jobs=[job]),
        cred_filter or CredentialFilter(),
    )


# ── Edge classification (direct_map / expression / constant / lookup / dynamic) ──


class TestEdgeClassification:
    def test_direct_map_is_certain(self):
        job = _job(entries=[TMapEntry(table_name="out1", column_name="x", expression="row1.x")])
        matrix = _matrix(job)
        assert len(matrix.entries) == 1
        entry = matrix.entries[0]
        assert entry.transformations[0].transformation_type == "direct_map"
        assert entry.confidence == LineageConfidence.CERTAIN
        assert entry.resolved is True
        assert entry.source_table == "row1"
        assert entry.source_column == "x"

    def test_multi_ref_expression_is_likely(self):
        job = _job(entries=[TMapEntry(
            table_name="out1", column_name="y",
            expression='row1.a + "-" + row1.b',
        )])
        matrix = _matrix(job)
        types = {t.transformation_type for e in matrix.entries for t in e.transformations}
        assert types == {"expression"}
        assert all(e.confidence == LineageConfidence.LIKELY for e in matrix.entries)
        # Fans out into one entry per distinct resolved column.
        sources = {(e.source_table, e.source_column) for e in matrix.entries}
        assert sources == {("row1", "a"), ("row1", "b")}

    def test_constant_only_expression(self):
        job = _job(entries=[TMapEntry(table_name="out1", column_name="c", expression='"a fixed value"')])
        matrix = _matrix(job)
        assert len(matrix.entries) == 1
        entry = matrix.entries[0]
        assert entry.transformations[0].transformation_type == "constant"
        assert entry.confidence == LineageConfidence.CERTAIN
        assert entry.resolved is False
        assert entry.source_table == "" and entry.source_column == ""

    def test_dynamic_only_expression_never_dropped(self):
        job = _job(entries=[TMapEntry(table_name="out1", column_name="d", expression="context.SomeSetting")])
        matrix = _matrix(job)
        assert len(matrix.entries) == 1
        entry = matrix.entries[0]
        assert entry.transformations[0].transformation_type == "dynamic"
        assert entry.confidence == LineageConfidence.DYNAMIC
        assert entry.resolved is False
        assert entry.dynamic_refs == ["context.SomeSetting"]

    def test_lookup_input_classified_distinctly(self):
        job = _job(
            entries=[TMapEntry(table_name="out1", column_name="l", expression="lkp.name")],
            input_tables=("row1", "lkp"),
        )
        matrix = _matrix(job)
        entry = matrix.entries[0]
        assert entry.transformations[0].transformation_type == "lookup"
        assert entry.source_table == "lkp"

    def test_mixed_resolved_and_dynamic_still_records_both(self):
        job = _job(entries=[TMapEntry(
            table_name="out1", column_name="m",
            expression='"ok".equalsIgnoreCase(row1.flag) ? ((String)globalMap.get("k")) : row1.other',
        )])
        matrix = _matrix(job)
        assert len(matrix.entries) == 2  # fans out over row1.flag, row1.other
        for entry in matrix.entries:
            assert entry.resolved is True
            assert entry.dynamic_refs == ["globalMap.get"]
            assert entry.confidence == LineageConfidence.LIKELY  # static signal, but noisy


# ── Var chaining (architecture doc sec 2.2, step 2) ──────────────────────


class TestVarChaining:
    def test_simple_var_passthrough_is_certain(self):
        """A clean Var passthrough end to end (both hops direct_map) is CERTAIN."""
        job = _job(
            entries=[TMapEntry(table_name="out1", column_name="Message", expression="Var.Message")],
            var_entries=[TMapEntry(table_name="Var", column_name="Message", expression="row1.msg")],
        )
        matrix = _matrix(job)
        assert len(matrix.entries) == 1
        entry = matrix.entries[0]
        assert entry.source_table == "row1"
        assert entry.source_column == "msg"
        assert entry.confidence == LineageConfidence.CERTAIN
        assert [t.transformation_type for t in entry.transformations] == ["direct_map", "direct_map"]
        # Var hop recorded first (data-flow order), final mapping last.
        assert entry.transformations[0].expression == "row1.msg"
        assert entry.transformations[1].expression == "Var.Message"

    def test_var_hop_with_multi_ref_expression_downgrades_to_likely(self):
        job = _job(
            entries=[TMapEntry(table_name="out1", column_name="Message", expression="Var.Message")],
            var_entries=[TMapEntry(
                table_name="Var", column_name="Message",
                expression='"x".equalsIgnoreCase(row1.a) ? row1.b : row1.c',
            )],
        )
        matrix = _matrix(job)
        # Ternary branches are over-approximated (architecture doc D2): the
        # condition's own reference (row1.a) counts as a source too, along
        # with both branches.
        sources = {(e.source_table, e.source_column) for e in matrix.entries}
        assert sources == {("row1", "a"), ("row1", "b"), ("row1", "c")}
        assert all(e.confidence == LineageConfidence.LIKELY for e in matrix.entries)

    def test_unresolvable_var_reference_recorded_as_dynamic(self):
        """Var.X referenced but never defined in var_entries -- never silently dropped."""
        job = _job(entries=[TMapEntry(table_name="out1", column_name="mv", expression="Var.Missing")])
        matrix = _matrix(job)
        entry = matrix.entries[0]
        assert entry.resolved is False
        assert entry.dynamic_refs == ["Var.Missing"]

    def test_cyclic_var_reference_terminates_and_is_flagged_dynamic(self):
        """Var.A -> Var.B -> Var.A must not infinite-loop; the cycle is flagged."""
        job = _job(
            entries=[TMapEntry(table_name="out1", column_name="cyc", expression="Var.A")],
            var_entries=[
                TMapEntry(table_name="Var", column_name="A", expression="Var.B"),
                TMapEntry(table_name="Var", column_name="B", expression="Var.A"),
            ],
        )
        matrix = _matrix(job)  # must return, not hang
        entry = matrix.entries[0]
        assert entry.resolved is False
        assert entry.confidence == LineageConfidence.DYNAMIC
        assert "Var.A" in entry.dynamic_refs


# ── Row -> component resolution (architecture doc sec 2.2, step 3) ──────


class TestRowComponentResolution:
    def test_resolves_upstream_and_downstream_components(self):
        job = _job(
            entries=[TMapEntry(table_name="out1", column_name="x", expression="row1.x")],
            connections=[
                Connection(name="row1", source="tMSSqlInput_1", target="tMap_1", connector_name="FLOW"),
                Connection(name="out1", source="tMap_1", target="tMSSqlOutput_1", connector_name="FLOW"),
            ],
            components=[
                Component(unique_name="tMSSqlInput_1", component_type="tMSSqlInput", metadata_connection_ref="SourceDB"),
                Component(unique_name="tMSSqlOutput_1", component_type="tMSSqlOutput", metadata_connection_ref="TargetDB"),
            ],
        )
        matrix = _matrix(job)
        entry = matrix.entries[0]
        assert entry.source_component == "tMSSqlInput_1"
        assert entry.target_component == "tMSSqlOutput_1"
        assert entry.source_system == "SourceDB"
        assert matrix.unmapped_columns == []

    def test_unwired_row_name_surfaces_as_unmapped_not_fabricated(self):
        job = _job(entries=[TMapEntry(table_name="out1", column_name="x", expression="row1.x")])
        matrix = _matrix(job)
        entry = matrix.entries[0]
        assert entry.source_component == ""
        assert entry.target_component == ""
        reasons = {u["reason"] for u in matrix.unmapped_columns}
        assert "no matching connection for input row name" in reasons
        assert "no matching connection for output row name" in reasons


# ── Credential filtering (D6) ────────────────────────────────────────────


class TestCredentialFiltering:
    def test_password_bearing_expression_is_redacted(self):
        """A tMap expression that embeds a connection string with credentials
        must never reach lineage.json unredacted."""
        expr = (
            '"jdbc:sqlserver://admin:hunter2fake@myserver.example.com:1433/mydb"'
            ".equalsIgnoreCase(row1.connStr)"
        )
        job = _job(entries=[TMapEntry(table_name="out1", column_name="c", expression=expr)])
        matrix = _matrix(job, CredentialFilter())
        dumped = matrix.model_dump_json()
        assert "hunter2fake" not in dumped
        assert "[CONNECTION_STRING_REDACTED]" in dumped

    def test_bearer_token_in_expression_is_redacted(self):
        expr = 'Bearer_Header = "Bearer abcdEFGH12345678ijklMNOPqrst"'
        job = _job(entries=[TMapEntry(table_name="out1", column_name="c", expression=expr)])
        matrix = _matrix(job, CredentialFilter())
        dumped = matrix.model_dump_json()
        assert "abcdEFGH12345678ijklMNOPqrst" not in dumped
