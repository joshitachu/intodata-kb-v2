"""Bounded, read-only CSV/XLSX source adapter without spreadsheet execution."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterable
from xml.etree import ElementTree as ET

from mcp_server.experiments.domain.sources import (
    RedactionState,
    SourceDescriptor,
    SourceLimits,
    SourceSnapshotContent,
    SourceSnapshotItem,
)
from mcp_server.experiments.infrastructure.sources.registry import (
    AdapterSnapshotResult,
    CapturedRawSource,
    SourceAdapterError,
    read_regular,
)


_FORMULA_PREFIX = re.compile(r"^\s*[=+@-]")
_CELL_REFERENCE = re.compile(r"^([A-Z]+)([0-9]+)$")
_XML_NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_REL_NS = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}
_WORKBOOK_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


@dataclass(frozen=True, slots=True)
class TabularLimits:
    max_file_bytes: int = 8 * 1024 * 1024
    max_rows: int = 10_000
    max_columns: int = 128
    max_cell_bytes: int = 16 * 1024
    max_zip_members: int = 256
    max_zip_part_bytes: int = 2 * 1024 * 1024
    max_zip_uncompressed_bytes: int = 16 * 1024 * 1024
    max_shared_strings: int = 50_000

    def __post_init__(self) -> None:
        if any(
            value < 1
            for value in (
                self.max_file_bytes,
                self.max_rows,
                self.max_columns,
                self.max_cell_bytes,
                self.max_zip_members,
                self.max_zip_part_bytes,
                self.max_zip_uncompressed_bytes,
                self.max_shared_strings,
            )
        ):
            raise ValueError("tabular limits must be positive")


class TabularFileAdapter:
    """Produces one generic evidence item per bounded data row."""

    id = "tabular-file"
    version = "1"

    def __init__(self, *, limits: TabularLimits = TabularLimits()) -> None:
        self._limits = limits

    def supports(self, descriptor: SourceDescriptor) -> bool:
        return descriptor.source_kind == "tabular_file"

    def snapshot(self, descriptor: SourceDescriptor, limits: SourceLimits) -> AdapterSnapshotResult:
        suffix = descriptor.source_path.suffix.casefold()
        max_bytes = min(limits.max_bytes, self._limits.max_file_bytes)
        raw = read_regular(descriptor.source_path, max_bytes=max_bytes)
        if suffix == ".csv":
            rows = self._read_csv(raw)
            media_type = "text/csv"
            source_name = descriptor.source_path.name
            captured = self._row_items(
                descriptor=descriptor,
                source_name=source_name,
                raw=raw,
                media_type=media_type,
                rows=rows,
            )
            return AdapterSnapshotResult(
                items=tuple(item for item, _ in captured),
                contents=tuple(content for _, content in captured),
                raw_sources=(
                    CapturedRawSource(
                        f"{descriptor.id}:{source_name}",
                        hashlib.sha256(raw).hexdigest(),
                        len(raw),
                    ),
                ),
            )
        if suffix == ".xlsx":
            sheets = self._read_xlsx(raw)
            captured: list[tuple[SourceSnapshotItem, SourceSnapshotContent]] = []
            for sheet_name, rows in sheets:
                captured.extend(
                    self._row_items(
                        descriptor=descriptor,
                        source_name=descriptor.source_path.name,
                        raw=raw,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        rows=rows,
                        sheet_name=sheet_name,
                    )
                )
            if not captured:
                raise SourceAdapterError("XLSX source contains no data rows")
            if len(captured) > self._limits.max_rows:
                raise SourceAdapterError("tabular row limit exceeded")
            return AdapterSnapshotResult(
                items=tuple(item for item, _ in captured),
                contents=tuple(content for _, content in captured),
                raw_sources=(
                    CapturedRawSource(
                        f"{descriptor.id}:{descriptor.source_path.name}",
                        hashlib.sha256(raw).hexdigest(),
                        len(raw),
                    ),
                ),
            )
        raise SourceAdapterError("tabular adapter supports only .csv and .xlsx files")

    def _read_csv(self, raw: bytes) -> tuple[tuple[int, tuple[str, ...]], ...]:
        decoded: str | None = None
        for encoding in ("utf-8-sig", "utf-8"):
            try:
                decoded = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if decoded is None:
            raise SourceAdapterError("CSV encoding must be UTF-8 or UTF-8 with BOM")
        sample = decoded[:8192]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(io.StringIO(decoded, newline=""), dialect=dialect)
        rows: list[tuple[int, tuple[str, ...]]] = []
        for row_number, row in enumerate(reader, start=1):
            if len(row) > self._limits.max_columns:
                raise SourceAdapterError("CSV column limit exceeded")
            normalised = tuple(self._bounded_cell(value) for value in row)
            if row_number > self._limits.max_rows + 1:
                raise SourceAdapterError("CSV row limit exceeded")
            rows.append((row_number, normalised))
        if not rows:
            raise SourceAdapterError("CSV source is empty")
        return tuple(rows)

    def _read_xlsx(
        self, raw: bytes
    ) -> tuple[tuple[str, tuple[tuple[int, tuple[dict[str, object], ...]], ...]], ...]:
        try:
            archive = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile as error:
            raise SourceAdapterError("XLSX source is not a valid ZIP archive") from error
        with archive:
            infos = archive.infolist()
            self._validate_archive(infos)
            names = {info.filename for info in infos}
            if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
                raise SourceAdapterError("XLSX workbook parts are missing")
            lowered = {name.casefold() for name in names}
            if any(name.endswith("vbaproject.bin") for name in lowered):
                raise SourceAdapterError("XLSX macro/VBA content is not allowed")
            if "xl/connections.xml" in lowered or any(
                name.startswith("xl/externallinks/") for name in lowered
            ):
                raise SourceAdapterError("XLSX external connections/links are not allowed")
            shared_strings = self._shared_strings(archive, names)
            sheet_paths = self._sheet_paths(archive, names)
            sheets = []
            total_rows = 0
            for sheet_name, path in sheet_paths:
                rows = self._worksheet_rows(archive, path, shared_strings)
                total_rows += max(0, len(rows) - 1)
                if total_rows > self._limits.max_rows:
                    raise SourceAdapterError("XLSX row limit exceeded")
                sheets.append((sheet_name, rows))
            return tuple(sheets)

    def _validate_archive(self, infos: Iterable[zipfile.ZipInfo]) -> None:
        infos = tuple(infos)
        if len(infos) > self._limits.max_zip_members:
            raise SourceAdapterError("XLSX ZIP member limit exceeded")
        total = 0
        for info in infos:
            path = PurePosixPath(info.filename)
            if "\\" in info.filename or path.is_absolute() or ".." in path.parts:
                raise SourceAdapterError("XLSX archive contains unsafe member path")
            if info.flag_bits & 0x1:
                raise SourceAdapterError("encrypted XLSX archives are not allowed")
            if info.file_size > self._limits.max_zip_part_bytes:
                raise SourceAdapterError("XLSX ZIP part exceeds size limit")
            if info.file_size and info.compress_size == 0:
                raise SourceAdapterError("XLSX ZIP member has invalid compression size")
            if info.compress_size and info.file_size / info.compress_size > 100:
                raise SourceAdapterError("XLSX ZIP compression ratio exceeds limit")
            total += info.file_size
            if total > self._limits.max_zip_uncompressed_bytes:
                raise SourceAdapterError("XLSX ZIP uncompressed size limit exceeded")

    def _xml(self, archive: zipfile.ZipFile, name: str) -> ET.Element:
        try:
            payload = archive.read(name)
        except KeyError as error:
            raise SourceAdapterError(f"XLSX part is missing: {name}") from error
        if len(payload) > self._limits.max_zip_part_bytes:
            raise SourceAdapterError("XLSX XML part exceeds size limit")
        if b"<!DOCTYPE" in payload or b"<!ENTITY" in payload:
            raise SourceAdapterError("XLSX XML entities are not allowed")
        try:
            return ET.fromstring(payload)
        except ET.ParseError as error:
            raise SourceAdapterError(f"XLSX XML is malformed: {name}") from error

    def _shared_strings(self, archive: zipfile.ZipFile, names: set[str]) -> tuple[str, ...]:
        if "xl/sharedStrings.xml" not in names:
            return ()
        root = self._xml(archive, "xl/sharedStrings.xml")
        values = []
        for item in root.findall("x:si", _XML_NS):
            if len(values) >= self._limits.max_shared_strings:
                raise SourceAdapterError("XLSX shared string limit exceeded")
            values.append(self._bounded_cell("".join(item.itertext())))
        return tuple(values)

    def _sheet_paths(
        self, archive: zipfile.ZipFile, names: set[str]
    ) -> tuple[tuple[str, str], ...]:
        rels_name = "xl/_rels/workbook.xml.rels"
        if rels_name not in names:
            raise SourceAdapterError("XLSX workbook relationships are missing")
        rels = self._xml(archive, rels_name)
        targets = {
            relationship.attrib.get("Id"): relationship.attrib.get("Target", "")
            for relationship in rels.findall("r:Relationship", _REL_NS)
            if relationship.attrib.get("Type", "").startswith(_WORKBOOK_REL)
        }
        workbook = self._xml(archive, "xl/workbook.xml")
        sheets = []
        for sheet in workbook.findall("x:sheets/x:sheet", _XML_NS):
            sheet_name = sheet.attrib.get("name", "").strip()
            relation_id = sheet.attrib.get(
                "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
            )
            target = targets.get(relation_id, "")
            if not sheet_name or not target:
                raise SourceAdapterError("XLSX sheet relationship is invalid")
            path = PurePosixPath("xl") / target
            if path.is_absolute() or ".." in path.parts or str(path) not in names:
                raise SourceAdapterError("XLSX worksheet relationship has unsafe target")
            sheets.append((sheet_name, str(path)))
        if not sheets:
            raise SourceAdapterError("XLSX workbook has no worksheets")
        return tuple(sheets)

    def _worksheet_rows(
        self, archive: zipfile.ZipFile, path: str, shared_strings: tuple[str, ...]
    ) -> tuple[tuple[int, tuple[dict[str, object], ...]], ...]:
        root = self._xml(archive, path)
        rows = []
        for row in root.findall("x:sheetData/x:row", _XML_NS):
            row_number = int(row.attrib.get("r", len(rows) + 1))
            cells = []
            for cell in row.findall("x:c", _XML_NS):
                reference = cell.attrib.get("r", "")
                match = _CELL_REFERENCE.match(reference)
                if match is None:
                    raise SourceAdapterError("XLSX cell reference is invalid")
                column_index = self._column_index(match.group(1))
                if column_index > self._limits.max_columns:
                    raise SourceAdapterError("XLSX column limit exceeded")
                formula = cell.findtext("x:f", default=None, namespaces=_XML_NS)
                value = None if formula is not None else self._cell_value(cell, shared_strings)
                cells.append(
                    {
                        "column": match.group(1),
                        "column_index": column_index,
                        "value": value,
                        "formula": None if formula is None else self._bounded_cell(formula),
                    }
                )
            rows.append(
                (row_number, tuple(sorted(cells, key=lambda item: int(item["column_index"]))))
            )
        return tuple(rows)

    def _cell_value(self, cell: ET.Element, shared_strings: tuple[str, ...]) -> str:
        cell_type = cell.attrib.get("t")
        if cell_type == "inlineStr":
            return self._bounded_cell(
                "".join(cell.find("x:is", _XML_NS).itertext())
                if cell.find("x:is", _XML_NS) is not None
                else ""
            )
        raw_value = cell.findtext("x:v", default="", namespaces=_XML_NS)
        if cell_type == "s":
            try:
                return shared_strings[int(raw_value)]
            except (IndexError, ValueError) as error:
                raise SourceAdapterError("XLSX shared string reference is invalid") from error
        return self._bounded_cell(raw_value)

    def _row_items(
        self,
        *,
        descriptor: SourceDescriptor,
        source_name: str,
        raw: bytes,
        media_type: str,
        rows: tuple[tuple[int, tuple[object, ...]], ...],
        sheet_name: str | None = None,
    ) -> tuple[tuple[SourceSnapshotItem, SourceSnapshotContent], ...]:
        if len(rows) < 2:
            raise SourceAdapterError("tabular source requires a header and at least one data row")
        headers = self._headers(rows[0][1])
        raw_sha = hashlib.sha256(raw).hexdigest()
        captured = []
        for row_number, row in rows[1:]:
            values = self._normalised_row(row, headers)
            formula_cells = [value["column"] for value in values if value.get("formula")]
            formula_like_cells = [
                value["column"]
                for value in values
                if isinstance(value.get("value"), str) and _FORMULA_PREFIX.match(value["value"])
            ]
            identity_suffix = (
                f"sheet={sheet_name}&row={row_number}" if sheet_name else f"row={row_number}"
            )
            logical_identity = f"{descriptor.id}:{source_name}#{identity_suffix}"
            extracted = {
                "columns": headers,
                "formula_policy": "preserved_as_text_not_evaluated",
                "row": row_number,
                "sheet": sheet_name,
                "values": values,
            }
            encoded = json.dumps(
                extracted, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            metadata: dict[str, object] = {
                "format": "xlsx" if sheet_name else "csv",
                "columns": headers,
                "row": row_number,
                "sheet": sheet_name,
                "values": values,
                "formula_policy": "preserved_as_text_not_evaluated",
                "formula_cells": formula_cells,
                "formula_like_cells": formula_like_cells,
            }
            item = SourceSnapshotItem(
                id=hashlib.sha256(logical_identity.encode("utf-8")).hexdigest(),
                source_descriptor_id=descriptor.id,
                logical_identity=logical_identity,
                source_kind=descriptor.source_kind,
                source_role=descriptor.source_role,
                capabilities=descriptor.capabilities,
                locator=f"{descriptor.provenance_locator}#{identity_suffix}",
                raw_sha256=raw_sha,
                extracted_sha256=hashlib.sha256(encoded).hexdigest(),
                media_type=media_type,
                size_bytes=len(raw),
                extracted_size_bytes=len(encoded),
                trust_level=descriptor.trust_level,
                redaction_state=RedactionState.APPROVED,
                adapter_id=self.id,
                adapter_version=self.version,
                metadata=metadata,
            )
            captured.append(
                (
                    item,
                    SourceSnapshotContent(
                        item_id=item.id,
                        extracted_bytes=encoded,
                        extracted_sha256=item.extracted_sha256,
                        media_type="application/json",
                        size_bytes=len(encoded),
                    ),
                )
            )
        return tuple(captured)

    def _headers(self, header_row: tuple[object, ...]) -> tuple[str, ...]:
        headers: list[str] = []
        for index, value in enumerate(header_row, start=1):
            if isinstance(value, dict):
                candidate = str(value.get("value") or value.get("column") or "")
            else:
                candidate = str(value)
            candidate = " ".join(candidate.split()) or f"column_{index}"
            count = sum(
                existing == candidate or existing.startswith(f"{candidate}_")
                for existing in headers
            )
            headers.append(candidate if count == 0 else f"{candidate}_{count + 1}")
        return tuple(headers)

    def _normalised_row(
        self, row: tuple[object, ...], headers: tuple[str, ...]
    ) -> list[dict[str, object]]:
        if len(row) > self._limits.max_columns:
            raise SourceAdapterError("tabular column limit exceeded")
        values: list[dict[str, object]] = []
        for index, raw_value in enumerate(row, start=1):
            if isinstance(raw_value, dict):
                value = raw_value.get("value")
                formula = raw_value.get("formula")
                column = str(raw_value.get("column", self._column_name(index)))
            else:
                value = raw_value
                formula = None
                column = self._column_name(index)
            values.append(
                {
                    "column": column,
                    "column_index": index,
                    "header": headers[index - 1] if index <= len(headers) else f"column_{index}",
                    "value": None if value is None else self._bounded_cell(str(value)),
                    "formula": None if formula is None else self._bounded_cell(str(formula)),
                }
            )
        return values

    def _bounded_cell(self, value: str) -> str:
        if len(value.encode("utf-8")) > self._limits.max_cell_bytes:
            raise SourceAdapterError("tabular cell exceeds byte limit")
        return value

    @staticmethod
    def _column_index(column: str) -> int:
        value = 0
        for character in column:
            value = value * 26 + ord(character) - ord("A") + 1
        return value

    @staticmethod
    def _column_name(index: int) -> str:
        result = ""
        while index:
            index, remainder = divmod(index - 1, 26)
            result = chr(ord("A") + remainder) + result
        return result
