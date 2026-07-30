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
    LineageMatrix,
    TMapData,
    TMapEntry,
    TXMLMapData,
    TXMLMapField,
    TXMLMapTree,
    TdmMap,
    TdmMapField,
    TdmMapTree,
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


# ── tXMLMap / TDM completeness (L4) ─────────────────────────────────────


def _txmlmap_job(txmap: TXMLMapData, *, name: str = "XmlJob") -> ParsedJob:
    return ParsedJob(name=name, path="p", txmlmap_data=[txmap])


class TestTXMLMapLineage:
    def test_nested_captured_input_path_resolves_without_component_guessing(self):
        txmap = TXMLMapData(
            input_trees=[TXMLMapTree(name="row1", fields=[
                TXMLMapField(name="GUID", xpath="row1/Envelope/GUID"),
            ])],
            output_trees=[TXMLMapTree(name="Output", fields=[
                TXMLMapField(name="id", xpath="Output/Body/id", expression="row1.GUID"),
            ])],
            raw_connections=["//@node.0/@inputTrees.0/@nodes.0 -> //@node.0/@outputTrees.0/@nodes.0"],
            component_name="tXMLMap_1",
        )
        matrix = _matrix(_txmlmap_job(txmap))

        assert len(matrix.entries) == 1
        entry = matrix.entries[0]
        assert (entry.source_table, entry.source_column) == ("row1", "row1/Envelope/GUID")
        assert (entry.target_table, entry.target_column) == ("Output", "Output/Body/id")
        assert entry.confidence == LineageConfidence.CERTAIN
        assert entry.transformations[0].transformation_type == "direct_map"
        assert entry.source_component == ""
        assert entry.target_component == "tXMLMap_1"
        assert matrix.summary["txmlmap_components_processed"] == 1
        assert any("positional" in item["reason"] for item in matrix.unmapped_columns)

    def test_mixed_and_unknown_input_references_remain_likely_with_dynamic_evidence(self):
        txmap = TXMLMapData(
            input_trees=[TXMLMapTree(name="row1", fields=[
                TXMLMapField(name="GUID", xpath="row1/GUID"),
            ])],
            output_trees=[TXMLMapTree(name="Output", fields=[
                TXMLMapField(name="id", xpath="Output/id", expression="row1.GUID + external.Missing"),
            ])],
        )
        matrix = _matrix(_txmlmap_job(txmap))

        entry = matrix.entries[0]
        assert entry.resolved is True
        assert entry.confidence == LineageConfidence.LIKELY
        assert entry.dynamic_refs == ["external.Missing"]
        assert any(item.get("reference") == "external.Missing" for item in matrix.unmapped_columns)

    def test_ambiguous_nested_input_reference_is_not_selected(self):
        txmap = TXMLMapData(
            input_trees=[TXMLMapTree(name="row1", fields=[
                TXMLMapField(name="ID", xpath="row1/Header/ID"),
                TXMLMapField(name="ID", xpath="row1/Body/ID"),
            ])],
            output_trees=[TXMLMapTree(name="Output", fields=[
                TXMLMapField(name="id", xpath="Output/id", expression="row1.ID"),
            ])],
        )
        matrix = _matrix(_txmlmap_job(txmap))

        entry = matrix.entries[0]
        assert entry.resolved is False
        assert entry.confidence == LineageConfidence.DYNAMIC
        assert entry.dynamic_refs == ["row1.ID"]
        assert any("ambiguous" in item["reason"] for item in matrix.unmapped_columns)

    def test_constant_and_unmapped_output_fields_are_emitted_not_dropped(self):
        txmap = TXMLMapData(output_trees=[TXMLMapTree(name="Output", fields=[
            TXMLMapField(name="constant", xpath="Output/constant", expression='"fixed"'),
            TXMLMapField(name="unmapped", xpath="Output/unmapped"),
        ])])
        matrix = _matrix(_txmlmap_job(txmap))

        assert len(matrix.entries) == 2
        constant, unmapped = matrix.entries
        assert constant.confidence == LineageConfidence.CERTAIN
        assert constant.transformations[0].transformation_type == "constant"
        assert unmapped.confidence == LineageConfidence.DYNAMIC
        assert "tXMLMap:output_field_without_expression" in unmapped.dynamic_refs

    def test_txmlmap_expression_is_filtered_again_when_written_to_lineage(self):
        secret = "abcdEFGH12345678ijklMNOPqrst"
        txmap = TXMLMapData(
            input_trees=[TXMLMapTree(name="row1", fields=[
                TXMLMapField(name="GUID", xpath="row1/GUID"),
            ])],
            output_trees=[TXMLMapTree(name="Output", fields=[
                TXMLMapField(
                    name="id", xpath="Output/id",
                    expression=f'row1.GUID + "Bearer {secret}"',
                ),
            ])],
        )
        matrix = _matrix(_txmlmap_job(txmap), CredentialFilter())

        dumped = matrix.model_dump_json()
        assert secret not in dumped
        assert "[BEARER_TOKEN_REDACTED]" in dumped


