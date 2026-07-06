"""Tests for talend_intelligence: ESB facts are consumed from scanner-computed
fields, not recomputed from a duplicated component set."""

import json

from mcp_server.providers.custom.talend_intelligence import (
    detect_talend_esb_patterns,
    write_talend_intelligence,
)


def _job(name, *, is_esb_route=False, esb_component_types=None, components=None):
    job = {"name": name, "components": components or []}
    if is_esb_route:
        job["is_esb_route"] = True
    if esb_component_types is not None:
        job["esb_component_types"] = esb_component_types
    return job


class TestDetectTalendEsbPatterns:
    def test_no_esb_jobs(self):
        scan = {"jobs": [_job("DI_A"), _job("DI_B")]}
        facts = detect_talend_esb_patterns(scan)
        assert facts.esb_detected is False
        assert facts.esb_components == []
        assert facts.esb_component_types == []
        assert facts.esb_job_count == 0

    def test_reads_precomputed_fields(self):
        scan = {
            "jobs": [
                _job("Route_A", is_esb_route=True, esb_component_types=["cRoute", "cREST"]),
                _job("DI_B"),
            ]
        }
        facts = detect_talend_esb_patterns(scan)
        assert facts.esb_detected is True
        assert facts.esb_components == ["Route_A"]  # holds job names
        assert facts.esb_component_types == ["cREST", "cRoute"]
        assert facts.esb_job_count == 1

    def test_aggregates_and_sorts_across_jobs(self):
        scan = {
            "jobs": [
                _job("Route_B", is_esb_route=True, esb_component_types=["cJMS"]),
                _job("Route_A", is_esb_route=True, esb_component_types=["cRoute", "cJMS"]),
            ]
        }
        facts = detect_talend_esb_patterns(scan)
        assert facts.esb_detected is True
        assert facts.esb_components == ["Route_A", "Route_B"]
        assert facts.esb_component_types == ["cJMS", "cRoute"]
        assert facts.esb_job_count == 2

    def test_does_not_recompute_from_components(self):
        # Old-cache defensive read: a job that has c-prefixed components but no
        # is_esb_route field is treated as a plain DI job (no re-derivation).
        scan = {
            "jobs": [
                _job("Legacy_Route", components=[{"type": "cRoute"}, {"type": "cREST"}]),
            ]
        }
        facts = detect_talend_esb_patterns(scan)
        assert facts.esb_detected is False
        assert facts.esb_component_types == []


class TestWriteTalendIntelligence:
    def test_writes_esb_facts_only_when_detected(self, tmp_path):
        scan = {
            "jobs": [
                _job("Route_A", is_esb_route=True, esb_component_types=["cRoute"]),
            ]
        }
        esb_facts, _ = write_talend_intelligence(scan, tmp_path)
        assert esb_facts.esb_detected is True
        facts_file = tmp_path / "_preprocess" / "talend_esb_facts.json"
        assert facts_file.exists()
        data = json.loads(facts_file.read_text(encoding="utf-8"))
        assert data["esb_detected"] is True
        assert data["esb_component_types"] == ["cRoute"]

    def test_skips_esb_facts_file_when_no_routes(self, tmp_path):
        scan = {"jobs": [_job("DI_A")]}
        esb_facts, _ = write_talend_intelligence(scan, tmp_path)
        assert esb_facts.esb_detected is False
        assert not (tmp_path / "_preprocess" / "talend_esb_facts.json").exists()
