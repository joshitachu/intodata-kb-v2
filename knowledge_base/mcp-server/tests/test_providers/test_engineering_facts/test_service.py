from __future__ import annotations

import json

from mcp_server.providers.custom.engineering_facts.service import EngineeringFactsService
from mcp_server.providers.custom.models import FactsManifest


def test_service_writes_artifacts_from_scan_result(tmp_path):
    scan_dir = tmp_path / "scan_out"
    staging = tmp_path / "staging"
    scan_dir.mkdir()
    (scan_dir / "scan_result.json").write_text(
        json.dumps({
            "platform": "generic",
            "files": [{"relative_path": "src/app.py", "file_type": "routine"}],
            "jobs": [{"name": "app", "path": "src/app.py", "group": "src", "components": []}],
            "manifest": {},
        }),
        encoding="utf-8",
    )
    facts_manifest = FactsManifest(
        project_id="proj",
        platform="generic",
        canonical_unit_label="units",
        unit_count=1,
    )

    facts, written = EngineeringFactsService().extract_and_write(
        project_id="proj",
        scan_output_dir=scan_dir,
        facts_manifest=facts_manifest,
        staging_dir=staging,
    )

    assert facts.entities
    assert (staging / "engineering_facts.json").exists()
    assert (staging / "entity_index.json").exists()
    assert any(path.name == "entity_index.csv" for path in written)
