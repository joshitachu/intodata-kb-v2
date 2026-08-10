"""EngineeringFacts extraction and artifact writing service."""

from __future__ import annotations

import json
from pathlib import Path

from mcp_server.providers.custom.engineering_facts.extractors.registry import ExtractorRegistry, default_registry
from mcp_server.providers.custom.engineering_facts.models import EngineeringFacts
from mcp_server.providers.custom.engineering_facts.writers.csv_writer import CsvFactWriter
from mcp_server.providers.custom.engineering_facts.writers.json_writer import JsonFactWriter
from mcp_server.providers.custom.engineering_facts.writers.markdown_writer import MarkdownFactWriter
from mcp_server.providers.custom.models import FactsManifest


class EngineeringFactsService:
    """Coordinates extractor selection and artifact serialization."""

    def __init__(self, registry: ExtractorRegistry | None = None) -> None:
        self.registry = registry or default_registry()
        self.json_writer = JsonFactWriter()
        self.csv_writer = CsvFactWriter()
        self.markdown_writer = MarkdownFactWriter()

    def extract_and_write(
        self,
        *,
        project_id: str,
        scan_output_dir: Path,
        facts_manifest: FactsManifest,
        staging_dir: Path,
        unit_allowlist: set[str] | frozenset[str] | None = None,
    ) -> tuple[EngineeringFacts, list[Path]]:
        """Extract engineering facts and write JSON/CSV/markdown artifacts.

        ``unit_allowlist`` (composite sub-project scoping) filters the raw scan
        jobs by canonical unit name before ANY extractor runs, so every derived
        artifact (dependency_map, sql_usage, config_runtime_map, ...) covers
        only the scoped partition -- one filter at the seam instead of per
        extractor. ``None`` keeps the full scan: behaviour unchanged.
        """
        scan = _load_scan_result(scan_output_dir)
        if unit_allowlist is not None:
            scan = {
                **scan,
                "jobs": [
                    job for job in scan.get("jobs", []) or []
                    if str(job.get("job_id") or job.get("name") or "") in unit_allowlist
                ],
            }
        platform = str(scan.get("platform") or facts_manifest.platform or "generic").lower()
        extractor = self.registry.get(platform)
        facts = extractor.extract(project_id, scan, facts_manifest)
        written: list[Path] = []
        written.extend(self.json_writer.write(facts, staging_dir))
        written.extend(self.csv_writer.write(facts, staging_dir))
        written.extend(self.markdown_writer.write(facts, staging_dir))
        return facts, sorted(set(written))


def _load_scan_result(scan_output_dir: Path) -> dict:
    mono = scan_output_dir / "scan_result.json"
    if mono.exists():
        return json.loads(mono.read_text(encoding="utf-8"))

    scan_dir = scan_output_dir / "scan"
    if not scan_dir.exists():
        raise FileNotFoundError(f"No scan result found in {scan_output_dir}")

    meta = json.loads((scan_dir / "meta.json").read_text(encoding="utf-8"))
    jobs = []
    jobs_dir = scan_dir / "jobs"
    if jobs_dir.exists():
        for group_dir in sorted(p for p in jobs_dir.iterdir() if p.is_dir()):
            for path in sorted(group_dir.glob("*.json")):
                jobs.append(json.loads(path.read_text(encoding="utf-8")))
    files = []
    files_path = scan_dir / "files_index.json"
    if files_path.exists():
        files = json.loads(files_path.read_text(encoding="utf-8"))
    return {**meta, "jobs": jobs, "files": files, "warnings": meta.get("warnings", [])}
