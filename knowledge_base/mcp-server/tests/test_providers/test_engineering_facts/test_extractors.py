from __future__ import annotations

from mcp_server.providers.custom.engineering_facts.extractors.generic import GenericFactExtractor
from mcp_server.providers.custom.engineering_facts.extractors.structured import StructuredScanFactExtractor
from mcp_server.providers.custom.engineering_facts.extractors.talend import TalendFactExtractor
from mcp_server.providers.custom.engineering_facts.extractors.workato import WorkatoFactExtractor
from mcp_server.providers.custom.models import FactsManifest


def _facts(platform: str = "generic") -> FactsManifest:
    return FactsManifest(
        project_id="proj",
        platform=platform,
        canonical_unit_label="units",
        unit_count=1,
    )


def test_generic_extractor_finds_modules_imports_routes_env_and_tests():
    content = """
import os
from fastapi import FastAPI
app = FastAPI()
API_KEY = os.getenv("API_KEY")

@app.get("/customers")
def list_customers():
    return {"ok": True}
"""
    scan = {
        "platform": "generic",
        "files": [
            {"relative_path": "src/main.py", "file_type": "routine"},
            {"relative_path": "tests/test_main.py", "file_type": "routine"},
        ],
        "jobs": [
            {
                "name": "main",
                "path": "src/main.py",
                "group": "src",
                "components": [
                    {
                        "unique_name": "main",
                        "component_type": "CodeFile",
                        "params": [
                            {"name": "file_path", "value": "src/main.py"},
                            {"name": "content", "value": content},
                        ],
                    }
                ],
            }
        ],
        "manifest": {"scripts": {"test": "pytest"}, "dependencies": {"fastapi": "^1.0.0"}},
    }

    facts = GenericFactExtractor().extract("proj", scan, _facts())

    assert any(entity.entity_type == "function" and entity.name == "list_customers" for entity in facts.entities)
    assert any(dep.target == "fastapi" and dep.dependency_type in {"import", "package_dependency"} for dep in facts.dependencies)
    assert any(api.route == "/customers" and api.method == "GET" for api in facts.api_surface)
    assert any(cfg.key == "API_KEY" and cfg.config_type == "env_var" for cfg in facts.config_runtime)
    assert any(test.test_file == "tests/test_main.py" for test in facts.test_coverage)


def test_generic_extractor_redacts_web_config_settings_connections_and_risks():
    content = """
<configuration>
  <appSettings>
    <add key="ReportingConnectionString" value="Server=db01;Database=Reports;User Id=report;Password=[INLINE_CREDENTIAL];Encrypt=True" />
    <add key="FeatureFlag" value="true" />
  </appSettings>
  <connectionStrings>
    <add name="MainDb" connectionString="Server=sql01;Database=Main;User ID=app;Password=[INLINE_CREDENTIAL];Persist Security Info=False;Connect Timeout=30" providerName="System.Data.SqlClient" />
    <add name="AuditDb" connectionString="Host=pg01;Database=Audit;Username=app;Password=[INLINE_CREDENTIAL]" providerName="Npgsql" />
  </connectionStrings>
</configuration>
"""
    scan = {
        "platform": "generic",
        "jobs": [{
            "name": "config",
            "path": "Web.config",
            "group": "root",
            "components": [{
                "unique_name": "Web",
                "component_type": "ConfigFile",
                "params": [
                    {"name": "file_path", "value": "Web.config"},
                    {"name": "content", "value": content},
                ],
            }],
        }],
    }

    facts = GenericFactExtractor().extract("proj", scan, _facts())

    assert any(cfg.config_type == "config_file" and cfg.key == "Web.config" for cfg in facts.config_runtime)
    assert any(cfg.config_type == "setting" and cfg.key == "FeatureFlag" for cfg in facts.config_runtime)
    assert any(cfg.config_type == "connection_string" and cfg.key == "MainDb" for cfg in facts.config_runtime)
    assert any(cfg.config_type == "connection_string" and cfg.key == "AuditDb" for cfg in facts.config_runtime)
    assert any(cfg.config_type == "connection_string" and cfg.key == "ReportingConnectionString" for cfg in facts.config_runtime)
    assert any(
        cfg.key == "MainDb" and cfg.metadata.get("database_type") == "SQL Server"
        for cfg in facts.config_runtime
    )
    assert any(risk.risk_type == "inline_secret_config" for risk in facts.known_risks)
    dumped = facts.model_dump_json()
    assert "[INLINE_CREDENTIAL]" not in dumped


