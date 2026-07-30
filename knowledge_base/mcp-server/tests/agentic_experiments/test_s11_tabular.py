from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from mcp_server.experiments.domain.sources import SourceDescriptor, SourceLimits
from mcp_server.experiments.infrastructure.sources.registry import SourceAdapterError
from mcp_server.experiments.infrastructure.sources.tabular import TabularFileAdapter, TabularLimits


FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "agentic_experiments" / "logistics_operations"


def _descriptor(path: Path) -> SourceDescriptor:
    return SourceDescriptor(
        id="logistics-shipments",
        source_kind="tabular_file",
        source_role="shipment_events",
        source_path=path,
        provenance_locator="fixture://logistics/shipments",
        capabilities=frozenset({"tabular_data", "structured_records"}),
        trust_level="governed_fixture",
    )


def _write_xlsx(path: Path, *, extra_parts: dict[str, str] | None = None) -> None:
    workbook = """<?xml version=\"1.0\"?><workbook xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\" xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\"><sheets><sheet name=\"Shipments\" sheetId=\"1\" r:id=\"rId1\"/></sheets></workbook>"""
    rels = """<?xml version=\"1.0\"?><Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\"><Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet\" Target=\"worksheets/sheet1.xml\"/></Relationships>"""
    sheet = """<?xml version=\"1.0\"?><worksheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\"><sheetData><row r=\"1\"><c r=\"A1\" t=\"inlineStr\"><is><t>shipment_id</t></is></c><c r=\"B1\" t=\"inlineStr\"><is><t>status</t></is></c></row><row r=\"2\"><c r=\"A2\" t=\"inlineStr\"><is><t>SHP-200</t></is></c><c r=\"B2\"><f>SUM(1,2)</f><v>3</v></c></row></sheetData></worksheet>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", rels)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
        for name, content in (extra_parts or {}).items():
            archive.writestr(name, content)


def test_csv_logistics_fixture_produces_bounded_generic_row_items() -> None:
    result = TabularFileAdapter().snapshot(_descriptor(FIXTURE_ROOT / "shipments.csv"), SourceLimits())

    assert len(result.items) == 2
    second = result.items[1]
    assert second.source_role == "shipment_events"
    assert second.capabilities == frozenset({"tabular_data", "structured_records"})
    assert second.redaction_state.value == "approved"
    assert second.locator.endswith("#row=3")
    assert second.metadata["formula_like_cells"] == ["B"]
    assert second.metadata["formula_policy"] == "preserved_as_text_not_evaluated"


def test_xlsx_formula_is_preserved_but_never_evaluated(tmp_path: Path) -> None:
    xlsx = tmp_path / "shipments.xlsx"
    _write_xlsx(xlsx)

    result = TabularFileAdapter().snapshot(_descriptor(xlsx), SourceLimits())

    item = result.items[0]
    assert item.locator.endswith("#sheet=Shipments&row=2")
    assert item.metadata["formula_cells"] == ["B"]
    assert item.metadata["values"][1]["formula"] == "SUM(1,2)"
    assert item.metadata["values"][1]["value"] is None


@pytest.mark.parametrize(
    ("extra_parts", "limits", "message"),
    [
        ({"xl/vbaProject.bin": "macro"}, TabularLimits(), "macro/VBA"),
        ({"xl/externalLinks/externalLink1.xml": "<externalLink/>"}, TabularLimits(), "external"),
        ({"../zip-slip": "bad"}, TabularLimits(), "unsafe member path"),
        ({"xl/styles.xml": "x" * 4096}, TabularLimits(max_zip_part_bytes=2048), "ZIP part"),
        (
            {"xl/sharedStrings.xml": "<sst xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\"><si><t>a</t></si><si><t>b</t></si></sst>"},
            TabularLimits(max_shared_strings=1),
            "shared string limit",
        ),
    ],
)
def test_xlsx_adversarial_archives_are_rejected(
    tmp_path: Path, extra_parts: dict[str, str], limits: TabularLimits, message: str
) -> None:
    xlsx = tmp_path / "unsafe.xlsx"
    _write_xlsx(xlsx, extra_parts=extra_parts)

    with pytest.raises(SourceAdapterError, match=message):
        TabularFileAdapter(limits=limits).snapshot(_descriptor(xlsx), SourceLimits())


def test_csv_cell_and_row_limits_are_enforced(tmp_path: Path) -> None:
    csv_path = tmp_path / "oversized.csv"
    csv_path.write_text("id,value\n1,too-large\n2,also-large\n", encoding="utf-8")

    with pytest.raises(SourceAdapterError, match="cell"):
        TabularFileAdapter(limits=TabularLimits(max_cell_bytes=3)).snapshot(
            _descriptor(csv_path), SourceLimits()
        )
    with pytest.raises(SourceAdapterError, match="row"):
        TabularFileAdapter(limits=TabularLimits(max_rows=1)).snapshot(
            _descriptor(csv_path), SourceLimits()
        )
