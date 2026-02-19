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
