"""Cross-platform parser-to-landscape contract tests.

These are intentionally small integration checks: platform parsers must emit
enough identity and resource evidence that the deterministic landscape pass
has a real application node and an auditable data edge, not merely populated
manifest lists.
"""

from pathlib import Path

import pytest

from intodoc.landscape import build_landscape
from intodoc.lineage import build_lineage_matrix
from intodoc.models import ProjectManifest
from intodoc.platforms.base_distiller import BaseDistiller
from intodoc.platforms.generic import GenericPlugin
from intodoc.platforms.lobster import LobsterPlugin
from intodoc.platforms.workato import WorkatoPlugin
from intodoc.scanner.credential_filter import CredentialFilter


FIXTURES = Path(__file__).parents[1] / "fixtures"


@pytest.mark.parametrize(
    ("plugin", "fixture_name"),
    [
        (GenericPlugin(), "generic_sample"),
        (WorkatoPlugin(), "workato_sample"),
        (LobsterPlugin(), "lobster_sample"),
    ],
)
def test_platform_scan_builds_an_application_and_resource_edge(plugin, fixture_name):
    credential_filter = CredentialFilter()
    scan_result = plugin.parse_project(FIXTURES / fixture_name, credential_filter)

    assert scan_result.manifest.source_roots == [{
        "id": ".",
        "path": ".",
        "platform": scan_result.platform,
        "role": "execution",
        "label": fixture_name,
    }]
    assert all(job.job_id and job.source_root == "." for job in scan_result.jobs)
    assert set(scan_result.manifest.job_source_roots) == {
        job.job_id for job in scan_result.jobs
    }

    landscape = build_landscape(
        scan_result,
        build_lineage_matrix(scan_result, credential_filter),
        credential_filter,
    )

    assert len(landscape.applications) == 1
    assert landscape.applications[0].id == "."
    assert any(edge.dependency_type == "integration" for edge in landscape.edges)
    assert all(edge.provenance for edge in landscape.edges)


def test_chunk_manifest_retains_topology_facts_for_its_artifact():
    manifest = ProjectManifest(
        source_roots=[{"id": ".", "path": ".", "platform": "generic"}],
        job_source_roots={"A": ".", "B": "."},
        artifact_kinds={"A": "entry_point", "B": "module"},
        artifact_relations=[{"source": "A", "target": "B"}],
        artifact_channels=[{"artifact": "A", "channel": "events"}],
        artifact_endpoints=[{"artifact": "A", "endpoint_type": "http_route"}],
        artifact_resources=[{"artifact": "A", "resource": "AWS"}],
    )

    filtered = BaseDistiller._filter_manifest(manifest, {"A"})

    assert filtered.source_roots == manifest.source_roots
    assert filtered.job_source_roots == {"A": "."}
    assert filtered.artifact_kinds == {"A": "entry_point"}
    assert filtered.artifact_relations == [{"source": "A", "target": "B"}]
    assert filtered.artifact_channels == [{"artifact": "A", "channel": "events"}]
    assert filtered.artifact_endpoints == [{"artifact": "A", "endpoint_type": "http_route"}]
    assert filtered.artifact_resources == [{"artifact": "A", "resource": "AWS"}]
