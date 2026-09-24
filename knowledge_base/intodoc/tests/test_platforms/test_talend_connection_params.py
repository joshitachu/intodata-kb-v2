"""Tests for parameters declared on a <connection>, not on a node.

A Talend DI Job puts every rule in its components and uses edges as pure
topology. A Talend ESB Route is Apache Camel, which inverts that:
``choice().when(<predicate>)`` is serialized as ``elementParameter`` children
of ``<connection connectorName="ROUTE_WHEN">``. The parser read exactly two of
those children (UNIQUE_NAME, ACTIVATE) and dropped the rest, so every routing
rule in every scanned route was lost while the topology survived.

The XML in these fixtures is the real serialization shape taken from a Talend 8
route export. Predicate text is generic on purpose -- no customer identifiers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from intodoc.models import Connection
from intodoc.platforms.talend.parser import TalendParser
from intodoc.scanner.credential_filter import CredentialFilter

_ITEM_XMLNS = (
    'xmlns:talendfile="platform:/resource/org.talend.model/model/TalendFile.xsd" '
    'xmlns:xmi="http://www.omg.org/XMI" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
)

# Verbatim shape of a Camel choice branch, with the customer-specific
# predicate replaced by a generic exception test.
_ROUTE_WHEN = (
    '<connection connectorName="ROUTE_WHEN" label="when3" lineStyle="15" '
    'metaname="cMessageRouter_3" offsetLabelX="0" offsetLabelY="0" '
    'source="cMessageRouter_3" target="cLog_16" xmi:id="_c1">'
    '<elementParameter field="CLOSED_LIST" name="ROUTETYPE" value="simple" xmi:id="_c2"/>'
    '<elementParameter field="MEMO_JAVA" name="CONDITION" '
    "value=\"&quot;${body} is 'java.lang.Exception'&quot;\" xmi:id=\"_c3\"/>"
    '<elementParameter field="CHECK" name="ENDOFCHOICE" value="true" xmi:id="_c4"/>'
    '<elementParameter field="CHECK" name="USE_NAMESPACES" show="false" value="false" xmi:id="_c5"/>'
    '<elementParameter field="TABLE" name="NAMESPACES" show="false" xmi:id="_c6"/>'
    '<elementParameter field="LABEL" name="WARNING" show="false" '
    'value="*Note: For Bean Language, the Expression should be a qualified class name '
    'without quotes." xmi:id="_c7"/>'
    '<elementParameter field="TEXT" name="UNIQUE_NAME" show="false" value="when3" xmi:id="_c8"/>'
    "</connection>"
)

# The same edge with an XPath predicate, whose namespace bindings live in a
# TABLE parameter: the predicate cannot be read without them.
_ROUTE_WHEN_XPATH = (
    '<connection connectorName="ROUTE_WHEN" label="when1" lineStyle="15" '
    'source="cMessageRouter_1" target="cLog_1" xmi:id="_x1">'
    '<elementParameter field="CLOSED_LIST" name="ROUTETYPE" value="xpath" xmi:id="_x2"/>'
    '<elementParameter field="MEMO_JAVA" name="CONDITION" '
    'value="&quot;/ord:order/ord:status = \'OPEN\'&quot;" xmi:id="_x3"/>'
    '<elementParameter field="CHECK" name="USE_NAMESPACES" value="true" xmi:id="_x4"/>'
    '<elementParameter field="TABLE" name="NAMESPACES" xmi:id="_x5">'
    '<elementValue elementRef="PREFIX" value="ord"/>'
    '<elementValue elementRef="URI" value="http://example.org/orders"/>'
    '<elementValue elementRef="PREFIX" value="env"/>'
    '<elementValue elementRef="URI" value="http://example.org/envelope"/>'
    "</elementParameter>"
    '<elementParameter field="TEXT" name="UNIQUE_NAME" value="when1" xmi:id="_x6"/>'
    "</connection>"
)

# A plain DI row link: the edge declares nothing but its own name.
_DI_ROW_LINK = (
    '<connection connectorName="FLOW" label="row1" lineStyle="0" '
    'source="tDBInput_1" target="tMap_1" xmi:id="_d1">'
    '<elementParameter field="TEXT" name="UNIQUE_NAME" show="false" value="row1" xmi:id="_d2"/>'
    "</connection>"
)

_DEACTIVATED_LINK = (
    '<connection connectorName="FLOW" label="row2" lineStyle="0" '
    'source="tMap_1" target="tLogRow_1" xmi:id="_e1">'
    '<elementParameter field="CHECK" name="ACTIVATE" value="false" xmi:id="_e2"/>'
    '<elementParameter field="TEXT" name="UNIQUE_NAME" show="false" value="row2" xmi:id="_e3"/>'
    "</connection>"
)


@pytest.fixture
def parser() -> TalendParser:
    return TalendParser(CredentialFilter())


def _write_design(root: Path, design_dir: str, name: str, body: str) -> None:
    """Write one .item/.properties pair under process/ or routes/."""
    target = root / "SYNTH" / design_dir / "Group"
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{name}_0.1.item").write_text(
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>'
        f'<talendfile:ProcessType {_ITEM_XMLNS} defaultContext="Default" jobType="Standard">'
        f"{body}</talendfile:ProcessType>",
        encoding="utf-8",
    )
    (target / f"{name}_0.1.properties").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<xmi:XMI xmi:version="2.0" xmlns:xmi="http://www.omg.org/XMI" '
        'xmlns:TalendProperties="http://www.talend.org/properties">'
        f'<TalendProperties:Property label="{name}" version="0.1"/>'
        "</xmi:XMI>",
        encoding="utf-8",
    )


def _connection(parser: TalendParser, root: Path, design: str, name: str, body: str) -> Connection:
    _write_design(root, design, name, body)
    result = parser.parse(root)
    job = next(job for job in result.jobs if job.name == name)
    assert len(job.connections) == 1
    return job.connections[0]


class TestRouteBranchPredicates:
    def test_route_when_keeps_condition_routetype_and_endofchoice(self, parser, tmp_path):
        conn = _connection(parser, tmp_path, "routes", "Branching", _ROUTE_WHEN)

        params = conn.compact_params()
        assert params["CONDITION"] == "\"${body} is 'java.lang.Exception'\""
        assert params["ROUTETYPE"] == "simple"
        assert params["ENDOFCHOICE"] == "true"

    def test_topology_fields_are_unchanged(self, parser, tmp_path):
        conn = _connection(parser, tmp_path, "routes", "Branching", _ROUTE_WHEN)

        assert conn.name == "when3"
        assert conn.source == "cMessageRouter_3"
        assert conn.target == "cLog_16"
        assert conn.connector_name == "ROUTE_WHEN"
        assert conn.line_style == 15
        assert conn.active is True

    def test_unique_name_is_promoted_and_not_repeated_in_params(self, parser, tmp_path):
        """UNIQUE_NAME becomes `name`, exactly as a node's becomes `unique_name`."""
        conn = _connection(parser, tmp_path, "routes", "Branching", _ROUTE_WHEN)

        assert conn.name == "when3"
        assert "UNIQUE_NAME" not in conn.compact_params()

    def test_field_type_is_kept_so_a_predicate_can_be_read(self, parser, tmp_path):
        """ROUTETYPE is a CLOSED_LIST, CONDITION a MEMO_JAVA -- both recorded."""
        conn = _connection(parser, tmp_path, "routes", "Branching", _ROUTE_WHEN)

        by_name = {param.name: param.field_type for param in conn.params}
        assert by_name["ROUTETYPE"] == "CLOSED_LIST"
        assert by_name["CONDITION"] == "MEMO_JAVA"

    def test_namespaces_table_keeps_its_rows(self, parser, tmp_path):
        """An XPath predicate is uninterpretable without its prefix bindings.

        The URI cell reads ``[URL_REDACTED]`` because the text-level credential
        filter redacts every http(s) URL in the source before parsing -- the
        same treatment a component parameter's URL gets, and the reason the
        row structure (which prefix is bound at all) is worth keeping even
        when a cell is masked.
        """
        conn = _connection(parser, tmp_path, "routes", "XPathBranch", _ROUTE_WHEN_XPATH)

        namespaces = next(p for p in conn.params if p.name == "NAMESPACES")
        assert namespaces.field_type == "TABLE"
        assert namespaces.rows == [
            {"PREFIX": "ord", "URI": "[URL_REDACTED]"},
            {"PREFIX": "env", "URI": "[URL_REDACTED]"},
        ]
        assert conn.compact_params()["ROUTETYPE"] == "xpath"

    def test_empty_table_parameter_is_dropped_like_any_empty_parameter(self, parser, tmp_path):
        """The simple-language branch declares NAMESPACES with no rows."""
        conn = _connection(parser, tmp_path, "routes", "Branching", _ROUTE_WHEN)

        assert "NAMESPACES" not in conn.compact_params()

    def test_presentation_parameters_follow_the_component_rule(self, parser, tmp_path):
        """WARNING is a LABEL-field designer note, and components keep those.

        Connections use the same reader, so they keep them too. A
        connection-only exclusion would be a second parameter policy.
        """
        conn = _connection(parser, tmp_path, "routes", "Branching", _ROUTE_WHEN)

        assert conn.compact_params()["WARNING"].startswith("*Note: For Bean Language")


