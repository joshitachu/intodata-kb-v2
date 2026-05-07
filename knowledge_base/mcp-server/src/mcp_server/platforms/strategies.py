"""Platform strategies for MCP-side behavior.

The parser/plugin source of truth remains ``intodoc.platforms.registry``.
These strategies own MCP-specific wording, validation helpers, and legacy
fallback derivation used by API/provider code.
"""

from __future__ import annotations

import json
from abc import ABC
from typing import Any

from mcp_server.metadata.platforms import PlatformDescriptor, get_platform_descriptor


_FALLBACK_INTERNAL_COMPONENTS: frozenset[str] = frozenset({
    "tMap", "tLogRow", "tJavaRow", "tJava", "tJavaFlex", "tPrejob", "tPostjob",
    "tRunJob", "tFixedFlowInput", "tFlowToIterate", "tSetGlobalVar", "tContextLoad",
    "tRouteInput", "tRouteOutput",
    "tUnite", "tReplicate", "tSortRow", "tFilterRow", "tAggregateRow",
    "tFileInputDelimited", "tFileOutputDelimited", "tFileInputExcel", "tFileOutputExcel",
    "tWriteJSONField", "tExtractJSONFields", "tExtractXMLField",
    "tSchemaComplianceCheck", "tSleep", "tLoop", "tFileList", "tFileDelete",
    "tFileCopy", "tFileExist",
    "tMsgBox", "tWarn", "tDie", "tBufferInput", "tBufferOutput",
    "tNormalize", "tDenormalize", "tIterateToFlow",
    "tLogCatcher", "tStatCatcher", "tFlowMeter", "tFlowMeterCatcher",
    "tChronometerStart", "tChronometerStop", "tAssert", "tAssertCatcher",
})

_FALLBACK_TALEND_MAP: dict[str, str] = {
    "tSalesforce": "Salesforce", "tBigQuery": "BigQuery",
    "tMysql": "MySQL", "tMSSql": "MSSQL", "tPostgresql": "PostgreSQL",
    "tOracle": "Oracle", "tSnowflake": "Snowflake", "tRedshift": "Redshift",
    "tMongoDB": "MongoDB", "tCassandra": "Cassandra",
    "tAmazonS3": "Amazon S3", "tS3": "Amazon S3",
    "tRESTClient": "REST API", "tREST": "REST API",
    "tHTTP": "HTTP API", "tSOAP": "SOAP API", "tWebService": "SOAP API",
    "tKafka": "Kafka", "tFTP": "FTP", "tSFTP": "SFTP",
    "tSAP": "SAP", "tLDAP": "LDAP", "tJDBC": "JDBC",
    "tElasticsearch": "Elasticsearch", "tDynamoDB": "DynamoDB",
    "tNetSuite": "NetSuite", "tSendMail": "Email",
    "tHBase": "HBase", "tHive": "Hive", "tImpala": "Impala", "tNeo4j": "Neo4j",
    "tAzureStorage": "Azure Storage", "tGoogleStorage": "Google Cloud Storage",
    "tActiveMQ": "ActiveMQ", "tRabbitMQ": "RabbitMQ", "tMarketo": "Marketo",
}

_FALLBACK_WORKATO_MAP: dict[str, str] = {
    "salesforce": "Salesforce", "netsuite": "NetSuite",
    "postgresql": "PostgreSQL", "mysql": "MySQL", "snowflake": "Snowflake",
    "redshift": "Redshift", "bigquery": "BigQuery",
    "s3": "S3", "gcs": "GCS", "http": "HTTP/REST",
    "workato_webhooks": "Webhook", "ftp": "FTP", "sftp": "SFTP", "smtp": "SMTP",
    "jira": "Jira", "zendesk": "Zendesk", "slack": "Slack", "teams": "MSTeams",
    "sap": "SAP", "oracle": "Oracle", "mssql": "MSSQL",
    "dynamodb": "DynamoDB", "mongodb": "MongoDB",
    "google_sheets": "GoogleSheets", "box": "Box", "dropbox": "Dropbox",
    "workday": "Workday", "hubspot": "HubSpot", "marketo": "Marketo",
    "stripe": "Stripe", "quickbooks": "QuickBooks", "xero": "Xero",
    "servicenow": "ServiceNow", "email": "Email",
}

_FALLBACK_WORKATO_INTERNAL: frozenset[str] = frozenset({
    "clock", "workato", "workato_scheduler", "logger",
})


class PlatformStrategy(ABC):
    id: str
    intodoc_plugin_name: str

    @property
    def descriptor(self) -> PlatformDescriptor:
        return get_platform_descriptor(self.id)

    def canonical_unit_label(self) -> str:
        return "units"

    def unit_type_for(self, job: dict[str, Any]) -> str:
        return "code_unit"

    def analysis_requirements(self) -> str:
        return (
            "Generic-code required evidence: code units/modules/files, entrypoints, APIs, configs, dependencies, "
            "data stores, runtime jobs/tasks, error handling, security boundaries, and migration concerns. "
            "Do not use platform-specific Talend wording unless the scanned source uses it."
        )

    def detected_source_units(self, files: list[dict[str, Any]], jobs: list[dict[str, Any]]) -> tuple[str, int]:
        return "parsed_units", len(jobs)

    def raw_source_audit(self, scan: dict[str, Any], jobs: list[dict[str, Any]]) -> dict[str, Any]:
        files = scan.get("files", []) or []
        by_type: dict[str, int] = {}
        for f in files:
            ftype = str(f.get("file_type", "unknown"))
            by_type[ftype] = by_type.get(ftype, 0) + 1

        if by_type.get("job"):
            detected_label = "job_files"
            detected = by_type["job"]
        else:
            detected_label, detected = self.detected_source_units(files, jobs)

        return {
            "detected_source_label": detected_label,
            "detected_source_units": detected,
            "parsed_units": len(jobs),
            "files_by_type": by_type,
            "parse_gap": max(0, detected - len(jobs)),
        }

    def derive_external_systems(self, job_data: dict[str, Any]) -> list[str]:
        return []


