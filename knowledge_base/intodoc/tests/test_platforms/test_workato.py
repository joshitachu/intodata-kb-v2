"""Tests for the Workato platform plugin."""

from __future__ import annotations

from pathlib import Path

import pytest

from intodoc.models import (
    Component,
    ComponentParam,
    FileType,
    MetadataConnection,
    ParsedJob,
    ScanResult,
)
from intodoc.platforms.workato import WorkatoPlugin
from intodoc.platforms.workato.parser import WorkatoParser
from intodoc.platforms.registry import get_platform, list_platforms


FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "workato_sample"


# ── Registry ────────────────────────────────────────────────────────


def test_workato_in_registry():
    """Workato should be a registered platform."""
    assert "workato" in list_platforms()


def test_get_workato_plugin():
    """get_platform('workato') should return WorkatoPlugin."""
    plugin = get_platform("workato")
    assert isinstance(plugin, WorkatoPlugin)
    assert plugin.name == "workato"
    assert plugin.display_name == "Workato"


# ── File Classification ─────────────────────────────────────────────


class TestClassifyFile:
    @pytest.fixture
    def plugin(self):
        return WorkatoPlugin()

    @pytest.mark.parametrize("filename,expected", [
        ("sync.recipe.json", FileType.JOB),
        ("prod.connection.json", FileType.CONNECTION),
        ("mapping.lookup_table.json", FileType.SCHEMA),
        ("data.workato_db_table.json", FileType.SCHEMA),
        ("url.account_property.json", FileType.CONTEXT),
        ("batch.project_property.json", FileType.CONTEXT),
        ("model.workato_schema.json", FileType.SCHEMA),
        ("msg.workato_template.json", FileType.CONFIG),
        ("custom.custom_adapter.json", FileType.ROUTINE),
        ("events.topic.json", FileType.CONNECTION),
        ("v1.api_group.json", FileType.CONFIG),
        ("get_users.api_endpoint.json", FileType.JOB),
        ("app.lcap_app.json", FileType.JOB),
        ("home.lcap_page.json", FileType.CONFIG),
    ])
    def test_classify_workato_assets(self, plugin, filename, expected):
        """All 14 Workato asset types should be correctly classified."""
        assert plugin.classify_file(Path(filename)) == expected

    def test_classify_manifest(self, plugin):
        assert plugin.classify_file(Path("manifest.json")) == FileType.CONFIG

    def test_classify_unknown_json(self, plugin):
        """Non-Workato JSON files should fall through to Generic (CONFIG)."""
        assert plugin.classify_file(Path("random.json")) == FileType.CONFIG

    def test_classify_code_fallback(self, plugin, tmp_path):
        """Non-Workato text files should fall through to Generic classification."""
        py = tmp_path / "script.py"
        py.write_text("print('hello')\n")
        assert plugin.classify_file(py) == FileType.ROUTINE
        assert plugin.classify_file(Path("query.sql")) == FileType.SQL_PATTERN


# ── Parsing ──────────────────────────────────────────────────────────


class TestWorkatoParser:
    @pytest.fixture
    def cred_filter(self):
        from intodoc.scanner.credential_filter import CredentialFilter
        return CredentialFilter()

    @pytest.fixture
    def parser(self, cred_filter):
        return WorkatoParser(cred_filter)

    @pytest.fixture
    def scan_result(self, cred_filter) -> ScanResult:
        """Parse the workato_sample fixture."""
        if not FIXTURES_DIR.exists():
            pytest.skip("workato_sample fixture not found")
        plugin = WorkatoPlugin()
        return plugin.parse_project(FIXTURES_DIR, cred_filter)

    def test_parse_returns_scan_result(self, scan_result):
        assert isinstance(scan_result, ScanResult)
        assert scan_result.platform == "workato"

    def test_parse_finds_recipes(self, scan_result):
        """Should find all 3 recipe files."""
        recipe_jobs = [j for j in scan_result.jobs if "recipe" in j.path.lower() or ".recipe." in j.path.lower()]
        assert len(recipe_jobs) >= 3

    def test_parse_finds_connections(self, scan_result):
        """Should find all 3 connections + 1 lookup table."""
        assert len(scan_result.metadata_connections) >= 3

    def test_parse_connection_types(self, scan_result):
        """Connection types should be mapped from provider names."""
        conn_types = {c.connection_type for c in scan_result.metadata_connections}
        assert "Salesforce" in conn_types
        assert "NetSuite" in conn_types
        assert "Slack" in conn_types

    def test_parse_finds_context_groups(self, scan_result):
        """Should find account and project properties."""
        assert len(scan_result.context_groups) >= 1
        group_names = {g.name for g in scan_result.context_groups}
        assert "account_properties" in group_names or "project_properties" in group_names

    def test_parse_finds_lookup_table(self, scan_result):
        """Lookup table should be parsed as MetadataConnection."""
        lookup_conns = [
            c for c in scan_result.metadata_connections
            if c.connection_type == "LookupTable"
        ]
        assert len(lookup_conns) >= 1

    def test_manifest_has_recipe_calls(self, scan_result):
        """Manifest should track call_recipe references."""
        # The orchestrator recipe calls the other two recipes
        assert len(scan_result.manifest.job_calls) > 0

    def test_manifest_has_connection_usage(self, scan_result):
        """Manifest should track which jobs use which connections."""
        assert len(scan_result.manifest.job_connections_used) > 0

    def test_manifest_has_job_groups(self, scan_result):
        """Manifest should contain job groups."""
        assert len(scan_result.manifest.job_groups) > 0


