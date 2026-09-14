"""Enterprise-fidelity coverage for the Talend scanner.

Every fixture here is synthetic (invented names such as Orders_Load, CRM,
Warehouse). The assertions are exact counts and exact strings on purpose:
the point of this slice is that a downstream consumer can trust the
numbers, so a test that only asserts "> 0" would not test the thing.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from intodoc.capability_ledger import build_capability_ledger
from intodoc.mapping_accounting import build_mapping_accounting
from intodoc.models import FileType
from intodoc.platforms.talend import TalendPlugin
from intodoc.platforms.talend.parser import TalendParser
from intodoc.platforms.talend.structures import parse_structure_file
from intodoc.platforms.talend.xslt import parse_xslt_file
from intodoc.scanner.credential_filter import CredentialFilter
from intodoc.scanner.evidence_store import write_sanitized_workspace
from intodoc.scanner.post_filter import filter_scan_result
from intodoc.scanner.redaction_verifier import verify_scan_result

_XMLNS = (
    'xmlns:talendfile="platform:/resource/org.talend.model/model/TalendFile.xsd" '
    'xmlns:TalendMapper="http://www.talend.org/mapper" '
    'xmlns:xmi="http://www.omg.org/XMI" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
)


@pytest.fixture
def cred_filter():
    return CredentialFilter()


@pytest.fixture
def parser(cred_filter):
    return TalendParser(cred_filter)


def _write_properties(path, *, label, version, property_id):
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<xmi:XMI xmi:version="2.0" xmlns:xmi="http://www.omg.org/XMI" '
        'xmlns:TalendProperties="http://www.talend.org/properties">'
        f'<TalendProperties:Property id="{property_id}" label="{label}" '
        f'version="{version}"/>'
        "</xmi:XMI>",
        encoding="utf-8",
    )


def _write_design(directory, *, name, version, property_id, body, label=None):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}_{version}.item").write_text(
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>'
        f'<talendfile:ProcessType {_XMLNS} defaultContext="Default" '
        f'jobType="Standard">{body}</talendfile:ProcessType>',
        encoding="utf-8",
    )
    _write_properties(
        directory / f"{name}_{version}.properties",
        label=label or name, version=version, property_id=property_id,
    )


# ── Fixture bodies ───────────────────────────────────────────────────

# A tMap with: a direct map, a Var hop, a lookup join, a string constant,
# a globalMap runtime lookup, a reference to a row that does not exist,
# an unmapped output column, a reject output, and one offered input column
# nobody reads. Its second input arrives from a component whose schema
# carries an id_Dynamic column, so the offered list is a lower bound.
_MAPPER_BODY = (
    '<node componentName="tDBInput">'
    '<elementParameter field="TEXT" name="UNIQUE_NAME" value="tDBInput_1"/>'
    '<elementParameter field="CLOSED_LIST" name="PROPERTY:PROPERTY_TYPE" value="REPOSITORY"/>'
    '<elementParameter field="TECHNICAL" name="PROPERTY:REPOSITORY_PROPERTY_TYPE" value="_crmConn"/>'
    '<elementParameter field="CLOSED_LIST" name="SCHEMA:SCHEMA_TYPE" value="BUILT_IN"/>'
    '<metadata connector="FLOW" name="tDBInput_1">'
    '<column defaultValue="" key="true" length="10" name="order_id" nullable="false" '
    'precision="0" sourceType="int" type="id_Integer" comment="PK"/>'
    '<column defaultValue="" key="false" length="255" name="customer" nullable="true" '
    'precision="0" sourceType="varchar" type="id_String"/>'
    '<column defaultValue="" key="false" length="0" name="unused_col" nullable="true" '
    'precision="0" sourceType="varchar" type="id_String"/>'
    "</metadata></node>"
    '<node componentName="tFileInputDelimited">'
    '<elementParameter field="TEXT" name="UNIQUE_NAME" value="tFileInputDelimited_1"/>'
    '<elementParameter field="CLOSED_LIST" name="SCHEMA:SCHEMA_TYPE" value="REPOSITORY"/>'
    '<elementParameter field="TECHNICAL" name="SCHEMA:REPOSITORY_SCHEMA_TYPE" value="_warehouseSchema"/>'
    '<metadata connector="FLOW" name="tFileInputDelimited_1">'
    '<column defaultValue="" key="false" length="0" name="dyn" nullable="true" '
    'precision="0" sourceType="" type="id_Dynamic"/>'
    "</metadata></node>"
    '<node componentName="tMap">'
    '<elementParameter field="TEXT" name="UNIQUE_NAME" value="tMap_1"/>'
    '<nodeData xsi:type="TalendMapper:MapperData">'
    '<inputTables name="row1" matchingMode="UNIQUE_MATCH">'
    '<mapperTableEntries name="order_id" type="id_Integer"/>'
    '<mapperTableEntries name="customer" type="id_String"/>'
    '<mapperTableEntries name="unused_col" type="id_String"/>'
    "</inputTables>"
    '<inputTables name="row2" lookupMode="RELOAD" matchingMode="ALL_MATCHES" '
    'innerJoin="true" expressionFilter="row2.active == 1" activateExpressionFilter="true">'
    '<mapperTableEntries name="region" type="id_String"/>'
    "</inputTables>"
    '<varTables name="Var">'
    '<mapperTableEntries name="upperName" type="id_String" '
    'expression="StringHandling.UPCASE(row1.customer)"/>'
    "</varTables>"
    '<outputTables name="out1">'
    '<mapperTableEntries name="id" type="id_Integer" expression="row1.order_id"/>'
    '<mapperTableEntries name="name" type="id_String" expression="Var.upperName"/>'
    '<mapperTableEntries name="region" type="id_String" expression="row2.region"/>'
    '<mapperTableEntries name="source" type="id_String" expression="&quot;ERP&quot;"/>'
    '<mapperTableEntries name="batch" type="id_String" '
    'expression="(String)globalMap.get(&quot;batchId&quot;)"/>'
    '<mapperTableEntries name="ghost" type="id_String" expression="row9.missing"/>'
    '<mapperTableEntries name="blank" type="id_String" expression=""/>'
    "</outputTables>"
    '<outputTables name="rejects" reject="true">'
    '<mapperTableEntries name="err" type="id_String" expression="row1.order_id"/>'
    "</outputTables>"
    "</nodeData></node>"
    '<node componentName="tLogRow">'
    '<elementParameter field="TEXT" name="UNIQUE_NAME" value="tLogRow_1"/>'
    '<elementParameter field="CHECK" name="ACTIVATE" value="false"/>'
    "</node>"
    '<node componentName="tRunJob">'
    '<elementParameter field="TEXT" name="UNIQUE_NAME" value="tRunJob_1"/>'
    '<elementParameter field="PROCESS_TYPE" name="PROCESS" value="Orders_Old_Name"/>'
    '<elementParameter field="TECHNICAL" name="PROCESS:PROCESS_TYPE_PROCESS" '
    'value="ORDERS:_stageGuid"/>'
    '<elementParameter field="TECHNICAL" name="PROCESS:PROCESS_TYPE_VERSION" value="2.1"/>'
    '<elementParameter field="TECHNICAL" name="PROCESS:PROCESS_TYPE_CONTEXT" value="Prod"/>'
    '<elementParameter field="CHECK" name="TRANSMIT_WHOLE_CONTEXT" value="false"/>'
    '<elementParameter field="CHECK" name="DIE_ON_CHILD_ERROR" value="true"/>'
    '<elementParameter field="TABLE" name="CONTEXTPARAMS" value="">'
    '<elementValue elementRef="PARAM_NAME_COLUMN" value="TargetTable"/>'
    '<elementValue elementRef="PARAM_VALUE_COLUMN" value="context.stage_table"/>'
    '<elementValue elementRef="PARAM_NAME_COLUMN" value="RunDate"/>'
    '<elementValue elementRef="PARAM_VALUE_COLUMN" value="context.run_date"/>'
    "</elementParameter></node>"
    '<connection connectorName="FLOW" label="row1" lineStyle="0" '
    'source="tDBInput_1" target="tMap_1"/>'
    '<connection connectorName="FLOW" label="row2" lineStyle="0" '
    'source="tFileInputDelimited_1" target="tMap_1">'
    '<elementParameter field="TEXT" name="UNIQUE_NAME" value="row2"/>'
    '<elementParameter field="CHECK" name="ACTIVATE" value="false"/>'
    "</connection>"
)

_ROUTE_BODY = (
    '<node componentName="cJMS">'
    '<elementParameter name="UNIQUE_NAME" value="cJMS_1"/>'
    '<elementParameter name="DESTINATION" value="context.orders_queue"/>'
    "</node>"
    '<node componentName="cXslt">'
    '<elementParameter name="UNIQUE_NAME" value="cXslt_1"/>'
    '<elementParameter name="RESOURCE_URI" value="route_resources/order_to_invoice.xsl"/>'
    "</node>"
    '<node componentName="cTalendJob">'
    '<elementParameter name="UNIQUE_NAME" value="callStage"/>'
    '<elementParameter name="SELECTED_JOB_NAME" value="Orders_Old_Name"/>'
    '<elementParameter name="SELECTED_JOB_NAME:PROCESS_TYPE_PROCESS" value="_stageGuid"/>'
    '<elementParameter name="SELECTED_JOB_NAME:PROCESS_TYPE_VERSION" value="2.1"/>'
    '<elementParameter name="USE_ROUTE_CONTEXT" value="true"/>'
    '<elementParameter field="TABLE" name="CONTEXTPARAMS" value="">'
    '<elementValue elementRef="PARAM_NAME_COLUMN" value="Mode"/>'
    '<elementValue elementRef="PARAM_VALUE_COLUMN" value="&quot;bulk&quot;"/>'
    "</elementParameter></node>"
    '<connection connectorName="ROUTE" source="cJMS_1" target="cXslt_1"/>'
)

_XSLT = (
    '<?xml version="1.0"?>'
    '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
    '<xsl:param name="tenant" select="\'CRM\'"/>'
    '<xsl:include href="common.xsl"/>'
    '<xsl:template match="/Order">'
    "<Invoice>"
    '<xsl:variable name="total" select="sum(Lines/Line/Amount)"/>'
    '<Id><xsl:value-of select="Header/Id"/></Id>'
    '<Total><xsl:value-of select="$total"/></Total>'
    '<xsl:for-each select="Lines/Line"><Line><xsl:copy-of select="."/></Line></xsl:for-each>'
    "<xsl:choose>"
    '<xsl:when test="Header/Rush=\'Y\'"><Priority>1</Priority></xsl:when>'
    "<xsl:otherwise><Priority>9</Priority></xsl:otherwise>"
    "</xsl:choose>"
    "</Invoice></xsl:template></xsl:stylesheet>"
)

_STRUCTURE = (
    '<?xml version="1.0"?><root name="OrderStruct">'
    '<rootElem name="Order" occurMaxTimes="1" groupType="SEQUENCE">'
    '<structNode idref="n1"/>'
    '<child name="Header" groupType="SEQUENCE" occurMaxTimes="1" nullable="false">'
    '<child name="Id" dataType="string" nullable="false" minSize="1" maxSize="20"/>'
    '<child name="Date" dataType="date" nullable="true"/>'
    "</child>"
    '<child name="Lines" occurMaxTimes="unbounded">'
    '<child name="Amount" dataType="decimal"/>'
    "</child>"
    "</rootElem></root>"
)

_WSDL = (
    '<?xml version="1.0"?>'
    '<definitions xmlns="http://schemas.xmlsoap.org/wsdl/" name="OrderService">'
    '<portType name="OrderPort">'
    '<operation name="CreateOrder">'
    '<input message="tns:CreateOrderRequest"/>'
    '<output message="tns:CreateOrderResponse"/>'
    "</operation>"
    '<operation name="GetOrder"><input message="tns:GetOrderRequest"/></operation>'
    "</portType></definitions>"
)

# A namespace URI that carries a credential-shaped query parameter. Layer 1
# deliberately preserves xmlns declarations (they are document structure),
# so this file cannot be proven clean and must never be mirrored.
_LEAKY_XML = (
    '<?xml version="1.0"?>'
    '<root xmlns:cfg="http://ns.invalid/?password=Hunter2Secret"><a>1</a></root>'
)


def _enterprise_project(root):
    """One Talend root exercising every enterprise artifact category."""
    project = root / "ORDERS"
    _write_design(
        project / "process" / "Orders",
        name="Orders_Load", version="1.0", property_id="_loadGuid",
        body=_MAPPER_BODY,
    )
    # The child was renamed after the caller was last saved, so both
    # callers above still cache the OLD label.
    _write_design(
        project / "process" / "Orders",
        name="Orders_Stage", version="2.1", property_id="_stageGuid",
        body='<node componentName="tJava">'
             '<elementParameter name="UNIQUE_NAME" value="tJava_1"/>'
             '<elementParameter name="CODE" value="System.out.println(1);"/></node>',
    )
    _write_design(
        project / "routes" / "Inbound",
        name="Inbound", version="0.1", property_id="_inboundGuid",
        body=_ROUTE_BODY,
    )
    (project / "route_resources").mkdir(parents=True)
    (project / "route_resources" / "order_to_invoice.xsl").write_text(_XSLT, encoding="utf-8")
    structures = project / "Structures" / "Order"
    structures.mkdir(parents=True)
    (structures / "OrderStruct_0.1.xml").write_text(_STRUCTURE, encoding="utf-8")
    _write_properties(
        structures / "OrderStruct_0.1.properties",
        label="OrderStruct", version="0.1", property_id="_structGuid",
    )
    services = project / "services"
    services.mkdir(parents=True)
    _write_design(
        services, name="OrderService", version="0.1",
        property_id="_serviceGuid", body="",
    )
    (services / "OrderService_0.1.wsdl").write_text(_WSDL, encoding="utf-8")
    (project / "context").mkdir(parents=True)
    (project / "context" / "envs_0.1.item").write_text(
        f'<?xml version="1.0"?><talendfile:ContextType {_XMLNS}>'
        '<ContextType name="Default">'
        '<contextParameter name="stage_table" type="id_String" value="STG_ORDERS"/>'
        '<contextParameter name="DbPassword" type="id_Password" value="Hunter2Secret"/>'
        "</ContextType></talendfile:ContextType>",
        encoding="utf-8",
    )
    (project / "resources").mkdir(parents=True)
    (project / "resources" / "leaky.xml").write_text(_LEAKY_XML, encoding="utf-8")
    (project / "resources" / "payload.jar").write_text("binary-ish", encoding="utf-8")
    return project


def _scan(parser, cred_filter, root):
    result = parser.parse(root)
    filter_scan_result(result, cred_filter)
    result.mapping_accounting = build_mapping_accounting(result)
    result.capability_ledger = build_capability_ledger(result)
    return result


# ── (a) Mapping accounting ───────────────────────────────────────────


class TestMappingAccounting:
    def test_tmap_accounting_is_exact(self, parser, cred_filter, tmp_path):
        _enterprise_project(tmp_path)
        result = _scan(parser, cred_filter, tmp_path)
        tmap = next(
            record for record in result.mapping_accounting
            if record.mapper_kind == "tmap"
        )

        assert tmap.unit_id == "Orders_Load"
        # TMapData.component_name was declared but never populated, so this
        # attribution is new evidence, not a rename.
        assert tmap.component_name == "tMap_1"
        assert tmap.offered_fields == 4
        assert tmap.written_fields == 8
        assert tmap.mapped_from_input == 3
        assert tmap.constant_or_generated == 1
        assert tmap.lookup_derived == 1
        assert tmap.dynamic_unresolved == 3
        assert tmap.unwritten_offered == ["row1.unused_col"]
        assert tmap.reject_outputs == ["rejects"]
        assert tmap.filters == ["row2.active == 1"]
        assert tmap.lookup_tables == [{
            "name": "row2", "match_model": "ALL_MATCHES",
            "load_model": "RELOAD", "inner_join": "true",
            "expression_filter": "row2.active == 1",
        }]
        assert tmap.table_id == hashlib.sha256(
            b"Orders_Load|tMap_1|tmap",
        ).hexdigest()[:16]

    def test_every_written_field_is_classified_exactly_once(self, parser, cred_filter, tmp_path):
        _enterprise_project(tmp_path)
        result = _scan(parser, cred_filter, tmp_path)
        assert result.mapping_accounting
        for record in result.mapping_accounting:
            assert record.written_fields == len(record.rows)
            assert (
                record.mapped_from_input
                + record.constant_or_generated
                + record.lookup_derived
                + record.dynamic_unresolved
            ) == record.written_fields

    def test_row_classification_and_provenance(self, parser, cred_filter, tmp_path):
        _enterprise_project(tmp_path)
        result = _scan(parser, cred_filter, tmp_path)
        tmap = next(
            record for record in result.mapping_accounting
            if record.mapper_kind == "tmap"
        )
        rows = {(row.output_table, row.output_field): row for row in tmap.rows}

        assert rows[("out1", "id")].classification == "direct_map"
        assert rows[("out1", "id")].source_refs == ["row1.order_id"]
        # A Var hop is a transformation, and the edge points at the Var's
        # own source -- not at "Var".
        assert rows[("out1", "name")].classification == "expression"
        assert rows[("out1", "name")].source_refs == ["row1.customer"]
        assert rows[("out1", "region")].classification == "lookup"
        assert rows[("out1", "source")].classification == "constant"
        assert rows[("out1", "batch")].classification == "dynamic"
        # row9 is not a declared input: a wiring gap, not a runtime lookup.
        assert rows[("out1", "ghost")].classification == "unresolved"
        assert rows[("out1", "ghost")].dynamic_refs == ["row9.missing"]
        # An output column declared with no expression is still written.
        assert rows[("out1", "blank")].classification == "unresolved"

    def test_dynamic_input_schema_makes_offered_a_lower_bound(self, parser, cred_filter, tmp_path):
        _enterprise_project(tmp_path)
        result = _scan(parser, cred_filter, tmp_path)
        tmap = next(
            record for record in result.mapping_accounting
            if record.mapper_kind == "tmap"
        )
        assert tmap.offered_enumerable is False
        assert tmap.unenumerable_inputs == ["row2"]

    def test_xslt_accounting_reports_no_declared_input_schema(self, parser, cred_filter, tmp_path):
        _enterprise_project(tmp_path)
        result = _scan(parser, cred_filter, tmp_path)
        xslt = next(
            record for record in result.mapping_accounting
            if record.mapper_kind == "xslt"
        )
        assert xslt.offered_fields == 0
        assert xslt.offered_enumerable is False
        assert xslt.unenumerable_inputs == [
            "xslt_input_document_has_no_declared_schema",
        ]
        # Only value-producing instructions are accounted rows.
        assert [row.output_field for row in xslt.rows] == [
            "Invoice/Id", "Invoice/Total", "Invoice/Line",
        ]
        assert xslt.unit_id == "Inbound"


# ── (b) Structures ───────────────────────────────────────────────────


class TestStructureParsing:
    def test_nested_children_are_path_qualified(self, cred_filter, tmp_path):
        path = tmp_path / "OrderStruct_0.1.xml"
        path.write_text(_STRUCTURE, encoding="utf-8")
        definition, message = parse_structure_file(
            path,
            relative_path="Structures/Order/OrderStruct_0.1.xml",
            source_root="ORDERS",
            read_text=lambda p: p.read_text(encoding="utf-8"),
            sidecar={"label": "OrderStruct", "version": "0.1", "property_id": "_s"},
        )

        assert message == ""
        assert definition.parse_outcome == "parsed"
        assert definition.root_element == "Order"
        assert definition.element_count == 6
        assert [element.path for element in definition.elements] == [
            "Order", "Order/Header", "Order/Header/Id", "Order/Header/Date",
            "Order/Lines", "Order/Lines/Amount",
        ]
        header_id = definition.elements[2]
        assert header_id.data_type == "string"
        assert header_id.nullable is False
        assert header_id.min_size == "1"
        assert header_id.max_size == "20"
        # An attribute the export never stated stays unknown, not False.
        assert definition.elements[0].nullable is None
        assert definition.elements[4].occur_max_times == "unbounded"

    def test_unknown_tag_makes_the_outcome_partial(self, tmp_path):
        path = tmp_path / "Odd_0.1.xml"
        path.write_text(
            '<?xml version="1.0"?><root name="Odd"><rootElem name="Root">'
            '<child name="A" dataType="string"/><futureThing name="B"/>'
            "</rootElem></root>",
            encoding="utf-8",
        )
        definition, message = parse_structure_file(
            path, relative_path="Structures/Odd_0.1.xml", source_root="ORDERS",
            read_text=lambda p: p.read_text(encoding="utf-8"), sidecar={},
        )
        assert definition.parse_outcome == "partial"
        assert definition.unmatched_tags == ["futureThing"]
        assert "futureThing" in message
        # The elements it DID understand are still published.
        assert [element.path for element in definition.elements] == ["Root", "Root/A"]

    def test_unparseable_structure_is_reported_not_dropped(self, tmp_path):
        path = tmp_path / "Broken_0.1.xml"
        path.write_text("<root><unclosed>", encoding="utf-8")
        definition, message = parse_structure_file(
            path, relative_path="Structures/Broken_0.1.xml", source_root="ORDERS",
            read_text=lambda p: p.read_text(encoding="utf-8"), sidecar={},
        )
        assert definition.parse_outcome == "unsupported"
        assert definition.name == "Broken_0.1"
        assert message

    def test_structure_is_discovered_by_the_scan(self, parser, cred_filter, tmp_path):
        _enterprise_project(tmp_path)
        result = _scan(parser, cred_filter, tmp_path)
        assert [structure.name for structure in result.structures] == ["OrderStruct"]
        structure = result.structures[0]
        assert structure.source_root == "ORDERS"
        assert structure.property_id == "_structGuid"
        assert structure.version == "0.1"
        assert result.stats["structure_count"] == 1
        assert result.stats["structure_element_count"] == 6


# ── (c) XSLT ─────────────────────────────────────────────────────────


class TestXsltParsing:
    def test_templates_instructions_and_declarations(self, tmp_path):
        path = tmp_path / "order_to_invoice.xsl"
        path.write_text(_XSLT, encoding="utf-8")
        transform, message = parse_xslt_file(
            path, relative_path="route_resources/order_to_invoice.xsl",
            source_root="ORDERS", read_text=lambda p: p.read_text(encoding="utf-8"),
        )

        assert message == ""
        assert transform.parse_outcome == "parsed"
        assert [template.match for template in transform.templates] == ["/Order"]
        assert transform.includes == ["common.xsl"]
        assert transform.params == [
            {"name": "tenant", "select": "'CRM'", "template_match": ""},
        ]
        assert transform.variables == [
            {"name": "total", "select": "sum(Lines/Line/Amount)",
             "template_match": "/Order"},
        ]
        instructions = [
            (mapping.instruction, mapping.select, mapping.output_path)
            for mapping in transform.mappings
        ]
        assert ("value-of", "Header/Id", "Invoice/Id") in instructions
        assert ("value-of", "$total", "Invoice/Total") in instructions
        assert ("for-each", "Lines/Line", "Invoice") in instructions
        assert ("copy-of", ".", "Invoice/Line") in instructions
        assert [
            mapping.test for mapping in transform.mappings
            if mapping.instruction == "when"
        ] == ["Header/Rush='Y'"]
        assert any(
            mapping.instruction == "otherwise" for mapping in transform.mappings
        )
        # Literal result elements are the output markup and are recorded too.
        assert ("literal-element", "", "Invoice") in instructions

    def test_unmodelled_instruction_makes_the_outcome_partial(self, tmp_path):
        path = tmp_path / "future.xsl"
        path.write_text(
            '<?xml version="1.0"?>'
            '<xsl:stylesheet version="3.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
            '<xsl:template match="/"><xsl:future-thing/></xsl:template>'
            "</xsl:stylesheet>",
            encoding="utf-8",
        )
        transform, message = parse_xslt_file(
            path, relative_path="resources/future.xsl", source_root="ORDERS",
            read_text=lambda p: p.read_text(encoding="utf-8"),
        )
        assert transform.parse_outcome == "partial"
        assert transform.unmatched_instructions == ["future-thing"]
        assert "future-thing" in message

    def test_route_reference_resolves_by_exact_path(self, parser, cred_filter, tmp_path):
        _enterprise_project(tmp_path)
        result = _scan(parser, cred_filter, tmp_path)
        transform = next(
            item for item in result.xslt_transforms
            if item.name == "order_to_invoice.xsl"
        )
        assert transform.referenced_by == ["Inbound::cXslt_1"]
        assert result.manifest.resource_refs == [{
            "artifact": "Inbound",
            "source_root": "ORDERS",
            "component_name": "cXslt_1",
            "param": "RESOURCE_URI",
            "resource_type": "xslt",
            "resource": "route_resources/order_to_invoice.xsl",
            "matched_token": "route_resources/order_to_invoice.xsl",
            "resolution": "exact",
        }]

    def test_missing_stylesheet_is_recorded_as_unresolved(self, parser, cred_filter, tmp_path):
        project = tmp_path / "ORDERS"
        _write_design(
            project / "routes" / "Inbound", name="Inbound", version="0.1",
            property_id="_r",
            body='<node componentName="cXslt">'
                 '<elementParameter name="UNIQUE_NAME" value="cXslt_1"/>'
                 '<elementParameter name="RESOURCE_URI" value="route_resources/absent.xsl"/>'
                 "</node>",
        )
        result = _scan(parser, cred_filter, tmp_path)
        assert result.manifest.resource_refs == [{
            "artifact": "Inbound", "source_root": "ORDERS",
            "component_name": "cXslt_1", "param": "RESOURCE_URI",
            "resource_type": "xslt", "resource": "",
            "resolution": "unresolved",
            "reason": "referenced_file_not_in_scan",
        }]


# ── (d) DI + ESB journey references ──────────────────────────────────


class TestDesignReferences:
    def test_trunjob_prefers_identity_over_a_stale_label(self, parser, cred_filter, tmp_path):
        _enterprise_project(tmp_path)
        result = _scan(parser, cred_filter, tmp_path)
        job = next(item for item in result.jobs if item.name == "Orders_Load")
        ref = job.trunjob_refs[0]

        assert ref.child_job == "Orders_Stage"
        assert ref.reference_source == "identity"
        assert ref.resolved_job_id == "Orders_Stage"
        assert ref.resolution == "same_root"
        assert ref.target_item_path == "process/Orders/Orders_Stage"
        assert ref.version == "2.1"
        assert ref.context_name == "Prod"
        assert ref.transmit_whole_context is False
        # Absent parameters stay unknown rather than defaulting to False.
        assert ref.transmit_original_context is None
        assert ref.die_on_child_error is True
        assert ref.dynamic_job is False
        assert [(p.name, p.value) for p in ref.context_params] == [
            ("TargetTable", "context.stage_table"),
            ("RunDate", "context.run_date"),
        ]

        stale = [w for w in result.warnings if w.kind == "stale_label"]
        assert len(stale) == 2
        assert all("Orders_Old_Name" in w.message for w in stale)
        assert all("Orders_Stage" in w.message for w in stale)

    def test_contextparams_table_rows_survive_parsing(self, parser, cred_filter, tmp_path):
        _enterprise_project(tmp_path)
        result = _scan(parser, cred_filter, tmp_path)
        job = next(item for item in result.jobs if item.name == "Orders_Load")
        param = next(
            p for component in job.components for p in component.params
            if p.name == "CONTEXTPARAMS"
        )
        # The TABLE parameter serializes value="" -- the payload is in the
        # element children, which the empty-value skip used to discard.
        assert param.value == ""
        assert param.field_type == "TABLE"
        assert param.rows == [
            {"PARAM_NAME_COLUMN": "TargetTable",
             "PARAM_VALUE_COLUMN": "context.stage_table"},
            {"PARAM_NAME_COLUMN": "RunDate",
             "PARAM_VALUE_COLUMN": "context.run_date"},
        ]

    def test_route_ctalendjob_resolves_by_identity_with_route_context(self, parser, cred_filter, tmp_path):
        _enterprise_project(tmp_path)
        result = _scan(parser, cred_filter, tmp_path)
        route = next(item for item in result.jobs if item.name == "Inbound")
        ref = route.artifact_refs[0]

        assert route.artifact_kind == "route"
        assert ref.target_name == "Orders_Stage"
        assert ref.reference_source == "identity"
        assert ref.resolved_artifact_id == "Orders_Stage"
        assert ref.resolution == "same_root"
        assert ref.version == "2.1"
        assert ref.use_route_context is True
        assert [(p.name, p.value) for p in ref.context_params] == [("Mode", '"bulk"')]

    def test_dynamic_trunjob_has_no_static_target(self, parser, cred_filter, tmp_path):
        project = tmp_path / "ORDERS"
        _write_design(
            project / "process" / "Orders", name="Orders_Load", version="1.0",
            property_id="_load",
            body='<node componentName="tRunJob">'
                 '<elementParameter name="UNIQUE_NAME" value="tRunJob_1"/>'
                 '<elementParameter name="PROCESS" value="Orders_Stage"/>'
                 '<elementParameter name="USE_DYNAMIC_JOB" value="true"/>'
                 "</node>",
        )
        _write_design(
            project / "process" / "Orders", name="Orders_Stage", version="1.0",
            property_id="_stage", body="",
        )
        result = _scan(parser, cred_filter, tmp_path)
        ref = next(
            item for item in result.jobs if item.name == "Orders_Load"
        ).trunjob_refs[0]

        assert ref.dynamic_job is True
        assert ref.resolution == "dynamic"
        # A runtime-selected child must not be bound to the cached label's
        # namesake, even though that design exists in the scan.
        assert ref.resolved_job_id == ""
        assert "Orders_Load" not in result.manifest.job_calls


# ── (e) Composite bundles ────────────────────────────────────────────


class TestCompositeResolution:
    def test_identity_resolves_inside_its_own_root_only(self, parser, cred_filter, tmp_path):
        for root_name, guid in (("ALPHA", "_alphaWorker"), ("BETA", "_betaWorker")):
            project = tmp_path / root_name
            _write_design(
                project / "process" / "Jobs", name="Worker", version="0.1",
                property_id=guid, body="",
            )
            _write_design(
                project / "process" / "Jobs", name="Caller", version="0.1",
                property_id=f"{guid}_caller",
                body='<node componentName="tRunJob">'
                     '<elementParameter name="UNIQUE_NAME" value="tRunJob_1"/>'
                     f'<elementParameter name="PROCESS:PROCESS_TYPE_PROCESS" value="{guid}"/>'
                     "</node>",
            )
        result = _scan(parser, cred_filter, tmp_path)

        callers = {job.source_root: job for job in result.jobs if job.name == "Caller"}
        workers = {job.source_root: job for job in result.jobs if job.name == "Worker"}
        assert set(callers) == {"ALPHA", "BETA"}
        for source_root, caller in callers.items():
            ref = caller.trunjob_refs[0]
            assert ref.reference_source == "identity"
            assert ref.resolution == "same_root"
            assert ref.resolved_job_id == workers[source_root].job_id

    def test_same_name_designs_in_one_root_are_split_by_identity_path(self, parser, cred_filter, tmp_path):
        project = tmp_path / "ORDERS"
        _write_design(
            project / "process" / "EU", name="Worker", version="0.1",
            property_id="_euWorker", body="",
        )
        _write_design(
            project / "process" / "US", name="Worker", version="0.1",
            property_id="_usWorker", body="",
        )
        _write_design(
            project / "process" / "Jobs", name="Caller", version="0.1",
            property_id="_caller",
            body='<node componentName="tRunJob">'
                 '<elementParameter name="UNIQUE_NAME" value="tRunJob_1"/>'
                 '<elementParameter name="PROCESS" value="Worker"/>'
                 '<elementParameter name="PROCESS:PROCESS_TYPE_PROCESS" value="_usWorker"/>'
                 "</node>",
        )
        result = _scan(parser, cred_filter, tmp_path)
        us_worker = next(
            job for job in result.jobs
            if job.name == "Worker" and job.path.startswith("US/")
        )
        ref = next(
            job for job in result.jobs if job.name == "Caller"
        ).trunjob_refs[0]

        # A label-only resolver reports this as ambiguous forever; the GUID
        # already named the exact repository path.
        assert ref.target_item_path == "process/US/Worker"
        assert ref.resolution == "same_root"
        assert ref.resolved_job_id == us_worker.job_id


# ── (f) Disabled components and links ────────────────────────────────


class TestActivationFlags:
    def test_disabled_component_and_connection_are_flagged(self, parser, cred_filter, tmp_path):
        _enterprise_project(tmp_path)
        result = _scan(parser, cred_filter, tmp_path)
        job = next(item for item in result.jobs if item.name == "Orders_Load")
        active = {c.unique_name: c.active for c in job.components}
        links = {c.name: c.active for c in job.connections}

        assert active["tLogRow_1"] is False
        # ACTIVATE is ABSENT on an active component: absence means active.
        assert active["tMap_1"] is True
        assert active["tDBInput_1"] is True
        assert links["row2"] is False
        assert links["row1"] is True
        assert result.stats["disabled_component_count"] == 1

    def test_component_schema_declaration_and_dynamic_flag(self, parser, cred_filter, tmp_path):
        _enterprise_project(tmp_path)
        result = _scan(parser, cred_filter, tmp_path)
        job = next(item for item in result.jobs if item.name == "Orders_Load")
        by_name = {c.unique_name: c for c in job.components}

        inline = by_name["tDBInput_1"].schemas[0]
        assert inline.connector == "FLOW"
        assert inline.declaration == "built_in"
        assert inline.dynamic is False
        assert [column.name for column in inline.columns] == [
            "order_id", "customer", "unused_col",
        ]
        assert inline.columns[0].key is True
        assert inline.columns[0].nullable is False
        assert inline.columns[0].comment == "PK"
        assert by_name["tDBInput_1"].declaration == "repository"
        assert by_name["tDBInput_1"].repository_property_ref == "_crmConn"

        repository = by_name["tFileInputDelimited_1"].schemas[0]
        assert repository.declaration == "repository"
        assert repository.repository_schema_ref == "_warehouseSchema"
        assert repository.dynamic is True
        assert repository.columns[0].dynamic is True


# ── File classification ──────────────────────────────────────────────


class TestFileClassification:
    @pytest.mark.parametrize(("relative", "expected"), [
        ("ORDERS/talend.project", FileType.PROJECT),
        ("ORDERS/Maps/Jobs/Orders_Load/map.xml", FileType.TDM_MAP),
        ("ORDERS/Structures/Order/OrderStruct_0.1.xml", FileType.STRUCTURE),
        ("ORDERS/Namespace Containers/ns.xml", FileType.NAMESPACE),
        ("ORDERS/route_resources/order_to_invoice.xsl", FileType.XSLT),
        ("ORDERS/resources/map.xslt", FileType.XSLT),
        ("ORDERS/services/OrderService_0.1.item", FileType.SERVICE),
        ("ORDERS/services/OrderService_0.1.wsdl", FileType.WSDL),
        ("ORDERS/resources/payload.jar", FileType.RESOURCE),
        # Route and routelet designs used to classify as UNKNOWN even
        # though the parser has always parsed them.
        ("ORDERS/routes/Inbound/Inbound_0.1.item", FileType.JOB),
        ("ORDERS/routes/Inbound/Inbound_0.1.properties", FileType.JOB_PROPERTIES),
        ("ORDERS/routelets/Frag/Frag_0.1.item", FileType.JOBLET),
        ("ORDERS/joblets/Frag/Frag_0.1.item", FileType.JOBLET),
        ("ORDERS/process/Orders/Orders_Load_1.0.item", FileType.JOB),
        ("ORDERS/logo.gif", FileType.UNKNOWN),
    ])
    def test_classification(self, tmp_path, relative, expected):
        assert TalendPlugin().classify_file(tmp_path / relative) is expected

    def test_every_walked_file_has_an_inventory_outcome(self, parser, cred_filter, tmp_path):
        _enterprise_project(tmp_path)
        result = _scan(parser, cred_filter, tmp_path)
        assert result.source_inventory
        assert all(entry.get("outcome") for entry in result.source_inventory)
        assert all(entry.get("kind") for entry in result.source_inventory)


# ── (g) Sanitized workspace ──────────────────────────────────────────


class TestSanitizedWorkspace:
    def _write(self, parser, cred_filter, tmp_path):
        _enterprise_project(tmp_path)
        result = _scan(parser, cred_filter, tmp_path)
        scan_dir = tmp_path / "out" / "scan"
        manifest = write_sanitized_workspace(result, scan_dir, cred_filter)
        return result, scan_dir, manifest

    def test_every_parsed_file_is_mirrored_with_a_matching_digest(self, parser, cred_filter, tmp_path):
        result, scan_dir, manifest = self._write(parser, cred_filter, tmp_path)
        mirrored = {entry["path"]: entry for entry in manifest["files"]}

        parsed = [
            entry["path"] for entry in result.source_inventory
            if entry["outcome"] == "parsed"
        ]
        assert parsed
        for path in parsed:
            assert path in mirrored, path
            written = (scan_dir / "sanitized" / path).read_bytes()
            assert hashlib.sha256(written).hexdigest() == mirrored[path]["sha256"]
            assert mirrored[path]["bytes"] == len(written)
        assert result.manifest.sanitized_workspace["file_count"] == str(len(mirrored))
        assert result.manifest.sanitized_workspace["path"] == "scan/sanitized/manifest.json"

    def test_written_manifest_matches_its_recorded_digest(self, parser, cred_filter, tmp_path):
        result, scan_dir, _ = self._write(parser, cred_filter, tmp_path)
        encoded = (scan_dir / "sanitized" / "manifest.json").read_text(encoding="utf-8")
        assert hashlib.sha256(encoded.encode("utf-8")).hexdigest() == (
            result.manifest.sanitized_workspace["sha256"]
        )
        assert json.loads(encoded)["capability_version"] == "talend-capability-2"

    def test_unprovable_file_is_excluded_by_reason_and_category_only(self, parser, cred_filter, tmp_path):
        _, scan_dir, manifest = self._write(parser, cred_filter, tmp_path)
        excluded = {entry["path"]: entry for entry in manifest["excluded"]}

        leaky = excluded["ORDERS/resources/leaky.xml"]
        assert leaky["reason"] == "residual_redaction_findings"
        assert leaky["categories"]
        assert not (scan_dir / "sanitized" / "ORDERS/resources/leaky.xml").exists()
        # The report names categories, never values.
        assert "Hunter2Secret" not in json.dumps(manifest)
        # Binaries are never copied: redaction cannot be verified on bytes
        # the filter cannot parse.
        assert excluded["ORDERS/resources/payload.jar"]["reason"] == "non_textual_extension"

    def test_no_written_file_carries_a_residual_finding(self, parser, cred_filter, tmp_path):
        _, scan_dir, manifest = self._write(parser, cred_filter, tmp_path)
        assert manifest["residual_free"] is True
        for entry in manifest["files"]:
            path = scan_dir / "sanitized" / entry["path"]
            text = path.read_text(encoding="utf-8")
            assert cred_filter.detect_residual(text, filename=entry["path"]) == {}
            assert "Hunter2Secret" not in text

    def test_context_password_is_redacted_in_the_mirrored_source(self, parser, cred_filter, tmp_path):
        _, scan_dir, _ = self._write(parser, cred_filter, tmp_path)
        mirrored = (
            scan_dir / "sanitized" / "ORDERS/context/envs_0.1.item"
        ).read_text(encoding="utf-8")

        # Layer 1's text patterns do not cover a Talend contextParameter;
        # only the structural layer did, and that one never touched the
        # source file. The mirror applies the same name-semantics rule.
        assert "Hunter2Secret" not in mirrored
        assert 'name="DbPassword"' in mirrored
        assert 'value="[REDACTED]"' in mirrored
        # A non-sensitive value is untouched: the mirror is evidence, not a
        # blanket redaction.
        assert 'value="STG_ORDERS"' in mirrored

    def test_plain_config_credentials_are_refused(self, parser, cred_filter, tmp_path):
        project = _enterprise_project(tmp_path)
        # Layer 1's key/value pattern is XML-attribute shaped, so a plain
        # `key=value` credential passes it untouched (verified). The mirror
        # must refuse such a file rather than publish it.
        (project / "resources" / "app.txt").write_text(
            "datasource.password=Hunter2Secret\n", encoding="utf-8",
        )
        result = _scan(parser, cred_filter, tmp_path)
        manifest = write_sanitized_workspace(
            result, tmp_path / "out" / "scan", cred_filter,
        )
        excluded = {entry["path"]: entry for entry in manifest["excluded"]}
        assert excluded["ORDERS/resources/app.txt"] == {
            "path": "ORDERS/resources/app.txt",
            "source_root": "ORDERS",
            "kind": "resource",
            "reason": "residual_redaction_findings",
            "categories": ["key_value_secret"],
        }


# ── (h) Capability ledger ────────────────────────────────────────────


class TestCapabilityLedger:
    def test_file_outcomes_account_for_every_inventory_row(self, parser, cred_filter, tmp_path):
        _enterprise_project(tmp_path)
        result = _scan(parser, cred_filter, tmp_path)
        ledger = result.capability_ledger

        assert ledger is not None
        assert ledger.capability_version == "talend-capability-2"
        assert sum(ledger.file_outcomes.values()) == len(result.source_inventory)
        assert set(ledger.file_outcomes) == {
            entry["outcome"] for entry in result.source_inventory
        }

    def test_construct_counts_are_derived_from_the_scan(self, parser, cred_filter, tmp_path):
        _enterprise_project(tmp_path)
        result = _scan(parser, cred_filter, tmp_path)
        rows = {
            (row.category, row.construct): row
            for row in result.capability_ledger.constructs
        }

        assert rows[("component", "disabled")].count == 1
        assert rows[("component", "disabled_connection")].count == 1
        assert rows[("schema", "dynamic")].status == "dynamic"
        assert rows[("schema", "dynamic")].count == 1
        assert rows[("schema", "inline")].count == 1
        assert rows[("mapping.tmap", "entry")].count == 8
        assert rows[("mapping.tmap", "lookup")].count == 1
        assert rows[("mapping.tmap", "reject_output")].count == 1
        assert rows[("reference.job_call", "identity")].count == 1
        assert rows[("reference.route_invocation", "identity")].count == 1
        assert rows[("structure", "definition")].count == 1
        assert rows[("structure", "element")].count == 6
        assert rows[("service", "operation")].count == 2
        assert rows[("reference.resource", "exact")].count == 1
        # A context/globalMap destination is not a shared channel identity.
        assert rows[("channel", "dynamic_destination")].status == "dynamic"
        assert ("channel", "literal_destination") not in rows
        # Zero-count rows are omitted: absence means "none in this scan".
        assert all(row.count > 0 for row in result.capability_ledger.constructs)

    def test_dynamic_channel_never_becomes_a_shared_dependency(self, parser, cred_filter, tmp_path):
        _enterprise_project(tmp_path)
        result = _scan(parser, cred_filter, tmp_path)
        channel = next(
            item for item in result.manifest.artifact_channels
            if item["component_name"] == "cJMS_1"
        )
        assert channel["channel_binding"] == "dynamic"
        assert channel["channel"] == "configured channel"
        assert result.manifest.job_connections_used == {}


# ── Services ─────────────────────────────────────────────────────────


class TestServiceDefinitions:
    def test_operations_come_from_the_item_and_its_wsdl(self, parser, cred_filter, tmp_path):
        _enterprise_project(tmp_path)
        result = _scan(parser, cred_filter, tmp_path)
        service = next(
            item for item in result.services if item.name == "OrderService"
        )
        assert service.parse_outcome == "parsed"
        assert service.wsdl_path == "services/OrderService_0.1.wsdl"
        assert service.property_id == "_serviceGuid"
        assert [(op.port_type, op.operation) for op in service.operations] == [
            ("OrderPort", "CreateOrder"), ("OrderPort", "GetOrder"),
        ]
        assert service.operations[0].input_message == "tns:CreateOrderRequest"
        assert service.operations[0].output_message == "tns:CreateOrderResponse"
        assert service.operations[1].output_message == ""


# ── Redaction gate coverage of the new collections ───────────────────


def test_new_collections_pass_the_layer_three_gate(parser, cred_filter, tmp_path):
    _enterprise_project(tmp_path)
    result = _scan(parser, cred_filter, tmp_path)
    verification = verify_scan_result(result, cred_filter)

    assert verification["passed"] is True, verification
    payload = result.model_dump_json()
    assert "Hunter2Secret" not in payload
    # The sweep must actually have walked the additive collections.
    assert result.structures and result.xslt_transforms and result.services
    assert result.mapping_accounting


def test_sensitive_table_row_is_redacted_and_verified(parser, cred_filter, tmp_path):
    project = tmp_path / "ORDERS"
    _write_design(
        project / "process" / "Orders", name="Orders_Load", version="1.0",
        property_id="_load",
        body='<node componentName="tRunJob">'
             '<elementParameter name="UNIQUE_NAME" value="tRunJob_1"/>'
             '<elementParameter name="PROCESS" value="Orders_Stage"/>'
             '<elementParameter field="TABLE" name="CONTEXTPARAMS" value="">'
             '<elementValue elementRef="PARAM_NAME_COLUMN" value="DbPassword"/>'
             '<elementValue elementRef="PARAM_VALUE_COLUMN" value="Hunter2Secret"/>'
             "</elementParameter></node>",
    )
    result = _scan(parser, cred_filter, tmp_path)
    job = result.jobs[0]
    param = next(p for p in job.components[0].params if p.name == "CONTEXTPARAMS")

    # The row states its own sensitivity in the NAME cell; the parameter is
    # called CONTEXTPARAMS, so a parameter-name check cannot see it.
    assert param.rows == [{
        "PARAM_NAME_COLUMN": "DbPassword",
        "PARAM_VALUE_COLUMN": "[REDACTED]",
    }]
    assert job.trunjob_refs[0].context_params[0].value == "[REDACTED]"
    assert verify_scan_result(result, cred_filter)["passed"] is True
    assert "Hunter2Secret" not in result.model_dump_json()


def test_historic_scan_json_still_loads_with_defaulted_new_fields():
    """Every field added by this slice is additive.

    A scan_result.json written before it must still load, and must not
    acquire a truthy value for anything nobody measured.
    """
    from intodoc.models import ScanResult as Model

    legacy = {
        "platform": "talend",
        "repo_root": "/sanitized/source",
        "files": [{
            "path": "/sanitized/source/ORDERS/process/A_0.1.item",
            "relative_path": "ORDERS/process/A_0.1.item",
            "file_type": "job", "sha256": "0" * 64, "size_bytes": 10,
        }],
        "jobs": [{
            "name": "A", "path": "A_0.1.item",
            "components": [{
                "unique_name": "tMap_1", "component_type": "tMap",
                "params": [{"name": "TABLE", "value": "STG"}],
            }],
            "connections": [{"name": "row1", "source": "a", "target": "b"}],
            "trunjob_refs": [{"child_job": "B"}],
            "artifact_refs": [{"target_name": "C", "relation_type": "job_calls_job"}],
        }],
        "manifest": {"job_calls": {"A": ["B"]}},
        "stats": {"total_jobs": 1},
    }
    result = Model.model_validate(legacy)

    assert result.structures == []
    assert result.xslt_transforms == []
    assert result.services == []
    assert result.mapping_accounting == []
    assert result.capability_ledger is None
    assert result.manifest.sanitized_workspace == {}
    assert result.manifest.resource_refs == []
    job = result.jobs[0]
    # Absent ACTIVATE means active, which is also the historic assumption.
    assert job.components[0].active is True
    assert job.components[0].schemas == []
    assert job.components[0].declaration == ""
    assert job.components[0].params[0].rows == []
    assert job.connections[0].active is True
    assert job.trunjob_refs[0].reference_source == "none"
    assert job.trunjob_refs[0].dynamic_job is False
    assert job.trunjob_refs[0].transmit_whole_context is None
    assert job.artifact_refs[0].use_route_context is None
    assert job.artifact_refs[0].context_params == []


# ── (i) Bundle-wide support-artifact discovery ───────────────────────


def _separately_uploaded_mappings(root):
    """The upload folder pattern that hid 34 of 42 stylesheets on a real estate.

    A customer ships the Talend export and its transformation files as two
    different archives. The mapping folder holds no ``talend.project``, so
    it is not a Talend project root -- but its stylesheets are the field
    mappings for the estate's largest integrations.
    """
    area = root / "Extra mappings" / "xsl"
    area.mkdir(parents=True)
    (area / "crm_to_warehouse.xsl").write_text(_XSLT, encoding="utf-8")
    (area / "PlanningTool.wsdl").write_text(_WSDL, encoding="utf-8")
    hidden = root / "Extra mappings" / ".backup"
    hidden.mkdir()
    (hidden / "crm_to_warehouse.xsl").write_text(_XSLT, encoding="utf-8")
    return area


class TestSupportArtifactDiscovery:
    """Discovery by file type spans the bundle; discovery by layout does not."""

    def test_stylesheet_outside_every_talend_root_is_parsed(
        self, parser, cred_filter, tmp_path,
    ):
        _enterprise_project(tmp_path)
        _separately_uploaded_mappings(tmp_path)

        result = _scan(parser, cred_filter, tmp_path)

        orphan = next(
            transform for transform in result.xslt_transforms
            if transform.name == "crm_to_warehouse.xsl"
        )
        assert orphan.parse_outcome == "parsed"
        assert [template.match for template in orphan.templates] == ["/Order"]
        # Attributed to the upload folder as a human named it, and joining
        # source_root to path reproduces the file-inventory path.
        assert orphan.source_root == "Extra mappings"
        assert orphan.path == "xsl/crm_to_warehouse.xsl"

    def test_in_project_stylesheet_keeps_its_project_attribution(
        self, parser, cred_filter, tmp_path,
    ):
        _enterprise_project(tmp_path)
        _separately_uploaded_mappings(tmp_path)

        result = _scan(parser, cred_filter, tmp_path)

        owned = next(
            transform for transform in result.xslt_transforms
            if transform.name == "order_to_invoice.xsl"
        )
        assert owned.source_root == "ORDERS"
        assert owned.path == "route_resources/order_to_invoice.xsl"

    def test_standalone_wsdl_outside_every_talend_root_is_parsed(
        self, parser, cred_filter, tmp_path,
    ):
        _enterprise_project(tmp_path)
        _separately_uploaded_mappings(tmp_path)

        result = _scan(parser, cred_filter, tmp_path)

        orphan = next(
            service for service in result.services
            if service.name == "PlanningTool"
        )
        assert orphan.source_root == "Extra mappings"
        assert orphan.path == "xsl/PlanningTool.wsdl"
        assert [operation.operation for operation in orphan.operations] == [
            "CreateOrder", "GetOrder",
        ]
        # The service item's own sibling WSDL is claimed by the item and is
        # not reported a second time by the bundle-wide pass.
        assert [
            (service.source_root, service.path) for service in result.services
        ] == [
            ("Extra mappings", "xsl/PlanningTool.wsdl"),
            ("ORDERS", "services/OrderService_0.1.item"),
        ]

    def test_hidden_directories_are_still_skipped(
        self, parser, cred_filter, tmp_path,
    ):
        _enterprise_project(tmp_path)
        _separately_uploaded_mappings(tmp_path)

        result = _scan(parser, cred_filter, tmp_path)

        assert [
            transform.path for transform in result.xslt_transforms
            if transform.source_root == "Extra mappings"
        ] == ["xsl/crm_to_warehouse.xsl"]

    def test_every_classified_stylesheet_row_reports_parsed(
        self, parser, cred_filter, tmp_path,
    ):
        _enterprise_project(tmp_path)
        _separately_uploaded_mappings(tmp_path)

        result = _scan(parser, cred_filter, tmp_path)

        stylesheet_rows = [
            entry for entry in result.source_inventory
            if entry["kind"] == FileType.XSLT.value
        ]
        assert stylesheet_rows, "fixture must produce classified stylesheet rows"
        # `classified_not_proof_of_parse` on a stylesheet is exactly the
        # state this discovery fix exists to remove: the scan knew the
        # filename and nothing else.
        assert {entry["outcome"] for entry in stylesheet_rows} == {"parsed"}

    def test_structures_stay_scoped_to_their_project(
        self, parser, cred_filter, tmp_path,
    ):
        """`Structures/` is a repository convention, not a file type.

        A directory of that name outside a Talend project proves nothing
        about its contents, so it is deliberately not searched bundle-wide.
        """
        _enterprise_project(tmp_path)
        stray = tmp_path / "Extra mappings" / "Structures"
        stray.mkdir(parents=True)
        (stray / "StrayStruct_0.1.xml").write_text(_STRUCTURE, encoding="utf-8")

        result = _scan(parser, cred_filter, tmp_path)

        assert [structure.source_root for structure in result.structures] == ["ORDERS"]

    def test_unscanned_directory_note_names_the_files_not_the_directory(
        self, parser, cred_filter, tmp_path,
    ):
        """`resources/` holds parsed stylesheets and unparsed payloads both.

        Reporting the whole directory as unparsed denied evidence that the
        scan demonstrably holds, which is the wording a client document then
        repeats back.
        """
        _enterprise_project(tmp_path)
        (tmp_path / "ORDERS" / "resources" / "extra.xslt").write_text(
            _XSLT, encoding="utf-8",
        )

        result = _scan(parser, cred_filter, tmp_path)

        note = next(
            warning.message for warning in result.warnings
            if warning.kind == "unscanned_category"
            and warning.file.endswith("resources")
        )
        assert note == (
            "Talend repository directory 'resources': 3 files, 1 parsed as "
            "supported artifacts, 2 of a kind this scanner does not parse -- "
            "those are invisible to downstream analysis."
        )
