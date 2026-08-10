"""Unit tests for the Level-2 job graph builder (pure transformation layer)."""

from mcp_server.services.job_graph_service import (
    build_job_graph,
    build_param_summary,
    component_display_label,
    derive_component_role,
    explain_component_type,
    explain_connector,
)


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
