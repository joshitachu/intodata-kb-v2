"""Synthetic Talend estate for the enterprise lane tests (no client content).

Two source designs (a route and two DI jobs), one shared context group, one
tMap with a lookup, a Structure file that no scanner fact references (so a
session must DISCOVER it through workspace search), and the deterministic scan
artifacts the intodoc scanner writes: ``scan/sanitized/``, mapping accounting
and the capability ledger.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from mcp_server.providers.custom.models import FactsManifest, UnitFact

ROUTE = "Inbound_Orders_Route"
LOADER = "Orders_Load"
WRITER = "Orders_To_Warehouse"
UNITS = (ROUTE, LOADER, WRITER)
TABLE_ID = "a1b2c3d4e5f60718"
STRUCTURE_PATH = "Structures/Orders/CMM_Orders.xml"

ROUTE_ITEM = """<?xml version="1.0" encoding="UTF-8"?>
<talendfile:ProcessType xmlns:talendfile="platform:/resource/org.talend.model/model/TalendFile.xsd" defaultContext="Default">
  <node componentName="cREST"><elementParameter name="UNIQUE_NAME" value="cREST_1"/><elementParameter name="REST_ENDPOINT" value="&quot;/orders&quot;"/></node>
  <node componentName="cTalendJob"><elementParameter name="UNIQUE_NAME" value="cTalendJob_1"/><elementParameter name="SELECTED_JOB_NAME:PROCESS_TYPE_PROCESS" value="_guidOrdersLoad"/><elementParameter name="USE_ROUTE_CONTEXT" value="true"/></node>
  <node componentName="cOnException"><elementParameter name="UNIQUE_NAME" value="cOnException_1"/></node>
  <connection connectorName="ROUTE" source="cREST_1" target="cTalendJob_1"/>
</talendfile:ProcessType>
"""

LOADER_ITEM = """<?xml version="1.0" encoding="UTF-8"?>
<talendfile:ProcessType xmlns:talendfile="platform:/resource/org.talend.model/model/TalendFile.xsd" defaultContext="Default">
  <context name="Default"><contextParameter name="db_host" type="id_String" value="[HOSTNAME]" repositoryContextId="_ctxOrders"/></context>
  <node componentName="tRouteInput"><elementParameter name="UNIQUE_NAME" value="tRouteInput_1"/></node>
  <node componentName="tMap"><elementParameter name="UNIQUE_NAME" value="tMap_1"/>
    <nodeData xsi:type="talendmapper:MapperData" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
      <outputTables name="out1"><mapperTableEntries name="order_id" expression="row1.order_id"/><mapperTableEntries name="status" expression="&quot;NEW&quot;"/><mapperTableEntries name="customer_name" expression="lookup.customer_name"/><mapperTableEntries name="loaded_at" expression="TalendDate.getCurrentDate()"/></outputTables>
    </nodeData>
  </node>
  <node componentName="tMSSqlOutput"><elementParameter name="UNIQUE_NAME" value="tMSSqlOutput_1"/><elementParameter name="TABLE" value="&quot;ORDERS&quot;"/></node>
  <node componentName="tRunJob"><elementParameter name="UNIQUE_NAME" value="tRunJob_1"/><elementParameter name="PROCESS:PROCESS_TYPE_PROCESS" value="_guidWarehouse"/><elementParameter name="PROCESS:PROCESS_TYPE_VERSION" value="0.3"/></node>
  <node componentName="tDie"><elementParameter name="UNIQUE_NAME" value="tDie_1"/></node>
  <node componentName="tLogRow"><elementParameter name="UNIQUE_NAME" value="tLogRow_1"/><elementParameter name="ACTIVATE" value="false"/></node>
  <connection connectorName="FLOW" source="tRouteInput_1" target="tMap_1"/>
  <connection connectorName="FLOW" source="tMap_1" target="tMSSqlOutput_1"/>
  <connection connectorName="COMPONENT_ERROR" source="tMSSqlOutput_1" target="tDie_1"/>
  <connection connectorName="SUBJOB_OK" source="tMSSqlOutput_1" target="tRunJob_1"/>