# ── Recipe Parsing (detailed) ───────────────────────────────────────


class TestRecipeParsing:
    @pytest.fixture
    def cred_filter(self):
        from intodoc.scanner.credential_filter import CredentialFilter
        return CredentialFilter()

    @pytest.fixture
    def scan_result(self, cred_filter) -> ScanResult:
        if not FIXTURES_DIR.exists():
            pytest.skip("workato_sample fixture not found")
        plugin = WorkatoPlugin()
        return plugin.parse_project(FIXTURES_DIR, cred_filter)

    def _find_job(self, scan_result: ScanResult, name_part: str) -> ParsedJob:
        for j in scan_result.jobs:
            if name_part.lower() in j.name.lower():
                return j
        pytest.fail(f"Job containing '{name_part}' not found")

    def test_single_component_per_recipe(self, scan_result):
        """Each recipe should produce exactly one WorkatoRecipe component."""
        job = self._find_job(scan_result, "Sync Customers")
        assert len(job.components) == 1
        assert job.components[0].component_type == "WorkatoRecipe"

    def test_raw_json_in_params(self, scan_result):
        """Recipe params should contain the raw recipe JSON keys."""
        job = self._find_job(scan_result, "Sync Customers")
        param_names = {p.name for p in job.components[0].params}
        assert "name" in param_names

    def test_no_connections_or_tmap(self, scan_result):
        """Raw passthrough recipes should have no connections or tmap_data."""
        job = self._find_job(scan_result, "Sync Customers")
        assert len(job.connections) == 0
        assert len(job.tmap_data) == 0

    def test_call_recipe_refs(self, scan_result):
        """Orchestrator recipe should have TRunJobRef for called recipes."""
        job = self._find_job(scan_result, "Orchestrator")
        assert len(job.trunjob_refs) >= 2

        # Should reference the sync recipe by name (resolved from ID)
        called_names = [ref.child_job for ref in job.trunjob_refs]
        assert any("Sync" in n or "12345" in n for n in called_names)


# ── Noise Stripping ─────────────────────────────────────────────────


class TestNoiseStripping:
    @pytest.fixture
    def parser(self):
        from intodoc.scanner.credential_filter import CredentialFilter
        return WorkatoParser(CredentialFilter())

    def test_noise_keys_stripped(self, parser):
        """Noise keys should be recursively removed from dicts."""
        data = {
            "name": "test",
            "extended_input_schema": [{"type": "string"}],
            "uuid": "abc-123",
            "steps": [
                {
                    "type": "action",
                    "provider": "sf",
                    "output_schema": [{"name": "id"}],
                    "position": 1,
                    "input": {
                        "email": "test@example.com",
                        "visible_config_fields": ["email"],
                    },
                }
            ],
        }

        parser._strip_noise_keys(data)

        assert "name" in data
        assert "extended_input_schema" not in data
        assert "uuid" not in data
        step = data["steps"][0]
        assert "provider" in step
        assert "output_schema" not in step
        assert "position" not in step
        assert "email" in step["input"]
        assert "visible_config_fields" not in step["input"]

    def test_noise_keys_in_nested_dicts(self, parser):
        """Noise keys should be stripped at all nesting levels."""
        data = {
            "code": {
                "keyword": "trigger",
                "number": 0,
                "block": [
                    {"number": 1, "dynamicPickListSelection": {}, "provider": "sf"}
                ],
            }
        }
        parser._strip_noise_keys(data)
        assert "number" not in data["code"]
        assert "number" not in data["code"]["block"][0]
        assert "dynamicPickListSelection" not in data["code"]["block"][0]
        assert "provider" in data["code"]["block"][0]


