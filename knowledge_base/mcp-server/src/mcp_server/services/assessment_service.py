"""Assessment page service -- generates a self-contained HTML assessment from markdown docs."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import markdown
from jinja2 import Environment, FileSystemLoader

from mcp_server.config import config

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

# Sections to extract from 00_EXECUTIVE_SUMMARY.md
EXEC_SECTIONS = [
    "At a Glance",
    "Key Findings",
    "Architecture Overview",
    "Regional Summary",
    "Risk Summary",
    "Recommendations",
    "Platform Statistics",
]

# Sections to extract from 11_MIGRATION_ASSESSMENT.md
MIGRATION_SECTIONS = [
    "Migration Readiness Score",
    "Dimension Scores",
    "Total Effort Summary",
    "Quick Wins",
]


@dataclass
class Section:
    """A section extracted from a markdown document."""

    title: str
    level: int
    md_content: str
    html_content: str = ""


@dataclass
class AssessmentData:
    """All data needed to render the assessment template."""

    project_id: str
    display_name: str = ""
    platform: str = ""
    job_count: int = 0
    doc_count: int = 0
    generated_at: str = ""
    exec_sections: list[Section] = field(default_factory=list)
    migration_sections: list[Section] = field(default_factory=list)
    has_migration: bool = False


class AssessmentService:
    """Generates a self-contained HTML assessment page from markdown docs."""

    def __init__(self) -> None:
        self._md = markdown.Markdown(
            extensions=["tables", "fenced_code", "toc", "sane_lists"],
        )
        self._jinja_env = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            autoescape=False,  # We trust our own markdown output
        )

    def extract_sections(
        self, md_text: str, section_titles: list[str]
    ) -> list[Section]:
        """Split markdown by headings, return sections matching titles.

        Uses substring matching on heading text (case-insensitive) so that
        minor heading variations across projects still match.
        """
        lines = md_text.split("\n")
        heading_re = re.compile(r"^(#{1,6})\s+(.+)$")

        # Build list of (line_idx, level, title)
        headings: list[tuple[int, int, str]] = []
        for idx, line in enumerate(lines):
            m = heading_re.match(line)
            if m:
                headings.append((idx, len(m.group(1)), m.group(2).strip()))

        results: list[Section] = []
        for i, (line_idx, level, title) in enumerate(headings):
            title_lower = title.lower()
            matched = any(
                wanted.lower() in title_lower for wanted in section_titles
            )
            if not matched:
                continue

            # Content: lines after this heading until next heading at same or higher level
            start = line_idx + 1
            end = len(lines)
            for j in range(i + 1, len(headings)):
                if headings[j][1] <= level:
                    end = headings[j][0]
                    break

            content = "\n".join(lines[start:end]).strip()

            # Include sub-headings in the content (they're within the section)
            results.append(Section(title=title, level=level, md_content=content))

        return results

    def _md_to_html(self, md_text: str) -> str:
        """Convert markdown text to HTML."""
        self._md.reset()
        return self._md.convert(md_text)

    async def generate(self, project_id: str) -> dict:
        """Generate assessment HTML for a project.

        Returns dict with project_id, status, html_path, error.
        """
        docs_dir = config.project_root / "output" / project_id / "docs"

        # Read doc 00 (required)
        exec_path = docs_dir / "00_EXECUTIVE_SUMMARY.md"
        if not exec_path.exists():
            return {
                "project_id": project_id,
                "status": "no_docs",
                "html_path": None,
                "error": "00_EXECUTIVE_SUMMARY.md not found",
            }

        exec_text = exec_path.read_text(encoding="utf-8", errors="replace")

        # Extract sections from doc 00
        exec_sections = self.extract_sections(exec_text, EXEC_SECTIONS)
        for section in exec_sections:
            section.html_content = self._md_to_html(section.md_content)

        # Read doc 11 (optional)
        migration_sections: list[Section] = []
        migration_path = docs_dir / "11_MIGRATION_ASSESSMENT.md"
        has_migration = False
        if migration_path.exists():
            migration_text = migration_path.read_text(encoding="utf-8", errors="replace")
            migration_sections = self.extract_sections(
                migration_text, MIGRATION_SECTIONS
            )
            for section in migration_sections:
                section.html_content = self._md_to_html(section.md_content)
            has_migration = bool(migration_sections)

        # Get project metadata from DB
        display_name = project_id
        platform = ""
        job_count = 0
        doc_count = 0
        try:
            from mcp_server.db.session import async_session_factory
            from mcp_server.services.project_registry import ProjectRegistry

            async with async_session_factory() as db:
                registry = ProjectRegistry(db)
                project = await registry.get_project(project_id)
                if project:
                    display_name = project.display_name or project_id
                    platform = project.platform or ""
                    job_count = project.job_count or 0
                    doc_count = project.doc_count or 0
        except Exception as e:
            logger.warning("Could not fetch project metadata: %s", e)

        # Build template data
        data = AssessmentData(
            project_id=project_id,
            display_name=display_name,
            platform=platform,
            job_count=job_count,
            doc_count=doc_count,
            generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            exec_sections=exec_sections,
            migration_sections=migration_sections,
            has_migration=has_migration,
        )

        # Render template
        try:
            template = self._jinja_env.get_template("assessment.html")
            html = template.render(data=data)
        except Exception as e:
            logger.error("Assessment template render failed: %s", e)
            return {
                "project_id": project_id,
                "status": "failed",
                "html_path": None,
                "error": f"Template render failed: {e}",
            }

        # Write output
        output_dir = config.project_root / "output" / project_id / "assessment"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "index.html"
        output_path.write_text(html, encoding="utf-8")

        logger.info("Assessment generated: %s", output_path)
        return {
            "project_id": project_id,
            "status": "completed",
            "html_path": str(output_path),
        }