def test_generic_extractor_finds_startup_ormlite_registrations():
    content = """
public class AppHost {
  public override void Configure(Container container) {
    var dbFactory = new OrmLiteConnectionFactory(
      ConfigurationManager.ConnectionStrings["MainDb"].ConnectionString,
      SqlServerDialect.Provider);
    dbFactory.RegisterConnection("ReportingDb",
      ConfigurationManager.AppSettings["ReportingConnectionString"],
      PostgreSqlDialect.Provider);
  }
}
"""
    scan = {
        "platform": "generic",
        "jobs": [{
            "name": "Global.asax",
            "path": "Global.asax.cs",
            "group": "root",
            "components": [{
                "unique_name": "Global_asax",
                "component_type": "CodeFile",
                "params": [
                    {"name": "file_path", "value": "Global.asax.cs"},
                    {"name": "content", "value": content},
                ],
            }],
        }],
    }

    facts = GenericFactExtractor().extract("proj", scan, _facts())

    assert any(cfg.config_type == "db_registration" and cfg.key == "OrmLiteConnectionFactory" for cfg in facts.config_runtime)
    assert any(cfg.config_type == "db_registration" and cfg.key == "ReportingDb" for cfg in facts.config_runtime)
    assert any(cfg.config_type == "connection_reference" and cfg.key == "MainDb" for cfg in facts.config_runtime)
    assert any(dep.dependency_type == "startup_uses_connection" and dep.target == "ReportingDb" for dep in facts.dependencies)


def test_workato_extractor_finds_steps_connections_lookup_usage_and_risks():
    raw_recipe = {
        "trigger": {"provider": "workato_webhooks", "name": "new_event"},
        "steps": [
            {
                "provider": "salesforce",
                "action": "upsert",
                "connection_id": "sf_prod",
                "input": {"SAP_Customer_ID__c": "{{trigger.customer_id}}"},
                "lookup_table_id": "country_codes",
                "formula": "trigger.email.downcase",
            }
        ],
    }
    scan = {
        "platform": "workato",
        "jobs": [
            {
                "name": "Sync customers",
                "path": "recipes/sync_customers.recipe.json",
                "group": "recipes",
                "components": [
                    {
                        "unique_name": "sync_customers",
                        "component_type": "WorkatoRecipe",
                        "params": [
                            {"name": "trigger", "value": "{\"provider\":\"workato_webhooks\",\"name\":\"new_event\"}"},
                            {"name": "steps", "value": "[{\"provider\":\"salesforce\",\"action\":\"upsert\",\"connection_id\":\"sf_prod\",\"input\":{\"SAP_Customer_ID__c\":\"{{trigger.customer_id}}\"},\"lookup_table_id\":\"country_codes\",\"formula\":\"trigger.email.downcase\"}]"},
                        ],
                    }
                ],
            }
        ],
        "manifest": {"connections": [{"name": "sf_prod"}], "lookup_tables": [{"name": "country_codes"}]},
    }

    facts = WorkatoFactExtractor().extract("proj", scan, _facts("workato"))

    assert any(entity.entity_type == "recipe" and entity.name == "Sync customers" for entity in facts.entities)
    assert any(dep.dependency_type == "uses_connection" and dep.target == "sf_prod" for dep in facts.dependencies)
    assert any(use.usage_type == "queried" and use.name == "country_codes" for use in facts.usage)
    assert any(flow.transformation == "trigger.email.downcase" for flow in facts.data_flows)
    assert any(risk.risk_type == "silent_failure_path" for risk in facts.known_risks)


def test_structured_extractor_handles_common_scan_primitives():
    scan = {
        "platform": "structured",
        "files": [{"relative_path": "jobs/main.item", "file_type": "job"}],
        "metadata_connections": [
            {"name": "SharedDB", "connection_type": "MSSQL", "host": "db.example.com", "port": "1433"}
        ],
        "context_groups": [
            {"name": "env", "variables": [{"name": "DB_HOST", "type": "String", "values": {"Default": "localhost", "PROD": "db.example.com"}}]}
        ],
        "routines": [{"name": "CustomerUtils", "source_code": "public class CustomerUtils {}", "is_system": False}],
        "jobs": [
            {
                "name": "ParentJob",
                "path": "process/ParentJob.item",
                "group": "process",
                "components": [
                    {
                        "unique_name": "input_1",
                        "component_type": "tMSSqlInput",
                        "metadata_connection_ref": "SharedDB",
                        "params": [
                            {"name": "QUERY", "value": "SELECT id FROM customers WHERE host = context.DB_HOST"},
                            {"name": "HOST", "value": "db.example.com"},
                        ],
                    }
                ],
                "connections": [{"name": "row1", "source": "input_1", "target": "map_1", "connector_name": "FLOW"}],
                "trunjob_refs": [{"child_job": "ChildJob"}],
                "raw_routines": ["CustomerUtils"],
            }
        ],
        "manifest": {
            "job_calls": {"ParentJob": ["ChildJob"]},
            "job_connections_used": {"ParentJob": ["SharedDB"]},
            "job_contexts_used": {"ParentJob": ["env"]},
            "routines_used_by": {"CustomerUtils": ["ParentJob"]},
            "job_external_systems": {"ParentJob": ["MSSQL"]},
        },
    }

    facts = StructuredScanFactExtractor().extract("proj", scan, _facts("structured"))

    assert any(entity.entity_type == "connection" and entity.name == "SharedDB" for entity in facts.entities)
    assert any(cfg.config_type == "context_var" and cfg.key == "DB_HOST" for cfg in facts.config_runtime)
    assert any(dep.dependency_type == "FLOW" and dep.source == "input_1" and dep.target == "map_1" for dep in facts.dependencies)
    assert any(dep.dependency_type == "calls_job" and dep.source == "ParentJob" and dep.target == "ChildJob" for dep in facts.dependencies)
    assert any(use.usage_type == "queried" for use in facts.usage)
    assert any(risk.risk_type == "hardcoded_config" for risk in facts.known_risks)