def _tdm_map(
    *,
    association: str = "directory_jobs",
    owning_artifact: str = "TdmJob",
    input_fields: list[TdmMapField] | None = None,
    output_fields: list[TdmMapField] | None = None,
) -> TdmMap:
    return TdmMap(
        name="orders-map",
        association=association,
        owning_artifact=owning_artifact,
        input_tree=TdmMapTree(
            name="In",
            fields=input_fields or [TdmMapField(name="field1", path="Order/field1")],
        ),
        output_tree=TdmMapTree(
            name="Out",
            fields=output_fields or [TdmMapField(
                name="requiredField", path="AssetCheck/requiredField", expression="in$/Order/field1",
            )],
        ),
    )


def _tdm_matrix(tdm: TdmMap) -> LineageMatrix:
    job = ParsedJob(name="TdmJob", path="p")
    return build_lineage_matrix(
        ScanResult(platform="talend", repo_root="/fake", jobs=[job], tdm_maps=[tdm]),
        CredentialFilter(),
    )


class TestTdmLineage:
    def test_exact_directory_owned_tdm_map_resolves_nested_input_path(self):
        matrix = _tdm_matrix(_tdm_map())

        assert len(matrix.entries) == 1
        entry = matrix.entries[0]
        assert (entry.source_table, entry.source_column) == ("In", "Order/field1")
        assert (entry.target_table, entry.target_column) == ("Out", "AssetCheck/requiredField")
        assert entry.confidence == LineageConfidence.CERTAIN
        assert entry.transformations[0].transformation_type == "direct_map"
        assert matrix.summary["tdm_maps_processed"] == 1
        assert matrix.summary["tdm_entries"] == 1

    def test_ambiguous_and_missing_tdm_paths_are_dynamic_and_recorded(self):
        matrix = _tdm_matrix(_tdm_map(
            input_fields=[
                TdmMapField(name="id", path="Order/id"),
                TdmMapField(name="id_duplicate", path="Order/id"),
            ],
            output_fields=[
                TdmMapField(name="ambiguous", path="Out/ambiguous", expression="in$/Order/id"),
                TdmMapField(name="missing", path="Out/missing", expression="in$/Order/notThere"),
            ],
        ))

        assert all(entry.resolved is False for entry in matrix.entries)
        assert all(entry.confidence == LineageConfidence.DYNAMIC for entry in matrix.entries)
        refs = {ref for entry in matrix.entries for ref in entry.dynamic_refs}
        assert refs == {"in$/Order/id", "in$/Order/notThere"}
        reasons = {item["reason"] for item in matrix.unmapped_columns}
        assert "TDM input path is ambiguous in the captured input tree" in reasons
        assert "TDM input path is not present in the captured input tree" in reasons

    def test_unresolved_tdm_ownership_is_reported_without_name_guessing(self):
        matrix = _tdm_matrix(_tdm_map(association="unresolved", owning_artifact=""))

        assert matrix.entries == []
        assert any("ownership is not an exact" in item["reason"] for item in matrix.unmapped_columns)


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
