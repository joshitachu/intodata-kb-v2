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
    TMapEntry,
    TRunJobRef,
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

    def test_classify_code_fallback(self, plugin):
        """Non-Workato files should fall through to Generic classification."""
        assert plugin.classify_file(Path("script.py")) == FileType.ROUTINE
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

    def test_sync_recipe_components(self, scan_result):
        """Sync recipe should have trigger + actions + conditions."""
        job = self._find_job(scan_result, "Sync Customers")

        comp_types = [c.component_type for c in job.components]

        # Should have a trigger
        assert any("Trigger" in t for t in comp_types)

        # Should have actions
        assert any("Action" in t for t in comp_types)

        # Should have conditionals
        assert any("Condition" in t for t in comp_types)

    def test_sync_recipe_connections(self, scan_result):
        """Sync recipe should have flow connections between steps."""
        job = self._find_job(scan_result, "Sync Customers")
        assert len(job.connections) > 0

        # Should have FLOW connections
        flow_conns = [c for c in job.connections if c.connector_name == "FLOW"]
        assert len(flow_conns) > 0

    def test_sync_recipe_datapills(self, scan_result):
        """Sync recipe should have datapill references extracted as TMapEntry."""
        job = self._find_job(scan_result, "Sync Customers")
        assert len(job.tmap_data) > 0
        assert len(job.tmap_data[0].entries) > 0

        # Should reference salesforce fields
        expressions = [e.expression for e in job.tmap_data[0].entries]
        assert any("salesforce" in expr for expr in expressions)

    def test_error_handling_recipe(self, scan_result):
        """Error handling recipe should have monitor/catch/stop components."""
        job = self._find_job(scan_result, "Error Handling")

        comp_types = [c.component_type for c in job.components]
        assert "WorkatoMonitor" in comp_types
        assert "WorkatoCatch" in comp_types
        assert "WorkatoStop" in comp_types

    def test_error_handling_connections(self, scan_result):
        """Error handling recipe should have ON_ERROR connection."""
        job = self._find_job(scan_result, "Error Handling")

        conn_types = [c.connector_name for c in job.connections]
        assert "ON_ERROR" in conn_types
        assert "STOP" in conn_types

    def test_call_recipe_refs(self, scan_result):
        """Orchestrator recipe should have TRunJobRef for called recipes."""
        job = self._find_job(scan_result, "Orchestrator")
        assert len(job.trunjob_refs) >= 2

        # Should reference the sync recipe by name (resolved from ID)
        called_names = [ref.child_job for ref in job.trunjob_refs]
        assert any("Sync" in n or "12345" in n for n in called_names)

    def test_foreach_loop(self, scan_result):
        """Orchestrator recipe should have a foreach loop component."""
        job = self._find_job(scan_result, "Orchestrator")

        comp_types = [c.component_type for c in job.components]
        assert any("Loop_foreach" in t for t in comp_types)


# ── Datapill Extraction ─────────────────────────────────────────────


class TestDatapillExtraction:
    def test_extract_datapill_refs(self):
        from intodoc.scanner.credential_filter import CredentialFilter
        parser = WorkatoParser(CredentialFilter())

        input_data = {
            "company_name": "#{_('data.salesforce.Account.Name')}",
            "email": "#{_('data.salesforce.Account.Email')}",
            "static_field": "hello world",
            "complex": "Name: #{_('data.sf.Name')} - #{_('data.sf.Id')}",
        }

        entries = parser._extract_datapill_refs(input_data, "output")

        # Should have entries for all fields
        assert len(entries) >= 4

        # Check datapill entries
        datapill_entries = [e for e in entries if "#{" in e.expression]
        assert len(datapill_entries) >= 3  # company_name, email, complex

        # Check static mapping
        static_entries = [e for e in entries if e.expression.startswith('"')]
        assert len(static_entries) >= 1

    def test_skip_non_string_values(self):
        from intodoc.scanner.credential_filter import CredentialFilter
        parser = WorkatoParser(CredentialFilter())

        input_data = {
            "count": 42,
            "active": True,
            "name": "#{_('data.sf.Name')}",
        }

        entries = parser._extract_datapill_refs(input_data, "output")
        # Only the string field should produce entries
        assert len(entries) == 1
        assert entries[0].column_name == "name"


