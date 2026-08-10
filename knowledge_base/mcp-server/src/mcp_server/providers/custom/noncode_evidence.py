"""Tier-3 non-code documented context for the legacy doc pipeline.

The Talend scanner parses the *code* export tree (jobs, routes, contexts). A
real project ships alongside it a mass of non-code material the scanner never
reads: Excel schema exports, XSLT/XSL field mappings, business notes, sample
request/response XML. This module walks that material, turns each file into
bounded redacted text with generic content probes, and associates it to the
units it describes so per-unit Phase 3 evidence bundles and the summary docs
gain documented context.

Architecture boundary
---------------------
This is a COPY / adaptation of the grounded pipeline's
``experiments/application/prepare_supporting_evidence.py`` (probes, redaction,
unsupported classification, caps) and ``mapping_association.py`` (unit
association). ``providers/custom`` must NOT import ``experiments/*`` -- the
dependency-direction guard forbids it -- so the reusable logic is copied here,
exactly as ``target_evidence.py`` was copied from the grounded unit-evidence
extractor rather than importing it.

CREDENTIAL SAFETY
-----------------
Every byte handed onward is passed through :func:`redact_text` first. No raw
file content ever leaves this module unredacted. Association keys on shared
identifiers only; the redaction placeholders themselves are excluded from
association so ``[REDACTED]`` never becomes a match driver.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
import zipfile
from collections import Counter
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Iterator, Mapping, Protocol, Sequence
from xml.etree import ElementTree as ET

from mcp_server.providers.custom.target_evidence import (
    HIGH_SIGNAL_PARAM_NAMES,
    HIGH_SIGNAL_PARAM_PATTERN,
    param_map,
)

logger = logging.getLogger("mcp_server.providers.custom.noncode_evidence")


# ── redaction (copied from prepare_supporting_evidence) ──────────────────────

_SENSITIVE_ASSIGNMENT = re.compile(
    r'''(?im)(?P<key>\b(?:password|passwd|pwd|secret|token|api[_-]?key|apikey|credential|clientsecret|authorization|cookie|set-cookie|x-api-key)\b\s*(?::|=)\s*)(?P<value>(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+|"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|[^\s,;&}]+)'''
)
_AUTH_SCHEME = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
_URL_USERINFO = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@")
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL
)
_JWT = re.compile(
    r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])"
)

# ── tabular credential redaction ──────────────────────────────────────────────
#
# Verified leak (werkorder run 74): ``TableExports/talendesb esb_ftp.xlsx``
# renders as delimiter-joined rows -- a header row naming an ``ftp_pw`` column
# and data rows carrying the plaintext FTP password. ``_SENSITIVE_ASSIGNMENT``
# only catches ``key: value`` / ``key=value`` shapes, so credential COLUMNS in
# tabular sources (xlsx probes, csv/tsv text) sailed through unredacted and
# were published in 05_RUNTIME_CONFIG_AND_EXTERNALS.md. This pass is generic
# over any delimiter-structured text, not xlsx-specific.

# Credential-named tokens matched against the words of a header cell (split on
# non-alphanumerics), so ``ftp_pw``, ``API Key``, ``ClientSecret`` and
# ``connection_string`` all hit without a per-domain list. Deliberately narrow
# single words (no bare ``key``/``auth``/``pass`` -- "Primary Key" and
# pass/fail columns are not credentials); compounds are matched pairwise.
_CREDENTIAL_WORDS = frozenset({
    "password", "passwd", "pwd", "pw", "secret", "secrets", "token", "tokens",
    "apikey", "credential", "credentials", "connectionstring", "passphrase",
    "privatekey", "clientsecret",
})
_CREDENTIAL_WORD_PAIRS = frozenset({
    "apikey", "clientsecret", "connectionstring", "accesskey", "secretkey",
    "privatekey", "apitoken", "accesstoken", "refreshtoken", "authtoken",
})
# Exact whole-header matches only (too generic as words inside longer names).
_CREDENTIAL_EXACT_HEADERS = frozenset({"pat"})
_CELL_WORD_RE = re.compile(r"[a-z0-9]+")
# A header cell is a short identifier-ish label, never a value with symbols.
_HEADER_CELL_RE = re.compile(r"[A-Za-z][\w .\-/()#]{0,63}")
_TABLE_DELIMITERS = ("|", "\t", ";", ",")
_REDACTED_CELL = "[REDACTED]"


def _is_credential_header(cell: str) -> bool:
    words = _CELL_WORD_RE.findall(cell.casefold())
    if not words:
        return False
    if "".join(words) in _CREDENTIAL_EXACT_HEADERS | _CREDENTIAL_WORDS:
        return True
    if any(word in _CREDENTIAL_WORDS for word in words):
        return True
    return any(f"{a}{b}" in _CREDENTIAL_WORD_PAIRS for a, b in zip(words, words[1:]))


def _looks_like_header_row(cells: list[str]) -> bool:
    populated = [cell for cell in cells if cell]
    if not populated:
        return False
    return all(_HEADER_CELL_RE.fullmatch(cell) for cell in populated)


def _redact_tabular_credentials(text: str) -> tuple[str, int]:
    """Redact cells under credential-named columns in delimiter-structured text.

    Streaming, per-delimiter: a header row (all identifier-ish cells, at least
    one credential-named) records which column positions are credentials; the
    data rows that follow with the same delimiter get those cells replaced with
    ``[REDACTED]``. A blank line or a delimiter-free line ends the table. A
    two-cell ``credential-name | value`` row is treated as a key/value pair
    regardless of header state (transposed exports).
    """
    lines = text.split("\n")
    redactions = 0
    # delimiter -> (column_count, credential column indices)
    active: dict[str, tuple[int, tuple[int, ...]]] = {}
    for index, line in enumerate(lines):
        if not line.strip():
            active.clear()
            continue
        for delimiter in _TABLE_DELIMITERS:
            if delimiter not in line:
                active.pop(delimiter, None)
                continue
            cells = [cell.strip() for cell in line.split(delimiter)]
            if len(cells) < 2:
                continue
            cred_cols = tuple(
                i for i, cell in enumerate(cells) if _is_credential_header(cell)
            )
            if cred_cols and _looks_like_header_row(cells):
                active[delimiter] = (len(cells), cred_cols)
                continue
            changed = False
            # Transposed key/value row: ``ftp_pw | <secret>``.
            if len(cells) == 2 and _is_credential_header(cells[0]) and cells[1]:
                if cells[1] != _REDACTED_CELL:
                    cells[1] = _REDACTED_CELL
                    changed = True
            header = active.get(delimiter)
            if header is not None:
                column_count, columns = header
                if len(cells) == column_count:
                    for col in columns:
                        if cells[col] and cells[col] != _REDACTED_CELL:
                            cells[col] = _REDACTED_CELL
                            changed = True
            if changed:
                lines[index] = f" {delimiter} ".join(cells) if delimiter == "|" else delimiter.join(cells)
                redactions += 1
    return "\n".join(lines), redactions


def redact_text(text: str) -> tuple[str, int]:
    """Strip credentials from text before it can reach any consumer.

    Returns the redacted text and the number of redactions applied. Mirrors the
    grounded preparation service so the two pipelines redact identically.
    """
    text, tabular_changes = _redact_tabular_credentials(text)
    text, assignment_changes = _SENSITIVE_ASSIGNMENT.subn(
        lambda m: f"{m.group('key')}[REDACTED]", text
    )
    text, url_changes = _URL_USERINFO.subn(r"\1[REDACTED]@", text)
    text, auth_changes = _AUTH_SCHEME.subn(r"\1 [REDACTED]", text)
    text, key_changes = _PRIVATE_KEY.subn("[REDACTED PRIVATE KEY]", text)
    text, jwt_changes = _JWT.subn("[REDACTED JWT]", text)
    return text, (
        tabular_changes + assignment_changes + url_changes + auth_changes
        + key_changes + jwt_changes
    )


# ── content probes (copied/adapted from prepare_supporting_evidence) ─────────


class ContentProbe(Protocol):
    """A generic, non-executing capability probe for one bounded raw file."""

    id: str
    version: str

    def extract(self, raw: bytes, *, max_bytes: int) -> bytes | None: ...


class UtfTextProbe:
    """Safely accepts ordinary UTF text without inferring a business file type."""

    id = "utf-text"
    version = "1"

    def extract(self, raw: bytes, *, max_bytes: int) -> bytes | None:
        if not raw or b"\x00" in raw[:max_bytes]:
            return None
        text: str | None = None
        for encoding in ("utf-8-sig", "utf-8", "utf-16"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            return None
        if any(ord(character) < 32 and character not in "\n\r\t" for character in text):
            return None
        return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


class OfficeOpenXmlProbe:
    """Extract bounded text from a safe Office Open XML package.

    Container-level probe, not an Excel/Word business parser: it accepts only
    ordinary OOXML ZIP packages and never runs macros, formulas, or external
    links. For ``.xlsx`` workbooks it additionally resolves the shared-string
    table so worksheet cells read as their text values (column headers *and*
    row data) instead of opaque ``<v>`` indices. Numeric/date cells still come
    through as their raw stored value (date serials are not converted) -- a
    documented, accepted limitation.
    """

    id = "office-open-xml"
    version = "2"
    _MAX_MEMBERS = 512
    _MAX_MEMBER_BYTES = 4 * 1024 * 1024
    _MAX_UNCOMPRESSED_BYTES = 32 * 1024 * 1024
    _MAX_COMPRESSION_RATIO = 100
    _XML_PREFIXES = ("word/", "xl/", "ppt/")
    _SHEET_RE = re.compile(r"(?i)^xl/worksheets/.*\.xml$")

    def extract(self, raw: bytes, *, max_bytes: int) -> bytes | None:
        if not raw.startswith(b"PK"):
            return None
        try:
            archive = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile:
            return None
        with archive:
            infos = archive.infolist()
            if len(infos) > self._MAX_MEMBERS:
                return None
            names = {info.filename for info in infos}
            if "[Content_Types].xml" not in names:
                return None
            if not any(name.startswith(self._XML_PREFIXES) for name in names):
                return None
            is_xlsx = any(self._SHEET_RE.match(name) for name in names)
            shared_strings = (
                self._shared_strings(archive) if is_xlsx else []
            )
            total = 0
            used = 0
            parts: list[str] = []
            for info in sorted(infos, key=lambda item: item.filename):
                if not self._safe_member(info):
                    return None
                total += info.file_size
                if total > self._MAX_UNCOMPRESSED_BYTES:
                    return None
                if (
                    not info.filename.startswith(self._XML_PREFIXES)
                    or not info.filename.casefold().endswith(".xml")
                ):
                    continue
                # The shared-string table is rendered inline into the sheets;
                # emitting it again as a flat blob is noise.
                if is_xlsx and info.filename.casefold() == "xl/sharedstrings.xml":
                    continue
                try:
                    payload = archive.read(info)
                except (OSError, RuntimeError, zipfile.BadZipFile):
                    return None
                if is_xlsx and self._SHEET_RE.match(info.filename):
                    text = self._sheet_text(payload, shared_strings)
                else:
                    text = self._xml_text(payload)
                if not text:
                    continue
                part = f"[Office XML part: {info.filename}]\n{text}"
                encoded = part.encode("utf-8")
                remaining = max_bytes - used
                if len(encoded) <= remaining:
                    parts.append(part)
                    used += len(encoded)
                    continue
                # A part larger than the remaining budget is truncated on a line
                # boundary (keeping the header row and as many data rows as fit)
                # rather than dropping the whole file -- schema headers matter and
                # a silent whole-file drop is exactly what we are avoiding.
                if remaining > 0:
                    slice_text = encoded[:remaining].decode("utf-8", errors="ignore")
                    slice_text = slice_text.rsplit("\n", 1)[0] if "\n" in slice_text else slice_text
                    if slice_text.strip():
                        parts.append(
                            slice_text
                            + f"\n[... {info.filename} truncated at the "
                            f"{max_bytes}-byte per-file probe budget ...]"
                        )
                break
            if not parts:
                return None
            return "\n\n".join(parts).encode("utf-8")

    def _safe_member(self, info: zipfile.ZipInfo) -> bool:
        path = PurePosixPath(info.filename)
        if (
            "\\" in info.filename
            or path.is_absolute()
            or ".." in path.parts
            or info.flag_bits & 0x1
            or info.file_size > self._MAX_MEMBER_BYTES
        ):
            return False
        if info.file_size and info.compress_size == 0:
            return False
        return not (
            info.compress_size
            and info.file_size / info.compress_size > self._MAX_COMPRESSION_RATIO
        )

    @staticmethod
    def _strip_ns(tag: str) -> str:
        return tag.rsplit("}", 1)[-1] if "}" in tag else tag

    def _shared_strings(self, archive: zipfile.ZipFile) -> list[str]:
        try:
            payload = archive.read("xl/sharedStrings.xml")
        except (KeyError, OSError, RuntimeError, zipfile.BadZipFile):
            return []
        if b"<!DOCTYPE" in payload or b"<!ENTITY" in payload:
            return []
        try:
            root = ET.fromstring(payload)
        except ET.ParseError:
            return []
        strings: list[str] = []
        for si in root:
            if self._strip_ns(si.tag) != "si":
                continue
            # <si> is either a single <t> or rich text: several <r><t>...</t></r>.
            text = "".join(
                node.text or ""
                for node in si.iter()
                if self._strip_ns(node.tag) == "t"
            )
            strings.append(text)
        return strings

    def _sheet_text(self, payload: bytes, shared_strings: list[str]) -> str | None:
        if b"<!DOCTYPE" in payload or b"<!ENTITY" in payload:
            return None
        try:
            root = ET.fromstring(payload)
        except ET.ParseError:
            return None
        rows: list[str] = []
        for row in root.iter():
            if self._strip_ns(row.tag) != "row":
                continue
            cells: list[str] = []
            for cell in row:
                if self._strip_ns(cell.tag) != "c":
                    continue
                cell_type = cell.get("t", "")
                value = ""
                for child in cell:
                    ctag = self._strip_ns(child.tag)
                    if ctag == "v":
                        value = child.text or ""
                    elif ctag == "is":  # inline string
                        value = "".join(
                            n.text or ""
                            for n in child.iter()
                            if self._strip_ns(n.tag) == "t"
                        )
                if cell_type == "s" and value.isdigit():
                    index = int(value)
                    value = shared_strings[index] if 0 <= index < len(shared_strings) else ""
                value = value.strip()
                if value:
                    cells.append(value)
            if cells:
                rows.append(" | ".join(cells))
        return "\n".join(rows) or None

    @staticmethod
    def _xml_text(payload: bytes) -> str | None:
        if b"<!DOCTYPE" in payload or b"<!ENTITY" in payload:
            return None
        try:
            root = ET.fromstring(payload)
        except ET.ParseError:
            return None
        text = " ".join(fragment.strip() for fragment in root.itertext() if fragment.strip())
        return text or None


class PdfProbe:
    """Extract bounded, redactable text from a PDF without executing anything."""

    id = "pdf-text"
    version = "1"
    _MAX_PAGES = 2000

    def extract(self, raw: bytes, *, max_bytes: int) -> bytes | None:
        if not raw.startswith(b"%PDF"):
            return None
        try:
            from pypdf import PdfReader
        except Exception:  # pragma: no cover - dependency missing at runtime
            logger.warning("PdfProbe unavailable: pypdf import failed")
            return None
        try:
            reader = PdfReader(io.BytesIO(raw))
            if reader.is_encrypted:
                try:
                    if reader.decrypt("") == 0:
                        return None
                except Exception:
                    return None
            pages = reader.pages
        except Exception:
            return None
        parts: list[str] = []
        total = 0
        for index, page in enumerate(pages):
            if index >= self._MAX_PAGES:
                break
            try:
                text = page.extract_text() or ""
            except Exception:
                continue
            text = text.strip()
            if not text:
                continue
            fragment = f"[PDF page {index + 1}]\n{text}"
            total += len(fragment.encode("utf-8"))
            if total > max_bytes:
                break
            parts.append(fragment)
        if not parts:
            return None
        return "\n\n".join(parts).encode("utf-8")


class XlsProbe:
    """Extract bounded text from a legacy binary ``.xls`` (OLE2/BIFF) workbook."""

    id = "legacy-xls"
    version = "1"
    _OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    _MAX_SHEETS = 256
    _MAX_ROWS_PER_SHEET = 100_000

    def extract(self, raw: bytes, *, max_bytes: int) -> bytes | None:
        if not raw.startswith(self._OLE2_MAGIC):
            return None
        try:
            import xlrd
        except Exception:  # pragma: no cover - dependency missing at runtime
            logger.warning("XlsProbe unavailable: xlrd import failed")
            return None
        try:
            book = xlrd.open_workbook(file_contents=raw, on_demand=True)
        except Exception:
            return None
        parts: list[str] = []
        total = 0
        try:
            for sheet_index in range(min(book.nsheets, self._MAX_SHEETS)):
                sheet = book.sheet_by_index(sheet_index)
                rows: list[str] = [f"[XLS sheet: {sheet.name}]"]
                for row_index in range(min(sheet.nrows, self._MAX_ROWS_PER_SHEET)):
                    cells = [
                        str(value).strip()
                        for value in sheet.row_values(row_index)
                        if str(value).strip()
                    ]
                    if cells:
                        rows.append(" | ".join(cells))
                if len(rows) == 1:
                    continue
                fragment = "\n".join(rows)
                total += len(fragment.encode("utf-8"))
                if total > max_bytes:
                    break
                parts.append(fragment)
        finally:
            with suppress(Exception):
                book.release_resources()
        if not parts:
            return None
        return "\n\n".join(parts).encode("utf-8")


# ── unsupported classification (copied from prepare_supporting_evidence) ─────

_IMAGE_SIGNATURES = (
    b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a",
    b"BM", b"II*\x00", b"MM\x00*",
)
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

STATUS_UNSUPPORTED = "unsupported_by_probes"
STATUS_NEEDS_OCR = "unsupported_needs_ocr"
STATUS_NEEDS_LEGACY_OFFICE = "unsupported_needs_legacy_office_reader"
STATUS_UNSUPPORTED_ARCHIVE = "unsupported_archive"
STATUS_ZIP_PRE_EXTRACTED = "zip_pre_extracted"
STATUS_EMPTY_AFTER_PREP = "empty_after_preparation"


def classify_unsupported(raw: bytes) -> str:
    head = raw[:16]
    if any(head.startswith(signature) for signature in _IMAGE_SIGNATURES):
        return STATUS_NEEDS_OCR
    if raw.startswith(b"%PDF"):
        return STATUS_NEEDS_OCR
    if raw.startswith(_OLE2_MAGIC):
        return STATUS_NEEDS_LEGACY_OFFICE
    if raw.startswith(b"PK\x03\x04"):
        return STATUS_UNSUPPORTED_ARCHIVE
    return STATUS_UNSUPPORTED


# ── caps ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class NonCodeLimits:
    """Hard caps for the non-code walk, independent of association budgets."""

    max_inventory_files: int = 20_000
    max_prepared_files: int = 2_000
    max_file_bytes: int = 8 * 1024 * 1024
    max_prepared_bytes: int = 40 * 1024 * 1024
    text_probe_bytes: int = 256 * 1024
    max_zip_recursion_members: int = 512


# ── prepared item ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PreparedNonCodeItem:
    """One non-code file turned into bounded, redacted, citable text."""

    evidence_id: str
    relative_path: str
    probe_id: str
    text: str
    size_bytes: int
    redactions: int
    origin: str = "file"  # "file" or "zip:<archive relative path>"

    @property
    def logical_identity(self) -> str:
        return self.relative_path

    @property
    def claim_class(self) -> str:
        # A stable, generic, non-domain label used in the rendered blocks.
        suffix = self.relative_path.rsplit(".", 1)[-1].lower() if "." in self.relative_path else ""
        return f"non-code documented context ({suffix or 'text'})"


@dataclass(frozen=True)
class NonCodeExtractionResult:
    items: tuple[PreparedNonCodeItem, ...]
    skipped: tuple[dict[str, object], ...]

    @property
    def prepared_count(self) -> int:
        return len(self.items)


# ── code-tree exclusion derived from the scan inventory ──────────────────────

_ZIP_MARKER = ".__intodoc_zip__"


def derive_code_exclusions(
    scan_files: Iterable[Mapping[str, Any]],
) -> tuple[frozenset[str], tuple[str, ...]]:
    """Compute what the scanner already owns, so non-code walking skips it.

    Returns ``(exact_relative_paths, subtree_prefixes)``:

    - Every scanned file's ``relative_path`` is an exact exclusion -- the
      scanner already parsed it as code.
    - For files that live under a composite-ZIP extraction directory
      (``X.__intodoc_zip__``), the whole extraction subtree is excluded, and so
      is the raw ``X.zip`` that produced it -- otherwise the zip-recursion
      safety net would re-ingest already-scanned code. This generalises: the
      exclusion is derived from what the scanner consumed, never a hardcoded
      folder name.
    """
    exact: set[str] = set()
    prefixes: set[str] = set()
    for entry in scan_files:
        rel = str(entry.get("relative_path") or "").strip()
        if not rel:
            continue
        exact.add(rel)
        parts = rel.split("/")
        for index, part in enumerate(parts):
            if _ZIP_MARKER in part:
                subtree = "/".join(parts[: index + 1])
                prefixes.add(subtree)
                # marker segment `X.__intodoc_zip__[...]` -> raw `X.zip`.
                stem = part.split(_ZIP_MARKER, 1)[0]
                raw_zip = "/".join(parts[:index] + [f"{stem}.zip"])
                prefixes.add(raw_zip)
                break
    return frozenset(exact), tuple(sorted(prefixes))


def _is_excluded(relative: str, exact: frozenset[str], prefixes: Sequence[str]) -> bool:
    if relative in exact:
        return True
    return any(relative == p or relative.startswith(f"{p}/") for p in prefixes)


# ── extractor ─────────────────────────────────────────────────────────────────


class NonCodeEvidenceExtractor:
    """Walk a project's non-code material into redacted, citable items.

    Everything the scanner already parsed (``exact`` + ``prefixes``) is skipped.
    Pre-extracted composite ZIPs are covered by walking their extracted files;
    an un-extracted ZIP is recursed OOXML-safely as a safety net so a project
    uploaded without pre-extraction still yields its non-code content. No silent
    drops: every skip is recorded with a reason.
    """

    def __init__(
        self,
        source_root: Path,
        *,
        exact_exclusions: frozenset[str] = frozenset(),
        exclusion_prefixes: Sequence[str] = (),
        limits: NonCodeLimits = NonCodeLimits(),
        probes: tuple[ContentProbe, ...] | None = None,
    ) -> None:
        self.source_root = Path(source_root)
        self.exact = exact_exclusions
        self.prefixes = tuple(exclusion_prefixes)
        self.limits = limits
        self.probes: tuple[ContentProbe, ...] = probes or (
            OfficeOpenXmlProbe(), PdfProbe(), XlsProbe(), UtfTextProbe(),
        )

    def extract(self) -> NonCodeExtractionResult:
        root = self.source_root
        if not root.is_dir():
            logger.warning("noncode: source root not a directory: %s", root)
            return NonCodeExtractionResult(items=(), skipped=())

        items: list[PreparedNonCodeItem] = []
        skipped: list[dict[str, object]] = []
        seen = 0
        prepared_bytes = 0

        def _skip(status: str, relative: str, size_bytes: int) -> None:
            skipped.append(
                {"relative_path": relative, "size_bytes": size_bytes, "status": status}
            )
            logger.info("noncode skip: %s relative=%s size=%d", status, relative, size_bytes)

        for path in _regular_files(root):
            seen += 1
            if seen > self.limits.max_inventory_files:
                _skip("skipped_inventory_limit", "<...>", 0)
                break
            relative = path.relative_to(root).as_posix()
            if _is_excluded(relative, self.exact, self.prefixes):
                continue  # scanner-owned code; silent by design (not non-code).
            try:
                size_bytes = path.stat().st_size
            except OSError:
                _skip("unreadable", relative, 0)
                continue

            if path.suffix.lower() == ".zip":
                # Pre-extracted composite ZIP? Its extracted files are walked
                # separately; recursing the raw archive would double-ingest.
                if self._has_extraction_sibling(path):
                    _skip(STATUS_ZIP_PRE_EXTRACTED, relative, size_bytes)
                    continue
                zip_items, zip_prepared = self._recurse_zip(
                    path, relative, prepared_bytes, skip=_skip
                )
                items.extend(zip_items)
                prepared_bytes += zip_prepared
                continue

            if size_bytes > self.limits.max_file_bytes:
                _skip("skipped_file_limit", relative, size_bytes)
                continue
            try:
                raw = path.read_bytes()
            except OSError:
                _skip("unreadable", relative, size_bytes)
                continue

            item = self._prepare_bytes(raw, relative, origin="file")
            if item is None:
                _skip(classify_unsupported(raw), relative, size_bytes)
                continue
            if not item.text.strip():
                _skip(STATUS_EMPTY_AFTER_PREP, relative, size_bytes)
                continue
            if len(items) >= self.limits.max_prepared_files:
                _skip("skipped_prepared_file_limit", relative, size_bytes)
                continue
            item_bytes = len(item.text.encode("utf-8"))
            if prepared_bytes + item_bytes > self.limits.max_prepared_bytes:
                _skip("skipped_prepared_byte_limit", relative, size_bytes)
                continue
            items.append(item)
            prepared_bytes += item_bytes

        if skipped:
            logger.info("noncode: prepared %d item(s), skipped %d", len(items), len(skipped))
        return NonCodeExtractionResult(items=tuple(items), skipped=tuple(skipped))

    # ── helpers ──────────────────────────────────────────────────────

    def _has_extraction_sibling(self, zip_path: Path) -> bool:
        parent = zip_path.parent
        stem = zip_path.stem
        try:
            for child in parent.iterdir():
                if child.is_dir() and child.name.startswith(f"{stem}{_ZIP_MARKER}"):
                    return True
        except OSError:
            return False
        return False

    def _prepare_bytes(
        self, raw: bytes, relative: str, *, origin: str
    ) -> PreparedNonCodeItem | None:
        extracted, probe = self._run_probes(raw)
        if extracted is None:
            return None
        try:
            text = extracted.decode("utf-8")
        except UnicodeDecodeError:
            return None
        safe, redactions = redact_text(text)
        evidence_id = hashlib.sha256(
            f"{relative}:{hashlib.sha256(raw).hexdigest()}".encode("utf-8")
        ).hexdigest()[:24]
        return PreparedNonCodeItem(
            evidence_id=evidence_id,
            relative_path=relative,
            probe_id=probe.id,
            text=safe,
            size_bytes=len(raw),
            redactions=redactions,
            origin=origin,
        )

    def _run_probes(self, raw: bytes) -> tuple[bytes | None, ContentProbe]:
        for probe in self.probes:
            result = probe.extract(raw, max_bytes=self.limits.text_probe_bytes)
            if result is not None:
                return result, probe
        return None, self.probes[0]

    def _recurse_zip(
        self,
        zip_path: Path,
        zip_relative: str,
        prepared_bytes: int,
        *,
        skip,
    ) -> tuple[list[PreparedNonCodeItem], int]:
        """OOXML-safe recursion into an un-extracted ZIP (safety net).

        Members are size-bounded and never executed. Nested archives are not
        recursed (depth 1 only) -- pre-extraction handles legitimate nesting.
        """
        items: list[PreparedNonCodeItem] = []
        added_bytes = 0
        try:
            archive = zipfile.ZipFile(zip_path)
        except (zipfile.BadZipFile, OSError):
            skip(STATUS_UNSUPPORTED_ARCHIVE, zip_relative, 0)
            return items, added_bytes
        with archive:
            infos = archive.infolist()
            if len(infos) > self.limits.max_zip_recursion_members:
                skip("skipped_zip_member_limit", zip_relative, 0)
                return items, added_bytes
            for info in sorted(infos, key=lambda i: i.filename):
                member = PurePosixPath(info.filename)
                if (
                    info.is_dir()
                    or "\\" in info.filename
                    or member.is_absolute()
                    or ".." in member.parts
                    or info.flag_bits & 0x1
                    or info.file_size > self.limits.max_file_bytes
                ):
                    continue
                member_rel = f"{zip_relative}!/{info.filename}"
                try:
                    raw = archive.read(info)
                except (OSError, RuntimeError, zipfile.BadZipFile):
                    skip("unreadable", member_rel, info.file_size)
                    continue
                item = self._prepare_bytes(raw, member_rel, origin=f"zip:{zip_relative}")
                if item is None:
                    skip(classify_unsupported(raw), member_rel, len(raw))
                    continue
                if not item.text.strip():
                    skip(STATUS_EMPTY_AFTER_PREP, member_rel, len(raw))
                    continue
                if len(items) >= self.limits.max_prepared_files:
                    skip("skipped_prepared_file_limit", member_rel, len(raw))
                    continue
                item_bytes = len(item.text.encode("utf-8"))
                if prepared_bytes + added_bytes + item_bytes > self.limits.max_prepared_bytes:
                    skip("skipped_prepared_byte_limit", member_rel, len(raw))
                    continue
                items.append(item)
                added_bytes += item_bytes
        return items, added_bytes


def _regular_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*"), key=lambda candidate: candidate.as_posix()):
        if path.is_symlink():
            continue
        if path.is_file():
            yield path


# ── association (copied/adapted from mapping_association) ─────────────────────

NONCODE_SECTION_HEADING = "DOCUMENTED CONTEXT (NON-CODE, TIER-3)"

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}")
_MIN_TOKEN_LEN = 4
# Tokens the redaction placeholders themselves tokenise to. Excluding these
# fixes the real over-association bug: `[REDACTED]`/`[URL_REDACTED]` else match
# every unit that contains any redacted value.
_REDACTION_TOKENS = frozenset({"redacted", "url_redacted"})
_STOPWORDS = frozenset(
    {
        # SQL / documentation prose
        "select", "from", "where", "join", "inner", "outer", "left", "right",
        "into", "update", "insert", "delete", "table", "values", "order",
        "group", "having", "limit", "offset", "null", "true", "false", "and",
        "not", "the", "for", "with", "count", "distinct", "case", "when",
        "then", "else", "end", "date", "time", "datetime", "timestamp",
        "varchar", "integer", "string", "boolean", "number", "double",
        "float", "value", "values", "column", "columns", "field", "fields",
        "name", "names", "type", "types", "code", "data", "file", "files",
        "path", "user", "host", "port", "main", "row", "rows", "input",
        "output", "source", "target", "system", "systems", "mapping",
        "mappings", "sheet", "page", "document", "interface", "process",
        "flow", "flows", "job", "jobs", "component", "components", "context",
        "default", "prod", "test", "yes", "no",
        # generic XML / SOAP / transport boilerplate flagged by the dry run
        "guid", "http", "https", "xmlns", "soap", "description", "status",
        "text", "element", "schema", "version", "encoding", "namespace",
        "header", "body", "message", "request", "response",
    }
    | _REDACTION_TOKENS
)
_SQL_TABLE_RE = re.compile(
    r"(?is)\b(?:from|join|into|update|delete\s+from|table)\s+([A-Za-z_][\w.$#]*)"
)


@dataclass(frozen=True)
class NonCodeAssociationPlan:
    """Deterministic non-code-item-to-unit association result."""

    items_by_id: dict[str, PreparedNonCodeItem]
    unit_refs: dict[str, tuple[str, ...]]
    unit_matched_tokens: dict[str, dict[str, tuple[str, ...]]]
    project_only_ids: tuple[str, ...]
    dynamic_stopwords: frozenset[str] = frozenset()

    @property
    def total_items(self) -> int:
        return len(self.items_by_id)

    @property
    def matched_item_ids(self) -> frozenset[str]:
        return frozenset(eid for refs in self.unit_refs.values() for eid in refs)

    def unit_section(self, unit_name: str, *, max_bytes: int) -> str | None:
        refs = self.unit_refs.get(unit_name)
        if not refs:
            return None
        tokens_by_ref = self.unit_matched_tokens.get(unit_name, {})
        blocks = [
            _render_item_block(self.items_by_id[eid], matched_tokens=tokens_by_ref.get(eid, ()))
            for eid in refs
            if eid in self.items_by_id
        ]
        if not blocks:
            return None
        section = f"## {NONCODE_SECTION_HEADING}\n\n{_UNIT_PREAMBLE}\n\n" + "\n\n".join(blocks)
        return _bound_text(section, max_bytes)

    def project_section(self, *, max_bytes: int) -> str | None:
        if not self.items_by_id:
            return None
        matched = self.matched_item_ids
        blocks: list[str] = []
        for eid in sorted(self.items_by_id):
            item = self.items_by_id[eid]
            note = (
                "associated with one or more units"
                if eid in matched
                else "not associated with any single unit (project-wide context)"
            )
            blocks.append(_render_item_block(item, matched_tokens=(), association_note=note))
        section = f"# {NONCODE_SECTION_HEADING}\n\n{_PROJECT_PREAMBLE}\n\n" + "\n\n".join(blocks)
        return _bound_text(section, max_bytes)


_UNIT_PREAMBLE = (
    "The following non-code material was associated with this unit because it "
    "shares concrete identifiers (tables, connections, endpoints, unit/group/"
    "component names) with the unit's scanner evidence. It is DOCUMENTED INTENT, "
    "not implemented truth: it ADDS context on top of the verbatim code/SQL/tMap "
    "evidence above and NEVER overrides it. Where it disagrees with the code, the "
    "code is authoritative -- surface the mismatch as a documented-intent-vs-"
    "implemented-code discrepancy. All values are already credential-redacted."
)
_PROJECT_PREAMBLE = (
    "The complete set of prepared, redacted non-code material (Excel schema "
    "exports, XSLT/XSL mappings, notes, sample XML) for this project. It is "
    "DOCUMENTED INTENT (Tier-3): additive context that never overrides the "
    "scanner facts. Use it for project-wide narrative and to flag documented-"
    "intent-vs-implemented-code discrepancies; never restate it as an "
    "implemented fact."
)


def build_noncode_association_plan(
    items: Sequence[PreparedNonCodeItem],
    units: Mapping[str, Mapping[str, Any]],
    *,
    max_units_per_item: int = 25,
    unit_token_df_threshold: float = 0.1,
    min_distinctive_overlap: int = 2,
) -> NonCodeAssociationPlan:
    """Associate each item to the units that share its distinctive identifiers.

    Precision controls (dry-run driven -- without them every item saturated the
    25-unit cap):
    - redaction-placeholder tokens are excluded (they otherwise match every
      unit that contains any redacted value);
    - a static stopword set drops SQL/prose and XML/SOAP boilerplate;
    - a dynamic document-frequency cap drops tokens present in more than
      ``unit_token_df_threshold`` of units -- project-wide identifiers (e.g. a
      target system name every job touches) are non-distinctive and must not
      drive matches. This generalises beyond any hardcoded domain word;
    - a match must share at least ``min_distinctive_overlap`` distinctive tokens.
      One shared token (even a moderately rare one) is weak evidence; requiring
      two independent shared identifiers is what actually breaks the saturation
      where schema exports carrying hundreds of column names touched every unit.
    """
    unit_tokens = {name: unit_signal_tokens(unit) for name, unit in units.items()}
    unit_tokens = {name: toks for name, toks in unit_tokens.items() if toks}

    dynamic_stop = _dynamic_stopwords(unit_tokens, unit_token_df_threshold)
    if dynamic_stop:
        unit_tokens = {
            name: frozenset(toks - dynamic_stop) for name, toks in unit_tokens.items()
        }
        unit_tokens = {name: toks for name, toks in unit_tokens.items() if toks}

    items_by_id: dict[str, PreparedNonCodeItem] = {}
    unit_refs: dict[str, list[str]] = {}
    unit_matched_tokens: dict[str, dict[str, tuple[str, ...]]] = {}
    project_only_ids: list[str] = []

    for item in sorted(items, key=lambda i: i.evidence_id):
        if item.evidence_id in items_by_id:
            continue
        items_by_id[item.evidence_id] = item
        item_tokens = _tokenize(item.text) - dynamic_stop
        if not item_tokens:
            project_only_ids.append(item.evidence_id)
            continue
        scored: list[tuple[int, str, tuple[str, ...]]] = []
        for name, tokens in unit_tokens.items():
            overlap = tokens & item_tokens
            if len(overlap) >= min_distinctive_overlap:
                scored.append((len(overlap), name, tuple(sorted(overlap))))
        if not scored:
            project_only_ids.append(item.evidence_id)
            continue
        scored.sort(key=lambda entry: (-entry[0], entry[1]))
        selected = scored[:max_units_per_item]
        if len(scored) > len(selected):
            logger.info(
                "noncode item %s matched %d units; capped attachment to %d "
                "(full set still surfaced at project tier)",
                item.evidence_id, len(scored), len(selected),
            )
        for _score, name, overlap in selected:
            unit_refs.setdefault(name, []).append(item.evidence_id)
            unit_matched_tokens.setdefault(name, {})[item.evidence_id] = overlap

    for name in unit_refs:
        unit_refs[name].sort()
    matched_ids = {eid for refs in unit_refs.values() for eid in refs}
    project_only_ids = [eid for eid in project_only_ids if eid not in matched_ids]
    logger.info(
        "noncode association coverage: %d item(s), %d matched a unit, "
        "%d project-tier-only, %d unit(s) received items, %d dynamic stopword(s)",
        len(items_by_id), len(matched_ids), len(project_only_ids),
        len(unit_refs), len(dynamic_stop),
    )
    return NonCodeAssociationPlan(
        items_by_id=items_by_id,
        unit_refs={name: tuple(refs) for name, refs in unit_refs.items()},
        unit_matched_tokens=unit_matched_tokens,
        project_only_ids=tuple(sorted(project_only_ids)),
        dynamic_stopwords=frozenset(dynamic_stop),
    )


def _dynamic_stopwords(
    unit_tokens: Mapping[str, frozenset[str]], threshold: float
) -> frozenset[str]:
    total = len(unit_tokens)
    if total == 0 or threshold >= 1.0:
        return frozenset()
    df: Counter[str] = Counter()
    for tokens in unit_tokens.values():
        df.update(set(tokens))
    limit = threshold * total
    return frozenset(tok for tok, count in df.items() if count > limit)


def unit_signal_tokens(unit: Mapping[str, Any]) -> frozenset[str]:
    """Extract distinctive identifier tokens from one scan unit dict.

    Uses the same high-signal param definitions as the target-evidence bundle so
    the two views of a unit agree on what counts as a concrete identifier.
    """
    raw: list[str] = [str(unit.get("name") or ""), str(unit.get("group") or "")]
    for comp in unit.get("components", []) or []:
        raw.append(str(comp.get("unique_name") or ""))
        params = param_map(comp)
        query = params.get("QUERY", "")
        if query:
            raw.extend(_SQL_TABLE_RE.findall(query))
        for pname, pvalue in params.items():
            if pname in HIGH_SIGNAL_PARAM_NAMES or HIGH_SIGNAL_PARAM_PATTERN.search(pname):
                raw.append(pvalue)
    for conn in unit.get("connections", []) or []:
        raw.append(str(conn.get("source") or ""))
        raw.append(str(conn.get("target") or ""))
        raw.append(str(conn.get("connector_name") or ""))
    for grp in unit.get("context_groups", []) or []:
        raw.append(str(grp.get("name") or ""))
        for var in grp.get("variables", []) or []:
            raw.append(str(var.get("name") or ""))
            for env_value in (var.get("values", {}) or {}).values():
                raw.append(str(env_value))
    tokens: set[str] = set()
    for value in raw:
        tokens |= _tokenize(value)
    return frozenset(tokens)


def _tokenize(text: str) -> set[str]:
    tokens: set[str] = set()
    for match in _TOKEN_RE.findall(text or ""):
        token = match.casefold()
        if len(token) >= _MIN_TOKEN_LEN and token not in _STOPWORDS:
            tokens.add(token)
    return tokens


def _render_item_block(
    item: PreparedNonCodeItem,
    *,
    matched_tokens: tuple[str, ...],
    association_note: str | None = None,
) -> str:
    lines = [
        f"### Non-code: {item.logical_identity}  [evidence:{item.evidence_id}]",
        f"Claim class: {item.claim_class} (probe: {item.probe_id})",
    ]
    if matched_tokens:
        lines.append("Associated by shared identifiers: " + ", ".join(matched_tokens))
    if association_note is not None:
        lines.append(f"Association: {association_note}")
    lines.append("")
    lines.append(item.text.strip())
    return "\n".join(lines)


def _bound_text(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    notice = (
        "\n\n> NOTE: documented non-code (Tier-3) context truncated to the "
        f"configured {max_bytes}-byte budget; verbatim scanner evidence is "
        "unaffected. See the project-tier documented context for the full set.\n"
    )
    budget = max(0, max_bytes - len(notice.encode("utf-8")))
    truncated = encoded[:budget].decode("utf-8", errors="ignore")
    logger.info("noncode section truncated to %d bytes (was %d)", max_bytes, len(encoded))
    return truncated + notice


# ── top-level build entry ─────────────────────────────────────────────────────


def build_noncode_context(
    *,
    source_root: Path,
    scan_files: Iterable[Mapping[str, Any]],
    units: Mapping[str, Mapping[str, Any]],
    limits: NonCodeLimits = NonCodeLimits(),
    max_units_per_item: int = 25,
    unit_token_df_threshold: float = 0.1,
    min_distinctive_overlap: int = 2,
) -> tuple[NonCodeAssociationPlan, NonCodeExtractionResult]:
    """Walk non-code material and associate it to units, in one deterministic step."""
    exact, prefixes = derive_code_exclusions(scan_files)
    extraction = NonCodeEvidenceExtractor(
        source_root,
        exact_exclusions=exact,
        exclusion_prefixes=prefixes,
        limits=limits,
    ).extract()
    plan = build_noncode_association_plan(
        extraction.items,
        units,
        max_units_per_item=max_units_per_item,
        unit_token_df_threshold=unit_token_df_threshold,
        min_distinctive_overlap=min_distinctive_overlap,
    )
    return plan, extraction
