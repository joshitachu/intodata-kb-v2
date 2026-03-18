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


# ── Best Practices ───────────────────────────────────────────────────


def test_best_practices_loads():
    """best_practices.yaml should load successfully."""
    plugin = WorkatoPlugin()
    bp = plugin.get_best_practices()
    assert bp["platform_name"] == "Workato"
    assert "concepts" in bp
    assert "anti_patterns" in bp
