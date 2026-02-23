"""Tests for the Talend-to-IR normalizer."""

import pytest

from intodoc.ir.normalizer import (
    COMPONENT_TYPE_MAP,
    normalize,
    _normalize_connections,
    _normalize_job,
)
from intodoc.ir.models import (
    ConnectorType,
    StepType,
    WriteMode,
)
from intodoc.models import (
    Component,
    ComponentParam,
    Connection,
    ContextGroup,
    ContextVariable,
    MetadataConnection,
    ParsedJob,
    ProjectManifest,
    ScanResult,
    TMapData,
    TMapEntry,
)


@pytest.fixture
def sample_metadata_connections():
    return [
        MetadataConnection(
            xmi_id="conn1",
            name="BRA_VBR_MSSQL",
            connection_type="MSSQL",
            host="server1.example.com",
            port="1433",
            database="VBR_DB",
            schema_name="dbo",
        ),
        MetadataConnection(
            xmi_id="conn2",
            name="BigQuery_DW",
            connection_type="BigQuery",
            host="",
            port="",
            database="project.dataset",
        ),
    ]


@pytest.fixture
def sample_job():
    return ParsedJob(
        name="VBR_Clean_Sales",
        path="BRA/VBR_Clean_Sales_0.1.item",
        group="BRA",
        components=[
            Component(
                unique_name="tMSSqlInput_1",
                component_type="tMSSqlInput",
                params=[
                    ComponentParam(name="QUERY", value='"SELECT * FROM sales"'),
                    ComponentParam(name="DIE_ON_ERROR", value="true"),
                ],
                metadata_connection_ref="BRA_VBR_MSSQL",
            ),
            Component(
                unique_name="tMap_1",
                component_type="tMap",
                params=[],
            ),
            Component(
                unique_name="tBigQueryOutput_1",
                component_type="tBigQueryOutput",
                params=[
                    ComponentParam(name="TABLE", value='"clean_sales_set"'),
                    ComponentParam(name="DATA_ACTION", value='"CLEAR_INSERT"'),
                ],
                metadata_connection_ref="BigQuery_DW",
            ),
        ],
        connections=[
            Connection(name="row1", source="tMSSqlInput_1", target="tMap_1"),
            Connection(name="out1", source="tMap_1", target="tBigQueryOutput_1"),
        ],
        tmap_data=[
            TMapData(
                input_tables=["row1"],
                output_tables=["out1"],
                entries=[
                    TMapEntry(table_name="out1", column_name="Entity", expression='"Vaxxinova Brazil"'),
                    TMapEntry(table_name="out1", column_name="Id", expression='row1.systemId'),
                ],
            ),
        ],
        context_groups=[
            ContextGroup(
                name="inline",
                variables=[
                    ContextVariable(name="db_host", type="String", values={"Default": "server1"}),
                ],
            ),
        ],
    )


class TestConnectionNormalization:
    def test_basic_connection(self, sample_metadata_connections):
        conns = _normalize_connections(sample_metadata_connections)
        assert len(conns) == 2

        mssql = next(c for c in conns if c.connection_id == "BRA_VBR_MSSQL")
        assert mssql.connector_type == ConnectorType.RDBMS
        assert mssql.host == "server1.example.com"
        assert mssql.port == "1433"

        bq = next(c for c in conns if c.connection_id == "BigQuery_DW")
        assert bq.connector_type == ConnectorType.CLOUD_DW

    def test_deduplication(self, sample_metadata_connections):
        # Add duplicate
        duped = sample_metadata_connections + [sample_metadata_connections[0]]
        conns = _normalize_connections(duped)
        assert len(conns) == 2


class TestJobNormalization:
    def test_basic_pipeline(self, sample_job, sample_metadata_connections):
        conn_by_name = {c.connection_id: c for c in _normalize_connections(sample_metadata_connections)}
        pipeline = _normalize_job(sample_job, conn_by_name)

        assert pipeline.pipeline_id == "VBR_Clean_Sales"
        assert pipeline.group == "BRA"
        assert len(pipeline.steps) >= 3  # source + transform + sink

    def test_source_step(self, sample_job, sample_metadata_connections):
        conn_by_name = {c.connection_id: c for c in _normalize_connections(sample_metadata_connections)}
        pipeline = _normalize_job(sample_job, conn_by_name)

        source = next(s for s in pipeline.steps if s.step_type == StepType.SOURCE)
        assert source.source_component_type == "tMSSqlInput"
        assert source.data_source is not None
        assert source.data_source.connection_ref == "BRA_VBR_MSSQL"

    def test_sink_step(self, sample_job, sample_metadata_connections):
        conn_by_name = {c.connection_id: c for c in _normalize_connections(sample_metadata_connections)}
        pipeline = _normalize_job(sample_job, conn_by_name)

        sink = next(s for s in pipeline.steps if s.step_type == StepType.SINK)
        assert sink.data_sink is not None
        assert sink.data_sink.table == "clean_sales_set"
        assert sink.data_sink.write_mode == WriteMode.TRUNCATE_INSERT

    def test_tmap_expressions_parsed(self, sample_job, sample_metadata_connections):
        conn_by_name = {c.connection_id: c for c in _normalize_connections(sample_metadata_connections)}
        pipeline = _normalize_job(sample_job, conn_by_name)

        transform = next(s for s in pipeline.steps if s.step_type == StepType.TRANSFORM)
        assert len(transform.column_mappings) >= 2

        entity_map = next(cm for cm in transform.column_mappings if cm.output_column == "Entity")
        assert entity_map.expression.raw == '"Vaxxinova Brazil"'

    def test_connections_used(self, sample_job, sample_metadata_connections):
        conn_by_name = {c.connection_id: c for c in _normalize_connections(sample_metadata_connections)}
        pipeline = _normalize_job(sample_job, conn_by_name)

        assert "BRA_VBR_MSSQL" in pipeline.connections_used
        assert "BigQuery_DW" in pipeline.connections_used

    def test_context_variables(self, sample_job, sample_metadata_connections):
        conn_by_name = {c.connection_id: c for c in _normalize_connections(sample_metadata_connections)}
        pipeline = _normalize_job(sample_job, conn_by_name)

        assert "db_host" in pipeline.context_variables


class TestComponentTypeMap:
    def test_all_source_types_have_connector(self):
        for comp_type, (step_type, conn_type) in COMPONENT_TYPE_MAP.items():
            if step_type == StepType.SOURCE:
                assert conn_type is not None, f"{comp_type} is SOURCE but has no connector type"

    def test_all_sink_types_have_connector(self):
        for comp_type, (step_type, conn_type) in COMPONENT_TYPE_MAP.items():
            if step_type == StepType.SINK:
                assert conn_type is not None, f"{comp_type} is SINK but has no connector type"


class TestFullNormalization:
    def test_minimal_scan_result(self, sample_job, sample_metadata_connections):
        scan = ScanResult(
            platform="talend",
            repo_root="/test",
            jobs=[sample_job],
            metadata_connections=sample_metadata_connections,
            manifest=ProjectManifest(),
        )
        project = normalize(scan)

        assert len(project.pipelines) == 1
        assert len(project.connections) == 2
        assert project.stats.total_pipelines == 1
        assert project.stats.total_expressions >= 2
        assert project.stats.parse_rate > 0
