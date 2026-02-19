"""Tests for the orchestration DAG builder."""

import pytest

from intodoc.ir.dag import build_dag
from intodoc.models import ParsedJob, ProjectManifest


@pytest.fixture
def simple_manifest():
    return ProjectManifest(
        job_calls={
            "Master_Job": ["Child_A", "Child_B"],
            "Child_A": ["Grandchild_1"],
        },
        job_called_by={
            "Child_A": ["Master_Job"],
            "Child_B": ["Master_Job"],
            "Grandchild_1": ["Child_A"],
        },
    )


@pytest.fixture
def jobs():
    return [
        ParsedJob(name="Master_Job", path="Global/Master_Job.item", group="Global"),
        ParsedJob(name="Child_A", path="BRA/Child_A.item", group="BRA"),
        ParsedJob(name="Child_B", path="BRA/Child_B.item", group="BRA"),
        ParsedJob(name="Grandchild_1", path="BRA/Grandchild_1.item", group="BRA"),
        ParsedJob(name="Standalone", path="USA/Standalone.item", group="USA"),
    ]


class TestDAGBuilding:
    def test_basic_structure(self, simple_manifest, jobs):
        dag = build_dag(simple_manifest, jobs)
        assert len(dag.nodes) == 5  # 4 from manifest + 1 standalone
        assert len(dag.edges) == 3

    def test_roots(self, simple_manifest, jobs):
        dag = build_dag(simple_manifest, jobs)
        assert "Master_Job" in dag.roots
        assert "Standalone" in dag.roots
        assert "Child_A" not in dag.roots

    def test_leaves(self, simple_manifest, jobs):
        dag = build_dag(simple_manifest, jobs)
        assert "Grandchild_1" in dag.leaves
        assert "Child_B" in dag.leaves
        assert "Standalone" in dag.leaves

    def test_depth(self, simple_manifest, jobs):
        dag = build_dag(simple_manifest, jobs)
        node_map = {n.node_id: n for n in dag.nodes}
        assert node_map["Master_Job"].depth == 0
        assert node_map["Child_A"].depth == 1
        assert node_map["Child_B"].depth == 1
        assert node_map["Grandchild_1"].depth == 2
        assert dag.max_depth == 2

    def test_acyclic(self, simple_manifest, jobs):
        dag = build_dag(simple_manifest, jobs)
        assert dag.is_acyclic is True

    def test_mermaid_generated(self, simple_manifest, jobs):
        dag = build_dag(simple_manifest, jobs)
        assert "graph TD" in dag.mermaid
        assert "Master_Job" in dag.mermaid

    def test_empty_manifest(self):
        dag = build_dag(ProjectManifest(), [])
        assert len(dag.nodes) == 0
        assert len(dag.edges) == 0
        assert dag.is_acyclic is True


class TestCycleDetection:
    def test_cyclic_graph(self):
        manifest = ProjectManifest(
            job_calls={
                "A": ["B"],
                "B": ["C"],
                "C": ["A"],  # cycle
            }
        )
        dag = build_dag(manifest, [])
        assert dag.is_acyclic is False

    def test_self_cycle(self):
        manifest = ProjectManifest(
            job_calls={"A": ["A"]}
        )
        dag = build_dag(manifest, [])
        assert dag.is_acyclic is False
