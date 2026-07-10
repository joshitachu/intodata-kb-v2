"""Tests for Talend ESB/Camel route classification.

Talend serializes DI Jobs and ESB Routes into the same .item format; the only
signal is c-prefixed Camel component usage. The scanner is the single source of
truth for the is_esb_route / esb_component_types fields on ParsedJob.
"""

import json
from pathlib import Path

from intodoc.models import Component, ParsedJob
from intodoc.platforms.talend.parser import TalendParser, classify_esb_components
from intodoc.scanner.credential_filter import CredentialFilter

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "talend_sample"


class TestClassifyEsbComponents:
    def test_only_di_components_returns_empty(self):
        di_types = ["tMSSqlInput", "tMap", "tBigQueryOutput", "tRunJob", "tLogRow"]
        assert classify_esb_components(di_types) == []

    def test_single_esb_component_detected(self):
        assert classify_esb_components(["tMap", "cRoute"]) == ["cRoute"]

    def test_multiple_esb_components_sorted_and_deduped(self):
        types = ["cREST", "cJMS", "cRoute", "cRoute", "tMap", "cSplitter"]
        assert classify_esb_components(types) == ["cJMS", "cREST", "cRoute", "cSplitter"]

    def test_camel_infrastructure_components_count_as_esb(self):
        # cProcessor / cLog etc. are still Camel components -> ESB route.
        assert classify_esb_components(["cProcessor", "cLog"]) == ["cLog", "cProcessor"]

    def test_unlisted_camel_component_detected_by_prefix(self):
        # cHttp / cTimer are real Camel components not covered by any hardcoded
        # allowlist -- classification must key off the c-prefix convention itself.
        assert classify_esb_components(["cTimer", "cHttp"]) == ["cHttp", "cTimer"]


class TestParsedJobEsbFields:
    def test_defaults_are_di_job(self):
        job = ParsedJob(name="Plain_DI_Job", path="DI/Plain_DI_Job")
        assert job.is_esb_route is False
        assert job.esb_component_types == []

    def test_di_job_serializes_esb_fields_as_false(self):
        job = ParsedJob(
            name="DI_Job",
            path="DI/DI_Job",
            components=[
                Component(unique_name="in", component_type="tMSSqlInput"),
                Component(unique_name="map", component_type="tMap"),
            ],
        )
        job.esb_component_types = classify_esb_components(
            c.component_type for c in job.components
        )
        job.is_esb_route = bool(job.esb_component_types)

        data = json.loads(job.model_dump_json())
        assert data["is_esb_route"] is False
        assert data["esb_component_types"] == []

    def test_esb_route_job_serializes_with_matching_field_names(self):
        job = ParsedJob(
            name="Camel_Route",
            path="ROUTES/Camel_Route",
            components=[
                Component(unique_name="rest", component_type="cREST"),
                Component(unique_name="route", component_type="cRoute"),
                Component(unique_name="split", component_type="cSplitter"),
            ],
        )
        job.esb_component_types = classify_esb_components(
            c.component_type for c in job.components
        )
        job.is_esb_route = bool(job.esb_component_types)

        # mcp-server reads these exact JSON keys off each job dict.
        data = json.loads(job.model_dump_json())
        assert data["is_esb_route"] is True
        assert data["esb_component_types"] == ["cREST", "cRoute", "cSplitter"]


class TestTalendParserEsbIntegration:
    """Runs the real TalendParser against synthetic fixture .item files.

    The fixtures live under tests/fixtures/talend_sample/ rather than a real
    customer project. Layout mirrors a real Talend Studio export: DI Job
    Designs under process/, ESB/Camel Route Designs under a separate top-level
    routes/ directory -- both must be walked by the scanner. Webhook_Route
    only uses cTimer/cHttp, real Camel component types absent from any
    hardcoded allowlist, so this also locks in that classification works off
    the c-prefix convention rather than a list.
    """

    def setup_method(self):
        self.parser = TalendParser(CredentialFilter())

    def test_route_under_routes_dir_is_found_and_detected(self):
        result = self.parser.parse(FIXTURES_DIR)
        route = next(j for j in result.jobs if j.name == "Webhook_Route")
        assert route.group == "Service"
        assert route.is_esb_route is True
        assert route.esb_component_types == ["cHttp", "cTimer"]

    def test_plain_di_job_under_process_dir_is_not_classified_as_route(self):
        result = self.parser.parse(FIXTURES_DIR)
        di_job = next(j for j in result.jobs if j.name == "Sample_DI_Job")
        assert di_job.group == "DI"
        assert di_job.is_esb_route is False
        assert di_job.esb_component_types == []

    def test_both_process_and_routes_dirs_are_walked(self):
        result = self.parser.parse(FIXTURES_DIR)
        assert {j.name for j in result.jobs} == {"Webhook_Route", "Sample_DI_Job"}
