"""Prepare generic supporting project evidence before any agent can read it.

This boundary deliberately knows nothing about Talend, business domains, or
individual document formats.  It inventories ordinary files, lets registered
content probes decide whether a bounded representation is safe to expose, and
writes only redacted prepared text plus an immutable raw-hash receipt.

The original workspace is never made agent-readable by this module.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import shutil
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Protocol
import zipfile
from xml.etree import ElementTree as ET

from mcp_server.experiments.domain.sources import SourceDescriptor
from mcp_server.experiments.infrastructure.sources.registry import (
    SourceAdapterError,
    read_regular,
)
from mcp_server.experiments.infrastructure.storage.canonical_json import canonical_json_bytes


logger = logging.getLogger("mcp_server.experiments.prepare_supporting_evidence")

_PREPARATION_SCHEMA_VERSION = 1
_MANIFEST_NAME = "prepared_evidence_manifest.json"
_RECEIPT_NAME = "redaction_receipt.json"
_CONTENT_DIR = "content"
_SENSITIVE_ASSIGNMENT = re.compile(
    r'''(?im)(?P<key>\b(?:password|passwd|pwd|secret|token|api[_-]?key|apikey|credential|clientsecret|authorization|cookie|set-cookie|x-api-key)\b\s*(?::|=)\s*)(?P<value>(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+|"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|[^\s,;&}]+)'''
)
_AUTH_SCHEME = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
_URL_USERINFO = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@")
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL)
_JWT = re.compile(
    r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])"
)


@dataclass(frozen=True, slots=True)
class SupportingEvidenceLimits:
    """Hard caps for preparation, separate from semantic source adapters."""

    max_inventory_files: int = 20_000
    max_prepared_files: int = 1_000
    max_file_bytes: int = 4 * 1024 * 1024
    max_raw_bytes: int = 100 * 1024 * 1024
    max_prepared_bytes: int = 25 * 1024 * 1024
    text_probe_bytes: int = 64 * 1024

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 1
            for value in (
                self.max_inventory_files,
                self.max_prepared_files,
                self.max_file_bytes,
                self.max_raw_bytes,
                self.max_prepared_bytes,
                self.text_probe_bytes,
            )
        ):
            raise ValueError("supporting evidence limits must be positive integers")


@dataclass(frozen=True, slots=True)
class PreparedSupportingEvidence:
    """The only descriptor that can be handed to the normal snapshot service."""

    descriptor: SourceDescriptor
    preparation_fingerprint_sha256: str
    inventory: tuple[dict[str, object], ...]
    prepared_count: int
    skipped_count: int
    # Files dropped by a size/budget cap, each with its relative path, size and
    # the cap that rejected it. Operator-facing accounting so a cap is never a
    # silent drop; mirrors the emitted warnings.
    skipped_files: tuple[dict[str, object], ...] = ()


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
        # Do not hand unusual control streams to an agent as "text".
        if any(ord(character) < 32 and character not in "\n\r\t" for character in text):
            return None
        return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


class OfficeOpenXmlProbe:
    """Extract bounded text from a safe Office Open XML package.

    This is a container-level probe rather than an Excel/Word business parser.
    It accepts only ordinary OOXML ZIP packages, never runs macros, formulas,
    external links, or embedded code.  The resulting text is simply evidence
    for a later agent to cite; it does not assign meaning to any cell, heading,
    or diagram.
    """

    id = "office-open-xml"
    version = "1"
    _MAX_MEMBERS = 512
    _MAX_MEMBER_BYTES = 2 * 1024 * 1024
    _MAX_UNCOMPRESSED_BYTES = 16 * 1024 * 1024
    _MAX_COMPRESSION_RATIO = 100
    _XML_PREFIXES = ("word/", "xl/", "ppt/")

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
            total = 0
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
                try:
                    payload = archive.read(info)
                except (OSError, RuntimeError, zipfile.BadZipFile):
                    return None
                text = self._xml_text(payload)
                if text:
                    parts.append(f"[Office XML part: {info.filename}]\n{text}")
                if sum(len(part.encode("utf-8")) for part in parts) > max_bytes:
                    return None
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
    """Extract bounded, redactable text from a PDF without executing anything.

    A PDF carries a document catalog and page content streams; ``pypdf`` reads
    the text operators only.  This probe never runs embedded JavaScript, follows
    ``/OpenAction`` / ``/AA`` entries, opens attachments, or resolves external
    references -- it just walks pages and pulls the visible text so a later agent
    can cite documented intent.  An image-only ("scanned") PDF yields no text; it
    returns ``None`` so the caller records it as needing OCR rather than emitting
    an empty representation.
    """

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
                # Only attempt the standard empty-password unlock; never guess a
                # credential. A still-locked document is treated as unreadable.
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
                # A malformed page must not abort the whole document; skip it and
                # keep the text recovered so far.
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
    """Extract bounded text from a legacy binary ``.xls`` (OLE2/BIFF) workbook.

    Old Excel workbooks predate the OOXML ``.xlsx`` container the office probe
    understands.  ``xlrd`` (>=2.0) is a read-only BIFF reader: it never
    evaluates formulas, runs macros, or resolves external links.  This probe
    accepts only files with the OLE2 compound-document signature and returns the
    cell text sheet by sheet.  A non-workbook OLE2 file (legacy ``.doc`` /
    ``.ppt``) fails to open and returns ``None`` so the caller can record it as
    needing a legacy reader instead of silently dropping it.
    """

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
            # Not a BIFF workbook (e.g. a legacy .doc/.ppt sharing the OLE2
            # container) or a corrupt file. The caller classifies it explicitly.
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


# File-category signatures used only to classify a file the probes could not
# turn into text, so the immutable inventory records WHY (needs OCR, needs a
# legacy reader, opaque archive) instead of a single silent "unsupported". These
# are magic-byte checks, never content interpretation.
_IMAGE_SIGNATURES = (
    b"\x89PNG\r\n\x1a\n",  # PNG
    b"\xff\xd8\xff",        # JPEG
    b"GIF87a",
    b"GIF89a",
    b"BM",                  # BMP
    b"II*\x00",             # TIFF little-endian
    b"MM\x00*",             # TIFF big-endian
)
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

STATUS_UNSUPPORTED = "unsupported_by_probes"
STATUS_NEEDS_OCR = "unsupported_needs_ocr"
STATUS_NEEDS_LEGACY_OFFICE = "unsupported_needs_legacy_office_reader"
STATUS_UNSUPPORTED_ARCHIVE = "unsupported_archive"


def classify_unsupported(raw: bytes) -> str:
    """Explain why a file could not be turned into citable text.

    OCR (images and scanned/image-only PDFs) is out of scope for this boundary
    but must be recorded as an explicit, distinct status -- never a silent drop.
    Legacy OLE2 office files that are not readable ``.xls`` workbooks are flagged
    as needing a legacy reader for the same reason.
    """

    head = raw[:16]
    if any(head.startswith(signature) for signature in _IMAGE_SIGNATURES):
        return STATUS_NEEDS_OCR
    if raw.startswith(b"%PDF"):
        # A PDF reached the classifier only because PdfProbe recovered no text:
        # an image-only / scanned document. OCR is out of scope.
        return STATUS_NEEDS_OCR
    if raw.startswith(_OLE2_MAGIC):
        return STATUS_NEEDS_LEGACY_OFFICE
    if raw.startswith(b"PK\x03\x04"):
        return STATUS_UNSUPPORTED_ARCHIVE
    return STATUS_UNSUPPORTED


class SupportingEvidencePreparationService:
    """Create an immutable, redacted supporting-evidence representation.

    Future generic probes (for example a sandboxed document renderer) can be
    registered here without changing the project-evidence snapshot contract.
    A probe gets only bounded bytes and cannot execute input content.
    """

    def __init__(
        self,
        *,
        probes: tuple[ContentProbe, ...] = (
            OfficeOpenXmlProbe(),
            PdfProbe(),
            XlsProbe(),
            UtfTextProbe(),
        ),
    ) -> None:
        if not probes or len({probe.id for probe in probes}) != len(probes):
            raise ValueError("supporting evidence probes must have unique non-empty ids")
        self._probes = probes

    def prepare(
        self,
        descriptor: SourceDescriptor,
        *,
        output_root: Path,
        limits: SupportingEvidenceLimits = SupportingEvidenceLimits(),
    ) -> PreparedSupportingEvidence:
        """Inventory a workspace and write an immutable redacted derivative.

        ``exclude_relative_prefixes`` is an optional generic ownership boundary
        in descriptor metadata.  The caller can use it to exclude files already
        represented by the implementation scanner; this service never guesses
        which project paths belong to a platform parser.
        """

        root = _require_directory(descriptor.source_path)
        exclusions = _excluded_prefixes(descriptor.metadata.get("exclude_relative_prefixes"))
        inventory: list[dict[str, object]] = []
        prepared: list[tuple[dict[str, object], bytes]] = []
        consumed_raw_bytes = 0
        consumed_prepared_bytes = 0
        files_seen = 0

        skipped: list[dict[str, object]] = []

        def _record_skip(status: str, relative: str, size_bytes: int, *, cap: int | None) -> None:
            # No silent caps: every skip is logged with the file's own relative
            # path and size so an operator can see exactly what was dropped and
            # raise the relevant limit. The relative path stays out of the
            # immutable inventory (which uses opaque ids), but it is safe in the
            # operator log -- the operator already owns the workspace.
            entry: dict[str, object] = {
                "relative_path": relative,
                "size_bytes": size_bytes,
                "status": status,
            }
            if cap is not None:
                entry["cap_bytes"] = cap
            skipped.append(entry)
            logger.warning(
                "supporting-evidence skip: %s relative=%s size=%d%s",
                status,
                relative,
                size_bytes,
                f" cap={cap}" if cap is not None else "",
            )

        for path in _regular_files(root):
            files_seen += 1
            if files_seen > limits.max_inventory_files:
                inventory.append({"status": "skipped_inventory_limit"})
                logger.warning(
                    "supporting-evidence skip: skipped_inventory_limit after %d files (cap=%d)",
                    files_seen - 1,
                    limits.max_inventory_files,
                )
                break
            relative = path.relative_to(root).as_posix()
            opaque_path = hashlib.sha256(relative.encode("utf-8")).hexdigest()
            size_bytes = path.stat().st_size
            base = {"reference_id": opaque_path, "size_bytes": size_bytes}
            if _excluded(relative, exclusions):
                inventory.append({**base, "status": "excluded_by_owner"})
                continue
            if size_bytes > limits.max_file_bytes:
                inventory.append({**base, "status": "skipped_file_limit"})
                _record_skip("skipped_file_limit", relative, size_bytes, cap=limits.max_file_bytes)
                continue
            if consumed_raw_bytes + size_bytes > limits.max_raw_bytes:
                inventory.append({**base, "status": "skipped_raw_budget"})
                _record_skip("skipped_raw_budget", relative, size_bytes, cap=limits.max_raw_bytes)
                continue
            try:
                raw = read_regular(path, max_bytes=limits.max_file_bytes)
            except (SourceAdapterError, OSError):
                inventory.append({**base, "status": "unreadable"})
                continue
            consumed_raw_bytes += len(raw)
            raw_sha256 = hashlib.sha256(raw).hexdigest()
            base = {**base, "raw_sha256": raw_sha256, "size_bytes": len(raw)}
            extracted, probe = self._extract(raw, max_bytes=limits.text_probe_bytes)
            if extracted is None:
                status = classify_unsupported(raw)
                inventory.append({**base, "status": status})
                if status != STATUS_UNSUPPORTED:
                    # A recognised-but-unsupported category (needs OCR / legacy
                    # reader / opaque archive) is logged so it is never a silent
                    # drop of otherwise-useful supporting material.
                    logger.warning(
                        "supporting-evidence unsupported: %s relative=%s size=%d",
                        status,
                        relative,
                        len(raw),
                    )
                continue
            safe, redaction_count = _redact(extracted)
            if not safe:
                inventory.append({**base, "status": "empty_after_preparation"})
                continue
            if len(prepared) >= limits.max_prepared_files:
                inventory.append({**base, "status": "skipped_prepared_file_limit"})
                _record_skip(
                    "skipped_prepared_file_limit",
                    relative,
                    len(safe),
                    cap=limits.max_prepared_files,
                )
                continue
            if consumed_prepared_bytes + len(safe) > limits.max_prepared_bytes:
                inventory.append({**base, "status": "skipped_prepared_byte_limit"})
                _record_skip(
                    "skipped_prepared_byte_limit",
                    relative,
                    len(safe),
                    cap=limits.max_prepared_bytes,
                )
                continue
            content_id = hashlib.sha256(
                f"{opaque_path}:{raw_sha256}".encode("utf-8")
            ).hexdigest()
            item = {
                **base,
                "content_id": content_id,
                "content_sha256": hashlib.sha256(safe).hexdigest(),
                "content_size_bytes": len(safe),
                "logical_identity": f"{descriptor.id}:prepared/{content_id}.txt",
                "media_type": "text/plain; charset=utf-8",
                "probe": {"id": probe.id, "version": probe.version},
                "redactions": redaction_count,
                "status": "prepared",
            }
            prepared.append((item, safe))
            inventory.append(item)
            consumed_prepared_bytes += len(safe)

        if not prepared:
            # We record a safe manifest but deliberately do not issue an
            # approved descriptor: an empty workspace must not look readable.
            raise SourceAdapterError("supporting workspace contains no safely preparable evidence")

        inventory = sorted(inventory, key=lambda item: str(item.get("reference_id", "")))
        identity = {
            "descriptor_id": descriptor.id,
            "inventory": inventory,
            "schema_version": _PREPARATION_SCHEMA_VERSION,
        }
        fingerprint = hashlib.sha256(canonical_json_bytes(identity)).hexdigest()
        destination = Path(output_root) / f"supporting-evidence-{fingerprint[:16]}"
        manifest = {
            "fingerprint_sha256": fingerprint,
            "inventory": inventory,
            "schema_version": _PREPARATION_SCHEMA_VERSION,
            "status": "approved",
        }
        receipt = {
            "schema_version": 1,
            "source_files": [
                {"raw_sha256": item["raw_sha256"], "size_bytes": item["size_bytes"]}
                for item in sorted(
                    (entry for entry, _ in prepared),
                    key=lambda entry: (str(entry["raw_sha256"]), int(entry["size_bytes"])),
                )
            ],
            "status": "approved",
        }
        self._write_immutable(destination, manifest, receipt, prepared)
        prepared_descriptor = replace(
            descriptor,
            source_kind="prepared_directory_evidence",
            source_path=destination,
            redaction_receipt_path=destination / _RECEIPT_NAME,
            trust_level="prepared_redacted_supporting_evidence",
            metadata={
                "preparation_fingerprint_sha256": fingerprint,
                "preparation_schema_version": _PREPARATION_SCHEMA_VERSION,
                "source_version": fingerprint[:16],
            },
        )
        if skipped:
            logger.warning(
                "supporting-evidence preparation skipped %d file(s) by caps: %s",
                len(skipped),
                ", ".join(
                    f"{entry['relative_path']}({entry['status']},{entry['size_bytes']}B)"
                    for entry in skipped
                ),
            )
        return PreparedSupportingEvidence(
            descriptor=prepared_descriptor,
            preparation_fingerprint_sha256=fingerprint,
            inventory=tuple(inventory),
            prepared_count=len(prepared),
            skipped_count=len(inventory) - len(prepared),
            skipped_files=tuple(skipped),
        )

    def _extract(self, raw: bytes, *, max_bytes: int) -> tuple[bytes | None, ContentProbe]:
        for probe in self._probes:
            result = probe.extract(raw, max_bytes=max_bytes)
            if result is not None:
                return result, probe
        return None, self._probes[0]

    @staticmethod
    def _write_immutable(
        destination: Path,
        manifest: dict[str, object],
        receipt: dict[str, object],
        prepared: list[tuple[dict[str, object], bytes]],
    ) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            existing = destination / _MANIFEST_NAME
            if existing.is_file() and existing.read_bytes() == canonical_json_bytes(manifest):
                return
            raise SourceAdapterError("prepared evidence destination identity collision")
        staging = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
        if staging.exists():
            shutil.rmtree(staging)
        try:
            (staging / _CONTENT_DIR).mkdir(parents=True)
            for item, content in prepared:
                target = staging / _CONTENT_DIR / f"{item['content_id']}.txt"
                target.write_bytes(content)
            (staging / _MANIFEST_NAME).write_bytes(canonical_json_bytes(manifest))
            (staging / _RECEIPT_NAME).write_bytes(canonical_json_bytes(receipt))
            staging.replace(destination)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging)
            raise


def _redact(content: bytes) -> tuple[bytes, int]:
    text = content.decode("utf-8")

    def replacement(match: re.Match[str]) -> str:
        return f"{match.group('key')}[REDACTED]"

    text, assignment_changes = _SENSITIVE_ASSIGNMENT.subn(replacement, text)
    text, url_changes = _URL_USERINFO.subn(r"\1[REDACTED]@", text)
    text, auth_changes = _AUTH_SCHEME.subn(r"\1 [REDACTED]", text)
    text, key_changes = _PRIVATE_KEY.subn("[REDACTED PRIVATE KEY]", text)
    text, jwt_changes = _JWT.subn("[REDACTED JWT]", text)
    return text.encode("utf-8"), (
        assignment_changes + url_changes + auth_changes + key_changes + jwt_changes
    )


def _require_directory(path: Path) -> Path:
    if Path(path).is_symlink():
        raise SourceAdapterError("supporting evidence root must not be a symlink")
    resolved = Path(path).resolve(strict=True)
    if not resolved.is_dir():
        raise SourceAdapterError("supporting evidence root must be a real directory")
    return resolved


def _regular_files(root: Path):
    for path in sorted(root.rglob("*"), key=lambda candidate: candidate.as_posix()):
        if path.is_symlink():
            continue
        if path.is_file():
            yield path


def _excluded_prefixes(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError("exclude_relative_prefixes must be a sequence")
    prefixes: list[str] = []
    for raw in value:
        if not isinstance(raw, str):
            raise ValueError("exclude_relative_prefixes entries must be strings")
        path = PurePosixPath(raw)
        if not raw or path.is_absolute() or "\\" in raw or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("exclude_relative_prefixes entries must be safe relative paths")
        prefixes.append(path.as_posix().rstrip("/"))
    return tuple(sorted(set(prefixes)))


def _excluded(relative: str, prefixes: tuple[str, ...]) -> bool:
    return any(relative == prefix or relative.startswith(f"{prefix}/") for prefix in prefixes)