</talendfile:ProcessType>
"""

WRITER_ITEM = """<?xml version="1.0" encoding="UTF-8"?>
<talendfile:ProcessType xmlns:talendfile="platform:/resource/org.talend.model/model/TalendFile.xsd" defaultContext="Default">
  <node componentName="tMSSqlInput"><elementParameter name="UNIQUE_NAME" value="tMSSqlInput_1"/><elementParameter name="QUERY" value="&quot;SELECT order_id, status FROM ORDERS WHERE status = 'NEW'&quot;"/></node>
  <node componentName="tFileOutputDelimited"><elementParameter name="UNIQUE_NAME" value="tFileOutputDelimited_1"/><elementParameter name="FILENAME" value="context.warehouse_drop"/></node>
  <connection connectorName="FLOW" source="tMSSqlInput_1" target="tFileOutputDelimited_1"/>
</talendfile:ProcessType>
"""

STRUCTURE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<root name="CMM_Orders"><rootElem name="Order"><child name="OrderId" dataType="2"/><child name="WarehouseCode" dataType="2"/></rootElem></root>
"""

CONTEXT_ITEM = """<?xml version="1.0" encoding="UTF-8"?>
<xmi:XMI xmlns:xmi="http://www.omg.org/XMI" xmlns:talendfile="platform:/resource/org.talend.model/model/TalendFile.xsd">
  <talendfile:ContextType name="Default"><contextParameter name="db_host" type="id_String" value="[HOSTNAME]"/><contextParameter name="warehouse_drop" type="id_String" value="/data/out"/></talendfile:ContextType>
  <talendfile:ContextType name="PROD"><contextParameter name="db_host" type="id_String" value="[HOSTNAME]"/><contextParameter name="warehouse_drop" type="id_String" value="/prod/out"/></talendfile:ContextType>
</xmi:XMI>
"""