# ── Call Recipe Detection ───────────────────────────────────────────


class TestCallRecipeDetection:
    @pytest.fixture
    def parser(self):
        from intodoc.scanner.credential_filter import CredentialFilter
        return WorkatoParser(CredentialFilter())

    def test_find_call_recipe_in_steps(self, parser):
        """call_recipe actions should be detected in nested JSON."""
        parser._recipe_id_index = {"999": "Target Recipe"}

        data = {
            "steps": [
                {"type": "action", "provider": "sf", "name": "create"},
                {
                    "type": "action",
                    "name": "call_recipe",
                    "input": {"recipe_id": "999"},
                },
            ]
        }

        refs = parser._find_call_recipe_refs(data)
        assert len(refs) == 1
        assert refs[0].child_job == "Target Recipe"

    def test_find_call_recipe_by_action_key(self, parser):
        """action='call_recipe' should also be detected."""
        parser._recipe_id_index = {"500": "Other Recipe"}

        data = {
            "steps": [
                {"type": "action", "action": "call_recipe", "input": {"recipe_id": "500"}},
            ]
        }

        refs = parser._find_call_recipe_refs(data)
        assert len(refs) == 1
        assert refs[0].child_job == "Other Recipe"

    def test_unresolved_recipe_id(self, parser):
        """Unknown recipe IDs should use fallback name."""
        data = {
            "steps": [
                {"type": "action", "name": "call_recipe", "input": {"recipe_id": "unknown_id"}},
            ]
        }

        refs = parser._find_call_recipe_refs(data)
        assert len(refs) == 1
        assert refs[0].child_job == "recipe_unknown_id"

    def test_no_call_recipe(self, parser):
        """Data without call_recipe should return empty list."""
        data = {"steps": [{"type": "action", "name": "create", "input": {}}]}
        refs = parser._find_call_recipe_refs(data)
        assert len(refs) == 0


# ── Connection Parsing ──────────────────────────────────────────────


class TestConnectionParsing:
    def test_parse_connection(self):
        from intodoc.scanner.credential_filter import CredentialFilter
        parser = WorkatoParser(CredentialFilter())

        conn_path = FIXTURES_DIR / "connections" / "salesforce_prod.connection.json"
        if not conn_path.exists():
            pytest.skip("fixture not found")

        conn = parser._parse_connection(conn_path)

        assert isinstance(conn, MetadataConnection)
        assert conn.name == "salesforce_prod"
        assert conn.connection_type == "Salesforce"
        assert conn.additional_params["provider"] == "salesforce"

    def test_parse_netsuite_connection(self):
        from intodoc.scanner.credential_filter import CredentialFilter
        parser = WorkatoParser(CredentialFilter())

        conn_path = FIXTURES_DIR / "connections" / "netsuite_prod.connection.json"
        if not conn_path.exists():
            pytest.skip("fixture not found")

        conn = parser._parse_connection(conn_path)

        assert conn.connection_type == "NetSuite"
        # Hostname may be redacted by credential filter
        assert conn.host in ("netsuite.example.com", "[HOSTNAME]")
        assert conn.port == "443"


# ── Distiller ────────────────────────────────────────────────────────


class TestWorkatoDistiller:
    def test_distill_is_passthrough(self):
        """Distiller should be a no-op (noise already stripped in parser)."""
        from intodoc.platforms.workato.distiller import WorkatoDistiller

        distiller = WorkatoDistiller()
        job = ParsedJob(
            name="test",
            path="test.recipe.json",
            group="root",
            components=[
                Component(
                    unique_name="test_recipe",
                    component_type="WorkatoRecipe",
                    params=[
                        ComponentParam(name="name", value="test", field_type="TEXT"),
                        ComponentParam(name="trigger", value='{"provider": "sf"}', field_type="INPUT"),
                    ],
                ),
            ],
        )

        distilled = distiller._distill_job(job)
        assert distilled is job  # Same object, no modification


# ── Unwrap Helper ────────────────────────────────────────────────────


