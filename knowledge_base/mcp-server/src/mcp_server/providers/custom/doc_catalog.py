"""Doc catalog -- loads doc_catalog.yaml and provides typed access to the standard doc set."""
from __future__ import annotations

from functools import cached_property
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

def _resolve_catalog_path() -> Path:
    # src/ layout (local dev): mcp-server/src/mcp_server/providers/custom/ -> 5 parents = mcp-server/
    src_layout = Path(__file__).resolve().parent.parent.parent.parent.parent / "data" / "doc_catalog.yaml"
    if src_layout.exists():
        return src_layout
    # Installed layout (Docker): WORKDIR is /app, data/ is copied there
    app_data = Path("/app/data/doc_catalog.yaml")
    if app_data.exists():
        return app_data
    # Last resort: data/ relative to cwd
    cwd_data = Path.cwd() / "data" / "doc_catalog.yaml"
    if cwd_data.exists():
        return cwd_data
    raise FileNotFoundError(
        f"doc_catalog.yaml not found at {src_layout}, {app_data}, or {cwd_data}"
    )


class DocCatalogEntry(BaseModel):
    name: str
    bundle: str = "core"
    required_sections: list[str] = Field(default_factory=list)
    phase2_sections: list[str] = Field(default_factory=list)
    doc_specific_instructions: str = ""
    # Expanded by the harness into one document per scanned unit
    # ("{name}__{group}__{unit}"). Per-unit docs are written first and the
    # catalog/summary docs synthesize from the finished unit docs.
    per_unit: bool = False


class DocCatalog(BaseModel):
    docs: list[DocCatalogEntry]

    @cached_property
    def required_docs(self) -> list[str]:
        return [d.name for d in self.docs]

    @cached_property
    def per_unit_docs(self) -> list[DocCatalogEntry]:
        return [d for d in self.docs if d.per_unit]

    @cached_property
    def doc_assignments(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for entry in self.docs:
            result.setdefault(entry.bundle, []).append(entry.name)
        return result

    @cached_property
    def doc_bundle_map(self) -> dict[str, str]:
        return {entry.name: entry.bundle for entry in self.docs}

    @cached_property
    def section_requirements(self) -> dict[str, list[str]]:
        return {entry.name: entry.required_sections for entry in self.docs}

    @cached_property
    def doc_section_bundle_map(self) -> dict[str, set[str]]:
        return {entry.name: set(entry.phase2_sections) for entry in self.docs}

    def doc_specific_instructions_for(self, name: str) -> str:
        for entry in self.docs:
            if entry.name == name:
                return entry.doc_specific_instructions
        return ""


_catalog_instance: DocCatalog | None = None


def load_doc_catalog(path: Path | None = None) -> DocCatalog:
    """Load and return the doc catalog singleton. Pass path to override for tests."""
    global _catalog_instance
    if _catalog_instance is None or path is not None:
        target = path or _resolve_catalog_path()
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
        instance = DocCatalog.model_validate(raw)
        if path is None:
            _catalog_instance = instance
        return instance
    return _catalog_instance