SANITIZED_FILES: dict[str, str] = {
    f"routes/Orders/{ROUTE}_0.1.item": ROUTE_ITEM,
    f"process/Orders/{LOADER}_0.2.item": LOADER_ITEM,
    f"process/Orders/{WRITER}_0.3.item": WRITER_ITEM,
    "context/Orders_0.1.item": CONTEXT_ITEM,
    STRUCTURE_PATH: STRUCTURE_XML,
    "talend.project": '<?xml version="1.0"?><TalendProperties:Project xmlns:TalendProperties="http://www.talend.org/properties" label="orders" technicalLabel="ORDERS"/>\n',
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def mapping_rows() -> list[dict[str, Any]]:
    return [
        {"output_table": "out1", "output_field": "order_id", "expression": "row1.order_id", "classification": "direct_map",
         "source_refs": ["row1.order_id"], "dynamic_refs": []},
        {"output_table": "out1", "output_field": "status", "expression": '"NEW"', "classification": "constant",
         "source_refs": [], "dynamic_refs": []},
        {"output_table": "out1", "output_field": "customer_name", "expression": "lookup.customer_name", "classification": "lookup",
         "source_refs": ["lookup.customer_name"], "dynamic_refs": []},
        {"output_table": "out1", "output_field": "loaded_at", "expression": "TalendDate.getCurrentDate()", "classification": "dynamic",
         "source_refs": [], "dynamic_refs": ["TalendDate.getCurrentDate"]},
    ]


def scan_result() -> dict[str, Any]:
    return {
        "platform": "talend",
        "repo_root": "/sanitized/orders",
        "jobs": [
            {"job_id": ROUTE, "name": ROUTE, "group": "Orders", "source_root": ".", "source_design": "routes",
             "path": f"Orders/{ROUTE}_0.1.item", "version": "0.1", "artifact_kind": "route", "artifact_role": "standalone",
             "components": [
                 {"unique_name": "cREST_1", "component_type": "cREST", "params": [], "active": True},
                 {"unique_name": "cTalendJob_1", "component_type": "cTalendJob", "params": [], "active": True},
                 {"unique_name": "cOnException_1", "component_type": "cOnException", "params": [], "active": True},
             ],
             "connections": [{"name": "route1", "source": "cREST_1", "target": "cTalendJob_1", "connector_name": "ROUTE"}],
             "context_groups": [], "tmap_data": [], "trunjob_refs": [],
             "artifact_refs": [{"target_name": LOADER, "target_xmi_id": "_guidOrdersLoad", "relation_type": "route_invokes_job",
                                "component_name": "cTalendJob_1", "resolved_artifact_id": LOADER, "resolution": "same_root",
                                "reference_source": "identity", "use_route_context": True, "version": "Latest"}]},
            {"job_id": LOADER, "name": LOADER, "group": "Orders", "source_root": ".", "source_design": "process",
             "path": f"Orders/{LOADER}_0.2.item", "version": "0.2", "artifact_kind": "job", "artifact_role": "standalone",
             "components": [
                 {"unique_name": "tRouteInput_1", "component_type": "tRouteInput", "params": [], "active": True},
                 {"unique_name": "tMap_1", "component_type": "tMap", "params": [], "active": True},
                 {"unique_name": "tMSSqlOutput_1", "component_type": "tMSSqlOutput", "params": [], "active": True},
                 {"unique_name": "tRunJob_1", "component_type": "tRunJob", "params": [], "active": True},
                 {"unique_name": "tDie_1", "component_type": "tDie", "params": [], "active": True},
                 {"unique_name": "tLogRow_1", "component_type": "tLogRow", "params": [], "active": False},
             ],
             "connections": [
                 {"name": "row1", "source": "tRouteInput_1", "target": "tMap_1", "connector_name": "FLOW"},
                 {"name": "out1", "source": "tMap_1", "target": "tMSSqlOutput_1", "connector_name": "FLOW"},
                 {"name": "err", "source": "tMSSqlOutput_1", "target": "tDie_1", "connector_name": "COMPONENT_ERROR"},
                 {"name": "ok", "source": "tMSSqlOutput_1", "target": "tRunJob_1", "connector_name": "SUBJOB_OK"},
             ],
             "context_groups": [{"name": "inline", "variables": [{"name": "db_host", "type": "id_String",
                                                                    "values": {"Default": "[HOSTNAME]"},
                                                                    "repository_context_id": "_ctxOrders"}]}],
             "tmap_data": [{"component_name": "tMap_1", "input_tables": ["row1", "lookup"], "output_tables": ["out1"],
                            "entries": [{"table_name": "out1", "column_name": r["output_field"], "expression": r["expression"]}
                                        for r in mapping_rows()]}],
             "trunjob_refs": [{"child_job": WRITER, "child_job_xmi_id": "_guidWarehouse", "resolved_job_id": WRITER,
                               "resolution": "same_root", "version": "0.3", "context_name": "Default",
                               "transmit_whole_context": True, "reference_source": "identity",
                               "context_params": [{"name": "batch", "value": "context.batch_id"}]}],
             "artifact_refs": []},
            {"job_id": WRITER, "name": WRITER, "group": "Orders", "source_root": ".", "source_design": "process",
             "path": f"Orders/{WRITER}_0.3.item", "version": "0.3", "artifact_kind": "job", "artifact_role": "standalone",
             "components": [
                 {"unique_name": "tMSSqlInput_1", "component_type": "tMSSqlInput", "params": [], "active": True},
                 {"unique_name": "tFileOutputDelimited_1", "component_type": "tFileOutputDelimited", "params": [], "active": True},
             ],
             "connections": [{"name": "row2", "source": "tMSSqlInput_1", "target": "tFileOutputDelimited_1", "connector_name": "FLOW"}],
             "context_groups": [], "tmap_data": [], "trunjob_refs": [], "artifact_refs": []},
        ],
        "context_groups": [{"name": "Orders", "context_key": "Orders", "context_id": "_ctxOrders", "source_root": ".",
                            "source_role": "execution",
                            "variables": [{"name": "db_host", "type": "id_String", "values": {"Default": "[HOSTNAME]", "PROD": "[HOSTNAME]"},
                                           "redaction_descriptors": {"Default": {"category": "db"}, "PROD": {"category": "db"}}},
                                          {"name": "warehouse_drop", "type": "id_String", "values": {"Default": "/data/out", "PROD": "/prod/out"}}]}],
        "metadata_connections": [],
        "routines": [],
        "manifest": {
            "job_calls": {LOADER: [WRITER]}, "job_called_by": {WRITER: [LOADER]},
            "artifact_relations": [
                {"source": ROUTE, "target": LOADER, "relation_type": "route_invokes_job", "resolution": "same_root", "provenance": "cTalendJob"},
                {"source": LOADER, "target": WRITER, "relation_type": "job_calls_job", "resolution": "resolved", "provenance": "tRunJob"},
            ],
            "artifact_endpoints": [{"artifact": ROUTE, "endpoint_type": "rest", "direction": "inbound", "component_name": "cREST_1", "provenance": "cREST"}],
            "artifact_channels": [], "artifact_resources": [],
            "context_used_by": {"Orders": [LOADER]},
            "job_context_links": {LOADER: [{"context_key": "Orders", "context_id": "_ctxOrders", "resolution": "repository_id"}]},
            "context_metadata": {"Orders": {"name": "Orders", "environments": ["Default", "PROD"], "variable_count": 2}},
        },
        "mapping_accounting": [{
            "unit_id": LOADER, "component_name": "tMap_1", "mapper_kind": "tmap", "table_id": TABLE_ID,
            "offered_fields": 3, "offered_enumerable": True, "unenumerable_inputs": [], "written_fields": 4,
            "mapped_from_input": 1, "constant_or_generated": 1, "lookup_derived": 1, "dynamic_unresolved": 1,
            "unwritten_offered": ["row1.ignored_flag"], "lookup_tables": [{"name": "lookup", "match_model": "UNIQUE_MATCH",
                                                                            "load_model": "LOAD_ONCE", "inner_join": "false", "expression_filter": ""}],
            "filters": [], "reject_outputs": [], "rows": mapping_rows(),
        }],
        "capability_ledger": {"capability_version": "talend-capability-2",
                              "file_outcomes": {"parsed": 5, "indexed": 1},
                              "constructs": [{"category": "mapping.tmap", "construct": "entries", "status": "supported", "count": 4, "note": ""},
                                             {"category": "channel", "construct": "dynamic_destination", "status": "dynamic", "count": 1,
                                              "note": "one file destination is a context expression"}]},
        "source_inventory": [{"path": p, "outcome": "parsed", "reason": "supported_artifact_extracted"} for p in SANITIZED_FILES],
        "warnings": [],
    }


def write_scan_output(scan_dir: Path, *, residual_free: bool = True, tamper: bool = False) -> Path:
    scan_dir.mkdir(parents=True, exist_ok=True)
    result = scan_result()
    (scan_dir / "scan_result.json").write_text(json.dumps(result), encoding="utf-8")
    (scan_dir / "scan").mkdir(exist_ok=True)
    (scan_dir / "scan" / "manifest.json").write_text(json.dumps(result["manifest"]), encoding="utf-8")
    (scan_dir / "scan" / "mapping_accounting.json").write_text(json.dumps(result["mapping_accounting"]), encoding="utf-8")
    (scan_dir / "scan" / "capability_ledger.json").write_text(json.dumps(result["capability_ledger"]), encoding="utf-8")
    sanitized = scan_dir / "scan" / "sanitized"
    files = []
    for relative, text in SANITIZED_FILES.items():
        target = sanitized / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        files.append({"path": relative, "source_root": ".", "kind": "item", "sha256": _sha(text),
                      "bytes": len(text.encode("utf-8")), "outcome": "parsed"})
    if tamper:
        (sanitized / "talend.project").write_text("tampered", encoding="utf-8")
    (sanitized / "manifest.json").write_text(json.dumps({
        "schema_version": 1, "capability_version": "talend-capability-2", "source_roots": [{"path": ".", "role": "execution"}],
        "files": files, "excluded": [], "residual_free": residual_free,
    }), encoding="utf-8")
    return scan_dir


def facts() -> FactsManifest:
    return FactsManifest(
        project_id="orders", platform="talend", canonical_unit_label="Talend designs", unit_count=3,
        groups={"Orders": 3},
        units=[UnitFact(name=ROUTE, display_name=ROUTE, group="Orders", path=f"Orders/{ROUTE}_0.1.item", source_root="."),
               UnitFact(name=LOADER, display_name=LOADER, group="Orders", path=f"Orders/{LOADER}_0.2.item", source_root="."),
               UnitFact(name=WRITER, display_name=WRITER, group="Orders", path=f"Orders/{WRITER}_0.3.item", source_root=".")],
    )


def ws(unit: str) -> str:
    """Workspace path of a fixture design."""
    return {ROUTE: f"_workspace/routes/Orders/{ROUTE}_0.1.item", LOADER: f"_workspace/process/Orders/{LOADER}_0.2.item",
            WRITER: f"_workspace/process/Orders/{WRITER}_0.3.item"}[unit]


def ev(unit: str, quote: str = "", locator: str = "") -> dict[str, str]:
    return {"path": ws(unit), "locator": locator, "quote": quote}


# ---------------------------------------------------------------------------
# Canned "model" submissions: what a well-behaved OpenCode session would submit.
# ---------------------------------------------------------------------------

def design_payloads() -> list[dict[str, Any]]:
    def views(u: str) -> dict[str, str]:
        return {"developer": f"{u} mechanics", "operator": f"{u} runs when triggered", "architect": f"{u} depends on siblings",
                "business_owner": "unknown: no supplied documentation names an owner"}

    return [{
        "schema_version": "enterprise-estate-1",
        "designs": [
            {"unit_id": ROUTE, "kind": "route", "summary": "Receives orders over REST and invokes the loader job.",
             "views": views(ROUTE), "systems": ["REST clients"], "business_purpose_status": "inferred",
             "evidence": [ev(ROUTE, "cREST"), ev(ROUTE, "cTalendJob")], "unknowns": []},
            {"unit_id": LOADER, "kind": "job", "summary": "Maps incoming orders and writes them to the ORDERS table, then calls the warehouse writer.",
             "views": views(LOADER), "systems": ["ORDERS database", "CMM_Orders structure"], "business_purpose_status": "inferred",
             # Discovered through workspace search: no scanner fact references the Structure file.
             "evidence": [ev(LOADER, "tMSSqlOutput"), {"path": f"_workspace/{STRUCTURE_PATH}", "locator": "rootElem", "quote": "WarehouseCode"}],
             "unknowns": ["loaded_at uses the current date; business meaning of the timestamp unknown"]},
            {"unit_id": WRITER, "kind": "job", "summary": "Reads NEW orders and writes a delimited drop file for the warehouse.",
             "views": views(WRITER), "systems": ["ORDERS database", "warehouse drop folder"], "business_purpose_status": "inferred",
             "evidence": [ev(WRITER, "tFileOutputDelimited")], "unknowns": []},
        ],
        "systems": [{"system_id": "orders-db", "name": "ORDERS database", "kind": "database", "aliases": [],
                     "evidence": [ev(LOADER, "tMSSqlOutput")]}],
        "unknowns": [{"unknown_id": "owner-orders", "subject": LOADER, "question": "Who owns the order load?",
                      "reason": "not in the export", "audience": ["business_owner"], "locator": ""}],
    }]


def journey_payloads() -> list[dict[str, Any]]:
    return [{
        "schema_version": "enterprise-journeys-1",
        "journeys": [{
            "journey_id": "orders-intake", "name": "Order intake to warehouse",
            "trigger": {"kind": "rest", "unit_id": ROUTE, "detail": "POST /orders"},
            "steps": [
                {"order": 1, "unit_id": ROUTE, "components": ["cREST_1"], "mechanism": "rest", "description": "Receive the order.", "evidence": [ev(ROUTE, "cREST")]},
                {"order": 2, "unit_id": LOADER, "components": ["tMap_1", "tMSSqlOutput_1"], "mechanism": "cTalendJob", "description": "Map and persist the order.", "evidence": [ev(LOADER, "tMSSqlOutput")]},
                {"order": 3, "unit_id": WRITER, "components": ["tFileOutputDelimited_1"], "mechanism": "tRunJob", "description": "Write the warehouse drop file.", "evidence": [ev(WRITER, "tFileOutputDelimited")]},
            ],
            "destinations": [{"system": "ORDERS database", "mechanism": "db", "detail": "table ORDERS"},
                             {"system": "warehouse drop folder", "mechanism": "file", "detail": "context.warehouse_drop"}],
            "business_object": "customer order", "business_capability": "",
            "implementing_units": [ROUTE, LOADER, WRITER], "mapping_table_ids": [TABLE_ID], "error_path_ids": [],
            "monitoring": ["cOnException_1"], "confidence": "high",
            "evidence": [ev(ROUTE, "cTalendJob"), ev(LOADER, "tRunJob")],
        }],
        "excluded_units": [],
    }]


def model_payloads(*, retype_table: bool = False) -> list[dict[str, Any]]:
    summary = "Copies the order id, fixes status to NEW, resolves the customer name through the lookup, stamps the load time."
    if retype_table:
        summary = "| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n| 5 | 6 |"
    return [{
        "schema_version": "enterprise-model-1",
        "mappings": [{"table_id": TABLE_ID, "unit_id": LOADER, "component_name": "tMap_1", "summary": summary,
                      "rules": ["status is hard-coded to NEW", "customer_name comes from the lookup table (unique match, load once)"],
                      "business_meaning": "New orders enter the warehouse pipeline", "business_meaning_status": "inferred",
                      "risks": ["ignored_flag offered by the input is never written"], "evidence": [ev(LOADER, "row1.order_id")]}],
        "configuration": [
            {"scope": "context_group", "key": "Orders", "summary": "Holds the database host and the warehouse drop folder per environment.",
             "environment_differences": ["warehouse_drop differs between Default and PROD"], "drives": ["tFileOutputDelimited_1 target"],
             "evidence": [{"path": "_workspace/context/Orders_0.1.item", "locator": "PROD", "quote": "/prod/out"}]},
            {"scope": "unit", "key": LOADER, "summary": "Inline context binds db_host to the Orders repository context.",
             "environment_differences": [], "drives": ["database connection"], "evidence": [ev(LOADER, "repositoryContextId")]},
        ],
        "error_paths": [
            {"error_path_id": "orders-load-die", "unit_id": LOADER, "trigger": "tMSSqlOutput_1 raises a component error",
             "handling": "tDie stops the job", "recovery": "rerun after fixing the database issue", "blast_radius": "the warehouse writer is not called",
             "evidence": [ev(LOADER, "COMPONENT_ERROR")]},
            {"error_path_id": "route-exception", "unit_id": ROUTE, "trigger": "any exception in the route",
             "handling": "cOnException handles it", "recovery": "", "blast_radius": "the REST caller receives an error",
             "evidence": [ev(ROUTE, "cOnException")]},
        ],
        "business": [{"journey_id": "orders-intake", "statement": "Brings customer orders into the warehouse pipeline.",
                      "status": "inferred", "source_authority": "implemented_design", "evidence": [ev(LOADER, "ORDERS")]}],
        "unknowns": [],
    }]


def reconcile_payloads() -> list[dict[str, Any]]:
    return [{"schema_version": "enterprise-reconcile-1", "contradictions": [], "confirmations": ["No runtime source; nothing to reconcile."], "unknowns": []}]


def doc_payload(doc: str, claim_ids: list[str]) -> dict[str, Any]:
    long = ("This integration receives customer orders over a REST endpoint, maps them onto the ORDERS table and hands the "
            "new orders to the warehouse writer, which produces a delimited drop file per environment. ")
    if doc == "INTEGRATION_CATALOGUE":
        sections = {"overview": {"text": long + "The estate has one intake integration.", "claim_ids": claim_ids[:1]},
                    "how_to_read": {"text": "Rows link to dossiers where they exist; simple flows are described inline.", "claim_ids": []},
                    "estate_observations": {"text": "The lookup and the constant status are the two rules worth watching.", "claim_ids": claim_ids[:1]}}
    else:
        sections = {name: {"text": long + f"Section {name.replace('_', ' ')} explained for the reader in plain language.",
                           "claim_ids": claim_ids[:1]} for name in
                    ("overview", "business_purpose", "end_to_end_flow", "mapping_notes", "configuration", "operations", "errors_and_recovery")}
        sections["risks_and_open_questions"] = {"text": "Ownership of the order load is unknown; no supplied documentation names a business owner.", "claim_ids": []}
    return {"schema_version": "enterprise-doc-sections-1", "doc": doc, "sections": sections}


def review_payload(findings: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"schema_version": "enterprise-review-1", "findings": findings or []}