class TestUnwrap:
    def test_unwrap_nested(self):
        """Nested format: data["connection"] = {...} -> inner dict."""
        data = {"connection": {"id": 1, "name": "sf"}}
        result = WorkatoParser._unwrap(data, "connection")
        assert result == {"id": 1, "name": "sf"}

    def test_unwrap_flat(self):
        """Flat format: no wrapper key -> pass through."""
        data = {"id": 1, "name": "sf", "provider": "salesforce"}
        result = WorkatoParser._unwrap(data, "connection")
        assert result is data  # same object

    def test_unwrap_wrong_type(self):
        """Non-dict value for key -> pass through."""
        data = {"connection": "some_string", "id": 1}
        result = WorkatoParser._unwrap(data, "connection")
        assert result is data


NEW_FORMAT_FIXTURES = Path(__file__).parent.parent / "fixtures" / "workato_new_format"


# ── Connection Scanning ────────────────────────────────────────────


class TestConnectionScanning:
    @pytest.fixture
    def parser(self):
        from intodoc.scanner.credential_filter import CredentialFilter
        p = WorkatoParser(CredentialFilter())
        p._connection_id_index = {201: "salesforce", 202: "hubspot"}
        p._connection_index = {
            "salesforce": MetadataConnection(xmi_id="201", name="salesforce", connection_type="Salesforce"),
            "hubspot": MetadataConnection(xmi_id="202", name="hubspot", connection_type="HubSpot"),
        }
        p._provider_to_conn = {"slack": "slack_prod"}
        return p

    def test_scan_connection_id(self, parser):
        """connection_id values should be resolved via _connection_id_index."""
        conns: set[str] = set()
        parser._scan_connection_refs({"connection_id": 201, "provider": "salesforce"}, conns)
        assert "salesforce" in conns

    def test_scan_provider(self, parser):
        """provider values should be resolved to connections."""
        conns: set[str] = set()
        parser._scan_connection_refs({"provider": "slack"}, conns)
        assert "slack_prod" in conns

    def test_scan_trigger_application(self, parser):
        """trigger_application should be detected."""
        conns: set[str] = set()
        parser._scan_connection_refs({"trigger_application": "salesforce"}, conns)
        assert "salesforce" in conns

    def test_scan_action_applications(self, parser):
        """action_applications list should be scanned."""
        conns: set[str] = set()
        parser._scan_connection_refs({"action_applications": ["salesforce", "hubspot"]}, conns)
        assert "salesforce" in conns
        assert "hubspot" in conns

    def test_scan_nested(self, parser):
        """Connection refs deep in nested structures should be found."""
        conns: set[str] = set()
        data = {"steps": [{"type": "action", "provider": "salesforce", "input": {}}]}
        parser._scan_connection_refs(data, conns)
        assert "salesforce" in conns


# ── New-Format Integration Test ──────────────────────────────────────