# ── Block Walker ────────────────────────────────────────────────────


class TestBlockWalker:
    @pytest.fixture
    def parser(self):
        from intodoc.scanner.credential_filter import CredentialFilter
        return WorkatoParser(CredentialFilter())

    def test_simple_trigger_action(self, parser):
        """Simple trigger -> action should create 2 components + 1 connection."""
        code = {
            "number": 0,
            "provider": "salesforce",
            "name": "new_record",
            "keyword": "trigger",
            "as": "sf_trigger",
            "input": {"sobject": "Account"},
            "block": [
                {
                    "number": 1,
                    "provider": "netsuite",
                    "name": "create_record",
                    "keyword": "action",
                    "as": "ns_create",
                    "input": {"entity_type": "customer"},
                    "block": [],
                }
            ],
        }

        comps, conns = [], []
        tmap, trefs = [], []
        parser._walk_code_blocks(code, 0, comps, conns, tmap, trefs, "")

        assert len(comps) == 2
        assert comps[0].component_type == "WorkatoTrigger_salesforce_new_record"
        assert comps[1].component_type == "WorkatoAction_netsuite_create_record"
        assert len(conns) == 1
        assert conns[0].connector_name == "FLOW"

    def test_conditional_blocks(self, parser):
        """if/else should create condition components."""
        code = {
            "number": 0,
            "provider": "clock",
            "name": "scheduled",
            "keyword": "trigger",
            "as": "trigger",
            "input": {},
            "block": [
                {
                    "number": 1,
                    "keyword": "if",
                    "input": {"conditions": [{"operand": "present", "lhs": "x"}]},
                    "block": [],
                },
                {
                    "number": 2,
                    "keyword": "else",
                    "block": [],
                },
            ],
        }

        comps, conns = [], []
        parser._walk_code_blocks(code, 0, comps, conns, [], [], "")

        comp_types = [c.component_type for c in comps]
        assert "WorkatoCondition_if" in comp_types
        assert "WorkatoCondition_else" in comp_types

    def test_monitor_catch(self, parser):
        """monitor/catch should create appropriate components and connections."""
        code = {
            "number": 0,
            "provider": "clock",
            "name": "scheduled",
            "keyword": "trigger",
            "as": "trigger",
            "input": {},
            "block": [
                {
                    "number": 1,
                    "keyword": "monitor",
                    "block": [
                        {
                            "number": 2,
                            "provider": "http",
                            "name": "send_request",
                            "keyword": "action",
                            "as": "http_call",
                            "input": {"url": "https://api.test.com"},
                            "block": [],
                        }
                    ],
                },
                {
                    "number": 3,
                    "keyword": "catch",
                    "block": [],
                },
            ],
        }

        comps, conns = [], []
        parser._walk_code_blocks(code, 0, comps, conns, [], [], "")

        comp_types = [c.component_type for c in comps]
        assert "WorkatoMonitor" in comp_types
        assert "WorkatoCatch" in comp_types

        # Check ON_ERROR connection
        conn_types = [c.connector_name for c in conns]
        assert "ON_ERROR" in conn_types

    def test_foreach_loop(self, parser):
        """foreach should create a loop component."""
        code = {
            "number": 0,
            "provider": "clock",
            "name": "scheduled",
            "keyword": "trigger",
            "as": "trigger",
            "input": {},
            "block": [
                {
                    "number": 1,
                    "keyword": "foreach",
                    "input": {"list": "#{_('data.trigger.items')}"},
                    "block": [],
                },
            ],
        }

        comps, conns = [], []
        parser._walk_code_blocks(code, 0, comps, conns, [], [], "")

        assert any("Loop_foreach" in c.component_type for c in comps)

    def test_stop_action(self, parser):
        """stop should create a stop component."""
        code = {
            "number": 0,
            "provider": "clock",
            "name": "scheduled",
            "keyword": "trigger",
            "as": "trigger",
            "input": {},
            "block": [
                {
                    "number": 1,
                    "keyword": "stop",
                    "input": {"status": "failed", "message": "Error"},
                },
            ],
        }

        comps, conns = [], []
        parser._walk_code_blocks(code, 0, comps, conns, [], [], "")

        assert any("WorkatoStop" in c.component_type for c in comps)

    def test_call_recipe_detection(self, parser):
        """call_recipe action should generate TRunJobRef."""
        parser._recipe_id_index = {"999": "Target Recipe"}

        code = {
            "number": 0,
            "provider": "clock",
            "name": "scheduled",
            "keyword": "trigger",
            "as": "trigger",
            "input": {},
            "block": [
                {
                    "number": 1,
                    "provider": "workato",
                    "name": "call_recipe",
                    "keyword": "action",
                    "as": "call_target",
                    "input": {"recipe_id": "999"},
                    "block": [],
                },
            ],
        }

        comps, conns, tmap, trefs = [], [], [], []
        parser._walk_code_blocks(code, 0, comps, conns, tmap, trefs, "")

        assert len(trefs) == 1
        assert trefs[0].child_job == "Target Recipe"

    def test_nested_depth(self, parser):
        """Deeply nested blocks should be correctly processed."""
        code = {
            "number": 0,
            "provider": "clock",
            "name": "scheduled",
            "keyword": "trigger",
            "as": "trigger",
            "input": {},
            "block": [
                {
                    "number": 1,
                    "keyword": "foreach",
                    "input": {"list": "items"},
                    "block": [
                        {
                            "number": 2,
                            "keyword": "if",
                            "input": {"conditions": []},
                            "block": [
                                {
                                    "number": 3,
                                    "provider": "http",
                                    "name": "send_request",
                                    "keyword": "action",
                                    "as": "nested_http",
                                    "input": {},
                                    "block": [],
                                }
                            ],
                        }
                    ],
                },
            ],
        }

        comps, conns = [], []
        parser._walk_code_blocks(code, 0, comps, conns, [], [], "")

        # Should have: trigger + foreach + if + action = 4 components
        assert len(comps) == 4


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
    def test_distill_strips_noise(self):
        from intodoc.platforms.workato.distiller import WorkatoDistiller

        distiller = WorkatoDistiller()
        job = ParsedJob(
            name="test",
            path="test.recipe.json",
            group="root",
            components=[
                Component(
                    unique_name="action_1",
                    component_type="WorkatoAction_sf_create",
                    params=[
                        ComponentParam(name="provider", value="salesforce", field_type="TEXT"),
                        ComponentParam(name="uuid", value="abc-123", field_type="TEXT"),
                        ComponentParam(name="extended_input_schema", value="[{...}]", field_type="TEXT"),
                        ComponentParam(name="company_name", value="Test Corp", field_type="INPUT"),
                    ],
                ),
            ],
        )

        distilled = distiller._distill_job(job)

        param_names = [p.name for p in distilled.components[0].params]
        assert "provider" in param_names
        assert "company_name" in param_names
        assert "uuid" not in param_names
        assert "extended_input_schema" not in param_names

    def test_distill_keeps_empty_value_stripped(self):
        from intodoc.platforms.workato.distiller import WorkatoDistiller

        distiller = WorkatoDistiller()
        job = ParsedJob(
            name="test",
            path="test",
            group="root",
            components=[
                Component(
                    unique_name="a",
                    component_type="WorkatoAction_x_y",
                    params=[
                        ComponentParam(name="active", value="", field_type="TEXT"),
                        ComponentParam(name="name", value="  ", field_type="TEXT"),
                        ComponentParam(name="real", value="value", field_type="TEXT"),
                    ],
                ),
            ],
        )

        distilled = distiller._distill_job(job)
        param_names = [p.name for p in distilled.components[0].params]
        assert "real" in param_names
        assert "active" not in param_names
        assert "name" not in param_names


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