def test_talend_extractor_enriches_tmap_trunjob_config_impacts_and_risks():
    scan = {
        "platform": "talend",
        "jobs": [
            {
                "name": "ParentJob",
                "path": "process/ParentJob.item",
                "group": "Sales",
                "components": [
                    {
                        "unique_name": "db_input",
                        "component_type": "tMSSqlInput",
                        "metadata_connection_ref": "SharedDB",
                        "params": [
                            {"name": "PROPERTY_TYPE", "value": "BUILT_IN"},
                            {"name": "QUERY", "value": "SELECT * FROM customers"},
                        ],
                    },
                    {
                        "unique_name": "map_1",
                        "component_type": "tMap",
                        "params": [],
                    },
                    {
                        "unique_name": "run_child",
                        "component_type": "tRunJob",
                        "params": [
                            {"name": "PROCESS", "value": "ChildJob"},
                            {"name": "DIE_ON_CHILD_ERROR", "value": "false"},
                        ],
                    },
                ],
                "connections": [
                    {"name": "row1", "source": "db_input", "target": "map_1", "connector_name": "FLOW"},
                    {"name": "after_map", "source": "map_1", "target": "run_child", "connector_name": "SUBJOB_OK"},
                ],
                "context_groups": [
                    {"name": "inline", "variables": [{"name": "BATCH_SIZE", "type": "Integer", "values": {"Default": "1000"}}]}
                ],
                "tmap_data": [
                    {
                        "input_tables": ["row1"],
                        "output_tables": ["out1"],
                        "var_entries": [{"table_name": "Var", "column_name": "clean_id", "expression": "StringHandling.TRIM(row1.id)"}],
                        "entries": [{"table_name": "out1", "column_name": "customer_id", "expression": "Relational.ISNULL(Var.clean_id) ? null : Var.clean_id"}],
                    }
                ],
                "trunjob_refs": [{"child_job": "ChildJob"}],
            },
            {
                "name": "ChildJob",
                "path": "process/ChildJob.item",
                "group": "Sales",
                "components": [{"unique_name": "log_1", "component_type": "tLogRow", "params": []}],
            },
        ],
        "metadata_connections": [{"name": "SharedDB", "connection_type": "MSSQL", "host": "db.example.com"}],
        "context_groups": [{"name": "env", "variables": [{"name": "DB_HOST", "values": {"Default": "localhost", "PROD": "db.example.com"}}]}],
        "manifest": {},
    }

    facts = TalendFactExtractor().extract("proj", scan, _facts("talend"))

    assert any(entity.entity_type == "job" and entity.name == "ParentJob" for entity in facts.entities)
    assert any(entity.entity_type == "field" and entity.name == "out1.customer_id" for entity in facts.entities)
    assert any(dep.dependency_type == "tRunJob" and dep.source == "ParentJob" and dep.target == "ChildJob" for dep in facts.dependencies)
    assert any(dep.dependency_type == "FLOW" and dep.source == "db_input" and dep.target == "map_1" for dep in facts.dependencies)
    assert any(flow.target == "out1.customer_id" and "Relational.ISNULL" in (flow.transformation or "") for flow in facts.data_flows)
    assert any(cfg.config_type == "context_var" and cfg.key == "DB_HOST" for cfg in facts.config_runtime)
    assert any(impact.changed_entity == "ChildJob" and impact.impacted_entity == "ParentJob" for impact in facts.impacts)
    assert any(risk.risk_type == "built_in_connection" for risk in facts.known_risks)
    assert any(risk.risk_type == "silent_child_failure" for risk in facts.known_risks)
    assert any(risk.risk_type == "unsafe_query_pattern" for risk in facts.known_risks)
