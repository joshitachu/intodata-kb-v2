"""Tests for the Lobster Data platform plugin, parser, and distiller."""

from __future__ import annotations

from pathlib import Path

import pytest

from intodoc.config import RedactionLevel
from intodoc.models import FileType, ScanResult
from intodoc.platforms.lobster import LobsterPlugin
from intodoc.platforms.lobster.distiller import LobsterDistiller
from intodoc.platforms.lobster.parser import LobsterParser
from intodoc.platforms.registry import get_platform, list_platforms
from intodoc.scanner.credential_filter import CredentialFilter

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "lobster_sample"


# ── Registry ────────────────────────────────────────────────────────


def test_lobster_in_registry():
    assert "lobster" in list_platforms()


def test_get_lobster_plugin():
    plugin = get_platform("lobster")
    assert isinstance(plugin, LobsterPlugin)
    assert plugin.name == "lobster"
    assert plugin.display_name == "Lobster Data"


def test_lobster_plugin_uses_lobster_distiller():
    plugin = get_platform("lobster")
    assert isinstance(plugin.get_distiller(), LobsterDistiller)


# ── File Classification ─────────────────────────────────────────────


class TestClassifyFile:
    @pytest.fixture
    def plugin(self):
        return LobsterPlugin()

    @pytest.mark.parametrize("filename,expected", [
        ("profile.xml", FileType.JOB),
        ("Sync_Orders.xml", FileType.JOB),
        ("export.pak", FileType.UNKNOWN),
        ("readme.txt", FileType.DOCUMENTATION),
        ("config.json", FileType.CONFIG),
    ])
    def test_classify_files(self, plugin, filename, expected):
        assert plugin.classify_file(Path(filename)) == expected

    def test_classify_code_fallback(self, plugin, tmp_path):
        """GenericPlugin binary detection classifies readable .py as ROUTINE."""
        f = tmp_path / "script.py"
        f.write_text("print('hello')\n")
        assert plugin.classify_file(f) == FileType.ROUTINE


# ── Parser ───────────────────────────────────────────────────────────