class TestNewFormatIntegration:
    @pytest.fixture
    def cred_filter(self):
        from intodoc.scanner.credential_filter import CredentialFilter
        return CredentialFilter()

    @pytest.fixture
    def scan_result(self, cred_filter) -> ScanResult:
        """Parse the workato_new_format fixture."""
        if not NEW_FORMAT_FIXTURES.exists():
            pytest.skip("workato_new_format fixture not found")
        plugin = WorkatoPlugin()
        return plugin.parse_project(NEW_FORMAT_FIXTURES, cred_filter)

    def test_parse_returns_scan_result(self, scan_result):
        assert isinstance(scan_result, ScanResult)
        assert scan_result.platform == "workato"

    def test_recipe_has_single_component(self, scan_result):
        """New-format recipe should have exactly one WorkatoRecipe component."""
        recipe_jobs = [j for j in scan_result.jobs if "sync_leads" in j.name.lower() or "sync leads" in j.name.lower()]
        assert len(recipe_jobs) >= 1
        job = recipe_jobs[0]
        assert len(job.components) == 1
        assert job.components[0].component_type == "WorkatoRecipe"

    def test_recipe_raw_json_params(self, scan_result):
        """Raw recipe JSON keys should be present in component params."""
        recipe_jobs = [j for j in scan_result.jobs if "sync_leads" in j.name.lower() or "sync leads" in j.name.lower()]
        job = recipe_jobs[0]
        param_names = {p.name for p in job.components[0].params}
        assert "name" in param_names

    def test_recipe_no_connections_or_tmap(self, scan_result):
        """Raw passthrough should have no connections or tmap_data."""
        recipe_jobs = [j for j in scan_result.jobs if "sync_leads" in j.name.lower() or "sync leads" in j.name.lower()]
        job = recipe_jobs[0]
        assert len(job.connections) == 0
        assert len(job.tmap_data) == 0

    def test_recipe_has_purpose(self, scan_result):
        """New-format recipe should have description as purpose."""
        recipe_jobs = [j for j in scan_result.jobs if "sync_leads" in j.name.lower() or "sync leads" in j.name.lower()]
        job = recipe_jobs[0]
        assert job.purpose != ""

    def test_connections_parsed(self, scan_result):
        """Wrapped connections should be correctly parsed."""
        conn_types = {c.connection_type for c in scan_result.metadata_connections}
        assert "Salesforce" in conn_types
        assert "HubSpot" in conn_types
        assert "Slack" in conn_types

    def test_lookup_table_parsed(self, scan_result):
        """Wrapped lookup table should be parsed."""
        lookup_conns = [
            c for c in scan_result.metadata_connections
            if c.connection_type == "LookupTable"
        ]
        assert len(lookup_conns) >= 1

    def test_recipe_function_parsed(self, scan_result):
        """Recipe function should be parsed as a job with single component."""
        func_jobs = [j for j in scan_result.jobs if "func_" in j.name]
        assert len(func_jobs) >= 1
        assert len(func_jobs[0].components) == 1
        assert func_jobs[0].components[0].component_type == "WorkatoRecipeFunction"

    def test_manifest_has_connections(self, scan_result):
        """Manifest should track connection usage from new-format recipes."""
        assert len(scan_result.manifest.job_connections_used) > 0

    def test_call_recipe_ref(self, scan_result):
        """New-format recipe should have call_recipe TRunJobRef."""
        recipe_jobs = [j for j in scan_result.jobs if "sync_leads" in j.name.lower() or "sync leads" in j.name.lower()]
        job = recipe_jobs[0]
        assert len(job.trunjob_refs) >= 1

    def test_context_groups_parsed(self, scan_result):
        """Wrapped account_property should be parsed as context group."""
        assert len(scan_result.context_groups) >= 1

    def test_recipe_function_classify(self):
        """recipe_function.json should be classified as JOB."""
        plugin = WorkatoPlugin()
        assert plugin.classify_file(Path("helper.recipe_function.json")) == FileType.JOB


# ── Content-Based Classification ─────────────────────────────────────

NESTED_FIXTURES = Path(__file__).parent.parent / "fixtures" / "workato_nested"