# ── New-Format Step Walker ───────────────────────────────────────────

NEW_FORMAT_FIXTURES = Path(__file__).parent.parent / "fixtures" / "workato_new_format"


class TestStepsWalker:
    @pytest.fixture
    def parser(self):
        from intodoc.scanner.credential_filter import CredentialFilter
        return WorkatoParser(CredentialFilter())

    def test_action_step(self, parser):
        """Action step should create a Component with connection ref."""
        parser._connection_id_index = {202: "hubspot"}
        parser._connection_index = {"hubspot": MetadataConnection(
            xmi_id="202", name="hubspot", connection_type="HubSpot",
        )}

        steps = [
            {
                "type": "action",
                "provider": "hubspot",
                "name": "create_contact",
                "label": "Create HubSpot Contact",
                "connection_id": 202,
                "input": {"email": "{{trigger.Email}}"},
            }
        ]

        comps, conns, tmap, trefs = [], [], [], []
        parser._walk_steps(steps, 1, comps, conns, tmap, trefs, "")

        assert len(comps) == 1
        assert comps[0].component_type == "WorkatoAction_hubspot_create_contact"
        assert comps[0].metadata_connection_ref == "hubspot"
        # Datapill should be extracted
        assert len(tmap) >= 1

    def test_if_branches(self, parser):
        """If step should create condition + recurse into true/false."""
        steps = [
            {
                "type": "if",
                "condition": {"field": "{{trigger.Country}}", "operator": "is_present"},
                "steps_on_true": [
                    {"type": "action", "provider": "hubspot", "name": "update", "label": "Update True", "input": {}},
                ],
                "steps_on_false": [
                    {"type": "action", "provider": "slack", "name": "post", "label": "Notify False", "input": {}},
                ],
            }
        ]

        comps, conns, tmap, trefs = [], [], [], []
        parser._walk_steps(steps, 1, comps, conns, tmap, trefs, "")

        comp_types = [c.component_type for c in comps]
        assert "WorkatoCondition_if" in comp_types
        assert "WorkatoAction_hubspot_update" in comp_types
        assert "WorkatoAction_slack_post" in comp_types
        assert len(comps) == 3  # condition + true action + false action

    def test_foreach_loop(self, parser):
        """Foreach step should create loop + recurse into body."""
        steps = [
            {
                "type": "foreach",
                "list": "{{trigger.Tags}}",
                "steps": [
                    {"type": "action", "provider": "hubspot", "name": "add_tag", "label": "Add Tag", "input": {}},
                ],
            }
        ]

        comps, conns, tmap, trefs = [], [], [], []
        parser._walk_steps(steps, 1, comps, conns, tmap, trefs, "")

        comp_types = [c.component_type for c in comps]
        assert any("Loop_foreach" in t for t in comp_types)
        assert "WorkatoAction_hubspot_add_tag" in comp_types

    def test_monitor_catch_stop(self, parser):
        """Monitor/catch/stop steps should create appropriate components."""
        steps = [
            {
                "type": "monitor",
                "steps": [
                    {"type": "action", "provider": "http", "name": "get", "label": "HTTP Get", "input": {}},
                ],
            },
            {
                "type": "catch",
                "steps": [
                    {"type": "action", "provider": "slack", "name": "post", "label": "Notify", "input": {}},
                    {"type": "stop", "status": "failed", "message": "Error"},
                ],
            },
        ]

        comps, conns, tmap, trefs = [], [], [], []
        parser._walk_steps(steps, 1, comps, conns, tmap, trefs, "")

        comp_types = [c.component_type for c in comps]
        assert "WorkatoMonitor" in comp_types
        assert "WorkatoCatch" in comp_types
        assert "WorkatoStop" in comp_types

        conn_types = [c.connector_name for c in conns]
        assert "ON_ERROR" in conn_types
        assert "STOP" in conn_types

    def test_call_recipe_detection(self, parser):
        """call_recipe action in new format should generate TRunJobRef."""
        parser._recipe_id_index = {"50002": "Enrichment Recipe"}

        steps = [
            {
                "type": "action",
                "provider": "workato",
                "name": "call_recipe",
                "label": "Call Enrichment",
                "input": {"recipe_id": "50002"},
            }
        ]

        comps, conns, tmap, trefs = [], [], [], []
        parser._walk_steps(steps, 1, comps, conns, tmap, trefs, "")

        assert len(trefs) == 1
        assert trefs[0].child_job == "Enrichment Recipe"

    def test_lookup_table_detection(self, parser):
        """lookup_table_lookup action should create TMapEntry."""
        steps = [
            {
                "type": "action",
                "provider": "workato",
                "name": "lookup_table_lookup",
                "label": "Lookup Country",
                "input": {
                    "table_name": "country_codes",
                    "lookup_column": "country_name",
                    "lookup_value": "{{trigger.Country}}",
                },
            }
        ]

        comps, conns, tmap, trefs = [], [], [], []
        parser._walk_steps(steps, 1, comps, conns, tmap, trefs, "")

        lookup_entries = [e for e in tmap if e.table_name == "lookup_country_codes"]
        assert len(lookup_entries) >= 1
        assert lookup_entries[0].column_name == "country_name"


