"""Tests for Talend ESB/Camel route classification.

Talend serializes DI Jobs and ESB Routes into the same .item format; the only
signal is c-prefixed Camel component usage. The scanner is the single source of
truth for the is_esb_route / esb_component_types fields on ParsedJob.
"""

import json

from intodoc.models import Component, ParsedJob
from intodoc.platforms.talend.parser import classify_esb_components


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
