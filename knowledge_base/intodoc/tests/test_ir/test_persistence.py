"""Tests for IR persistence (save/load round-trip)."""

import pytest
from pathlib import Path

from intodoc.ir.models import (
    ConnectionDefinition,
    ConnectorType,
    NormalizationStats,
    NormalizedPipeline,
    NormalizedProject,
    OrchestrationDAG,
    OrchestrationNode,
    OrchestrationEdge,
    PipelineStep,
    StepType,
)
from intodoc.ir.persistence import save_ir, load_ir


@pytest.fixture
def sample_project():
    return NormalizedProject(
        pipelines=[
            NormalizedPipeline(
                pipeline_id="Test_Job_1",
                group="BRA",
                steps=[
                    PipelineStep(step_id="step_0", step_type=StepType.SOURCE, source_component_type="tMSSqlInput"),
                    PipelineStep(step_id="step_1", step_type=StepType.TRANSFORM, source_component_type="tMap"),
                ],
            ),
            NormalizedPipeline(
                pipeline_id="Test_Job_2",
                group="USA",
                steps=[
                    PipelineStep(step_id="step_0", step_type=StepType.SOURCE, source_component_type="tFileInputDelimited"),
                ],
            ),
        ],
        connections=[
            ConnectionDefinition(connection_id="conn1", connector_type=ConnectorType.RDBMS, host="server1"),
        ],
        orchestration=OrchestrationDAG(
            nodes=[
                OrchestrationNode(node_id="Test_Job_1", group="BRA"),
                OrchestrationNode(node_id="Test_Job_2", group="USA"),
            ],
            edges=[OrchestrationEdge(source="Test_Job_1", target="Test_Job_2")],
            roots=["Test_Job_1"],
            leaves=["Test_Job_2"],
            max_depth=1,
            is_acyclic=True,
        ),
        stats=NormalizationStats(
            total_pipelines=2,
            total_steps=3,
            total_expressions=10,
            expressions_parsed=9,
            expressions_unparsed=1,
            parse_rate=90.0,
            total_connections=1,
        ),
    )


class TestRoundTrip:
    def test_save_and_load_monolithic(self, sample_project, tmp_path):
        save_ir(sample_project, tmp_path)

        # Verify directory structure
        assert (tmp_path / "ir" / "project.json").exists()
        assert (tmp_path / "ir" / "meta.json").exists()
        assert (tmp_path / "ir" / "connections.json").exists()
        assert (tmp_path / "ir" / "orchestration.json").exists()
        assert (tmp_path / "ir" / "stats.json").exists()
        assert (tmp_path / "ir" / "pipelines" / "BRA" / "Test_Job_1.json").exists()
        assert (tmp_path / "ir" / "pipelines" / "USA" / "Test_Job_2.json").exists()

        # Load back
        loaded = load_ir(tmp_path)
        assert len(loaded.pipelines) == 2
        assert len(loaded.connections) == 1
        assert loaded.stats.total_pipelines == 2
        assert loaded.stats.parse_rate == 90.0

    def test_load_without_monolithic(self, sample_project, tmp_path):
        """Test fallback to segmented loading."""
        save_ir(sample_project, tmp_path)

        # Delete monolithic file
        (tmp_path / "ir" / "project.json").unlink()

        # Should still load from segmented files
        loaded = load_ir(tmp_path)
        assert len(loaded.pipelines) == 2
        assert len(loaded.connections) == 1

    def test_pipeline_data_preserved(self, sample_project, tmp_path):
        save_ir(sample_project, tmp_path)
        loaded = load_ir(tmp_path)

        p1 = next(p for p in loaded.pipelines if p.pipeline_id == "Test_Job_1")
        assert p1.group == "BRA"
        assert len(p1.steps) == 2
        assert p1.steps[0].step_type == StepType.SOURCE

    def test_orchestration_preserved(self, sample_project, tmp_path):
        save_ir(sample_project, tmp_path)
        loaded = load_ir(tmp_path)

        assert len(loaded.orchestration.nodes) == 2
        assert len(loaded.orchestration.edges) == 1
        assert loaded.orchestration.is_acyclic is True
