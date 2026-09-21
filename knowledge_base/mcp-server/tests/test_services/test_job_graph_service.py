"""Unit tests for the Level-2 job graph builder (pure transformation layer)."""

from mcp_server.services.job_graph_service import (
    build_job_graph,
    build_param_summary,
    build_param_tables,
    component_display_label,
    derive_component_role,
    explain_component_type,
    explain_connector,
)

#: Verbatim from the real pluripharm scan
#: (`collector_jde_shipping_notification_from_db_c006_mediq`). This one
#: component is the whole reason `param_tables` exists: an agent asked which
#: criterion routes a shipment to the Mediq or the Distrimed flow, opened this
#: component among 15 others, and got nothing back -- because the criterion is a
#: ROW, and a row-shaped param has an EMPTY scalar `value`, so `param_summary`
#: could not carry it in any form.
SET_XML_VAR_COMPONENT = {
    "unique_name": "cSetHeader_5",
    "component_type": "cSetHeader",
    "params": [
        {
            "name": "VALUES",
            "value": "",
            "field_type": "TABLE",
            "rows": [
                {
                    "NAME": '"AN8_CHECK"',
                    "LANGUAGE": "none",
                    "EXPRESSION": (
                        'xpath("/SHIPPING_CONFIRMATION/F470371/Row/SZAN8/text()",String.class)'
                    ),
                }
            ],
        },
        {"name": "USE_NAMESPACES", "value": "false", "field_type": "CHECK", "rows": []},
        {"name": "LABEL", "value": "setXmlVar", "field_type": "TEXT", "rows": []},
    ],
}


def test_role_derivation_by_type_pattern():
    assert derive_component_role("tFileInputDelimited") == "input"
    assert derive_component_role("tRouteInput") == "input"
    assert derive_component_role("tMSSqlOutput") == "output"
    assert derive_component_role("tRouteOutput") == "output"
    assert derive_component_role("tMap") == "transform"
    assert derive_component_role("tExtractJSONFields") == "transform"
    assert derive_component_role("tRunJob") == "orchestration"
    assert derive_component_role("tPrejob") == "orchestration"
    assert derive_component_role("tPostjob") == "orchestration"
    assert derive_component_role("tWaitForFile") == "orchestration"
    assert derive_component_role("tLogRow") == "logging"
    assert derive_component_role("tStatCatcher") == "logging"
    assert derive_component_role("tDie") == "logging"


def test_human_label_and_talend_term_explanations_are_deterministic():
    component = {
        "unique_name": "tXMLMap_1",
        "component_type": "tXMLMap",
        "params": [{"name": "LABEL", "value": '"LogMap"', "field_type": "TEXT"}],
    }
    assert component_display_label(component) == "LogMap"
    assert explain_component_type("tXMLMap") == "Maps hierarchical XML data."
    assert explain_connector("FLOW", False) == "Transfers rows of data."
    assert explain_connector("SUBJOB_OK", True) == (
        "Starts after the preceding subjob succeeds; carries no data."
    )


def test_job_graph_keeps_technical_identity_beside_human_label():
    job = {
        "components": [{
            "unique_name": "tXMLMap_1",
            "component_type": "tXMLMap",
            "params": [{"name": "LABEL", "value": "UpdateSendToMaximo"}],
        }],
        "connections": [],
    }
    node = build_job_graph("p1", "job", "grp", job).nodes[0]
    assert node.unique_name == "tXMLMap_1"
    assert node.display_label == "UpdateSendToMaximo"
    assert node.type_explanation == "Maps hierarchical XML data."


def test_generic_code_graph_never_describes_files_as_talend_components():
    job = {
        "components": [{
            "unique_name": "route",
            "component_type": "CodeFile",
            "params": [],
        }],
        "connections": [{
            "name": "imports",
            "source": "route",
            "target": "page",
            "connector_name": "IMPORTS",
            "line_style": 0,
        }],
    }

    graph = build_job_graph("p1", "app", "grp", job, platform="generic")

    assert graph.nodes[0].type_explanation == (
        "Source-code file analyzed as part of this application."
    )
    assert "Talend" not in graph.nodes[0].type_explanation
    assert graph.edges[0].explanation == (
        "References or depends on the following project artifact."
    )


def test_param_summary_is_bounded_and_credential_free():
    component = {
        "unique_name": "tDBOutput_1",
        "component_type": "tMSSqlOutput",
        "params": (
            [{"name": "PASSWORD", "value": "raw-secret", "field_type": "PASSWORD"}]
            + [{"name": "KEYSTORE_SECRET", "value": "x", "field_type": "TEXT"}]
            + [
                # More allowlisted params than the bound admits.
                {"name": name, "value": f'"{name.lower()}"', "field_type": "TEXT"}
                for name in (
                    "TABLE", "HOST", "PORT", "USER", "DBNAME", "QUERY",
                    "FILE_NAME", "METHOD", "ENCODING", "CONDITION",
                )
            ]
        ),
    }
    summary = build_param_summary(component)
    assert len(summary) <= 8
    assert "PASSWORD" not in summary
    assert "KEYSTORE_SECRET" not in summary
    assert "raw-secret" not in str(summary)