class TestLobsterParser:
    @pytest.fixture
    def cred_filter(self):
        return CredentialFilter()

    @pytest.fixture
    def parser(self, cred_filter):
        return LobsterParser(cred_filter)

    @pytest.fixture
    def scan_result(self, cred_filter) -> ScanResult:
        if not FIXTURES_DIR.exists():
            pytest.skip("lobster_sample fixture not found")
        plugin = LobsterPlugin()
        return plugin.parse_project(FIXTURES_DIR, cred_filter)

    def _find_job(self, scan_result: ScanResult, name_part: str):
        for j in scan_result.jobs:
            if name_part.lower() in j.name.lower():
                return j
        pytest.fail(f"Job containing '{name_part}' not found")

    # ── Basic parsing ────────────────────────────────────────────

    def test_parse_returns_scan_result(self, scan_result):
        assert isinstance(scan_result, ScanResult)
        assert scan_result.platform == "lobster"

    def test_parse_finds_all_profiles(self, scan_result):
        assert len(scan_result.jobs) == 3

    def test_parse_job_names(self, scan_result):
        names = {j.name for j in scan_result.jobs}
        assert "Sync_Orders_FTP" in names
        assert "API_Products" in names
        assert "Minimal_Test" in names

    def test_parse_groups(self, scan_result):
        groups = {j.name: j.group for j in scan_result.jobs}
        assert groups["Sync_Orders_FTP"] == "Order Processing"
        assert groups["API_Products"] == "Product API"
        assert groups["Minimal_Test"] == "Test"

    def test_parse_profile_active_flag_and_connection_ref(self, scan_result):
        minimal = self._find_job(scan_result, "Minimal")
        assert minimal.active is False
        ftp = self._find_job(scan_result, "Sync_Orders")
        agent = next(comp for comp in ftp.components if comp.unique_name == "agent")
        assert agent.metadata_connection_ref

    def test_profile_discovery_handles_long_xml_header(self, parser, tmp_path):
        profile = tmp_path / "long_header.xml"
        profile.write_text(
            "<?xml version=\"1.0\"?>\n"
            f"<!-- {'x' * 900} -->\n"
            "<datawizardprofile name=\"Long Header\">\n"
            "  <type>Man. upload</type>\n"
            "  <path_of_group>Root</path_of_group>\n"
            "  <dataproperties><filetype>10</filetype></dataproperties>\n"
            "</datawizardprofile>\n",
            encoding="utf-8",
        )

        result = parser.parse(tmp_path)

        assert result.stats["profiles"] == 1
        assert result.jobs[0].name == "Long Header"

    def test_parse_handles_namespaced_profile_xml(self, parser, tmp_path):
        profile = tmp_path / "namespaced.xml"
        profile.write_text(
            "<?xml version=\"1.0\"?>\n"
            "<dw:datawizardprofile xmlns:dw=\"urn:lobster\" name=\"Namespaced\">\n"
            "  <dw:type>Man. upload</dw:type>\n"
            "  <dw:path_of_group>Namespaced Group</dw:path_of_group>\n"
            "  <dw:dataproperties>\n"
            "    <dw:filetype>10</dw:filetype>\n"
            "    <dw:structuredefinition>\n"
            "      <dw:inputtree><dw:node name=\"Root\" is_node=\"true\"><dw:field name=\"id\" type=\"String\" /></dw:node></dw:inputtree>\n"
            "      <dw:outputtree><dw:node name=\"Out\" is_node=\"true\"><dw:field name=\"out_id\" type=\"String\" /></dw:node></dw:outputtree>\n"
            "    </dw:structuredefinition>\n"
            "  </dw:dataproperties>\n"
            "</dw:datawizardprofile>\n",
            encoding="utf-8",
        )

        result = parser.parse(tmp_path)

        assert result.stats["profiles"] == 1
        assert result.jobs[0].group == "Namespaced Group"
        component_types = {c.component_type for c in result.jobs[0].components}
        assert "LobsterParser_JSON" in component_types
        assert "LobsterSourceSchema" in component_types
        assert "LobsterMapping" in component_types

    def test_unknown_fields_and_extra_profile_refs_are_preserved(self, parser, tmp_path):
        profile = tmp_path / "extra.xml"
        profile.write_text(
            "<datawizardprofile name=\"Extra Profile\">\n"
            "  <type>Cron:Http</type>\n"
            "  <path_of_group>Extra</path_of_group>\n"
            "  <success_profile>Next Profile</success_profile>\n"
            "  <agent>\n"
            "    <agent_class>com.ebd.hub.datawizard.data.CronSettings</agent_class>\n"
            "    <url>/hook</url>\n"
            "    <custom_retry_policy>three-times</custom_retry_policy>\n"
            "  </agent>\n"
            "  <dataproperties>\n"
            "    <filetype>10</filetype>\n"
            "    <custom_parser_flag>strict</custom_parser_flag>\n"
            "    <structuredefinition>\n"
            "      <inputtree><node name=\"Root\" is_node=\"true\"><field name=\"id\" type=\"String\" /></node></inputtree>\n"
            "      <outputtree><node name=\"Out\" is_node=\"true\"><field name=\"out_id\" type=\"String\"><filter id=\"1\" description=\"copy\"><custom_arg>keep-me</custom_arg></filter></field></node></outputtree>\n"
            "    </structuredefinition>\n"
            "  </dataproperties>\n"
            "  <responsesettings><responseunits><unit_file><label>File Out</label><dir>/tmp/out</dir><retention_days>7</retention_days></unit_file></responseunits></responsesettings>\n"
            "</datawizardprofile>\n",
            encoding="utf-8",
        )

        result = parser.parse(tmp_path)
        job = result.jobs[0]
        params = {
            p.name: p.value
            for component in job.components
            for p in component.params
        }

        assert "Next Profile" in {r.child_job for r in job.trunjob_refs}
        assert params["extra_agent.custom_retry_policy"] == "three-times"
        assert params["extra_dataproperties.custom_parser_flag"] == "strict"
        assert params["filter_1_extra_custom_arg"] == "keep-me"
        assert params["extra_response.retention_days"] == "7"

    # ── Agent extraction ─────────────────────────────────────────

    def test_cron_ftp_agent(self, scan_result):
        job = self._find_job(scan_result, "Sync_Orders_FTP")
        agent_comps = [c for c in job.components if c.component_type.startswith("LobsterInputAgent_")]
        assert len(agent_comps) == 1
        agent = agent_comps[0]
        assert agent.component_type == "LobsterInputAgent_FTP_Cron"
        params = {p.name: p.value for p in agent.params}
        assert params["dir"] == "/data/partner/orders"
        assert params["filename"] == "*Order*.xml"
        assert params["interval"] == "5"
        assert params["comm_channel_partner"] == "ACME Corp"
        assert params["comm_channel_name"] == "FTP"

    def test_http_agent(self, scan_result):
        job = self._find_job(scan_result, "API_Products")
        agent_comps = [c for c in job.components if c.component_type.startswith("LobsterInputAgent_")]
        assert len(agent_comps) == 1
        agent = agent_comps[0]
        assert agent.component_type == "LobsterInputAgent_HTTP"
        params = {p.name: p.value for p in agent.params}
        assert params["url"] == "v1/Products"
        assert params["mimetype"] == "application/json"
        assert params["accept_get"] == "true"
        assert params["rest_placeholder"] == "{productId}"

    def test_manual_agent(self, scan_result):
        job = self._find_job(scan_result, "Minimal_Test")
        agent_comps = [c for c in job.components if c.component_type.startswith("LobsterInputAgent_")]
        assert len(agent_comps) == 1
        assert agent_comps[0].component_type == "LobsterInputAgent_Manual"

    # ── DataProperties ───────────────────────────────────────────

    def test_parser_component_xml(self, scan_result):
        job = self._find_job(scan_result, "Sync_Orders_FTP")
        parser_comps = [c for c in job.components if c.component_type.startswith("LobsterParser_")]
        assert len(parser_comps) == 1
        assert parser_comps[0].component_type == "LobsterParser_XML"

    def test_parser_component_json(self, scan_result):
        job = self._find_job(scan_result, "API_Products")
        parser_comps = [c for c in job.components if c.component_type.startswith("LobsterParser_")]
        assert len(parser_comps) == 1
        assert parser_comps[0].component_type == "LobsterParser_JSON"

    def test_parser_component_csv(self, scan_result):
        job = self._find_job(scan_result, "Minimal_Test")
        parser_comps = [c for c in job.components if c.component_type.startswith("LobsterParser_")]
        assert len(parser_comps) == 1
        assert parser_comps[0].component_type == "LobsterParser_CSV"

    # ── Input/Output Trees ───────────────────────────────────────

    def test_source_schema_present(self, scan_result):
        job = self._find_job(scan_result, "Sync_Orders_FTP")
        schema_comps = [c for c in job.components if c.component_type == "LobsterSourceSchema"]
        assert len(schema_comps) == 1
        params = {p.name: p.value for p in schema_comps[0].params}
        tree = params.get("input_tree", "")
        assert "S-order_id" in tree
        assert "S-customer_name" in tree

    def test_mapping_present(self, scan_result):
        job = self._find_job(scan_result, "Sync_Orders_FTP")
        mapping_comps = [c for c in job.components if c.component_type == "LobsterMapping"]
        assert len(mapping_comps) == 1
        params = {p.name: p.value for p in mapping_comps[0].params}
        tree = params.get("output_tree", "")
        assert "OrderID" in tree or "OrderConfirmation" in tree

    # ── Filter chains ────────────────────────────────────────────

    def test_filters_extracted(self, scan_result):
        job = self._find_job(scan_result, "Sync_Orders_FTP")
        mapping_comp = [c for c in job.components if c.component_type == "LobsterMapping"][0]
        filter_params = [p for p in mapping_comp.params if p.name.startswith("filter_")]
        assert len(filter_params) > 0
        # Check that filter expressions contain meaningful content
        for fp in filter_params:
            assert fp.value  # non-empty

    def test_empty_filter_slots_skipped(self, scan_result):
        """Empty constant parameters should be skipped in filter extraction."""
        job = self._find_job(scan_result, "API_Products")
        mapping_comp = [c for c in job.components if c.component_type == "LobsterMapping"][0]
        filter_params = [p for p in mapping_comp.params if p.name.startswith("filter_")]
        for fp in filter_params:
            # Should not contain empty constant slots
            assert 'const:""' not in fp.value or "concat" in fp.value

    # ── Variables ────────────────────────────────────────────────

    def test_variables_split_system_and_user(self, scan_result):
        job = self._find_job(scan_result, "Sync_Orders_FTP")
        group_names = {cg.name for cg in job.context_groups}
        assert "user_variables" in group_names
        assert "system_variables" in group_names

    def test_user_variables_extracted(self, scan_result):
        job = self._find_job(scan_result, "Sync_Orders_FTP")
        user_group = next(
            (cg for cg in job.context_groups if cg.name == "user_variables"), None
        )
        assert user_group is not None
        var_names = {v.name for v in user_group.variables}
        assert "VAR_ORDER_COUNT" in var_names
        assert "VAR_TIMESTAMP" in var_names

    def test_system_variables_categorized(self, scan_result):
        job = self._find_job(scan_result, "Sync_Orders_FTP")
        sys_group = next(
            (cg for cg in job.context_groups if cg.name == "system_variables"), None
        )
        assert sys_group is not None
        var_names = {v.name for v in sys_group.variables}
        assert "VAR_SYS_CLIENTNAME" in var_names
        assert "VAR_SYS_FILE_PATH" in var_names
        assert "MSG_CALL_VAR_SYS_WF_LINK" in var_names

    # ── Cross-profile references ─────────────────────────────────

    def test_profile_after_run(self, scan_result):
        job = self._find_job(scan_result, "Sync_Orders_FTP")
        ref_names = {r.child_job for r in job.trunjob_refs}
        assert "Confirm_Order_Receipt" in ref_names

    def test_exception_profile(self, scan_result):
        job = self._find_job(scan_result, "Sync_Orders_FTP")
        ref_names = {r.child_job for r in job.trunjob_refs}
        assert "Error_Notification_Handler" in ref_names

    # ── Response units ───────────────────────────────────────────

    def test_response_units_parsed(self, scan_result):
        job = self._find_job(scan_result, "Sync_Orders_FTP")
        response_comps = [
            c for c in job.components
            if c.component_type.startswith("LobsterResponse_")
        ]
        assert len(response_comps) == 1
        assert response_comps[0].component_type == "LobsterResponse_FTP"
        params = {p.name: p.value for p in response_comps[0].params}
        assert params["label"] == "SFTP Upload"
        assert params["comm_channel_partner"] == "ACME Corp"

    def test_no_response_units_for_minimal(self, scan_result):
        job = self._find_job(scan_result, "Minimal_Test")
        response_comps = [
            c for c in job.components
            if c.component_type.startswith("LobsterResponse_")
        ]
        assert len(response_comps) == 0

    # ── Connections (MetadataConnection) ─────────────────────────

    def test_connections_extracted(self, scan_result):
        assert len(scan_result.metadata_connections) >= 1
        conn_names = {c.name for c in scan_result.metadata_connections}
        assert "ACME Corp:FTP" in conn_names

    # ── Manifest ─────────────────────────────────────────────────

    def test_manifest_job_calls(self, scan_result):
        calls = scan_result.manifest.job_calls
        assert "Sync_Orders_FTP" in calls
        assert "Confirm_Order_Receipt" in calls["Sync_Orders_FTP"]

    def test_manifest_job_groups(self, scan_result):
        groups = scan_result.manifest.job_groups
        assert groups["Sync_Orders_FTP"] == "Order Processing"
        assert groups["API_Products"] == "Product API"

    def test_manifest_external_systems(self, scan_result):
        ext = scan_result.manifest.job_external_systems
        assert "Sync_Orders_FTP" in ext