class TestClassifyJsonContent:
    """Unit tests for _classify_json_content()."""

    def test_recipe_with_code_block(self):
        data = {"id": 1, "name": "test", "code": {"keyword": "trigger", "block": []}}
        assert WorkatoParser._classify_json_content(data) == "recipe"

    def test_recipe_with_trigger(self):
        data = {"id": 1, "name": "test", "trigger": {"provider": "sf"}, "steps": []}
        assert WorkatoParser._classify_json_content(data) == "recipe"

    def test_recipe_with_steps_and_running(self):
        data = {"name": "test", "steps": [{"type": "action"}], "running": True, "version_no": 1}
        assert WorkatoParser._classify_json_content(data) == "recipe"

    def test_connection(self):
        data = {"name": "sf", "provider": "salesforce", "authorization_type": "oauth2"}
        assert WorkatoParser._classify_json_content(data) == "connection"

    def test_connection_with_base_url(self):
        data = {"name": "http_conn", "provider": "http", "base_url": "https://api.example.com"}
        assert WorkatoParser._classify_json_content(data) == "connection"

    def test_lookup_table(self):
        data = {"name": "codes", "columns": [{"name": "code", "type": "string"}], "row_count": 10}
        assert WorkatoParser._classify_json_content(data) == "lookup_table"

    def test_custom_adapter_source_code(self):
        data = {"name": "my_adapter", "source_code": "class MyAdapter; end"}
        assert WorkatoParser._classify_json_content(data) == "custom_adapter"

    def test_custom_adapter_actions_triggers(self):
        data = {"name": "my_adapter", "actions": {}, "triggers": {}}
        assert WorkatoParser._classify_json_content(data) == "custom_adapter"

    def test_api_endpoint(self):
        data = {"method": "GET", "url": "/api/users", "name": "get_users"}
        assert WorkatoParser._classify_json_content(data) == "api_endpoint"

    def test_api_endpoint_with_path(self):
        data = {"method": "POST", "path": "/api/orders"}
        assert WorkatoParser._classify_json_content(data) == "api_endpoint"

    def test_api_collection(self):
        data = {"name": "users_api", "endpoints": [{"method": "GET", "path": "/users"}]}
        assert WorkatoParser._classify_json_content(data) == "api_collection"

    def test_lcap_app(self):
        data = {"name": "my_app", "pages": [{"name": "home"}]}
        assert WorkatoParser._classify_json_content(data) == "lcap_app"

    def test_recipe_function(self):
        data = {"name": "format_date", "steps": [{"type": "action"}]}
        assert WorkatoParser._classify_json_content(data) == "recipe_function"

    def test_topic(self):
        data = {"name": "events", "schema": {"type": "object"}}
        assert WorkatoParser._classify_json_content(data) == "topic"

    def test_schema(self):
        data = {"name": "user_schema", "fields": [{"name": "email", "type": "string"}]}
        assert WorkatoParser._classify_json_content(data) == "schema"

    def test_property(self):
        data = {"name": "api_url", "value": "https://api.example.com"}
        assert WorkatoParser._classify_json_content(data) == "property"

    def test_property_list(self):
        data = [
            {"name": "api_url", "value": "https://api.example.com"},
            {"name": "batch_size", "value": "100"},
        ]
        assert WorkatoParser._classify_json_content(data) == "property_list"

    def test_manifest(self):
        data = {"export_id": "test-001", "assets": []}
        assert WorkatoParser._classify_json_content(data) == "manifest"

    def test_manifest_by_filename(self):
        data = {"some_field": "value"}
        assert WorkatoParser._classify_json_content(data, "manifest.json") == "manifest"

    def test_package(self):
        data = {"changelog": [{"version": "1.0"}]}
        assert WorkatoParser._classify_json_content(data) == "package"

    def test_wrapped_recipe(self):
        data = {"recipe": {"id": 1, "name": "test", "code": {}}}
        assert WorkatoParser._classify_json_content(data) == "recipe"

    def test_wrapped_connection(self):
        data = {"connection": {"name": "sf", "provider": "salesforce"}}
        assert WorkatoParser._classify_json_content(data) == "connection"

    def test_wrapped_lookup_table(self):
        data = {"lookup_table": {"name": "codes", "columns": []}}
        assert WorkatoParser._classify_json_content(data) == "lookup_table"

    def test_steps_array(self):
        data = [
            {"type": "action", "provider": "sf", "name": "create"},
            {"type": "action", "provider": "slack", "name": "post"},
        ]
        assert WorkatoParser._classify_json_content(data) == "steps"

    def test_empty_dict(self):
        assert WorkatoParser._classify_json_content({}) is None

    def test_empty_list(self):
        assert WorkatoParser._classify_json_content([]) is None

    def test_non_dict(self):
        assert WorkatoParser._classify_json_content("hello") is None

    # ── Format C classification tests ──

    def test_recipe_metadata_only(self):
        """Recipe with trigger_application but no trigger/code/steps should classify."""
        data = {"id": 1, "name": "test", "trigger_application": "scheduler",
                "action_applications": ["postgresql"], "running": True, "version_no": 1}
        assert WorkatoParser._classify_json_content(data) == "recipe"

    def test_recipe_with_connections_used(self):
        data = {"id": 1, "name": "test", "connections_used": ["pg", "s3"]}
        assert WorkatoParser._classify_json_content(data) == "recipe"

    def test_connection_with_application_and_auth_type(self):
        """Connection using 'application' + 'authentication_type' (Format C)."""
        data = {"name": "s3", "application": "amazon_s3", "authentication_type": "iam_role"}
        assert WorkatoParser._classify_json_content(data) == "connection"

    def test_connection_with_application_and_verified(self):
        data = {"name": "pg", "application": "postgresql", "verified": True}
        assert WorkatoParser._classify_json_content(data) == "connection"

    def test_connection_with_connection_id(self):
        data = {"name": "pg", "provider": "postgresql", "connection_id": "123"}
        assert WorkatoParser._classify_json_content(data) == "connection"

    def test_wrapped_steps_dict(self):
        """steps.json wrapped in {recipe_id, steps} is not a standalone asset.
        Classification returns None -- the parser handles it via sibling merge."""
        data = {"recipe_id": "123", "steps": [{"type": "action"}]}
        result = WorkatoParser._classify_json_content(data)
        # recipe_id alone doesn't trigger recipe classification; this is
        # an auxiliary file consumed by sibling-merge in _parse_recipe()
        assert result is None or result == "recipe_function"


