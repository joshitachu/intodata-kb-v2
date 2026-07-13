"""Tests for Talend parser against real vaxxinova-talend project."""

import pytest

from intodoc.scanner.credential_filter import CredentialFilter
from intodoc.platforms.talend.parser import TalendParser


@pytest.fixture
def parser():
    return TalendParser(CredentialFilter())


class TestTalendParserIntegration:
    """Integration tests using the real vaxxinova-talend project."""

    def test_parse_finds_jobs(self, parser, vaxxinova_talend_root):
        result = parser.parse(vaxxinova_talend_root)
        assert result.platform == "talend"
        assert len(result.jobs) > 0, "Should find at least one job"

    def test_parse_finds_connections(self, parser, vaxxinova_talend_root):
        result = parser.parse(vaxxinova_talend_root)
        assert len(result.metadata_connections) > 0, "Should find metadata connections"

    def test_parse_finds_contexts(self, parser, vaxxinova_talend_root):
        result = parser.parse(vaxxinova_talend_root)
        assert len(result.context_groups) > 0, "Should find context groups"

    def test_jobs_have_components(self, parser, vaxxinova_talend_root):
        result = parser.parse(vaxxinova_talend_root)
        jobs_with_components = [j for j in result.jobs if j.components]
        assert len(jobs_with_components) > 0, "At least some jobs should have components"

    def test_jobs_have_connections(self, parser, vaxxinova_talend_root):
        result = parser.parse(vaxxinova_talend_root)
        jobs_with_connections = [j for j in result.jobs if j.connections]
        assert len(jobs_with_connections) > 0, "At least some jobs should have connections"

    def test_no_credentials_in_output(self, parser, vaxxinova_talend_root):
        result = parser.parse(vaxxinova_talend_root)
        cred_filter = CredentialFilter()
        # Check that no raw credentials appear in job data
        for job in result.jobs:
            for comp in job.components:
                for param in comp.params:
                    assert not cred_filter.has_credentials(param.value), (
                        f"Credential found in {job.name}/{comp.unique_name}: {param.name}"
                    )

    def test_bra_vbr_raw_job_exists(self, parser, vaxxinova_talend_root):
        result = parser.parse(vaxxinova_talend_root)
        bra_jobs = [j for j in result.jobs if "BRA_VBR_RAW" in j.name]
        assert len(bra_jobs) > 0, "BRA_VBR_RAW job should exist"
        job = bra_jobs[0]
        assert job.group == "BRA"

    def test_stats_populated(self, parser, vaxxinova_talend_root):
        result = parser.parse(vaxxinova_talend_root)
        assert result.stats["total_jobs"] > 0
        assert result.stats["total_files"] > 0


# ── Synthetic project helpers ───────────────────────────────────────

_ITEM_XMLNS = (
    'xmlns:talendfile="platform:/resource/org.talend.model/model/TalendFile.xsd" '
    'xmlns:TalendMapper="http://www.talend.org/mapper" '
    'xmlns:xmi="http://www.omg.org/XMI" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
)


def _write_job(project, rel_dir, name, version, body=""):
    """Write a minimal .item + .properties pair for a job version."""
    job_dir = project / "process" / rel_dir
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / f"{name}_{version}.item").write_text(
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>'
        f'<talendfile:ProcessType {_ITEM_XMLNS} defaultContext="DEV" jobType="Standard">'
        f"{body}</talendfile:ProcessType>",
        encoding="utf-8",
    )
    (job_dir / f"{name}_{version}.properties").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<xmi:XMI xmi:version="2.0" xmlns:xmi="http://www.omg.org/XMI" '
        'xmlns:TalendProperties="http://www.talend.org/properties">'
        f'<TalendProperties:Property label="{name}" version="{version}"/>'
        "</xmi:XMI>",
        encoding="utf-8",
    )


def _synthetic_project(root):
    project = root / "SYNTH"
    (project / "process").mkdir(parents=True)
    (project / "metadata" / "connections").mkdir(parents=True)
    (project / "context").mkdir(parents=True)
    return project


_TMAP_NODE = (
    '<node componentName="tMap" componentVersion="0.1" posX="100" posY="100">'
    '<elementParameter field="TEXT" name="UNIQUE_NAME" value="tMap_1"/>'
    '<nodeData xsi:type="TalendMapper:MapperData">'
    '<inputTables name="row1"/>'
    '<outputTables name="out1">'
    '<mapperTableEntries name="wonum" expression="row1.wonum" type="id_String"/>'
    "</outputTables>"
    "</nodeData>"
    "</node>"
)