def test_param_summary_masks_url_userinfo_and_truncates():
    component = {
        "params": [
            {"name": "ENDPOINT_URL", "value": "https://svc:p4ss@host/api", "field_type": "TEXT"},
            {"name": "QUERY", "value": "SELECT " + "x" * 500, "field_type": "MEMO"},
        ],
    }
    summary = build_param_summary(component)
    assert summary["ENDPOINT_URL"] == "https://svc:***@host/api"
    assert len(summary["QUERY"]) <= 120
    assert summary["QUERY"].endswith("...")


def test_a_row_shaped_param_reaches_the_node_that_the_scalar_summary_cannot_hold():
    node = build_job_graph(
        "pluripharm", "collector", "Collectors", {"components": [SET_XML_VAR_COMPONENT]}
    ).nodes[0]

    # The scalar half is genuinely empty for this component -- which is why a
    # caller checking only `param_summary` reported it as having no settings.
    assert build_param_summary(SET_XML_VAR_COMPONENT) == {}
    table = node.param_tables[0]
    assert table.name == "VALUES"
    assert table.columns == ["NAME", "LANGUAGE", "EXPRESSION"]
    assert table.rows[0]["NAME"] == '"AN8_CHECK"'
    assert "SZAN8" in table.rows[0]["EXPRESSION"]
    assert (table.row_count, table.rows_omitted, table.rows_redacted) == (1, 0, 0)


def test_row_tables_are_screened_by_their_columns_not_by_the_component_type():
    """The class rule: author-written semantics in, per-schema-column flags out.

    Measured over the whole pluripharm scan, this keeps 5 997 of 41 388 rows.
    The three excluded tables below are its three largest sources of noise --
    one row per schema column, carrying nothing but scanner flags.
    """
    component = {
        "unique_name": "tDBOutput_1",
        "component_type": "tMSSqlOutput",
        "params": [
            # Kept: a filter's criterion, an XML field's XPath, a variable.
            {"name": "CONDITIONS", "field_type": "TABLE", "rows": [
                {"INPUT_COLUMN": "STATUS", "FUNCTION": "Empty", "OPERATOR": "!=", "RVALUE": "'X'"},
            ]},
            {"name": "MAPPING", "field_type": "TABLE", "rows": [
                {"SCHEMA_COLUMN": "OrderId", "QUERY": '"/Order/@id"', "NODECHECK": "false"},
            ]},
            {"name": "VARIABLES", "field_type": "TABLE", "rows": [
                {"KEY": '"env"', "VALUE": "context.environment"},
            ]},
            # Dropped: schema plumbing, one row per column of the schema.
            {"name": "FIELD_OPTIONS", "field_type": "TABLE", "rows": [
                {"SCHEMA_COLUMN": "OrderId", "UPDATE_KEY": "true", "DELETE_KEY": "false",
                 "INSERTABLE": "true", "UPDATABLE": "false"},
            ]},
            {"name": "TRIM_COLUMN", "field_type": "TABLE", "rows": [
                {"SCHEMA_COLUMN": "OrderId", "TRIM": "false"},
            ]},
            {"name": "LENGTHS", "field_type": "TABLE", "rows": [
                {"SCHEMA_COLUMN": "OrderId", "LENGTH": "12"},
            ]},
            {"name": "DRIVER_JAR", "field_type": "TABLE", "rows": [{"JAR_NAME": "mssql.jar"}]},
        ],
    }

    names = [table.name for table in build_param_tables(component)]

    assert names == ["CONDITIONS", "MAPPING", "VARIABLES"]


def test_a_credential_shaped_row_never_appears_in_a_row_structured_summary():
    """The whole row goes, not the cell: the pair is what makes it findable.

    A table row is usually a name/value pair, so the credential signal is in the
    DATA -- `NAME="Authorization"` with the bearer token beside it -- and every
    column name in the row is innocuous. Screening keys alone passes it through.
    """
    component = {
        "unique_name": "cSetHeader_9",
        "component_type": "cSetHeader",
        "params": [
            {"name": "VALUES", "field_type": "TABLE", "rows": [
                {"NAME": '"Content-Type"', "LANGUAGE": "constant", "EXPRESSION": '"text/xml"'},
                {"NAME": '"Authorization"', "LANGUAGE": "constant",
                 "EXPRESSION": '"Bearer eyJhbGciOi.header-token"'},
                {"NAME": '"client_secret"', "LANGUAGE": "constant", "EXPRESSION": '"s3cr3t"'},
                {"NAME": '"apiKey"', "LANGUAGE": "constant", "EXPRESSION": '"ak_live_9931"'},
                {"NAME": '"pwd"', "LANGUAGE": "constant", "EXPRESSION": '"hunter2"'},
                {"NAME": '"endpoint"', "LANGUAGE": "constant",
                 "EXPRESSION": '"https://svc:p4ss@host/api"'},
            ]},
            # A credential-named TABLE param never enters at all, whatever it holds.
            {"name": "OAUTH_PARAMS", "field_type": "TABLE", "rows": [
                {"NAME": '"grant_type"', "VALUE": '"client_credentials"'},
            ]},
        ],
    }

    tables = build_param_tables(component)
    serialized = str([table.model_dump() for table in tables])

    assert [table.name for table in tables] == ["VALUES"]
    for secret in ("eyJhbGciOi", "s3cr3t", "ak_live_9931", "hunter2", "p4ss"):
        assert secret not in serialized, secret
    assert "Authorization" not in serialized
    values = tables[0]
    assert values.rows_redacted == 4
    assert values.row_count == 6
    # The surviving URL row keeps its userinfo masked, exactly as a scalar does.
    assert values.rows[-1]["EXPRESSION"] == '"https://svc:***@host/api"'