# ── New Datapill Extraction ──────────────────────────────────────────


class TestNewDatapillExtraction:
    def test_handlebars_syntax(self):
        """{{...}} datapill syntax should be extracted."""
        from intodoc.scanner.credential_filter import CredentialFilter
        parser = WorkatoParser(CredentialFilter())

        input_data = {
            "email": "{{trigger.Email}}",
            "name": "{{trigger.FirstName}} {{trigger.LastName}}",
            "static": "hello world",
        }

        entries = parser._extract_datapill_refs(input_data, "output")

        datapill_entries = [e for e in entries if "{{" in e.expression]
        assert len(datapill_entries) >= 2  # email + name

    def test_mixed_formats(self):
        """Both old and new datapill formats should be extracted."""
        from intodoc.scanner.credential_filter import CredentialFilter
        parser = WorkatoParser(CredentialFilter())

        input_data = {
            "old_ref": "#{_('data.salesforce.Account.Name')}",
            "new_ref": "{{trigger.Email}}",
            "static": "plain text",
        }

        entries = parser._extract_datapill_refs(input_data, "output")
        # All 3 should produce entries (2 datapill + 1 static)
        assert len(entries) == 3


# ── Connection ID Resolution ────────────────────────────────────────


class TestConnectionIdResolution:
    def test_numeric_id_lookup(self):
        from intodoc.scanner.credential_filter import CredentialFilter
        parser = WorkatoParser(CredentialFilter())
        parser._connection_id_index = {201: "salesforce", 202: "hubspot"}

        assert parser._resolve_connection_ref("salesforce", 201) == "salesforce"
        assert parser._resolve_connection_ref("hubspot", 202) == "hubspot"

    def test_provider_fallback(self):
        from intodoc.scanner.credential_filter import CredentialFilter
        parser = WorkatoParser(CredentialFilter())
        parser._provider_to_conn = {"slack": "slack_prod"}

        # No numeric ID match, falls back to provider-to-conn
        assert parser._resolve_connection_ref("slack") == "slack_prod"

    def test_direct_name_match(self):
        from intodoc.scanner.credential_filter import CredentialFilter
        parser = WorkatoParser(CredentialFilter())
        parser._connection_index = {"hubspot": MetadataConnection(
            xmi_id="202", name="hubspot", connection_type="HubSpot",
        )}

        assert parser._resolve_connection_ref("hubspot") == "hubspot"

    def test_no_match_returns_empty(self):
        from intodoc.scanner.credential_filter import CredentialFilter
        parser = WorkatoParser(CredentialFilter())

        assert parser._resolve_connection_ref("unknown") == ""


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

    def test_recipe_has_components(self, scan_result):
        """New-format recipe should have non-empty components."""
        recipe_jobs = [j for j in scan_result.jobs if "sync_leads" in j.name.lower() or "sync leads" in j.name.lower()]
        assert len(recipe_jobs) >= 1
        job = recipe_jobs[0]
        assert len(job.components) > 0

        # Should have trigger + actions + conditions
        comp_types = [c.component_type for c in job.components]
        assert any("Trigger" in t for t in comp_types)
        assert any("Action" in t for t in comp_types)
        assert any("Condition" in t for t in comp_types)

    def test_recipe_has_connections(self, scan_result):
        """New-format recipe should have flow connections."""
        recipe_jobs = [j for j in scan_result.jobs if "sync_leads" in j.name.lower() or "sync leads" in j.name.lower()]
        job = recipe_jobs[0]
        assert len(job.connections) > 0
        flow_conns = [c for c in job.connections if c.connector_name == "FLOW"]
        assert len(flow_conns) > 0

    def test_recipe_has_tmap_data(self, scan_result):
        """New-format recipe should have datapill references."""
        recipe_jobs = [j for j in scan_result.jobs if "sync_leads" in j.name.lower() or "sync leads" in j.name.lower()]
        job = recipe_jobs[0]
        assert len(job.tmap_data) > 0
        assert len(job.tmap_data[0].entries) > 0

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
        """Recipe function should be parsed as a job."""
        func_jobs = [j for j in scan_result.jobs if "func_" in j.name]
        assert len(func_jobs) >= 1
        # Should have components from steps
        assert len(func_jobs[0].components) > 0

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

    def test_recipes_have_steps_from_sibling(self, scan_result):
        """Steps from sibling steps.json should be merged into recipes."""
        sync_job = next(j for j in scan_result.jobs if "Sync Orders" in j.name)
        # Should have trigger + 2 action steps from steps.json
        assert len(sync_job.components) >= 3

    def test_recipe_has_trigger(self, scan_result):
        sync_job = next(j for j in scan_result.jobs if "Sync Orders" in j.name)
        comp_types = [c.component_type for c in sync_job.components]
        assert any("Trigger" in t for t in comp_types)

    def test_recipe_has_actions(self, scan_result):
        sync_job = next(j for j in scan_result.jobs if "Sync Orders" in j.name)
        comp_types = [c.component_type for c in sync_job.components]
        assert any("Action" in t for t in comp_types)

    def test_recipe_has_datapills(self, scan_result):
        sync_job = next(j for j in scan_result.jobs if "Sync Orders" in j.name)
        assert len(sync_job.tmap_data) > 0

    def test_conditional_recipe(self, scan_result):
        """Process Returns recipe should have condition from steps.json."""
        returns_job = next(j for j in scan_result.jobs if "Process Returns" in j.name)
        comp_types = [c.component_type for c in returns_job.components]
        # Condition type uses "if" for both "if" and "conditional" step types
        assert "WorkatoCondition_if" in comp_types

    def test_nightly_sync_has_wrapped_steps(self, scan_result):
        """Nightly sync recipe (wrapped steps.json dict) should have components."""
        nightly = next((j for j in scan_result.jobs if "Nightly" in j.name or "nightly" in j.name), None)
        assert nightly is not None, f"Nightly recipe not found. Jobs: {[j.name for j in scan_result.jobs]}"
        # Should have components from the wrapped steps.json
        assert len(nightly.components) >= 2

    def test_nightly_sync_has_format_c_actions(self, scan_result):
        """Steps using application/action (Format C) should resolve correctly."""
        nightly = next((j for j in scan_result.jobs if "Nightly" in j.name or "nightly" in j.name), None)
        assert nightly is not None
        comp_types = [c.component_type for c in nightly.components]
        # Should have action with postgresql (from application field)
        assert any("postgresql" in t for t in comp_types)

    def test_nightly_sync_has_conditional(self, scan_result):
        """conditional step type should be handled like if."""
        nightly = next((j for j in scan_result.jobs if "Nightly" in j.name or "nightly" in j.name), None)
        assert nightly is not None
        comp_types = [c.component_type for c in nightly.components]
        assert any("Condition" in t for t in comp_types)

    def test_nightly_sync_has_repeat_for_each(self, scan_result):
        """repeat_for_each step type should be handled like foreach."""
        nightly = next((j for j in scan_result.jobs if "Nightly" in j.name or "nightly" in j.name), None)
        assert nightly is not None
        comp_types = [c.component_type for c in nightly.components]
        assert any("Loop" in t for t in comp_types)

    def test_nightly_sync_has_formula(self, scan_result):
        """formula step type should produce a WorkatoFormula component."""
        nightly = next((j for j in scan_result.jobs if "Nightly" in j.name or "nightly" in j.name), None)
        assert nightly is not None
        comp_types = [c.component_type for c in nightly.components]
        assert "WorkatoFormula" in comp_types

    def test_nightly_sync_has_lookup(self, scan_result):
        """lookup step type should produce a WorkatoLookup component."""
        nightly = next((j for j in scan_result.jobs if "Nightly" in j.name or "nightly" in j.name), None)
        assert nightly is not None
        comp_types = [c.component_type for c in nightly.components]
        assert any("Lookup" in t for t in comp_types)

    def test_nightly_sync_has_trigger_step(self, scan_result):
        """trigger appearing as a step should produce a Trigger component."""
        nightly = next((j for j in scan_result.jobs if "Nightly" in j.name or "nightly" in j.name), None)
        assert nightly is not None
        comp_types = [c.component_type for c in nightly.components]
        assert any("Trigger" in t for t in comp_types)

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
