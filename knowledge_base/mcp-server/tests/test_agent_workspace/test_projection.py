"""Slice 1: the source boundary, enforced on bytes rather than on filenames.

The rule the workspace had was "no ``.item``, no ``.properties``, no
``sanitized/``". It was satisfied, and every tJava body in the estate reached
the agent anyway -- ``_stage_designs`` copied the scanner's shards verbatim and
those shards carry ``components[].code`` plus the ``CODE*`` parameters, while
``scan/routines.json`` carries ``source_code``.

The hardest assertion in this module is the one that holds two things apart
that look the same from a distance:

* a tMap ``entries[].expression`` is a parser fact, extracted per field and
  verified by the same redaction pass as everything else. It IS the
  field-level documentation and it STAYS;
* a ``tJava`` body is source text that happened to ride inside the parse. It
  GOES, and its removal is stated where the reader is.

Get that distinction backwards in either direction and the lane is either
unsafe or useless.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mcp_server.config import config
from mcp_server.providers.agent_workspace.contract import (
    CODE_PARAM_NAMES,
    VERBATIM_CODE_FIELDS,
)
from mcp_server.providers.agent_workspace.projection import (
    WITHHELD_KEY,
    WithholdingCensus,
    project_scan_payload,
    verbatim_code_findings,
)
from tests.test_agent_workspace.workspace_fixture import approval_tree, design, write_scan

_BODY = (
    "String host = context.erp_host;\n"
    'globalMap.put("rowCount", count);\n'
    "return routines.Vault.lookup(host);\n"
)


def _component(**overrides):
    component = {
        "unique_name": "tJava_1",
        "component_type": "tJava",
        "active": True,
        "code": _BODY,
        "params": [
            {"name": "CODE", "value": _BODY, "field_type": "MEMO_JAVA"},
            {"name": "QUERY", "value": "SELECT id FROM orders", "field_type": "MEMO_SQL"},
            {"name": "DESTINATION", "value": "queue/orders.in", "field_type": "TEXT"},
        ],
        "schemas": [{"connector": "FLOW", "columns": [{"name": "id", "type": "id_String"}]}],
    }
    component.update(overrides)
    return {"name": "OrdersLoad", "components": [component]}


# --- what goes ---------------------------------------------------------------


class TestCodeIsWithheld:
    def test_every_verbatim_field_is_removed(self) -> None:
        for field in VERBATIM_CODE_FIELDS:
            projected = project_scan_payload({"x": {field: _BODY}})
            assert field not in projected["x"]
            assert projected["x"][WITHHELD_KEY]["withheld"] is True

    def test_every_code_parameter_is_removed_and_settings_stay(self) -> None:
        projected = project_scan_payload(_component())
        params = projected["components"][0]["params"]
        assert [param["name"] for param in params] == ["QUERY", "DESTINATION"]
        assert params[0]["value"] == "SELECT id FROM orders"

    def test_no_line_of_the_body_survives_anywhere(self) -> None:
        """Not a snippet, not a preview, not a first-N-characters prefix."""

        import json

        projected = project_scan_payload(_component())
        serialised = json.dumps(projected)
        for line in _BODY.splitlines():
            assert line.strip() not in serialised

    def test_an_empty_code_field_is_removed_without_a_summary(self) -> None:
        """`code: ""` beside a withholding block reads as "there is no code"."""

        projected = project_scan_payload({"code": "", "params": [{"name": "CODE", "value": ""}]})
        assert "code" not in projected
        assert projected["params"] == []
        assert WITHHELD_KEY not in projected

    def test_every_declared_code_parameter_name_is_covered(self) -> None:
        for name in CODE_PARAM_NAMES:
            projected = project_scan_payload({"params": [{"name": name, "value": _BODY}]})
            assert projected["params"] == []
            assert projected[WITHHELD_KEY]["sources"] == [f"params.{name}"]


# --- what the removal states -------------------------------------------------


class TestTheRemovalIsStated:
    def test_the_summary_carries_size_and_a_digest_of_the_body(self) -> None:
        projected = project_scan_payload({"code": _BODY})
        summary = projected[WITHHELD_KEY]
        assert summary["lines"] == 3
        assert summary["characters"] == len(_BODY)
        assert summary["sha256"] == hashlib.sha256(_BODY.encode("utf-8")).hexdigest()

    def test_the_summary_names_the_identifiers_the_body_touched(self) -> None:
        """Facts, not source text: every one is already elsewhere in the scan."""

        summary = project_scan_payload({"code": _BODY})[WITHHELD_KEY]
        assert summary["references"]["context_variables"] == ["erp_host"]
        assert summary["references"]["global_map_keys"] == ["rowCount"]
        assert summary["references"]["routines"] == ["Vault.lookup"]

    def test_the_summary_keeps_safe_behavior_without_source_fragments(self) -> None:
        body = (
            'try { if (context.enabled) { globalMap.put("rowCount", count); '
            "log.info(secretValue); } } catch (Exception exc) { throw new RuntimeException(); }"
        )
        summary = project_scan_payload({"code": body})[WITHHELD_KEY]

        assert summary["behavior_summary"]["control_flow"] == [
            "conditional",
            "exception_boundary",
            "throws",
        ]
        assert summary["behavior_summary"]["effects"] == [
            "reads_context",
            "writes_shared_state",
            "emits_log_output",
        ]
        assert summary["behavior_summary"]["detail_status"] == "structural_only"
        # No condition, exception message, variable value, or source fragment
        # is copied into the derived summary.
        rendered = str(summary["behavior_summary"])
        for fragment in ("context.enabled", "rowCount", "secretValue", "RuntimeException"):
            assert fragment not in rendered

    def test_absent_reference_kinds_are_stated_as_empty(self) -> None:
        """Absence is a fact; a missing key is an invitation to guess."""

        summary = project_scan_payload({"code": "int x = 1;"})[WITHHELD_KEY]
        assert summary["references"] == {
            "routines": [],
            "context_variables": [],
            "global_map_keys": [],
        }

    def test_a_global_map_key_that_is_not_an_identifier_is_dropped(self) -> None:
        """The one capture that reads a string LITERAL out of the body."""

        body = 'globalMap.put("Bearer abc123def456ghi", x); globalMap.get("ok_key");'
        summary = project_scan_payload({"code": body})[WITHHELD_KEY]
        assert summary["references"]["global_map_keys"] == ["ok_key"]

    def test_the_census_counts_bodies_lines_characters_and_files(self) -> None:
        census = WithholdingCensus()
        project_scan_payload(_component(), census=census)
        project_scan_payload({"routines": [{"source_code": "int a;"}]}, census=census)
        project_scan_payload({"components": [{"component_type": "tMap"}]}, census=census)
        assert census.bodies == 2
        assert census.characters == len(f"{_BODY}\n{_BODY}") + len("int a;")
        # The payload with no code contributes no artifact.
        assert census.artifacts == 2


# --- what stays --------------------------------------------------------------


class TestParserFactsSurvive:
    def test_a_mapping_expression_is_a_parser_fact_and_is_kept(self) -> None:
        """The distinction the whole slice turns on.

        Removing these would leave the lane unable to answer the question it
        exists for; keeping a tJava body would make it unsafe. They are not
        the same kind of thing and this test is what says so.
        """

        payload = {
            "tmap_data": [
                {
                    "component_name": "tMap_1",
                    "entries": [
                        {
                            "table_name": "out",
                            "column_name": "total",
                            "expression": "row1.qty * row1.price",
                        }
                    ],
                    "var_entries": [
                        {"table_name": "Var", "column_name": "v", "expression": "context.rate"}
                    ],
                }
            ],
            "txmlmap_data": [
                {"entries": [{"column_name": "id", "expression": "row2.id.toUpperCase()"}]}
            ],
        }
        assert project_scan_payload(payload) == payload

    def test_structure_settings_schemas_and_connections_survive(self) -> None:
        projected = project_scan_payload(_component())
        component = projected["components"][0]
        assert component["component_type"] == "tJava"
        assert component["active"] is True
        assert component["schemas"] == [
            {"connector": "FLOW", "columns": [{"name": "id", "type": "id_String"}]}
        ]

    def test_a_payload_with_no_code_is_returned_unchanged(self) -> None:
        payload = {"connections": [{"source": "a", "target": "b"}], "context_groups": []}
        assert project_scan_payload(payload) == payload


# --- determinism and the assertion half --------------------------------------


def test_the_projection_is_byte_stable_across_calls() -> None:
    import json

    first = json.dumps(project_scan_payload(_component()), sort_keys=False)
    second = json.dumps(project_scan_payload(_component()), sort_keys=False)
    assert first == second


class TestFindings:
    def test_a_surviving_body_is_reported_by_path_and_never_by_value(self) -> None:
        findings = verbatim_code_findings(_component(), label="scan/jobs/g/A.json")
        assert findings == [
            "scan/jobs/g/A.json.components[0].code",
            "scan/jobs/g/A.json.components[0].params[0].CODE",
        ]
        assert not any(_BODY.splitlines()[0] in finding for finding in findings)

    def test_a_projected_payload_reports_nothing(self) -> None:
        assert verbatim_code_findings(project_scan_payload(_component()), label="x") == []

    def test_an_empty_body_is_not_a_finding(self) -> None:
        assert verbatim_code_findings({"code": "", "source_code": "   "}, label="x") == []

    def test_a_name_match_outside_params_is_not_a_finding(self) -> None:
        """This check FAILS THE BUILD, so it must mirror the strip exactly.

        `scan/xslt.json` carries `variables: [{name, select}]`, and `CODE` is
        a perfectly ordinary XSLT variable name. The projection leaves it
        alone -- correctly, it is not a code body -- so flagging it here would
        refuse a safe estate with no way for an operator to see why.
        """

        stylesheet = {
            "name": "map.xsl",
            "variables": [{"name": "CODE", "value": "concat($a, $b)"}],
            "params": [{"name": "LOCALE", "value": "nl-NL"}],
        }
        assert verbatim_code_findings(stylesheet, label="scan/xslt.json") == []
        assert project_scan_payload(stylesheet) == stylesheet


# --- the same distinction, on a REAL staged workspace ------------------------
#
# Plan decision 2: complete approved SQL, mapping expressions, XSLT and
# configuration stay available. Verified already true of `projection.py`
# itself, above -- these pin it end to end instead, because the projection is
# one of six things between the scan and the agent and the others (the design
# shards, the estate collections, the oversize splitter, the `_refuse_*`
# assertions) are all free to lose the same data for their own reasons.
#
# The failure this guards against is the one the whole lane exists for: a
# document that says the query was unavailable while the scan held it.

#: A query long enough that "kept" and "truncated to a preview" are different
#: outcomes, and multi-line so a naive whitespace fold would show up.
_SQL = """SELECT o.order_id,
       o.customer_ref,
       SUM(l.quantity * l.unit_price) AS order_total,
       CASE WHEN o.status = 'X' THEN 'cancelled' ELSE 'open' END AS state
  FROM orders o
  JOIN order_lines l ON l.order_id = o.order_id
 WHERE o.updated_at >= ?
   AND o.tenant = context.tenant_code
 GROUP BY o.order_id, o.customer_ref, o.status
 ORDER BY o.order_id"""

#: What the scanner's redaction pass leaves behind. It must survive verbatim:
#: a placeholder that got dropped reads as "there is no password here", and a
#: placeholder that got replaced by a real value is the failure the whole
#: approval boundary exists to prevent.
_PLACEHOLDER = "[PASSWORD_REDACTED]"


@pytest.fixture
def detailed_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A built workspace over a scan carrying SQL, mappings, XSLT and secrets."""

    root = tmp_path / "intodoc-output"
    root.mkdir()
    load = design("OrdersLoad", group="orders")
    load["components"].append(
        {
            "unique_name": "tDBInput_1",
            "component_type": "tDBInput",
            "active": True,
            "declaration": "built_in",
            "params": [
                {"name": "QUERY", "value": _SQL, "field_type": "MEMO_SQL"},
                {"name": "DBNAME", "value": "ORDERS_PRD", "field_type": "TEXT"},
                {"name": "PASS", "value": _PLACEHOLDER, "field_type": "PASSWORD"},
            ],
            "schemas": [
                {
                    "connector": "FLOW",
                    "columns": [
                        {"name": "order_id", "type": "id_String", "length": 32},
                        {"name": "order_total", "type": "id_BigDecimal", "precision": 2},
                    ],
                }
            ],
        }
    )
    load["connections"] = [
        {"source": "tDBInput_1", "target": "tMap_1", "connector_name": "row1", "type": "FLOW"}
    ]
    load["tmap_data"] = [
        {
            "component_name": "tMap_1",
            "input_tables": ["row1"],
            "output_tables": ["out"],
            "entries": [
                {
                    "table_name": "out",
                    "column_name": "total_incl_vat",
                    "expression": (
                        "row1.order_total.multiply(new BigDecimal(context.vat_rate))"
                        ".setScale(2, java.math.RoundingMode.HALF_UP)"
                    ),
                },
                {
                    "table_name": "out",
                    "column_name": "state",
                    "expression": 'row1.state == null ? "unknown" : row1.state.toUpperCase()',
                },
            ],
            "var_entries": [
                {"table_name": "Var", "column_name": "rate", "expression": "context.vat_rate"}
            ],
        }
    ]
    load["txmlmap_data"] = [
        {
            "component_name": "tXMLMap_1",
            "entries": [
                {
                    "table_name": "out",
                    "column_name": "CustomerRef",
                    "expression": "row2.customer.getRef().trim()",
                }
            ],
            "var_entries": [],
        }
    ]
    load["context_groups"] = [
        {
            "name": "Default",
            "variables": [
                {"name": "vat_rate", "value": "0.21", "type": "id_String"},
                {"name": "erp_password", "value": _PLACEHOLDER, "type": "id_Password"},
            ],
        }
    ]

    scan_dir = write_scan(root, designs=[load, design("InvoiceSync", group="invoices")])
    (scan_dir / "scan" / "xslt.json").write_text(
        json.dumps(
            [
                {
                    "name": "orders_to_edifact.xsl",
                    "path": "PROJ/resources/orders_to_edifact.xsl",
                    "templates": [
                        {
                            "match": "/Orders/Order",
                            "select": "concat(Header/Ref, '-', Header/Version)",
                            "applies": ["Line"],
                        }
                    ],
                    "variables": [
                        {"name": "currency", "select": "/Orders/@currency"},
                        {"name": "CODE", "select": "concat($a, $b)"},
                    ],
                    "params": [{"name": "LOCALE", "value": "nl-NL"}],
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    (scan_dir / "scan" / "connections.json").write_text(
        json.dumps(
            [
                {
                    "name": "ORDERS_PRD",
                    "connection_id": "conn-1",
                    "type": "MSSQL",
                    "server": "sql-prd-01.internal",
                    "database": "ORDERS",
                    "username": "svc_orders",
                    "password": _PLACEHOLDER,
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    approval = json.loads((scan_dir / "publication_approval.json").read_text())
    approval["tree"] = approval_tree(scan_dir)
    (scan_dir / "publication_approval.json").write_text(json.dumps(approval, indent=2))

    monkeypatch.setattr(config, "intodoc_output_dir", root)
    monkeypatch.setattr(config, "runtime_evidence_enabled", False)
    target = tmp_path / "detailed-staging"
    from mcp_server.providers.agent_workspace.workspace import build_workspace

    build_workspace(project_id="PROJECT", staging_dir=target, platform="talend", goal="g")
    return target


def _orders_shard(workspace: Path) -> dict:
    return json.loads((workspace / "scan" / "jobs" / "orders" / "OrdersLoad.json").read_text())


class TestApprovedDetailSurvivesTheWholeBuild:
    def test_the_complete_query_is_staged_byte_for_byte(self, detailed_workspace: Path) -> None:
        """Not a preview, not a first-N-characters prefix, not reflowed.

        A prefix of a query reads like the whole query, which is worse than an
        absent one: the agent documents a filter that is not there.
        """

        component = next(
            item
            for item in _orders_shard(detailed_workspace)["components"]
            if item["component_type"] == "tDBInput"
        )
        query = next(param for param in component["params"] if param["name"] == "QUERY")
        assert query["value"] == _SQL
        assert query["value"].count("\n") == _SQL.count("\n")

    def test_mapping_expressions_survive_in_both_mappers(self, detailed_workspace: Path) -> None:
        shard = _orders_shard(detailed_workspace)
        tmap = shard["tmap_data"][0]
        assert [entry["expression"] for entry in tmap["entries"]] == [
            "row1.order_total.multiply(new BigDecimal(context.vat_rate))"
            ".setScale(2, java.math.RoundingMode.HALF_UP)",
            'row1.state == null ? "unknown" : row1.state.toUpperCase()',
        ]
        assert tmap["var_entries"][0]["expression"] == "context.vat_rate"
        assert (
            shard["txmlmap_data"][0]["entries"][0]["expression"] == "row2.customer.getRef().trim()"
        )

    def test_schemas_and_connections_survive(self, detailed_workspace: Path) -> None:
        shard = _orders_shard(detailed_workspace)
        component = next(
            item for item in shard["components"] if item["component_type"] == "tDBInput"
        )
        assert component["schemas"][0]["columns"] == [
            {"name": "order_id", "type": "id_String", "length": 32},
            {"name": "order_total", "type": "id_BigDecimal", "precision": 2},
        ]
        assert shard["connections"] == [
            {
                "source": "tDBInput_1",
                "target": "tMap_1",
                "connector_name": "row1",
                "type": "FLOW",
            }
        ]

    def test_xslt_transformation_detail_survives(self, detailed_workspace: Path) -> None:
        """Including the variable literally called ``CODE``.

        It is an ordinary XSLT variable name, not a code body, and the strip
        is position-aware for exactly this reason. Losing it would silently
        remove a transformation rule from the evidence.
        """

        stylesheets = json.loads((detailed_workspace / "scan" / "xslt.json").read_text())
        template = stylesheets[0]["templates"][0]
        assert template["match"] == "/Orders/Order"
        assert template["select"] == "concat(Header/Ref, '-', Header/Version)"
        assert {variable["name"] for variable in stylesheets[0]["variables"]} == {
            "currency",
            "CODE",
        }

    def test_a_redacted_credential_stays_redacted_and_stays_visible(
        self, detailed_workspace: Path
    ) -> None:
        """Both directions, because only one of them is usually tested.

        The placeholder must still be there -- dropping it makes the staged
        connection read as one with no password, and the documentation then
        says the estate has none. And it must still BE a placeholder: the
        approval boundary exists so no run can turn it back into a value.
        """

        connections = (detailed_workspace / "scan" / "connections.json").read_text()
        assert _PLACEHOLDER in connections
        assert "svc_orders" in connections
        shard = _orders_shard(detailed_workspace)
        component = next(
            item for item in shard["components"] if item["component_type"] == "tDBInput"
        )
        assert [param["value"] for param in component["params"] if param["name"] == "PASS"] == [
            _PLACEHOLDER
        ]
        variables = shard["context_groups"][0]["variables"]
        assert {variable["name"]: variable["value"] for variable in variables} == {
            "vat_rate": "0.21",
            "erp_password": _PLACEHOLDER,
        }

    def test_the_configuration_register_still_states_the_variable_exists(
        self, detailed_workspace: Path
    ) -> None:
        """Withholding a VALUE is not withholding the fact of a setting.

        The Steven failure was documentation asserting configuration was
        unavailable while the scan held it; a register that dropped the row
        along with the secret would reproduce it one layer down.
        """

        register = (detailed_workspace / "output" / "reference" / "configuration.md").read_text()
        assert "vat_rate" in register
        assert "erp_password" in register
