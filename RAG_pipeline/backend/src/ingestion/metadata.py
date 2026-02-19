from __future__ import annotations

import io
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def extract_metadata(file_bytes: bytes, filename: str) -> dict[str, str | None]:
    """Extract author, date, and other metadata from document properties."""
    ext = Path(filename).suffix.lower().lstrip(".")
    extractors = {
        "pdf": _pdf_metadata,
        "docx": _docx_metadata,
        "pptx": _pptx_metadata,
        "xlsx": _xlsx_metadata,
    }
    extractor = extractors.get(ext)
    if extractor is None:
        return {"author": None, "department": None}

    try:
        return extractor(file_bytes)
    except Exception as e:
        logger.warning(f"Failed to extract metadata from {filename}: {e}")
        return {"author": None, "department": None}


def _pdf_metadata(data: bytes) -> dict[str, str | None]:
    from PyPDF2 import PdfReader

    reader = PdfReader(io.BytesIO(data))
    info = reader.metadata
    if info is None:
        return {"author": None, "department": None}
    return {"author": info.get("/Author"), "department": None}


def _docx_metadata(data: bytes) -> dict[str, str | None]:
    from docx import Document

    doc = Document(io.BytesIO(data))
    props = doc.core_properties
    return {"author": props.author, "department": props.category}


def _pptx_metadata(data: bytes) -> dict[str, str | None]:
    from pptx import Presentation

    prs = Presentation(io.BytesIO(data))
    props = prs.core_properties
    return {"author": props.author, "department": props.category}


def _xlsx_metadata(data: bytes) -> dict[str, str | None]:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True)
    props = wb.properties
    return {"author": props.creator, "department": props.category}
