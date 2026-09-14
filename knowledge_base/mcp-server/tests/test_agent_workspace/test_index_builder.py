"""``index/*.jsonl`` -- the deterministic navigation layer (PRD 8.1-8.5).

The fixture is built HERE, from the staged-tree shapes the builder actually
reads (``facts/*.json`` as ``build_exact_tables`` renders them, projected
``scan/jobs/<group>/<design>.json``, ``scan/mapping_accounting.json``,
``scan/lineage.json``, ``runtime/evidence.json``), so this module owns its own
inputs and does not import ``workspace_fixture``. Names are synthetic and
deliberately include a pair differing only by a trailing letter and a context
variable pair differing only by a suffix: both are shapes real estates have,
and both are the shapes a lossy join gets wrong.

What is pinned, in order of how much it cost to learn: bytes are identical
across builds AND across interpreters with different hash seeds; every
forward relationship has its generated reverse; a redacted configuration
value is ``redacted`` from the marker alone, with no descriptor; dropped and
unresolved lineage rows exist; a malformed required input raises the lane's
error family naming the artifact and writes nothing; an index with no rows is
absent and the manifest says so.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mcp_server.providers.agent_workspace import index_builder
from mcp_server.providers.agent_workspace.contract import WorkspaceUnavailableError
from mcp_server.providers.agent_workspace.fact_state import FactState
from mcp_server.providers.agent_workspace.index_builder import (
    INDEX_DIR,
    INDEX_MANIFEST,
    INDEX_ORDER,
    RELATION_INVERSE,
    build_indexes,
)

ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# Fixture: a tiny staged workspace with the shapes the builder reads
# ---------------------------------------------------------------------------

JOB_A = "Orders_GetLocation"  # job; calls Orders_Stage; REST client; DB read
ROUTE_A = "Orders_GetLocations"  # route; cTimer 30ms; cOnException chain
JOB_STAGE = "Orders_Stage"  # callee; unresolved tRunJob; xslt reference
JOB_HANDLER = "Orders_ErrorHandler"  # invoked by the route's exception chain
ROUTE_MON = "Orders_Monitor"  # consumes the route's JMS error topic
JOB_NO_DESIGN = "Orders_NoDesign"  # a unit whose design was not staged
GROUP = "orders"

SECRET_VALUE = "s3cr3t-value-that-must-never-appear"
CLEAR_VALUE_DEV = "abc-123-dev"
CLEAR_VALUE_PROD = "xyz-999-prod"
TABLE_A = "aaaa0000bbbb1111"
TABLE_XSLT = "bbbb0000cccc2222"


def _param(name: str, value: str, field_type: str = "TEXT", rows: list | None = None) -> dict:
    return {
        "name": name,
        "value": value,
        "field_type": field_type,
        "rows": rows or [],
        "redaction_descriptor": None,
    }


def _component(name: str, ctype: str, params: list[dict], *, active: bool = True) -> dict:
    return {
        "unique_name": name,
        "component_type": ctype,
        "params": params,
        "incoming_connections": [],
        "outgoing_connections": [],
        "metadata_connection_ref": "",
        "schemas": [],
        "active": active,
        "declaration": "",
        "repository_property_ref": "",
    }


def _connection(source: str, target: str, connector: str, *, active: bool = True) -> dict:
    return {
        "name": f"{source}->{target}",
        "source": source,
        "target": target,
        "connector_name": connector,
        "line_style": 0,
        "active": active,
    }


def _design(
    unit_id: str, kind: str, components: list[dict], connections: list[dict], **extra
) -> dict:
    return {
        "name": unit_id,
        "job_id": unit_id,
        "source_root": extra.pop("source_root", "PROJ/A"),
        "group": GROUP,
        "artifact_kind": kind,
        "artifact_role": "standalone",
        "is_esb_route": kind == "route",
        "scan_quality": "complete",
        "components": components,
        "connections": connections,
        "context_groups": extra.pop("context_groups", []),
        "tmap_data": [],
        "txmlmap_data": [],
        "trunjob_refs": extra.pop("trunjob_refs", []),
        "artifact_refs": extra.pop("artifact_refs", []),
        "raw_routines": extra.pop("raw_routines", []),
    }


def _unit(unit_id: str, kind: str, **overrides) -> dict:
    unit = {
        "unit_id": unit_id,
        "display_name": unit_id,
        "kind": kind,
        "role": "standalone",
        "group": GROUP,
        "source_root": "PROJ/A",
        "path": f"{GROUP}/{unit_id}_0.1.item",
        "version": "0.1",
        "purpose": "",
        "component_count": 3,
        "disabled_component_count": 0,
        "component_types": [],
        "entry_point": False,
        "inbound_interfaces": False,
        "trigger_interfaces": False,
        "callers": [],
        "callees": [],
        "error_handling_constructs": [],
        "has_error_handling": False,
        "mapping_table_ids": [],
        "scan_quality": "complete",
        "scan_path": f"scan/jobs/{GROUP}/{unit_id}.json",
    }
    unit.update(overrides)
    return unit


def _designs() -> dict[str, dict]:
    job_a = _design(
        JOB_A,
        "job",
        [
            _component(
                "tRESTClient_1",
                "tRESTClient",
                [
                    _param("URL", 'context.baseUrl + "/locations"'),
                    _param("METHOD", "GET"),
                    _param("HEADERS", "", "TABLE", [{"NAME": '"Auth"', "VALUE": "context.token"}]),
                ],
            ),
            _component(
                "tDBInput_1",
                "tDBInput",
                [_param("QUERY", '"select id, nm from locations"'), _param("TABLE", '"locations"')],
            ),
            _component("tMap_1", "tMap", []),
            _component("tRunJob_1", "tRunJob", [_param("PROCESS", JOB_STAGE, "PROCESS_TYPE")]),
            _component("tDie_1", "tDie", [_param("MESSAGE", '"stage failed"')]),
            _component(
                "tLogCatcher_1", "tLogCatcher", [_param("CATCH_JAVA_EXCEPTION", "true", "CHECK")]
            ),
            _component(
                "tJava_1", "tJava", [_param("VALUES", "", "TABLE", [{"VALUE": "context.tokenId"}])]
            ),
        ],
        [
            _connection("tDBInput_1", "tMap_1", "FLOW"),
            _connection("tMap_1", "tRunJob_1", "FLOW"),
            _connection("tRunJob_1", "tDie_1", "SUBJOB_ERROR"),
        ],
        trunjob_refs=[
            {
                "child_job": JOB_STAGE,
                "resolved_job_id": JOB_STAGE,
                "resolution": "same_root",
                "dynamic_job": False,
                "context_name": "Default",
                "context_params": [{"name": "p", "value": "context.token"}],
            }
        ],
        raw_routines=["Utils"],
    )
    route_a = _design(
        ROUTE_A,
        "route",
        [
            _component(
                "cTimer_1",
                "cTimer",
                [_param("PERIOD", "30"), _param("DELAY", "0"), _param("REPEATCOUNT", "0")],
            ),
            _component("cTimer_2", "cTimer", [_param("PERIOD", "context.period")]),
            _component(
                "cMessageFilter_1",
                "cMessageFilter",
                [
                    _param("LANGUAGES", "simple", "LANGUAGE_CLOSED_LIST"),
                    _param("EXPRESSION", "\"${in.header.status} == 'ok'\""),
                ],
            ),
            _component(
                "cMessageRouter_1", "cMessageRouter", [_param("ROUTE_WHEN_LANGUAGES", "constant")]
            ),
            _component(
                "cJMS_1", "cJMS", [_param("TYPE", "queue"), _param("DESTINATION", '"orders.out"')]
            ),
            _component(
                "cJMS_2",
                "cJMS",
                [_param("TYPE", "topic"), _param("DESTINATION", '"orders.errors"')],
            ),
            _component("cOnException_1", "cOnException", [_param("HANDLE", "true", "CHECK")]),
            _component("cDirect_1", "cDirect", [_param("NAME", '"toCollector"')]),
            _component("cDirect_2", "cDirect", [_param("NAME", '"toCollector"')]),
            _component("cDirect_3", "cDirect", [_param("NAME", '""')]),
            _component(
                "cSetHeader_1",
                "cSetHeader",
                [
                    _param(
                        "VALUES",
                        "",
                        "TABLE",
                        [{"NAME": '"CorrelationId"', "LANGUAGE": "constant", "EXPRESSION": '"x"'}],
                    )
                ],
            ),
            _component(
                "cLog_1",
                "cLog",
                [_param("LOGGING_CATEGORY", '"orders.route"'), _param("LEVEL_TYPE", "WARN")],
            ),
            _component("cTalendJob_1", "cTalendJob", [_param("SELECTED_JOB_NAME", JOB_HANDLER)]),
        ],
        [
            _connection("cTimer_1", "cMessageFilter_1", "ROUTE"),
            _connection("cMessageFilter_1", "cJMS_1", "FILTER"),
            _connection("cMessageRouter_1", "cLog_1", "ROUTE_WHEN"),
            _connection("cOnException_1", "cDirect_1", "ROUTE"),
            _connection("cDirect_2", "cSetHeader_1", "ROUTE"),
            _connection("cSetHeader_1", "cTalendJob_1", "ROUTE"),
            _connection("cSetHeader_1", "cJMS_2", "ROUTE"),
            _connection("cSetHeader_1", "cDirect_3", "ROUTE"),
        ],
        artifact_refs=[
            {
                "target_name": JOB_HANDLER,
                "relation_type": "route_invokes_job",
                "component_name": "cTalendJob_1",
                "resolved_artifact_id": JOB_HANDLER,
                "resolution": "same_root",
                "context_name": "DEV",
                "context_params": [],
            }
        ],
        context_groups=[
            {
                "name": "inline",
                "variables": [
                    {
                        "name": "period",
                        "type": "id_Integer",
                        "values": {"Default": "30"},
                        "repository_context_id": "built-in",
                        "redaction_descriptors": {},
                    }
                ],
            }
        ],
    )
    stage = _design(
        JOB_STAGE,
        "job",
        [
            _component("tMap_1", "tMap", []),
            _component(
                "tFileOutputDelimited_1",
                "tFileOutputDelimited",
                [_param("FILENAME", '"/out/orders.csv"')],
            ),
            _component("tRunJob_1", "tRunJob", [_param("PROCESS", "Ghost", "PROCESS_TYPE")]),
        ],
        [_connection("tMap_1", "tFileOutputDelimited_1", "FLOW")],
        trunjob_refs=[
            {
                "child_job": "Ghost",
                "resolved_job_id": "",
                "resolution": "unresolved",
                "dynamic_job": False,
                "context_params": [],
            }
        ],
    )
    handler = _design(
        JOB_HANDLER,
        "job",
        [_component("tLogRow_1", "tLogRow", [])],
        [],
        source_root="PROJ/B",
    )
    monitor = _design(
        ROUTE_MON,
        "route",
        [
            _component(
                "cJMS_1",
                "cJMS",
                [_param("TYPE", "topic"), _param("DESTINATION", '"orders.errors"')],
            ),
            _component(
                "cJMS_9", "cJMS", [_param("TYPE", "queue"), _param("DESTINATION", "context.topic")]
            ),
            _component("cLog_1", "cLog", [_param("LOGGING_CATEGORY", '"orders.monitor"')]),
        ],
        [_connection("cJMS_1", "cLog_1", "ROUTE")],
    )
    return {
        JOB_A: job_a,
        ROUTE_A: route_a,
        JOB_STAGE: stage,
        JOB_HANDLER: handler,
        ROUTE_MON: monitor,
    }


def _channel(
    unit: str,
    component: str,
    name: str,
    direction: str,
    ctype: str = "direct",
    binding: str = "literal",
) -> dict:
    return {
        "artifact": unit,
        "channel_type": ctype,
        "channel": name,
        "channel_binding": binding,
        "channel_descriptor": None,
        "direction": direction,
        "component_name": component,
        "provenance": "cJMS" if ctype == "jms" else "cDirect",
        "dynamic": False,
    }


def _by_unit_channel(row: dict) -> dict:
    return {
        "kind": "channel",
        "type": row["channel_type"],
        "channel": row["channel"],
        "direction": {"output": "outbound", "input": "inbound"}.get(
            row["direction"], row["direction"]
        ),
        "component_name": row["component_name"],
        "descriptor": None,
        "provenance": row["provenance"],
    }


def stage_workspace(root: Path, *, runtime: str | None = "data") -> Path:
    """Materialise the staged tree. ``runtime`` is the mirror mode, or ``None``
    to leave ``runtime/evidence.json`` out entirely."""

    root.mkdir(parents=True, exist_ok=True)
    designs = _designs()
    for unit_id, design in designs.items():
        path = root / "scan" / "jobs" / GROUP / f"{unit_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(design, indent=2), encoding="utf-8")

    units = {
        JOB_A: _unit(
            JOB_A,
            "job",
            entry_point=True,
            callees=[JOB_STAGE],
            error_handling_constructs=["SUBJOB_ERROR", "tDie", "tLogCatcher"],
            has_error_handling=True,
            mapping_table_ids=[TABLE_A],
        ),
        ROUTE_A: _unit(
            ROUTE_A,
            "route",
            entry_point=True,
            trigger_interfaces=True,
            inbound_interfaces=True,
            callees=[JOB_HANDLER],
            error_handling_constructs=["cOnException"],
            has_error_handling=True,
        ),
        JOB_STAGE: _unit(JOB_STAGE, "job", callers=[JOB_A]),
        JOB_HANDLER: _unit(JOB_HANDLER, "job", callers=[ROUTE_A], source_root="PROJ/B"),
        ROUTE_MON: _unit(ROUTE_MON, "route", entry_point=True, trigger_interfaces=True),
        JOB_NO_DESIGN: _unit(JOB_NO_DESIGN, "job", entry_point=True),
    }
    del units[JOB_NO_DESIGN]["scan_path"]

    channels = [
        _channel(ROUTE_A, "cJMS_1", '"orders.out"', "output", "jms"),
        _channel(ROUTE_A, "cJMS_2", '"orders.errors"', "output", "jms"),
        _channel(ROUTE_A, "cDirect_1", '"toCollector"', "output"),
        _channel(ROUTE_A, "cDirect_2", '"toCollector"', "input"),
        _channel(ROUTE_A, "cDirect_3", '""', "output"),
        _channel(ROUTE_MON, "cJMS_1", '"orders.errors"', "input", "jms"),
        _channel(ROUTE_MON, "cJMS_9", "configured channel", "unknown", "jms", binding="dynamic"),
    ]
    by_unit: dict[str, list[dict]] = {unit_id: [] for unit_id in units}
    for row in channels:
        by_unit[row["artifact"]].append(_by_unit_channel(row))
    by_unit[ROUTE_A].append(
        {
            "kind": "endpoint",
            "type": "timer",
            "direction": "inbound",
            "component_name": "cTimer_1",
            "descriptor": None,
            "provenance": "cTimer",
        }
    )
    by_unit[JOB_A] += [
        {
            "kind": "component",
            "type": "tRESTClient",
            "family": "rest",
            "direction": "outbound",
            "component_name": "tRESTClient_1",
            "active": True,
            "repository_connection": "",
        },
        {
            "kind": "component",
            "type": "tDBInput",
            "family": "database",
            "direction": "inbound",
            "component_name": "tDBInput_1",
            "active": True,
            "repository_connection": "PROJ/B::OrdersDB",
        },
        {
            "kind": "component",
            "type": "tRunJob",
            "family": "route_bridge",
            "direction": "unknown",
            "component_name": "tRunJob_1",
            "active": True,
            "repository_connection": "",
        },
    ]
    by_unit[JOB_STAGE].append(
        {
            "kind": "component",
            "type": "tFileOutputDelimited",
            "family": "file",
            "direction": "outbound",
            "component_name": "tFileOutputDelimited_1",
            "active": True,
            "repository_connection": "",
        }
    )

    facts = root / "facts"
    facts.mkdir(exist_ok=True)
    (facts / "units.json").write_text(
        json.dumps({"schema_version": "exact-units-1", "units": units}, indent=2), encoding="utf-8"
    )
    (facts / "dependencies.json").write_text(
        json.dumps(
            {
                "schema_version": "exact-dependencies-1",
                "edges": [
                    {
                        "source": JOB_A,
                        "target": JOB_STAGE,
                        "declared_target": JOB_STAGE,
                        "relation_type": "job_calls_job",
                        "resolution": "same_root",
                        "component_name": "",
                        "context_name": "Default",
                        "dynamic": False,
                    },
                    {
                        "source": ROUTE_A,
                        "target": JOB_HANDLER,
                        "declared_target": JOB_HANDLER,
                        "relation_type": "route_invokes_job",
                        "resolution": "same_root",
                        "component_name": "cTalendJob_1",
                        "context_name": "DEV",
                    },
                ],
                "unresolved": [
                    {
                        "source": JOB_STAGE,
                        "target": "",
                        "declared_target": "Ghost",
                        "relation_type": "job_calls_job",
                        "resolution": "unresolved",
                        "component_name": "",
                        "dynamic": False,
                    }
                ],
                "roots": [JOB_A, ROUTE_A, ROUTE_MON, JOB_NO_DESIGN],
                "leaves": [JOB_STAGE, JOB_HANDLER, ROUTE_MON, JOB_NO_DESIGN],
                "routine_links": {
                    JOB_A: [
                        {
                            "routine_id": "PROJ/A::r1",
                            "name": "Utils",
                            "resolution": "same_root",
                            "path": "code/routines/Utils_0.1.item",
                            "source_root": "PROJ/A",
                        }
                    ]
                },
                "unresolved_routine_refs": {
                    JOB_A: [{"name": "Missing", "resolution": "unresolved", "candidates": []}]
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (facts / "interfaces.json").write_text(
        json.dumps(
            {"schema_version": "exact-interfaces-1", "by_unit": by_unit, "channels": channels},
            indent=2,
        ),
        encoding="utf-8",
    )
    (facts / "configuration.json").write_text(
        json.dumps(
            {
                "schema_version": "exact-configuration-1",
                "context_groups": [
                    {
                        "key": "ORD",
                        "name": "ORD",
                        "source_root": "PROJ/A",
                        "source_role": "execution",
                        "environments": ["DEV", "PROD"],
                        "variables": [
                            # Marker-only redaction: NO descriptor. The verified real shape.
                            {
                                "name": "token",
                                "type": "id_String",
                                "values": {"DEV": "[REDACTED]", "PROD": "[REDACTED]"},
                                "redacted_environments": [],
                            },
                            # Descriptor redaction with differing descriptors.
                            {
                                "name": "baseUrl",
                                "type": "id_String",
                                "values": {"DEV": "[URL_REDACTED]", "PROD": "[URL_REDACTED]"},
                                "redacted_environments": ["DEV", "PROD"],
                                "redaction_descriptors": {
                                    "DEV": {"scheme": "https", "tls": True},
                                    "PROD": {"scheme": "http", "tls": False},
                                },
                            },
                            {
                                "name": "tokenId",
                                "type": "id_String",
                                "values": {"DEV": CLEAR_VALUE_DEV, "PROD": CLEAR_VALUE_PROD},
                                "redacted_environments": [],
                            },
                            {
                                "name": "batchSize",
                                "type": "id_Integer",
                                "values": {"DEV": "10", "PROD": "10"},
                                "redacted_environments": [],
                            },
                            {
                                "name": "emptyVar",
                                "type": "id_String",
                                "values": {"DEV": "", "PROD": ""},
                                "redacted_environments": [],
                            },
                            {
                                "name": "apiSecret",
                                "type": "id_String",
                                "values": {"DEV": SECRET_VALUE, "PROD": SECRET_VALUE},
                                "redacted_environments": ["DEV", "PROD"],
                            },
                        ],
                        "used_by": [JOB_A],
                        "metadata": {},
                    }
                ],
                "inline_contexts": {ROUTE_A: designs[ROUTE_A]["context_groups"]},
                "job_context_links": {
                    JOB_A: [
                        {"context_key": "ORD", "context_id": "_ord", "resolution": "repository_id"}
                    ]
                },
                "unresolved_context_refs": {
                    JOB_STAGE: [{"context_id": "_dead", "resolution": "unresolved"}]
                },
                "repository_connections": [
                    {
                        "name": "PROJ/B::OrdersDB",
                        "connection_type": "Microsoft SQL Server",
                        "source_root": "PROJ/B",
                        "used_by": [JOB_A],
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    mappings = facts / "mappings"
    mappings.mkdir(exist_ok=True)
    (mappings / f"{JOB_A}__tMap_1.md").write_text(
        f"# Exact mapping table {TABLE_A}\n\nDesign: `{JOB_A}`; mapper: `tMap_1` (tmap).\n",
        encoding="utf-8",
    )
    (mappings / "unknown__map.xslt.md").write_text(
        f"# Exact mapping table {TABLE_XSLT}\n\nDesign: ``; mapper: `map.xslt` (xslt).\n",
        encoding="utf-8",
    )

    scan = root / "scan"
    accounting = [
        {
            "unit_id": JOB_A,
            "component_name": "tMap_1",
            "mapper_kind": "tmap",
            "offered_fields": 6,
            "offered_enumerable": True,
            "unenumerable_inputs": [],
            "written_fields": 9,
            "mapped_from_input": 4,
            "constant_or_generated": 1,
            "lookup_derived": 1,
            "dynamic_unresolved": 2,
            "unwritten_offered": ["row1.dropped"],
            "lookup_tables": [
                {
                    "name": "row2",
                    "match_model": "UNIQUE_MATCH",
                    "load_model": "LOAD_ONCE",
                    "inner_join": "true",
                    "expression_filter": "row2.active == true",
                }
            ],
            "filters": ["row1.active == true"],
            "reject_outputs": ["Reject"],
            "rows": [
                {
                    "output_table": "out1",
                    "output_field": "id",
                    "expression": "row1.id",
                    "classification": "direct_map",
                    "source_refs": ["row1.id"],
                    "dynamic_refs": [],
                },
                {
                    "output_table": "out1",
                    "output_field": "name",
                    "expression": "row1.nm",
                    "classification": "direct_map",
                    "source_refs": ["row1.nm"],
                    "dynamic_refs": [],
                },
                {
                    "output_table": "out1",
                    "output_field": "kind",
                    "expression": '"LOC"',
                    "classification": "constant",
                    "source_refs": [],
                    "dynamic_refs": [],
                },
                {
                    "output_table": "out1",
                    "output_field": "flag",
                    "expression": 'row1.a > 0 ? "y" : "n"',
                    "classification": "expression",
                    "source_refs": ["row1.a"],
                    "dynamic_refs": [],
                },
                {
                    "output_table": "out1",
                    "output_field": "total",
                    "expression": "row1.a + row1.b",
                    "classification": "expression",
                    "source_refs": ["row1.a", "row1.b"],
                    "dynamic_refs": [],
                },
                {
                    "output_table": "out1",
                    "output_field": "token",
                    "expression": "context.tokenId",
                    "classification": "dynamic",
                    "source_refs": [],
                    "dynamic_refs": ["context.tokenId"],
                },
                {
                    "output_table": "out1",
                    "output_field": "weird",
                    "expression": "???",
                    "classification": "unresolved",
                    "source_refs": [],
                    "dynamic_refs": [],
                },
                {
                    "output_table": "out1",
                    "output_field": "code",
                    "expression": "row2.code",
                    "classification": "direct_map",
                    "source_refs": ["row2.code"],
                    "dynamic_refs": [],
                },
                {
                    "output_table": "out1",
                    "output_field": "blank",
                    "expression": "",
                    "classification": "constant",
                    "source_refs": [],
                    "dynamic_refs": [],
                },
                {
                    "output_table": "Reject",
                    "output_field": "id",
                    "expression": "row1.id",
                    "classification": "direct_map",
                    "source_refs": ["row1.id"],
                    "dynamic_refs": [],
                },
            ],
            "table_id": TABLE_A,
        },
        {
            "unit_id": "",
            "component_name": "map.xslt",
            "mapper_kind": "xslt",
            "offered_fields": 0,
            "offered_enumerable": False,
            "unenumerable_inputs": ["xslt_input_document_has_no_declared_schema"],
            "written_fields": 1,
            "mapped_from_input": 1,
            "constant_or_generated": 0,
            "lookup_derived": 0,
            "dynamic_unresolved": 0,
            "unwritten_offered": [],
            "lookup_tables": [],
            "filters": [],
            "reject_outputs": [],
            "rows": [
                {
                    "output_table": "/",
                    "output_field": "Root/Name",
                    "expression": "/root/name",
                    "classification": "expression",
                    "source_refs": ["/root/name"],
                    "dynamic_refs": [],
                }
            ],
            "table_id": TABLE_XSLT,
        },
    ]
    (scan / "mapping_accounting.json").write_text(
        json.dumps(accounting, indent=2), encoding="utf-8"
    )
    (scan / "lineage.json").write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "source_system": "",
                        "source_table": "row1",
                        "source_column": "id",
                        "transformations": [
                            {
                                "transformation_type": "direct_map",
                                "expression": "row1.id",
                                "human_explanation": "",
                            }
                        ],
                        "target_table": "out1",
                        "target_column": "id",
                        "job_name": JOB_A,
                        "confidence": "certain",
                        "source_component": "tDBInput_1",
                        "target_component": "tRunJob_1",
                        "resolved": True,
                        "dynamic_refs": [],
                        "source_job_name": "",
                    },
                    {
                        "source_system": "",
                        "source_table": "row7",
                        "source_column": "x",
                        "transformations": [
                            {
                                "transformation_type": "direct_map",
                                "expression": "row7.x",
                                "human_explanation": "",
                            }
                        ],
                        "target_table": "out9",
                        "target_column": "x",
                        "job_name": JOB_STAGE,
                        "confidence": "likely",
                        "source_component": "tMap_1",
                        "target_component": "tFileOutputDelimited_1",
                        "resolved": False,
                        "dynamic_refs": [],
                        "source_job_name": "",
                    },
                ],
                "unmapped_columns": [
                    {
                        "job_name": JOB_STAGE,
                        "table": "tHMap_1",
                        "column": "",
                        "reason": "TDM map does not expose resolved component wiring",
                        "mapping_type": "TDM",
                    }
                ],
                "summary": {},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (scan / "routines.json").write_text(
        json.dumps(
            [
                {
                    "name": "Utils",
                    "routine_id": "PROJ/A::r1",
                    "source_root": "PROJ/A",
                    "path": "code/routines/Utils_0.1.item",
                    "is_system": False,
                }
            ]
        ),
        encoding="utf-8",
    )
    (scan / "services.json").write_text(
        json.dumps(
            [
                {
                    "name": "ordersService",
                    "path": "services/ordersService_0.1.item",
                    "source_root": "PROJ/A",
                    "operations": [{"operation": "getOrders"}],
                    "parse_outcome": "partial",
                }
            ]
        ),
        encoding="utf-8",
    )
    (scan / "xslt.json").write_text(
        json.dumps(
            [
                {
                    "name": "map.xslt",
                    "path": "resources/map.xslt",
                    "source_root": "PROJ/A",
                    "referenced_by": [JOB_STAGE],
                }
            ]
        ),
        encoding="utf-8",
    )
    (scan / "connections.json").write_text(
        json.dumps(
            [
                {
                    "xmi_id": "_c1",
                    "name": "PROJ/B::OrdersDB",
                    "connection_type": "Microsoft SQL Server",
                    "host": "[HOST_REDACTED]",
                    "port": "context.dbPort",
                    "database": "orders",
                    "schema_name": "",
                    "file_path": "",
                    "additional_params": {"username": "context.dbUser", "source_root": "PROJ/B"},
                    "host_redaction": {"host_present": True},
                    "additional_params_redactions": {},
                }
            ]
        ),
        encoding="utf-8",
    )

    if runtime is not None:
        (root / "runtime").mkdir(exist_ok=True)
        payload = {
            "schema_version": 1,
            "window_days": 90,
            "mode": runtime,
            "partial": False,
            "scope_mismatch": False,
            "requested_units": len(units),
            "batches": 1,
            "failed_batches": 0,
            "source_key": "tmc:tenant",
            "by_unit": {},
            "unmatched_units": sorted(units),
            "unretrieved_units": [],
            "orphan_runtime_jobs": [],
            "alias_conflicts": [],
            "read_environments": ["PROD"],
            "shared_read": False,
            "shared_from": [],
            "redaction_flags": [],
        }
        if runtime == "data":
            payload["by_unit"] = {
                JOB_A: {
                    "unit_name": JOB_A,
                    "job_name": JOB_A,
                    "source_key": "tmc:tenant",
                    "resolved_by": "job_name",
                    "environments": ["PROD"],
                    "total_runs": 10,
                    "success_runs": 9,
                    "failed_runs": 1,
                    "unfinished_runs": 0,
                    "failure_rate": 0.1,
                    "current_failure_streak": 0,
                    "first_run_at": "2026-08-01T00:00:00Z",
                    "last_run_at": "2026-09-01T00:00:00Z",
                    "runs_per_day": 0.3,
                    "p50_duration_ms": 1200,
                    "p95_duration_ms": 4000,
                    "max_duration_ms": 9000,
                    "plans": ["nightly"],
                    "recent_failures": [
                        {
                            "failed_at": "2026-08-20T01:00:00Z",
                            "error_summary": "timeout",
                            "occurrences": 1,
                        }
                    ],
                    "matched_alias": JOB_A,
                    "alias_kind": "unit_name",
                    "gap_p25_seconds": 86000.0,
                    "gap_p50_seconds": 86400.0,
                    "gap_p75_seconds": 86800.0,
                    "gap_sample_size": 9,
                    "log_quality": {},
                    "incident_insights": [],
                }
            }
            payload["unmatched_units"] = sorted(set(units) - {JOB_A, ROUTE_MON})
            payload["unretrieved_units"] = [ROUTE_MON]
            payload["orphan_runtime_jobs"] = ["Legacy_Nightly"]
        (root / "runtime" / "evidence.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
    return root


def _rows(root: Path, name: str) -> list[dict]:
    rows: list[dict] = []
    for shard in sorted((root / INDEX_DIR).glob(f"{name}*.jsonl")):
        rows.extend(json.loads(line) for line in shard.read_text(encoding="utf-8").splitlines())
    return rows


def _manifest(root: Path) -> dict:
    return json.loads((root / INDEX_MANIFEST).read_text(encoding="utf-8"))


def _digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted((root / INDEX_DIR).rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = stage_workspace(tmp_path / "staging")
    build_indexes(root)
    return root


# ---------------------------------------------------------------------------
# Shape and manifest
# ---------------------------------------------------------------------------


class TestManifestAndShape:
    def test_returns_rows_per_index_and_writes_every_index_the_prd_names(
        self, tmp_path: Path
    ) -> None:
        root = stage_workspace(tmp_path / "s")
        counts = build_indexes(root)
        assert tuple(counts) == INDEX_ORDER
        assert all(counts[name] > 0 for name in INDEX_ORDER), counts
        for name in INDEX_ORDER:
            assert (root / INDEX_DIR / f"{name}.jsonl").is_file()
        manifest = _manifest(root)
        assert [entry["name"] for entry in manifest["indexes"]] == list(INDEX_ORDER)
        for entry in manifest["indexes"]:
            assert entry["rows"] == counts[entry["name"]]
            assert entry["present"] is True
            assert entry["shards"] == [f"index/{entry['name']}.jsonl"]
            assert entry["resolves_to"]

    def test_every_line_is_one_complete_record_with_an_index_field(self, workspace: Path) -> None:
        for name in INDEX_ORDER:
            for shard in (workspace / INDEX_DIR).glob(f"{name}*.jsonl"):
                for line in shard.read_text(encoding="utf-8").splitlines():
                    record = json.loads(line)
                    assert record["index"] == name
                    assert record["state"] in {state.value for state in FactState}

    def test_manifest_states_what_is_not_built_and_where_it_lives(self, workspace: Path) -> None:
        manifest = _manifest(workspace)
        assert set(manifest["not_built"]) == {"units", "interfaces", "integrations"}
        assert "facts/navigation/units" in manifest["not_built"]["units"]
        assert "notes/integration_model.json" in manifest["not_built"]["integrations"]
        assert set(manifest["states"]) == {state.value for state in FactState}
        assert manifest["relations"]["invokes-routine"]["inverse"] == "used-by"

    def test_manifest_records_inputs_read_absent_and_notices(self, workspace: Path) -> None:
        manifest = _manifest(workspace)
        assert "facts/units.json" in manifest["inputs"]["read"]
        assert "scan/tdm_maps.json" in manifest["inputs"]["absent"]
        assert manifest["inputs"]["units"] == 6
        assert manifest["inputs"]["designs_indexed"] == 5
        assert any(JOB_NO_DESIGN in notice for notice in manifest["notices"])

    def test_every_evidence_path_is_inside_a_safe_root(self, workspace: Path) -> None:
        for name in INDEX_ORDER:
            for row in _rows(workspace, name):
                for key in ("evidence", "mapping_table"):
                    value = row.get(key)
                    if value:
                        assert value.split("/")[0] in {"scan", "facts", "runtime"}, (name, value)
                        assert ".." not in value

    def test_a_rebuild_replaces_the_previous_index_directory(self, workspace: Path) -> None:
        stale = workspace / INDEX_DIR / "stale.jsonl"
        stale.write_text("{}\n", encoding="utf-8")
        build_indexes(workspace)
        assert not stale.exists()


class TestSharding:
    def test_shards_split_on_bytes_and_keep_the_prd_filename_first(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = stage_workspace(tmp_path / "s")
        monkeypatch.setattr(index_builder, "INDEX_SHARD_BYTES", 2_000)
        build_indexes(root)
        manifest = _manifest(root)
        relationships = next(e for e in manifest["indexes"] if e["name"] == "relationships")
        assert len(relationships["shards"]) > 1
        assert relationships["shards"][0] == "index/relationships.jsonl"
        assert relationships["shards"][1] == "index/relationships.part_002.jsonl"
        for shard in relationships["shards"]:
            text = (root / shard).read_text(encoding="utf-8")
            assert text.endswith("\n")
            for line in text.splitlines():
                json.loads(line)  # never a split row
        total = sum(
            len((root / s).read_text(encoding="utf-8").splitlines())
            for s in relationships["shards"]
        )
        assert total == relationships["rows"]
        assert manifest["shard_bytes"] == 2_000

    def test_a_glob_on_the_prd_name_reaches_every_shard(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = stage_workspace(tmp_path / "s")
        monkeypatch.setattr(index_builder, "INDEX_SHARD_BYTES", 2_000)
        counts = build_indexes(root)
        assert len(_rows(root, "relationships")) == counts["relationships"]


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_two_builds_of_one_tree_are_byte_identical(self, tmp_path: Path) -> None:
        root = stage_workspace(tmp_path / "s")
        build_indexes(root)
        first = _digest(root)
        build_indexes(root)
        assert _digest(root) == first

    def test_byte_stable_across_processes_with_different_hash_seeds(self, tmp_path: Path) -> None:
        """A same-process rebuild cannot catch set-iteration order; this does."""

        probe = (
            "import hashlib, sys\n"
            "from pathlib import Path\n"
            "from tests.test_agent_workspace import test_index_builder as t\n"
            f"root = t.stage_workspace(Path({str(tmp_path)!r}) / 'seed' / sys.argv[1])\n"
            "t.build_indexes(root)\n"
            "print(t._digest(root))\n"
        )
        digests = set()
        for seed in ("0", "1", "524287"):
            env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(ROOT)}
            result = subprocess.run(
                [sys.executable, "-c", probe, seed],
                capture_output=True,
                text=True,
                cwd=ROOT,
                env=env,
                check=False,
            )
            assert result.returncode == 0, result.stderr
            digests.add(result.stdout.strip())
        assert len(digests) == 1, digests


# ---------------------------------------------------------------------------
# candidates (8.1)
# ---------------------------------------------------------------------------


class TestCandidates:
    def test_a_30ms_timer_is_a_scheduled_job_candidate_with_its_literal_period(
        self, workspace: Path
    ) -> None:
        scheduled = [
            r for r in _rows(workspace, "candidates") if r["candidate_kind"] == "scheduled-job"
        ]
        by_component = {(r["unit_id"], r["component_name"]): r for r in scheduled}
        literal = by_component[(ROUTE_A, "cTimer_1")]
        assert literal["detail"]["period"] == "30"
        assert literal["detail"]["period_unit"] == "milliseconds"
        assert literal["state"] == "exact"
        assert literal["evidence"] == f"scan/jobs/{GROUP}/{ROUTE_A}.json"
        dynamic = by_component[(ROUTE_A, "cTimer_2")]
        assert dynamic["detail"]["period"] == "context.period"
        assert dynamic["state"] == "dynamic"

    def test_candidate_kinds_cover_the_prd_list_where_the_fixture_has_evidence(
        self, workspace: Path
    ) -> None:
        kinds = {r["candidate_kind"] for r in _rows(workspace, "candidates")}
        assert {
            "entry-job",
            "scheduled-job",
            "event-publisher",
            "event-consumer",
            "api-client",
            "api-service",
            "database-operation",
            "cross-project-call",
            "outbound-interface",
            "runtime-observed-workload",
        } <= kinds

    def test_a_candidate_never_asserts_an_integration(self, workspace: Path) -> None:
        for row in _rows(workspace, "candidates"):
            assert "integration" not in json.dumps(row).casefold()
        assert "starting point" in _manifest(workspace)["how_to_read"]

    def test_in_memory_channels_are_wiring_not_candidates(self, workspace: Path) -> None:
        rows = _rows(workspace, "candidates")
        assert not any(r["detail"].get("channel_type") == "direct" for r in rows)
        publishers = [r for r in rows if r["candidate_kind"] == "event-publisher"]
        assert {r["detail"]["channel_id"] for r in publishers} == {
            "channel:jms:orders.out",
            "channel:jms:orders.errors",
        }

    def test_cross_project_call_names_the_other_source_root(self, workspace: Path) -> None:
        rows = [
            r for r in _rows(workspace, "candidates") if r["candidate_kind"] == "cross-project-call"
        ]
        assert [
            (r["unit_id"], r["detail"]["target"], r["detail"]["target_source_root"]) for r in rows
        ] == [(ROUTE_A, JOB_HANDLER, "PROJ/B")]

    def test_runtime_observed_workloads_include_the_orphan_as_unresolved(
        self, workspace: Path
    ) -> None:
        rows = {
            r["unit_id"]: r
            for r in _rows(workspace, "candidates")
            if r["candidate_kind"] == "runtime-observed-workload"
        }
        assert rows[JOB_A]["state"] == "exact" and rows[JOB_A]["detail"]["scanned"] is True
        assert (
            rows["Legacy_Nightly"]["state"] == "unresolved"
            and rows["Legacy_Nightly"]["detail"]["scanned"] is False
        )

    def test_a_unit_without_a_staged_design_is_still_a_candidate_from_facts(
        self, workspace: Path
    ) -> None:
        rows = [r for r in _rows(workspace, "candidates") if r["unit_id"] == JOB_NO_DESIGN]
        assert [r["candidate_kind"] for r in rows] == ["entry-job"]
        assert rows[0]["evidence"] == "facts/units.json"


# ---------------------------------------------------------------------------
# relationships (8.2)
# ---------------------------------------------------------------------------


class TestRelationships:
    def test_every_forward_row_has_exactly_one_reverse_with_the_same_evidence_ref(
        self, workspace: Path
    ) -> None:
        rows = _rows(workspace, "relationships")
        forward = [r for r in rows if r["direction"] == "forward"]
        reverse = [r for r in rows if r["direction"] == "reverse"]
        assert len(forward) == len(reverse) == len(rows) / 2
        reverse_keys = {
            (r["source_id"], r["relation"], r["target_id"], r["evidence_ref"], r["state"])
            for r in reverse
        }
        for row in forward:
            expected = (
                row["target_id"],
                RELATION_INVERSE[row["relation"]],
                row["source_id"],
                row["evidence_ref"],
                row["state"],
            )
            assert expected in reverse_keys, row

    def test_the_prd_relation_pairs_are_registered_with_distinct_inverses(self) -> None:
        for forward, inverse in (
            ("calls", "called-by"),
            ("reads-from", "read-by"),
            ("writes-to", "written-by"),
            ("maps-with", "mapped-by"),
            ("configured-by", "configures"),
            ("queries", "queried-by"),
            ("routes-errors-to", "handles-errors-for"),
            ("monitored-by", "monitors"),
            ("invokes-routine", "used-by"),
        ):
            assert RELATION_INVERSE[forward] == inverse
        assert "publishes-to" in RELATION_INVERSE and "consumed-by" in RELATION_INVERSE.values()
        assert (
            "implements-interface" in RELATION_INVERSE and "runtime-observed-as" in RELATION_INVERSE
        )
        assert not set(RELATION_INVERSE) & set(RELATION_INVERSE.values())

    def test_an_unregistered_relation_cannot_be_emitted(self) -> None:
        with pytest.raises(WorkspaceUnavailableError, match="no registered inverse") as caught:
            index_builder._relationship(
                source_id="a",
                source_kind="unit",
                relation="teleports-to",
                target_id="b",
                target_kind="unit",
                evidence_ref="x",
                evidence="facts/units.json",
                state=FactState.EXACT,
            )
        assert caught.value.stage == "navigation"

    def test_routines_get_used_by_rows_resolved_and_unresolved(self, workspace: Path) -> None:
        rows = _rows(workspace, "relationships")
        used_by = {r["source_id"]: r for r in rows if r["relation"] == "used-by"}
        assert used_by["routine:Utils"]["target_id"] == JOB_A
        assert used_by["routine:Utils"]["state"] == "exact"
        assert used_by["routine:Missing"]["state"] == "unresolved"
        assert used_by["routine:Missing"]["source_kind"] == "unresolved-routine"

    def test_calls_and_called_by_include_the_unresolved_call(self, workspace: Path) -> None:
        rows = _rows(workspace, "relationships")
        calls = {(r["source_id"], r["target_id"]): r for r in rows if r["relation"] == "calls"}
        assert calls[(JOB_A, JOB_STAGE)]["state"] == "exact"
        assert calls[(ROUTE_A, JOB_HANDLER)]["detail"]["relation_type"] == "route_invokes_job"
        assert calls[(JOB_STAGE, "Ghost")]["state"] == "unresolved"
        assert calls[(JOB_STAGE, "Ghost")]["target_kind"] == "unresolved-unit"
        called_by = {(r["source_id"], r["target_id"]) for r in rows if r["relation"] == "called-by"}
        assert (JOB_STAGE, JOB_A) in called_by and ("Ghost", JOB_STAGE) in called_by

    def test_error_paths_resolve_to_components_and_to_the_invoked_handler_unit(
        self, workspace: Path
    ) -> None:
        rows = [r for r in _rows(workspace, "relationships") if r["relation"] == "routes-errors-to"]
        component_level = {
            (r["source_id"], r["target_id"]): r for r in rows if r["source_kind"] == "component"
        }
        connector = component_level[(f"component:{JOB_A}/tRunJob_1", f"component:{JOB_A}/tDie_1")]
        assert connector["detail"]["connector"] == "SUBJOB_ERROR" and connector["state"] == "exact"
        unit_level = {
            (r["source_id"], r["target_id"]): r for r in rows if r["source_kind"] == "unit"
        }
        # The route's exception handler: cOnException -> cDirect_1 (toCollector)
        # -> intra-unit hop -> cDirect_2 -> cSetHeader_1 -> cTalendJob_1 -> handler job.
        via_call = unit_level[(ROUTE_A, JOB_HANDLER)]
        assert via_call["detail"]["mechanism"] == "call"
        assert via_call["detail"]["via"] == "cOnException_1"
        assert via_call["state"] == "exact"
        # ... and -> cJMS_2 (orders.errors topic) consumed by the monitor route.
        via_topic = unit_level[(ROUTE_A, ROUTE_MON)]
        assert via_topic["detail"]["mechanism"] == "channel:jms:orders.errors"
        handles = {
            (r["source_id"], r["target_id"])
            for r in _rows(workspace, "relationships")
            if r["relation"] == "handles-errors-for"
        }
        assert (JOB_HANDLER, ROUTE_A) in handles and (ROUTE_MON, ROUTE_A) in handles

    def test_a_context_local_channel_never_joins_two_standalone_routes(
        self, tmp_path: Path
    ) -> None:
        """Two routes with their own ``direct:toCollector`` are two channels."""

        root = stage_workspace(tmp_path / "s")
        interfaces = json.loads((root / "facts" / "interfaces.json").read_text(encoding="utf-8"))
        interfaces["channels"].append(_channel(ROUTE_MON, "cDirect_7", '"toCollector"', "input"))
        interfaces["by_unit"][ROUTE_MON].append(_by_unit_channel(interfaces["channels"][-1]))
        (root / "facts" / "interfaces.json").write_text(json.dumps(interfaces), encoding="utf-8")
        build_indexes(root)
        handlers = {
            (r["source_id"], r["target_id"], r["detail"]["mechanism"])
            for r in _rows(root, "relationships")
            if r["relation"] == "routes-errors-to"
            and r["source_kind"] == "unit"
            and r["target_kind"] == "unit"
        }
        assert (ROUTE_A, ROUTE_MON, "channel:direct:toCollector") not in handlers
        assert (ROUTE_A, ROUTE_MON, "channel:jms:orders.errors") in handlers
        publishes = [
            r
            for r in _rows(root, "relationships")
            if r["relation"] == "publishes-to" and r["target_id"] == "channel:direct:toCollector"
        ]
        assert all(r["detail"]["context_local"] is True for r in publishes)

    def test_an_unnamed_channel_gets_a_component_local_identity(self, workspace: Path) -> None:
        rows = [
            r
            for r in _rows(workspace, "relationships")
            if r["relation"] == "publishes-to" and r["detail"]["component_name"] == "cDirect_3"
        ]
        assert [r["target_id"] for r in rows] == [f"channel:direct:{ROUTE_A}/cDirect_3"]
        assert rows[0]["state"] == "not-observed"

    def test_a_dynamic_channel_binding_is_dynamic_even_when_its_text_is_a_placeholder(
        self, workspace: Path
    ) -> None:
        rows = [
            r
            for r in _rows(workspace, "relationships")
            if r["detail"].get("component_name") == "cJMS_9" and r["direction"] == "forward"
        ]
        assert rows and rows[0]["state"] == "dynamic" and rows[0]["relation"] == "exchanges-with"
        assert rows[0]["detail"]["binding"] == "dynamic"

    def test_interfaces_become_typed_relationships(self, workspace: Path) -> None:
        rows = {
            (r["source_id"], r["relation"], r["target_id"])
            for r in _rows(workspace, "relationships")
            if r["direction"] == "forward"
        }
        assert (JOB_A, "reads-from", 'rest:context.baseUrl + "/locations"') in rows  # GET client
        assert (JOB_A, "queries", "database:PROJ/B::OrdersDB") in rows
        assert (JOB_STAGE, "writes-to", "file:/out/orders.csv") in rows
        assert (ROUTE_A, "publishes-to", "channel:jms:orders.out") in rows
        assert (ROUTE_MON, "consumes", "channel:jms:orders.errors") in rows
        assert not any(t.startswith("route_bridge") for _, _, t in rows)

    def test_configuration_and_mapping_relationships(self, workspace: Path) -> None:
        rows = {
            (r["source_id"], r["relation"], r["target_id"]): r
            for r in _rows(workspace, "relationships")
            if r["direction"] == "forward"
        }
        assert rows[(JOB_A, "configured-by", "context:ORD")]["state"] == "exact"
        assert rows[(JOB_STAGE, "configured-by", "context:_dead")]["state"] == "unresolved"
        assert (
            rows[(JOB_A, "configured-by", "connection:PROJ/B::OrdersDB")]["target_kind"]
            == "repository-connection"
        )
        maps = rows[(JOB_A, "maps-with", f"mapping:{TABLE_A}")]
        assert maps["evidence"] == f"facts/mappings/{JOB_A}__tMap_1.md"
        assert (JOB_STAGE, "maps-with", "stylesheet:map.xslt") in rows

    def test_monitoring_and_runtime_relationships(self, workspace: Path) -> None:
        rows = {
            (r["source_id"], r["relation"], r["target_id"]): r
            for r in _rows(workspace, "relationships")
            if r["direction"] == "forward"
        }
        monitor = rows[(JOB_A, "monitored-by", f"component:{JOB_A}/tLogCatcher_1")]
        assert monitor["detail"]["catches"] == ["java_exception"]
        observed = rows[(JOB_A, "runtime-observed-as", f"runtime:tmc:tenant:{JOB_A}")]
        assert observed["evidence"] == "runtime/evidence.json"

    def test_names_differing_by_one_letter_stay_separate(self, workspace: Path) -> None:
        rows = _rows(workspace, "relationships")
        a = {r["relation"] for r in rows if r["source_id"] == JOB_A}
        b = {r["relation"] for r in rows if r["source_id"] == ROUTE_A}
        assert "queries" in a and "queries" not in b
        assert "publishes-to" in b and "publishes-to" not in a


# ---------------------------------------------------------------------------
# fields (8.3)
# ---------------------------------------------------------------------------


class TestFields:
    def test_the_lineage_kinds_are_distinguished(self, workspace: Path) -> None:
        rows = {
            (r["output_table"], r["output_field"], r["lineage_kind"]): r
            for r in _rows(workspace, "fields")
            if r["unit_id"] == JOB_A
        }
        assert ("out1", "id", "direct") in rows
        assert ("out1", "name", "rename") in rows
        assert ("out1", "kind", "constant") in rows
        assert ("out1", "flag", "conditional") in rows
        assert ("out1", "total", "calculation") in rows
        assert ("out1", "token", "dynamic") in rows and rows[("out1", "token", "dynamic")][
            "state"
        ] == "dynamic"
        assert ("out1", "weird", "unresolved") in rows and rows[("out1", "weird", "unresolved")][
            "state"
        ] == "unresolved"
        assert ("out1", "code", "lookup") in rows
        assert ("out1", "blank", "default") in rows and rows[("out1", "blank", "default")][
            "state"
        ] == "partial"

    def test_dropped_fields_and_filters_are_rows_not_silence(self, workspace: Path) -> None:
        rows = [r for r in _rows(workspace, "fields") if r["unit_id"] == JOB_A]
        dropped = [r for r in rows if r["lineage_kind"] == "dropped"]
        assert [r["source_fields"] for r in dropped] == [["row1.dropped"]]
        assert dropped[0]["state"] == "exact"
        filters = {r["expression"]: r for r in rows if r["lineage_kind"] == "filter"}
        assert (
            "row1.active == true" in filters
            and filters["row1.active == true"]["output_role"] == "filter"
        )
        assert (
            "row2.active == true" in filters
            and filters["row2.active == true"]["output_role"] == "lookup-filter"
        )
        reject = [r for r in rows if r["output_table"] == "Reject"]
        assert reject and reject[0]["output_role"] == "reject"

    def test_rows_resolve_to_the_staged_mapping_table_and_join_lineage_components(
        self, workspace: Path
    ) -> None:
        row = next(
            r
            for r in _rows(workspace, "fields")
            if r["unit_id"] == JOB_A and r["output_field"] == "id" and r["output_table"] == "out1"
        )
        assert row["mapping_table"] == row["evidence"] == f"facts/mappings/{JOB_A}__tMap_1.md"
        assert row["source_component"] == "tDBInput_1" and row["target_component"] == "tRunJob_1"
        assert row["lineage_confidence"] == "certain"

    def test_a_mapper_without_an_implementing_unit_is_partial_and_says_so(
        self, workspace: Path
    ) -> None:
        rows = [r for r in _rows(workspace, "fields") if r["table_id"] == TABLE_XSLT]
        assert rows
        assert all(r["unit_id"] == "" and r["state"] in {"partial", "unresolved"} for r in rows)
        assert any("not enumerable" in r["note"] for r in rows)
        assert any("implementing unit" in r["note"] for r in rows)

    def test_lineage_only_entries_and_unsupported_lineage_are_emitted(
        self, workspace: Path
    ) -> None:
        rows = [r for r in _rows(workspace, "fields") if r["unit_id"] == JOB_STAGE]
        lineage_only = [r for r in rows if r["mapper_kind"] == "lineage"]
        assert (
            lineage_only
            and lineage_only[0]["output_field"] == "x"
            and lineage_only[0]["state"] == "partial"
        )
        tdm = [r for r in rows if r["mapper_kind"] == "tdm"]
        assert tdm and tdm[0]["state"] == "unsupported" and "TDM" in tdm[0]["note"]

    def test_lineage_kinds_are_a_closed_set(self, workspace: Path) -> None:
        kinds = {r["lineage_kind"] for r in _rows(workspace, "fields")}
        assert kinds <= set(index_builder.LINEAGE_KINDS)
        assert set(_manifest(workspace)["lineage_kinds"]) == set(index_builder.LINEAGE_KINDS)


# ---------------------------------------------------------------------------
# configuration (8.4)
# ---------------------------------------------------------------------------


class TestConfiguration:
    def test_a_marker_only_redaction_is_redacted_not_absent(self, workspace: Path) -> None:
        token = next(
            r
            for r in _rows(workspace, "configuration")
            if r["variable"] == "token" and r["scope"] == "repository"
        )
        assert token["value_states"] == {"DEV": "redacted", "PROD": "redacted"}
        assert token["state"] == "redacted"
        assert token["environment_differences"] == "unknown-redacted"

    def test_no_configuration_value_is_ever_written(self, workspace: Path) -> None:
        blob = b"".join(p.read_bytes() for p in (workspace / INDEX_DIR).rglob("*"))
        for value in (SECRET_VALUE, CLEAR_VALUE_DEV, CLEAR_VALUE_PROD):
            assert value.encode("utf-8") not in blob

    def test_environment_differences_are_computed_without_comparing_redacted_text(
        self, workspace: Path
    ) -> None:
        rows = {
            r["variable"]: r
            for r in _rows(workspace, "configuration")
            if r["scope"] == "repository"
        }
        assert rows["baseUrl"]["environment_differences"] == "differs"  # descriptors differ
        assert rows["baseUrl"]["redaction_descriptor"]["DEV"]["tls"] is True
        assert rows["tokenId"]["environment_differences"] == "differs"
        assert rows["batchSize"]["environment_differences"] == "same"
        assert (
            rows["emptyVar"]["environment_differences"] == "same"
            and rows["emptyVar"]["state"] == "not-observed"
        )
        assert rows["apiSecret"]["state"] == "redacted"  # descriptor-only redaction

    def test_consumers_join_on_the_exact_variable_name_not_a_prefix(self, workspace: Path) -> None:
        rows = {
            r["variable"]: r
            for r in _rows(workspace, "configuration")
            if r["scope"] == "repository"
        }
        token_consumers = {
            (c["component_name"], c["param"], c["role"]) for c in rows["token"]["consumers"]
        }
        assert (
            "tRESTClient_1",
            "HEADERS",
            "endpoint",
        ) not in token_consumers  # HEADERS is not an address param
        assert ("tRESTClient_1", "HEADERS", "other") in token_consumers
        assert ("", "context_params", "invocation") in token_consumers
        assert not any(c["component_name"] == "tJava_1" for c in rows["token"]["consumers"])
        token_id_consumers = {
            (c["component_name"], c["role"]) for c in rows["tokenId"]["consumers"]
        }
        assert ("tJava_1", "transformation") in token_id_consumers
        assert ("tMap_1", "transformation") in token_id_consumers
        assert not any(c["component_name"] == "tRESTClient_1" for c in rows["tokenId"]["consumers"])

    def test_consumer_roles_name_endpoints_schedules_and_filters(self, workspace: Path) -> None:
        rows = {
            r["variable"]: r
            for r in _rows(workspace, "configuration")
            if r["scope"] == "repository"
        }
        assert ("tRESTClient_1", "URL", "endpoint") in {
            (c["component_name"], c["param"], c["role"]) for c in rows["baseUrl"]["consumers"]
        }
        inline = next(
            r
            for r in _rows(workspace, "configuration")
            if r["scope"] == "inline" and r["variable"] == "period"
        )
        assert inline["linked_units"] == [ROUTE_A]
        assert {(c["component_name"], c["role"]) for c in inline["consumers"]} == {
            ("cTimer_2", "schedule")
        }

    def test_a_reference_with_no_linked_definition_is_an_unresolved_row(
        self, workspace: Path
    ) -> None:
        rows = [r for r in _rows(workspace, "configuration") if r["kind"] == "context-reference"]
        assert {(r["linked_units"][0], r["variable"]) for r in rows} == {(ROUTE_MON, "topic")}
        assert rows[0]["state"] == "unresolved"

    def test_unresolved_context_links_and_repository_connections(self, workspace: Path) -> None:
        rows = _rows(workspace, "configuration")
        link = next(r for r in rows if r["kind"] == "context-link")
        assert (
            link["linked_units"] == [JOB_STAGE]
            and link["group_key"] == "_dead"
            and link["state"] == "unresolved"
        )
        connection = next(r for r in rows if r["kind"] == "repository-connection")
        assert connection["value_states"]["host"] == "redacted"
        assert connection["value_states"]["port"] == "dynamic"
        assert connection["value_states"]["database"] == "exact"
        assert connection["context_variables_referenced"] == ["dbPort", "dbUser"]
        assert connection["linked_units"] == [JOB_A]
        # States only: the clear database name from scan/connections.json is not copied.
        assert '"orders"' not in json.dumps(connection)


# ---------------------------------------------------------------------------
# runtime (8.5)
# ---------------------------------------------------------------------------


class TestRuntime:
    def test_mirror_status_is_always_the_first_fact(self, workspace: Path) -> None:
        status = [r for r in _rows(workspace, "runtime") if r["kind"] == "mirror-status"]
        assert len(status) == 1 and status[0]["mode"] == "data" and status[0]["state"] == "exact"
        assert status[0]["matched_units"] == 1 and status[0]["orphan_runtime_jobs"] == 1
        assert status[0]["read_environments"] == ["PROD"]

    def test_bindings_distinguish_matched_unmatched_unretrieved_and_runtime_only(
        self, workspace: Path
    ) -> None:
        rows = {(r["kind"], r["unit_id"]): r for r in _rows(workspace, "runtime")}
        assert rows[("unit-binding", JOB_A)]["state"] == "exact"
        assert rows[("unit-binding", JOB_STAGE)]["state"] == "not-observed"
        assert rows[("unit-binding", ROUTE_MON)]["state"] == "unresolved"
        assert rows[("runtime-only-unit", "Legacy_Nightly")]["state"] == "unresolved"

    def test_execution_profile_carries_cadence_and_failures(self, workspace: Path) -> None:
        profile = next(r for r in _rows(workspace, "runtime") if r["kind"] == "execution-profile")
        assert profile["unit_id"] == JOB_A and profile["total_runs"] == 10
        assert isinstance(profile["cadence"], dict) and "label" in profile["cadence"]
        failure = next(r for r in _rows(workspace, "runtime") if r["kind"] == "failure-mode")
        assert failure["error_summary"] == "timeout"

    def test_interface_observations_are_partial_and_say_why(self, workspace: Path) -> None:
        rows = [r for r in _rows(workspace, "runtime") if r["kind"] == "interface-observation"]
        assert rows and all(r["state"] == "partial" and "per-interface" in r["note"] for r in rows)
        assert {r["interface_id"] for r in rows} == {
            "database:PROJ/B::OrdersDB",
            'rest:context.baseUrl + "/locations"',
        }

    def test_static_error_handling_and_monitoring_rows_need_no_mirror(self, tmp_path: Path) -> None:
        root = stage_workspace(tmp_path / "s", runtime=None)
        build_indexes(root)
        rows = _rows(root, "runtime")
        status = next(r for r in rows if r["kind"] == "mirror-status")
        assert status["mode"] == "not-staged" and status["state"] == "not-observed"
        assert (
            next(e for e in _manifest(root)["indexes"] if e["name"] == "runtime")["mirror_mode"]
            == "not-staged"
        )
        handling = {r["unit_id"]: r for r in rows if r["kind"] == "error-handling"}
        assert handling[ROUTE_A]["handler_units"] == [JOB_HANDLER, ROUTE_MON]
        assert handling[JOB_A]["constructs"] == ["SUBJOB_ERROR", "tDie", "tLogCatcher"]
        assert handling[JOB_A]["design_staged"] is True
        monitoring = {
            (r["unit_id"], r["component_name"]): r
            for r in rows
            if r["kind"] == "monitoring-construct"
        }
        assert monitoring[(ROUTE_A, "cLog_1")]["identifier"] == "orders.route"
        assert monitoring[(JOB_A, "tLogCatcher_1")]["catches"] == ["java_exception"]
        header = next(r for r in rows if r["kind"] == "message-header")
        assert header["header"] == "CorrelationId" and header["setting_units"] == [ROUTE_A]

    def test_an_opted_out_mirror_states_itself_per_unit(self, tmp_path: Path) -> None:
        root = stage_workspace(tmp_path / "s", runtime="opted_out")
        build_indexes(root)
        rows = _rows(root, "runtime")
        status = next(r for r in rows if r["kind"] == "mirror-status")
        assert status["mode"] == "opted_out" and status["state"] == "not-observed"
        bindings = [r for r in rows if r["kind"] == "unit-binding"]
        assert len(bindings) == 6 and all("opted_out" in r["note"] for r in bindings)
        assert not [
            r
            for r in _rows(root, "candidates")
            if r["candidate_kind"] == "runtime-observed-workload"
        ]


# ---------------------------------------------------------------------------
# Fail-closed and absence
# ---------------------------------------------------------------------------


class TestFailClosed:
    def test_malformed_required_input_raises_naming_the_artifact_and_writes_nothing(
        self, tmp_path: Path
    ) -> None:
        root = stage_workspace(tmp_path / "s")
        (root / "facts" / "units.json").write_text("{not json", encoding="utf-8")
        with pytest.raises(WorkspaceUnavailableError, match="facts/units.json") as caught:
            build_indexes(root)
        assert caught.value.stage == "navigation"
        assert not (root / INDEX_DIR).exists()

    def test_a_missing_required_input_raises(self, tmp_path: Path) -> None:
        root = stage_workspace(tmp_path / "s")
        (root / "scan" / "mapping_accounting.json").unlink()
        with pytest.raises(WorkspaceUnavailableError, match="mapping_accounting.json"):
            build_indexes(root)

    def test_a_design_named_by_the_fact_table_but_not_staged_raises(self, tmp_path: Path) -> None:
        root = stage_workspace(tmp_path / "s")
        (root / "scan" / "jobs" / GROUP / f"{JOB_STAGE}.json").unlink()
        with pytest.raises(WorkspaceUnavailableError, match=JOB_STAGE):
            build_indexes(root)

    def test_a_malformed_optional_input_raises_rather_than_being_skipped(
        self, tmp_path: Path
    ) -> None:
        root = stage_workspace(tmp_path / "s")
        (root / "scan" / "routines.json").write_text("[", encoding="utf-8")
        with pytest.raises(WorkspaceUnavailableError, match="scan/routines.json"):
            build_indexes(root)

    def test_a_mapping_table_without_its_header_raises(self, tmp_path: Path) -> None:
        root = stage_workspace(tmp_path / "s")
        (root / "facts" / "mappings" / "rogue.md").write_text("# not a table\n", encoding="utf-8")
        with pytest.raises(WorkspaceUnavailableError, match="rogue.md"):
            build_indexes(root)

    def test_an_unsafe_scan_path_in_the_fact_table_raises(self, tmp_path: Path) -> None:
        root = stage_workspace(tmp_path / "s")
        units = json.loads((root / "facts" / "units.json").read_text(encoding="utf-8"))
        units["units"][JOB_A]["scan_path"] = "../../etc/passwd"
        (root / "facts" / "units.json").write_text(json.dumps(units), encoding="utf-8")
        with pytest.raises(WorkspaceUnavailableError, match="unsafe path"):
            build_indexes(root)


class TestAbsence:
    def test_an_index_with_no_rows_is_absent_and_the_manifest_says_so(self, tmp_path: Path) -> None:
        root = stage_workspace(tmp_path / "s", runtime=None)
        for name in ("mapping_accounting.json",):
            (root / "scan" / name).write_text("[]", encoding="utf-8")
        (root / "scan" / "lineage.json").write_text(
            json.dumps({"entries": [], "unmapped_columns": []}), encoding="utf-8"
        )
        for path in (root / "facts" / "mappings").glob("*.md"):
            path.unlink()
        counts = build_indexes(root)
        assert counts["fields"] == 0
        assert not (root / INDEX_DIR / "fields.jsonl").exists()
        entry = next(e for e in _manifest(root)["indexes"] if e["name"] == "fields")
        assert entry["present"] is False and entry["shards"] == []
        assert "fact about the estate" in entry["absent_because"]


# ---------------------------------------------------------------------------
# Vocabulary pins
# ---------------------------------------------------------------------------


def test_error_vocabulary_is_pinned_to_the_deterministic_renderer() -> None:
    """Mirrored, not imported; this is the check that the mirror is current."""

    from mcp_server.providers.custom.enterprise import workspace as enterprise

    assert index_builder.ERROR_COMPONENTS == frozenset(enterprise._ERROR_COMPONENTS)
    assert index_builder.ERROR_CONNECTORS == frozenset(enterprise._ERROR_CONNECTORS)
    assert index_builder.MONITOR_COMPONENTS <= index_builder.ERROR_COMPONENTS


def test_index_doc_covers_every_index_and_nothing_else() -> None:
    assert set(index_builder.INDEX_DOC) == set(INDEX_ORDER)
    assert set(index_builder.RELATION_MEANINGS) == set(RELATION_INVERSE)