class TestAssetRegistry:
    """Tests for _build_asset_registry() on both flat and nested fixtures."""

    @pytest.fixture
    def parser(self):
        from intodoc.scanner.credential_filter import CredentialFilter
        return WorkatoParser(CredentialFilter())

    def test_flat_fixture_finds_recipes(self, parser):
        """Flat fixture (workato_sample) should find recipes via content."""
        if not FIXTURES_DIR.exists():
            pytest.skip("workato_sample fixture not found")
        plugin = WorkatoPlugin()
        all_files = __import__("intodoc.scanner.walker", fromlist=["walk_repo"]).walk_repo(FIXTURES_DIR, plugin)
        registry = parser._build_asset_registry(all_files)

        assert "recipe" in registry
        assert len(registry["recipe"]) == 3

    def test_flat_fixture_finds_connections(self, parser):
        if not FIXTURES_DIR.exists():
            pytest.skip("workato_sample fixture not found")
        plugin = WorkatoPlugin()
        all_files = __import__("intodoc.scanner.walker", fromlist=["walk_repo"]).walk_repo(FIXTURES_DIR, plugin)
        registry = parser._build_asset_registry(all_files)

        assert "connection" in registry
        assert len(registry["connection"]) == 3

    def test_nested_fixture_finds_recipes(self, parser):
        """Nested fixture should find recipes via content classification."""
        if not NESTED_FIXTURES.exists():
            pytest.skip("workato_nested fixture not found")
        plugin = WorkatoPlugin()
        all_files = __import__("intodoc.scanner.walker", fromlist=["walk_repo"]).walk_repo(NESTED_FIXTURES, plugin)
        registry = parser._build_asset_registry(all_files)

        assert "recipe" in registry
        assert len(registry["recipe"]) == 3

    def test_nested_fixture_finds_connections(self, parser):
        if not NESTED_FIXTURES.exists():
            pytest.skip("workato_nested fixture not found")
        plugin = WorkatoPlugin()
        all_files = __import__("intodoc.scanner.walker", fromlist=["walk_repo"]).walk_repo(NESTED_FIXTURES, plugin)
        registry = parser._build_asset_registry(all_files)

        assert "connection" in registry
        assert len(registry["connection"]) == 3

    def test_nested_fixture_finds_lookup_table(self, parser):
        if not NESTED_FIXTURES.exists():
            pytest.skip("workato_nested fixture not found")
        plugin = WorkatoPlugin()
        all_files = __import__("intodoc.scanner.walker", fromlist=["walk_repo"]).walk_repo(NESTED_FIXTURES, plugin)
        registry = parser._build_asset_registry(all_files)

        assert "lookup_table" in registry
        assert len(registry["lookup_table"]) == 1

    def test_nested_fixture_finds_property_list(self, parser):
        if not NESTED_FIXTURES.exists():
            pytest.skip("workato_nested fixture not found")
        plugin = WorkatoPlugin()
        all_files = __import__("intodoc.scanner.walker", fromlist=["walk_repo"]).walk_repo(NESTED_FIXTURES, plugin)
        registry = parser._build_asset_registry(all_files)

        assert "property_list" in registry
        assert len(registry["property_list"]) == 1


# ── Nested Format Integration Test ──────────────────────────────────