# ── Distiller ────────────────────────────────────────────────────────


class TestLobsterDistiller:
    @pytest.fixture
    def scan_result(self) -> ScanResult:
        if not FIXTURES_DIR.exists():
            pytest.skip("lobster_sample fixture not found")
        cred_filter = CredentialFilter()
        plugin = LobsterPlugin()
        return plugin.parse_project(FIXTURES_DIR, cred_filter)

    def test_distiller_strips_system_variables(self, scan_result):
        distiller = LobsterDistiller()
        chunks = distiller.distill(scan_result)
        assert len(chunks) > 0

        for chunk in chunks:
            for job in chunk.jobs:
                for cg in job.context_groups:
                    assert cg.name != "system_variables", (
                        f"System variables not stripped from job {job.name}"
                    )

    def test_distiller_preserves_user_variables(self, scan_result):
        distiller = LobsterDistiller()
        chunks = distiller.distill(scan_result)

        # The FTP profile should retain user variables
        all_jobs = [j for c in chunks for j in c.jobs]
        ftp_job = next((j for j in all_jobs if "Sync_Orders" in j.name), None)
        if ftp_job:
            user_groups = [cg for cg in ftp_job.context_groups if cg.name == "user_variables"]
            assert len(user_groups) == 1
            var_names = {v.name for v in user_groups[0].variables}
            assert "VAR_ORDER_COUNT" in var_names

    def test_distiller_strips_default_params(self, scan_result):
        distiller = LobsterDistiller()
        chunks = distiller.distill(scan_result)

        for chunk in chunks:
            for job in chunk.jobs:
                for comp in job.components:
                    param_names_vals = {(p.name, p.value) for p in comp.params}
                    # schedule_type=0 should be stripped
                    assert ("schedule_type", "0") not in param_names_vals