def test_a_table_whose_rows_were_all_withheld_still_says_so():
    """Absence is stated. A vanished table reads as a component with no config."""
    component = {
        "unique_name": "cSetHeader_9",
        "component_type": "cSetHeader",
        "params": [
            {"name": "VALUES", "field_type": "TABLE", "rows": [
                {"NAME": '"password"', "VALUE": '"hunter2"'},
            ]},
        ],
    }

    table = build_param_tables(component)[0]

    assert table.rows == []
    assert (table.row_count, table.rows_redacted) == (1, 1)
    assert "hunter2" not in str(table.model_dump())


def test_param_tables_are_bounded_and_report_what_they_elided():
    component = {
        "unique_name": "cSetHeader_9",
        "component_type": "cSetHeader",
        "params": [
            {
                "name": f"VALUES_{index}",
                "field_type": "TABLE",
                "rows": [
                    {"NAME": f'"VAR_{row}"', "EXPRESSION": "x" * 400} for row in range(40)
                ],
            }
            for index in range(6)
        ],
    }

    tables = build_param_tables(component)

    assert len(tables) == 4
    for table in tables:
        assert len(table.rows) == 12
        assert table.row_count == 40
        assert table.rows_omitted == 28
        assert all(len(value) <= 120 for row in table.rows for value in row.values())


def test_trunjob_target_falls_back_to_positional_refs():
    job = {
        "components": [
            {"unique_name": "tRunJob_1", "component_type": "tRunJob", "params": []},
            {"unique_name": "tRunJob_2", "component_type": "tRunJob", "params": []},
        ],
        "connections": [],
        "tmap_data": [],
        "trunjob_refs": [
            {"child_job": "Child_A"},
            {"child_job": "Child_B"},
        ],
    }
    graph = build_job_graph("p1", "job", "grp", job)
    targets = {n.unique_name: n.subjob_target for n in graph.nodes}
    assert targets == {"tRunJob_1": "Child_A", "tRunJob_2": "Child_B"}


def test_tmap_tables_positional_association_with_synthetic_fallback():
    job = {
        "components": [
            {"unique_name": "tMap_1", "component_type": "tMap", "params": []},
        ],
        "connections": [],
        "tmap_data": [
            {"input_tables": ["a"], "output_tables": ["b"], "entries": [], "var_entries": []},
            # Extra entry with no matching tMap component (old-scan mismatch).
            {"input_tables": ["c"], "output_tables": [], "entries": [], "var_entries": []},
        ],
        "trunjob_refs": [],
    }
    graph = build_job_graph("p1", "job", "grp", job)
    assert graph.tmap_tables["tMap_1"].input_tables == ["a"]
    assert graph.tmap_tables["tMap#2"].input_tables == ["c"]


def test_edges_built_from_connections_not_component_fields():
    job = {
        "components": [
            # Per-component connection fields are empty, as on real scans.
            {"unique_name": "tA_1", "component_type": "tJava",
             "incoming_connections": [], "outgoing_connections": [], "params": []},
            {"unique_name": "tB_1", "component_type": "tJava",
             "incoming_connections": [], "outgoing_connections": [], "params": []},
        ],
        "connections": [
            {"name": "row1", "source": "tA_1", "target": "tB_1",
             "connector_name": "FLOW", "line_style": 0},
            {"name": "SubjobOk1", "source": "tA_1", "target": "tB_1",
             "connector_name": "SUBJOB_OK", "line_style": 1},
        ],
        "tmap_data": [],
        "trunjob_refs": [],
    }
    graph = build_job_graph("p1", "job", "grp", job)
    assert len(graph.edges) == 2
    flow, trigger = graph.edges
    assert flow.is_control_flow is False
    assert trigger.is_control_flow is True
    assert trigger.connector_name == "SUBJOB_OK"
    assert flow.explanation == "Transfers rows of data."
    assert trigger.explanation == (
        "Starts after the preceding subjob succeeds; carries no data."
    )


def test_empty_job_produces_empty_graph():
    graph = build_job_graph("p1", "job", "grp", {})
    assert graph.nodes == []
    assert graph.edges == []
    assert graph.tmap_tables == {}