class TestNestedFormatIntegration:
    """Full integration test of the nested export format."""

    @pytest.fixture
    def cred_filter(self):
        from intodoc.scanner.credential_filter import CredentialFilter
        return CredentialFilter()

    @pytest.fixture
    def scan_result(self, cred_filter) -> ScanResult:
        if not NESTED_FIXTURES.exists():
            pytest.skip("workato_nested fixture not found")
        plugin = WorkatoPlugin()
        return plugin.parse_project(NESTED_FIXTURES, cred_filter)

    def test_parse_returns_scan_result(self, scan_result):
        assert isinstance(scan_result, ScanResult)
        assert scan_result.platform == "workato"

    def test_finds_all_recipes(self, scan_result):
        """Should find all recipes from nested dirs, including metadata-only."""
        assert len(scan_result.jobs) >= 3
        job_names = [j.name for j in scan_result.jobs]
        assert any("Sync Orders" in n for n in job_names)
        assert any("Process Returns" in n for n in job_names)
        assert any("Nightly" in n or "nightly" in n for n in job_names)

    def test_recipes_have_single_component(self, scan_result):
        """Each recipe should have exactly one WorkatoRecipe component."""
        sync_job = next(j for j in scan_result.jobs if "Sync Orders" in j.name)
        assert len(sync_job.components) == 1
        assert sync_job.components[0].component_type == "WorkatoRecipe"

    def test_recipe_has_steps_in_params(self, scan_result):
        """Steps from sibling steps.json should be merged and appear in params."""
        sync_job = next(j for j in scan_result.jobs if "Sync Orders" in j.name)
        param_names = {p.name for p in sync_job.components[0].params}
        assert "steps" in param_names

    def test_recipe_no_tmap_or_connections(self, scan_result):
        """Raw passthrough recipes should have no tmap_data or connections."""
        sync_job = next(j for j in scan_result.jobs if "Sync Orders" in j.name)
        assert len(sync_job.tmap_data) == 0
        assert len(sync_job.connections) == 0

    def test_nightly_sync_has_single_component(self, scan_result):
        """Nightly sync recipe should have a single WorkatoRecipe component."""
        nightly = next((j for j in scan_result.jobs if "Nightly" in j.name or "nightly" in j.name), None)
        assert nightly is not None, f"Nightly recipe not found. Jobs: {[j.name for j in scan_result.jobs]}"
        assert len(nightly.components) == 1
        assert nightly.components[0].component_type == "WorkatoRecipe"

    def test_nightly_sync_has_steps_in_params(self, scan_result):
        """Nightly sync should have steps JSON in its params."""
        nightly = next((j for j in scan_result.jobs if "Nightly" in j.name or "nightly" in j.name), None)
        assert nightly is not None
        param_names = {p.name for p in nightly.components[0].params}
        assert "steps" in param_names

    def test_finds_connections(self, scan_result):
        """Should find all connections from nested dirs, including Format C."""
        conn_types = {c.connection_type for c in scan_result.metadata_connections}
        assert "Salesforce" in conn_types
        assert "PostgreSQL" in conn_types
        assert "AMAZON_S3" in conn_types or "S3" in conn_types or any("s3" in t.lower() for t in conn_types)

    def test_finds_lookup_table(self, scan_result):
        lookup_conns = [c for c in scan_result.metadata_connections if c.connection_type == "LookupTable"]
        assert len(lookup_conns) >= 1

    def test_finds_properties(self, scan_result):
        """Should find properties from the property list file."""
        assert len(scan_result.context_groups) >= 1
        # Should have all 3 properties from settings.json
        all_vars = []
        for g in scan_result.context_groups:
            all_vars.extend(g.variables)
        assert len(all_vars) >= 3

    def test_manifest_has_connections(self, scan_result):
        assert len(scan_result.manifest.job_connections_used) > 0

    def test_manifest_has_groups(self, scan_result):
        assert len(scan_result.manifest.job_groups) > 0


# ── Directory-Based Classification ──────────────────────────────────


class TestDirectoryClassification:
    """Test parent-directory fallback for file classification."""

    @pytest.fixture
    def plugin(self):
        return WorkatoPlugin()

    def test_recipe_in_recipes_dir(self, plugin):
        path = Path("recipes/sync_orders/recipe.json")
        assert plugin.classify_file(path) == FileType.JOB

    def test_connection_in_connections_dir(self, plugin):
        path = Path("connections/salesforce/connection.json")
        assert plugin.classify_file(path) == FileType.CONNECTION

    def test_lookup_in_lookup_tables_dir(self, plugin):
        path = Path("lookup_tables/codes.json")
        assert plugin.classify_file(path) == FileType.SCHEMA

    def test_property_in_properties_dir(self, plugin):
        path = Path("properties/settings.json")
        assert plugin.classify_file(path) == FileType.CONTEXT

    def test_suffixed_files_still_work(self, plugin):
        """Suffix-based classification should still take priority."""
        path = Path("some_dir/sync.recipe.json")
        assert plugin.classify_file(path) == FileType.JOB


# ── Best Practices ───────────────────────────────────────────────────


def test_best_practices_loads():
    """best_practices.yaml should load successfully."""
    plugin = WorkatoPlugin()
    bp = plugin.get_best_practices()
    assert bp["platform_name"] == "Workato"
    assert "concepts" in bp
    assert "anti_patterns" in bp
