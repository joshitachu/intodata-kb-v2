"""PDF conversion service using markdown + weasyprint."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import markdown

from mcp_server.config import config

logger = logging.getLogger(__name__)

PDF_CSS = """
@page {
    size: A4;
    margin: 2cm 2.5cm;
    @bottom-center { content: counter(page); font-size: 9pt; color: #666; }
}
body {
    font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
    font-size: 11pt;
    line-height: 1.6;
    color: #1a1a1a;
}
h1 { font-size: 22pt; margin-top: 1.5em; margin-bottom: 0.5em; color: #111; border-bottom: 2px solid #ddd; padding-bottom: 0.3em; }
h2 { font-size: 17pt; margin-top: 1.3em; margin-bottom: 0.4em; color: #222; }
h3 { font-size: 14pt; margin-top: 1.1em; margin-bottom: 0.3em; color: #333; }
h4 { font-size: 12pt; margin-top: 1em; color: #444; }
code {
    font-family: "Cascadia Code", "Fira Code", "Consolas", monospace;
    font-size: 9.5pt;
    background: #f4f4f5;
    padding: 0.15em 0.35em;
    border-radius: 3px;
}
pre {
    background: #f4f4f5;
    border: 1px solid #e0e0e0;
    border-radius: 5px;
    padding: 0.8em 1em;
    overflow-x: auto;
    font-size: 9pt;
    line-height: 1.5;
}
pre code { background: none; padding: 0; }
table {
    width: 100%;
    border-collapse: collapse;
    margin: 1em 0;
    font-size: 10pt;
}
th, td {
    border: 1px solid #d0d0d0;
    padding: 0.5em 0.8em;
    text-align: left;
}
th { background: #f0f0f0; font-weight: 600; }
tr:nth-child(even) { background: #fafafa; }
blockquote {
    border-left: 4px solid #ddd;
    margin: 1em 0;
    padding: 0.5em 1em;
    color: #555;
    background: #fafafa;
}
ul, ol { margin: 0.5em 0; padding-left: 1.5em; }
li { margin: 0.2em 0; }
hr { border: none; border-top: 1px solid #ddd; margin: 1.5em 0; }
a { color: #2563eb; text-decoration: none; }
"""


class PdfService:
    """Converts markdown documentation to PDF via WeasyPrint."""

    def __init__(self) -> None:
        self._md = markdown.Markdown(
            extensions=["tables", "fenced_code", "toc", "sane_lists"],
        )

    def convert_file(self, md_path: Path) -> Path:
        """Convert a single .md file to PDF. Returns path to generated PDF."""
        if not md_path.exists():
            raise FileNotFoundError(f"Markdown file not found: {md_path}")

        md_text = md_path.read_text(encoding="utf-8", errors="replace")
        self._md.reset()
        html_body = self._md.convert(md_text)

        html_doc = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{PDF_CSS}</style></head>
<body>{html_body}</body></html>"""

        pdf_dir = md_path.parent.parent / "pdf"
        pdf_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = pdf_dir / md_path.with_suffix(".pdf").name

        from weasyprint import HTML
        HTML(string=html_doc).write_pdf(str(pdf_path))
        logger.info("Converted %s -> %s", md_path.name, pdf_path.name)
        return pdf_path

    def convert_project(self, project_id: str) -> dict:
        """Batch-convert all .md docs for a project. Returns summary dict."""
        docs_dir = config.project_root / "output" / project_id / "docs"
        if not docs_dir.is_dir():
            return {"converted": 0, "failed": 0, "errors": [f"No docs dir: {docs_dir}"]}

        md_files = sorted(docs_dir.glob("*.md"))
        if not md_files:
            return {"converted": 0, "failed": 0, "errors": ["No .md files found"]}

        converted = 0
        failed = 0
        errors: list[str] = []

        for md_file in md_files:
            try:
                self.convert_file(md_file)
                converted += 1
            except Exception as e:
                failed += 1
                errors.append(f"{md_file.name}: {e}")
                logger.error("Failed to convert %s: %s", md_file.name, e)

        return {"converted": converted, "failed": failed, "errors": errors}

    def list_docs(self, project_id: str) -> list[dict]:
        """List all docs with metadata for a project."""
        docs_dir = config.project_root / "output" / project_id / "docs"
        pdf_dir = config.project_root / "output" / project_id / "pdf"

        if not docs_dir.is_dir():
            return []

        result: list[dict] = []
        for md_file in sorted(docs_dir.glob("*.md")):
            stat = md_file.stat()
            pdf_exists = (pdf_dir / md_file.with_suffix(".pdf").name).exists() if pdf_dir.is_dir() else False
            result.append({
                "name": md_file.name,
                "type": "md",
                "size_bytes": stat.st_size,
                "has_pdf": pdf_exists,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            })

        return result

    def get_doc_path(self, project_id: str, filename: str) -> Path | None:
        """Resolve filename to absolute path with path-traversal prevention."""
        if ".." in filename or "/" in filename or "\\" in filename:
            return None

        if filename.endswith(".md"):
            target = config.project_root / "output" / project_id / "docs" / filename
        elif filename.endswith(".pdf"):
            target = config.project_root / "output" / project_id / "pdf" / filename
        else:
            return None

        # Path traversal check
        allowed_root = (config.project_root / "output" / project_id).resolve()
        try:
            resolved = target.resolve()
            if not str(resolved).startswith(str(allowed_root)):
                return None
        except (OSError, ValueError):
            return None

        if not target.exists():
            return None

        return target