class TalendPlatformStrategy(PlatformStrategy):
    id = "talend"
    intodoc_plugin_name = "talend"

    def canonical_unit_label(self) -> str:
        return "jobs"

    def unit_type_for(self, job: dict[str, Any]) -> str:
        return "job"

    def analysis_requirements(self) -> str:
        return (
            "Talend-specific required evidence: job purpose, component names/types, "
            "tMap/tJava/tJavaRow logic, context variables, routines, connections, file/API/SQL endpoints, "
            "die-on-error/error handling, inactive components, and deployment/migration concerns."
        )

    def detected_source_units(self, files: list[dict[str, Any]], jobs: list[dict[str, Any]]) -> tuple[str, int]:
        return "item_files_fallback", sum(
            1 for f in files if str(f.get("relative_path", "")).endswith(".item")
        )

    def derive_external_systems(self, job_data: dict[str, Any]) -> list[str]:
        systems: set[str] = set()
        for comp in job_data.get("components", []):
            system = _fallback_talend_system(comp.get("component_type", ""))
            if system and not system.startswith("REFERENCE"):
                systems.add(system)
        return sorted(systems)[:6]


class WorkatoPlatformStrategy(PlatformStrategy):
    id = "workato"
    intodoc_plugin_name = "workato"

    def canonical_unit_label(self) -> str:
        return "recipes/functions"

    def unit_type_for(self, job: dict[str, Any]) -> str:
        ctype = (job.get("components") or [{}])[0].get("component_type", "")
        if "Function" in ctype:
            return "function"
        return "recipe"

    def analysis_requirements(self) -> str:
        return (
            "Workato-specific required evidence: recipes and callable functions, triggers, actions, connectors, "
            "properties, lookup tables, branching/conditions, retry/error handling, callable recipe/function "
            "dependencies, and connector/account risks. Do not call Workato recipes Talend jobs."
        )

    def detected_source_units(self, files: list[dict[str, Any]], jobs: list[dict[str, Any]]) -> tuple[str, int]:
        detected = sum(
            1 for f in files
            if "recipe" in str(f.get("relative_path", "")).lower()
            or "recipe_function" in str(f.get("relative_path", "")).lower()
        )
        return "recipe_or_function_files", detected

    def derive_external_systems(self, job_data: dict[str, Any]) -> list[str]:
        providers: set[str] = set()
        for comp in job_data.get("components", []):
            for param in comp.get("params", []):
                value = param.get("value", "")
                parsed = None
                if isinstance(value, str) and value.startswith(("{", "[")):
                    try:
                        parsed = json.loads(value)
                    except (json.JSONDecodeError, ValueError):
                        pass
                if parsed is not None:
                    _fallback_collect_providers(parsed, providers)
        providers -= _FALLBACK_WORKATO_INTERNAL
        return sorted({_FALLBACK_WORKATO_MAP.get(p, p.replace("_", " ").title()) for p in providers})


class LobsterPlatformStrategy(PlatformStrategy):
    id = "lobster"
    intodoc_plugin_name = "lobster"

    def canonical_unit_label(self) -> str:
        return "profiles"

    def unit_type_for(self, job: dict[str, Any]) -> str:
        return "profile"

    def analysis_requirements(self) -> str:
        return (
            "Lobster-specific required evidence: profiles, agents, parser/input/output definitions, mappings, "
            "variables, schedules, routes, forward/exception profiles, format conversions, and operational "
            "handoff risks. Do not call Lobster profiles Talend jobs."
        )

    def detected_source_units(self, files: list[dict[str, Any]], jobs: list[dict[str, Any]]) -> tuple[str, int]:
        return "profile_xml_files", sum(
            1 for f in files if str(f.get("relative_path", "")).lower().endswith(".xml")
        )


class GenericPlatformStrategy(PlatformStrategy):
    id = "generic"
    intodoc_plugin_name = "generic"

    def canonical_unit_label(self) -> str:
        return "code units"


def _fallback_talend_system(component_type: str) -> str | None:
    if component_type in _FALLBACK_INTERNAL_COMPONENTS:
        return None
    best: str | None = None
    best_len = 0
    for prefix, system in _FALLBACK_TALEND_MAP.items():
        if component_type.startswith(prefix) and len(prefix) > best_len:
            best = system
            best_len = len(prefix)
    if best:
        return best
    name = component_type
    if name.startswith("t") and len(name) > 1:
        name = name[1:]
    for suffix in ("Input", "Output", "Connection", "Close", "Open", "Row",
                   "BulkExec", "CDC", "Commit", "Rollback", "Lookup"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name if len(name) >= 3 else None


def _fallback_collect_providers(obj: object, providers: set[str]) -> None:
    if isinstance(obj, dict):
        for key in ("provider", "application", "trigger_application"):
            val = obj.get(key)
            if isinstance(val, str) and val:
                providers.add(val)
        apps = obj.get("action_applications")
        if isinstance(apps, list):
            for app in apps:
                if isinstance(app, str) and app:
                    providers.add(app)
        for val in obj.values():
            _fallback_collect_providers(val, providers)
    elif isinstance(obj, list):
        for item in obj:
            _fallback_collect_providers(item, providers)