class TestDiConnectionsAreUnchanged:
    def test_plain_row_link_declares_nothing(self, parser, tmp_path):
        conn = _connection(parser, tmp_path, "process", "PlainJob", _DI_ROW_LINK)

        assert conn.params == []
        assert json.loads(conn.model_dump_json()) == {
            "name": "row1",
            "source": "tDBInput_1",
            "target": "tMap_1",
            "connector_name": "FLOW",
            "line_style": 0,
            "active": True,
            "params": [],
        }

    def test_deactivated_link_stays_inactive_and_keeps_the_parameter(self, parser, tmp_path):
        """`active` is derived by the same helper components use."""
        conn = _connection(parser, tmp_path, "process", "DisabledJob", _DEACTIVATED_LINK)

        assert conn.active is False
        assert conn.compact_params()["ACTIVATE"] == "false"


class TestConnectionModel:
    def test_params_default_to_empty(self):
        conn = Connection(name="row1", source="a", target="b")
        assert conn.params == []
        assert conn.compact_params() == {}

    def test_historic_scan_json_without_params_still_loads(self):
        """Every scan written before this field existed must still parse."""
        conn = Connection.model_validate({
            "name": "row1",
            "source": "a",
            "target": "b",
            "connector_name": "FLOW",
            "line_style": 0,
            "active": True,
        })
        assert conn.params == []
