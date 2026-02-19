"""Tests for the IR validator."""

import pytest

from intodoc.ir.models import (
    ColumnMapping,
    ConnectionDefinition,
    ConnectorType,
    NormalizationStats,
    NormalizedExpression,
    NormalizedPipeline,
    NormalizedProject,
    OrchestrationDAG,
    PipelineStep,
    StepType,
    Unparsed,
    FieldRef,
)
from intodoc.ir.validator import validate_ir
from intodoc.models import (
    MetadataConnection,
    ParsedJob,
    ProjectManifest,
    ScanResult,
)


@pytest.fixture
def matching_pair():
    """A project and scan_result that should validate cleanly."""
    project = NormalizedProject(
        pipelines=[
            NormalizedPipeline(pipeline_id="Job_A", group="BRA", steps=[
                PipelineStep(step_id="s0", step_type=StepType.SOURCE),
            ]),
            NormalizedPipeline(pipeline_id="Job_B", group="BRA", steps=[
                PipelineStep(step_id="s0", step_type=StepType.SINK),
            ]),
        ],
        connections=[
            ConnectionDefinition(connection_id="conn1", connector_type=ConnectorType.RDBMS),
        ],
        orchestration=OrchestrationDAG(is_acyclic=True),
        stats=NormalizationStats(parse_rate=98.0),
    )
    scan = ScanResult(
        platform="talend",
        repo_root="/test",
        jobs=[
            ParsedJob(name="Job_A", path="BRA/Job_A.item"),
            ParsedJob(name="Job_B", path="BRA/Job_B.item"),
        ],
        metadata_connections=[
            MetadataConnection(xmi_id="x1", name="conn1", connection_type="MSSQL"),
        ],
        manifest=ProjectManifest(),
    )
    return project, scan


class TestValidation:
    def test_valid_project(self, matching_pair):
        project, scan = matching_pair
        result = validate_ir(project, scan)
        assert result.is_valid is True
        errors = [i for i in result.issues if i.severity == "error"]
        assert len(errors) == 0

    def test_missing_pipeline(self, matching_pair):
        project, scan = matching_pair
        # Add a job that has no pipeline
        scan.jobs.append(ParsedJob(name="Job_C", path="USA/Job_C.item"))
        result = validate_ir(project, scan)
        assert any(i.category == "missing_pipeline" for i in result.issues)

    def test_missing_connection(self, matching_pair):
        project, scan = matching_pair
        # Add a connection that has no IR equivalent
        scan.metadata_connections.append(
            MetadataConnection(xmi_id="x2", name="conn2", connection_type="Oracle")
        )
        result = validate_ir(project, scan)
        assert any(i.category == "missing_connection" for i in result.issues)

    def test_low_parse_rate(self, matching_pair):
        project, scan = matching_pair
        project.stats.parse_rate = 80.0
        result = validate_ir(project, scan)
        assert any(i.category == "low_parse_rate" for i in result.issues)

    def test_cycle_detected(self, matching_pair):
        project, scan = matching_pair
        project.orchestration.is_acyclic = False
        result = validate_ir(project, scan)
        assert any(i.category == "cycle" for i in result.issues)
        assert result.is_valid is False

    def test_orphaned_step_warning(self, matching_pair):
        project, scan = matching_pair
        # Add a transform step with no connections in a multi-step pipeline
        project.pipelines[0].steps.append(
            PipelineStep(step_id="s1", step_type=StepType.TRANSFORM)
        )
        result = validate_ir(project, scan)
        assert any(i.category == "orphaned_step" for i in result.issues)
