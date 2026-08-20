"""The scanner landscape is an auditable technical fact graph only.

Business systems, aliases, payload names, purposes, and architectural roles
belong to the later AI topology task.  This regression test makes sure a
directional Talend artifact name cannot smuggle a semantic proposal back into
the deterministic scanner output.
"""

from intodoc.landscape import build_landscape
from intodoc.models import LineageMatrix, ParsedJob, ProjectManifest, ScanResult
from intodoc.scanner.credential_filter import CredentialFilter


def test_landscape_contains_only_technical_facts_for_directional_job_names():
    job = ParsedJob(
        name="Maximo2AssetCheck_SendWorkOrder",
        job_id="Maximo2AssetCheck_SendWorkOrder",
        source_root="assetcheck-root",
        path="process/Maximo2AssetCheck_SendWorkOrder",
    )
    scan = ScanResult(
        platform="talend",
        repo_root="/fake",
        jobs=[job],
        manifest=ProjectManifest(
            source_roots=[{
                "id": "assetcheck-root",
                "path": "assetcheck-root",
                "label": "AssetCheck",
                "role": "execution",
            }],
            job_source_roots={job.name: job.source_root},
        ),
    )

    landscape = build_landscape(scan, LineageMatrix(), CredentialFilter())
    payload = landscape.model_dump()

    assert payload.keys() == {
        "systems",
        "applications",
        "edges",
        "channels",
        "interfaces",
        "diagnostics",
        "unmapped",
        "summary",
    }
    assert "semantic_topology" not in payload
    assert payload["applications"][0]["label"] == "AssetCheck"
    assert "Maximo" not in str(payload)
    assert "Work Order" not in str(payload)