class TestVersionDedup:
    """Talend exports keep every saved item version; only the latest is current."""

    def test_only_latest_version_kept(self, parser, tmp_path):
        project = _synthetic_project(tmp_path)
        _write_job(project, "Iface", "WorkOrderJob", "0.9")
        _write_job(project, "Iface", "WorkOrderJob", "0.10")
        _write_job(project, "Iface", "WorkOrderJob", "3.2")
        result = parser.parse(tmp_path)
        wo_jobs = [j for j in result.jobs if j.name == "WorkOrderJob"]
        assert len(wo_jobs) == 1
        assert wo_jobs[0].version == "3.2"
        assert result.stats["superseded_job_versions"] == 2

    def test_numeric_version_ordering(self, parser, tmp_path):
        """'0.10' is newer than '0.9' -- versions compare numerically."""
        project = _synthetic_project(tmp_path)
        _write_job(project, "Iface", "NumJob", "0.9")
        _write_job(project, "Iface", "NumJob", "0.10")
        result = parser.parse(tmp_path)
        num_jobs = [j for j in result.jobs if j.name == "NumJob"]
        assert len(num_jobs) == 1
        assert num_jobs[0].version == "0.10"

    def test_same_name_in_different_folders_both_kept(self, parser, tmp_path):
        project = _synthetic_project(tmp_path)
        _write_job(project, "GroupA", "SharedName", "0.1")
        _write_job(project, "GroupB", "SharedName", "0.1")
        result = parser.parse(tmp_path)
        shared = [j for j in result.jobs if j.name == "SharedName"]
        assert len(shared) == 2

    def test_version_sort_key_ordering(self, parser):
        assert parser._version_sort_key("0.10") > parser._version_sort_key("0.9")
        assert parser._version_sort_key("3.3") > parser._version_sort_key("0.1")
        assert parser._version_sort_key("") == ()


class TestTMapExtraction:
    """tMap MapperData must survive credential filtering end-to-end.

    Regression: URL redaction used to rewrite xmlns declaration URIs,
    which broke the namespaced xsi:type lookup and silently dropped
    every tMap mapping from the scan.
    """

    def test_mapper_data_extracted_through_filter(self, parser, tmp_path):
        project = _synthetic_project(tmp_path)
        _write_job(project, "Iface", "MapJob", "1.0", body=_TMAP_NODE)
        result = parser.parse(tmp_path)
        map_job = next(j for j in result.jobs if j.name == "MapJob")
        assert len(map_job.tmap_data) == 1
        tmap = map_job.tmap_data[0]
        assert tmap.input_tables == ["row1"]
        assert tmap.output_tables == ["out1"]
        assert len(tmap.entries) == 1
        assert tmap.entries[0].column_name == "wonum"
        assert tmap.entries[0].expression == "row1.wonum"


def _write_context(project, rel_dir, name, version, var_xml):
    """Write a context group .item at context/<rel_dir>/ (empty rel_dir = flat)."""
    ctx_dir = project / "context" / rel_dir if rel_dir else project / "context"
    ctx_dir.mkdir(parents=True, exist_ok=True)
    (ctx_dir / f"{name}_{version}.item").write_text(
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>'
        '<xmi:XMI xmlns:xmi="http://www.omg.org/XMI" '
        'xmlns:talendfile="platform:/resource/org.talend.model/model/TalendFile.xsd" '
        'xmi:version="2.0">'
        f'<talendfile:ContextType name="DEV">{var_xml}</talendfile:ContextType>'
        "</xmi:XMI>",
        encoding="utf-8",
    )


class TestContextDiscovery:
    """Context items occur flat AND nested in per-group subfolders, and are
    version-duplicated like jobs -- both layouts must yield latest-only groups."""

    def test_nested_context_groups_found(self, parser, tmp_path):
        project = _synthetic_project(tmp_path)
        _write_context(project, "MAS", "MAS", "0.1",
                       '<contextParameter name="MAS_URL" type="id_String" value="x"/>')
        result = parser.parse(tmp_path)
        names = [g.name for g in result.context_groups]
        assert "MAS" in names

    def test_flat_context_groups_still_found(self, parser, tmp_path):
        project = _synthetic_project(tmp_path)
        _write_context(project, "", "FlatGroup", "0.2",
                       '<contextParameter name="Host" type="id_String" value="h"/>')
        result = parser.parse(tmp_path)
        assert "FlatGroup" in [g.name for g in result.context_groups]

    def test_context_versions_deduped_to_latest(self, parser, tmp_path):
        project = _synthetic_project(tmp_path)
        _write_context(project, "Grp", "Grp", "0.1",
                       '<contextParameter name="V" type="id_String" value="old"/>')
        _write_context(project, "Grp", "Grp", "0.7",
                       '<contextParameter name="V" type="id_String" value="new"/>')
        result = parser.parse(tmp_path)
        grps = [g for g in result.context_groups if g.name == "Grp"]
        assert len(grps) == 1
        assert grps[0].variables[0].values.get("DEV") == "new"

    def test_version_suffix_stripped_beyond_zero_versions(self, parser, tmp_path):
        """Group names strip any numeric version suffix, not only '_0.x'."""
        project = _synthetic_project(tmp_path)
        _write_context(project, "Big", "BigGroup", "3.4",
                       '<contextParameter name="V" type="id_String" value="v"/>')
        result = parser.parse(tmp_path)
        assert "BigGroup" in [g.name for g in result.context_groups]