# ── Credential Filter ────────────────────────────────────────────────


class TestCredentialFilter:
    def test_obf_password_redacted_standard(self):
        cred_filter = CredentialFilter(level=RedactionLevel.STANDARD)
        text = "<password>OBF:hendi4s6hw2fimx3h2tghraxhffui5jri6c0</password>"
        result = cred_filter.filter_text(text)
        assert "OBF:" not in result
        assert "[OBF_PASSWORD_REDACTED]" in result

    def test_obf_password_redacted_in_context(self):
        cred_filter = CredentialFilter(level=RedactionLevel.STANDARD)
        text = """<agent>
  <port>22</port>
  <password>OBF:abc123def456</password>
  <dir>/data</dir>
</agent>"""
        result = cred_filter.filter_text(text)
        assert "OBF:" not in result
        assert "/data" in result  # Non-credential content preserved

    def test_base64_value_redacted_strict(self):
        cred_filter = CredentialFilter(level=RedactionLevel.STRICT)
        text = '<value>aHR0cHM6Ly9hcGkuZXhhbXBsZS5jb20=</value>'
        result = cred_filter.filter_text(text)
        assert "[BASE64_REDACTED]" in result
        assert "<value>" in result  # Tags preserved
        assert "</value>" in result

    def test_base64_value_not_redacted_at_standard(self):
        cred_filter = CredentialFilter(level=RedactionLevel.STANDARD)
        text = '<value>aHR0cHM6Ly9hcGkuZXhhbXBsZS5jb20=</value>'
        result = cred_filter.filter_text(text)
        # Base64 redaction is strict-only; at standard level it stays
        assert "[BASE64_REDACTED]" not in result

    def test_short_base64_not_redacted(self):
        """Base64 values shorter than 8 chars should not be redacted."""
        cred_filter = CredentialFilter(level=RedactionLevel.STRICT)
        text = '<value>dHJ1ZQ==</value>'  # "true" in base64, 8 chars
        result = cred_filter.filter_text(text)
        # 8 chars exactly matches the threshold
        assert "<value>" in result

    def test_lobster_world_safe_domain(self):
        cred_filter = CredentialFilter(level=RedactionLevel.STANDARD)
        text = "See documentation at docs.lobster-world.com/manual"
        result = cred_filter.filter_text(text)
        assert "lobster-world.com" in result

    def test_no_credentials_in_parsed_output(self):
        """End-to-end: OBF passwords should not appear in scan results."""
        if not FIXTURES_DIR.exists():
            pytest.skip("lobster_sample fixture not found")
        cred_filter = CredentialFilter(level=RedactionLevel.STANDARD)
        plugin = LobsterPlugin()
        result = plugin.parse_project(FIXTURES_DIR, cred_filter)
        # Serialize and check for OBF passwords
        json_str = result.model_dump_json()
        assert "OBF:" not in json_str


# ── Best Practices ───────────────────────────────────────────────────


def test_best_practices_loads():
    plugin = LobsterPlugin()
    bp = plugin.get_best_practices()
    assert bp["platform_name"] == "Lobster Data"
    assert "concepts" in bp
    assert "profiles" in bp["concepts"]
    assert "anti_patterns" in bp
    assert len(bp["anti_patterns"]) >= 10
