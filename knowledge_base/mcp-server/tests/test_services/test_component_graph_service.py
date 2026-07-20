"""Unit tests for the Level-3 tMap component graph builder (pure transformation layer)."""

from mcp_server.services.component_graph_service import (
    build_component_graph,
    component_exists,
    is_tmap_component,
)

JOB = {
    "components": [
        {"unique_name": "tFileInputDelimited_1", "component_type": "tFileInputDelimited"},
        {"unique_name": "tMap_1", "component_type": "tMap"},
        {"unique_name": "tDBOutput_1", "component_type": "tMSSqlOutput"},
        {"unique_name": "tLogRow_1", "component_type": "tLogRow"},
    ],
    "connections": [
        {"name": "row1", "source": "tFileInputDelimited_1", "target": "tMap_1",
         "connector_name": "FLOW", "line_style": 0},
        {"name": "out1", "source": "tMap_1", "target": "tDBOutput_1",
         "connector_name": "FLOW", "line_style": 0},
        {"name": "row2", "source": "tMap_1", "target": "tLogRow_1",
         "connector_name": "FLOW", "line_style": 0},
    ],
    "tmap_data": [
        {
            "input_tables": ["row1"],
            "output_tables": ["out1", "row2"],
            "entries": [
                {"table_name": "out1", "column_name": "id", "expression": "row1.id"},
                {"table_name": "out1", "column_name": "message",
                 "expression": "Var.Message"},
                {"table_name": "row2", "column_name": "id", "expression": "row1.id"},
            ],
            "var_entries": [
                {"table_name": "Var", "column_name": "Message",
                 "expression": "row1.message.toUpperCase()"},
            ],
        },
    ],
    "trunjob_refs": [],
}


def test_component_exists():
    assert component_exists(JOB, "tMap_1") is True
    assert component_exists(JOB, "tDBOutput_1") is True
    assert component_exists(JOB, "NoSuchComponent") is False


def test_is_tmap_component():
    assert is_tmap_component(JOB, "tMap_1") is True
    assert is_tmap_component(JOB, "tFileInputDelimited_1") is False
    assert is_tmap_component(JOB, "tDBOutput_1") is False
    # Unknown component: not a tMap either (route layer handles the distinct
    # "component not found" 404 via component_exists first).
    assert is_tmap_component(JOB, "NoSuchComponent") is False


def test_build_component_graph_input_tables_resolved_via_connections():
    """Input table row names resolve to their upstream component via the
    job's connections array -- never via incoming/outgoing_connections."""
    graph = build_component_graph("p1", "job", "tMap_1", JOB)
    assert graph is not None
    assert len(graph.input_tables) == 1
    row1 = graph.input_tables[0]
    assert row1.table_name == "row1"
    assert row1.source_component == "tFileInputDelimited_1"
    # Input table columns are not captured by the current scanner -- empty
    # until the S4 tokenizer back-fills them.
    assert row1.columns == []


def test_build_component_graph_var_table():
    graph = build_component_graph("p1", "job", "tMap_1", JOB)
    assert graph is not None
    assert graph.var_table.table_name == "Var"
    assert graph.var_table.columns == ["Message"]
    assert graph.var_table.source_component is None


def test_build_component_graph_output_tables_and_columns():
    graph = build_component_graph("p1", "job", "tMap_1", JOB)
    assert graph is not None
    by_name = {t.table_name: t for t in graph.output_tables}
    assert set(by_name) == {"out1", "row2"}
    assert by_name["out1"].columns == ["id", "message"]
    assert by_name["row2"].columns == ["id"]


def test_build_component_graph_edges_carry_raw_expression_only():
    """Mapping edges expose {expression, resolved_refs: [], confidence:
    'opaque'} for now -- real resolution lands in slice S4."""
    graph = build_component_graph("p1", "job", "tMap_1", JOB)
    assert graph is not None
    # One edge per tmap entry + var entry.
    assert len(graph.edges) == 4
    for edge in graph.edges:
        assert edge.resolved_refs == []
        assert edge.confidence == "opaque"

    by_target = {(e.target_table, e.target_column): e for e in graph.edges}
    assert by_target[("out1", "id")].expression == "row1.id"
    assert by_target[("out1", "message")].expression == "Var.Message"
    assert by_target[("row2", "id")].expression == "row1.id"
    assert by_target[("Var", "Message")].expression == "row1.message.toUpperCase()"


def test_build_component_graph_positional_mismatch_returns_none():
    """A tMap component with no matching tmap_data entry (old-scan count
    mismatch) has no real graph to show -- callers must 404 to inspector."""
    job = {
        "components": [
            {"unique_name": "tMap_1", "component_type": "tMap"},
            {"unique_name": "tMap_2", "component_type": "tMap"},
        ],
        "connections": [],
        "tmap_data": [
            {"input_tables": [], "output_tables": [], "entries": [], "var_entries": []},
        ],
        "trunjob_refs": [],
    }
    assert build_component_graph("p1", "job", "tMap_1", job) is not None
    assert build_component_graph("p1", "job", "tMap_2", job) is None


def test_build_component_graph_no_var_entries_yields_empty_var_table():
    job = {
        "components": [{"unique_name": "tMap_1", "component_type": "tMap"}],
        "connections": [],
        "tmap_data": [
            {"input_tables": [], "output_tables": [], "entries": [], "var_entries": []},
        ],
        "trunjob_refs": [],
    }
    graph = build_component_graph("p1", "job", "tMap_1", job)
    assert graph is not None
    assert graph.var_table.table_name == "Var"
    assert graph.var_table.columns == []
