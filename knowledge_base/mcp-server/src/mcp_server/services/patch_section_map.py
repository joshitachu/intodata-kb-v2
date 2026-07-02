"""Platform-aware component_type -> Phase 2 section mapping for true patch mode.

Given a list of field-level changes (from Slice 3's extract_field_map diff),
this module determines which Phase 2 analysis sections need to be rewritten
and which Phase 3 docs are affected.

OI-05: extracting these maps to doc_catalog.yaml is deferred.  Keep them as
in-code dicts until that story is accepted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

# ---------------------------------------------------------------------------
# FieldChange -- shared data class imported by TruePatchExecutor
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldChange:
    """A single field-level change between prev and curr scan for a group.

    ``field_path`` is a human-readable key such as
    ``"(job_name, unique_name, param_name)"``.
    ``component_type`` is the platform component type (e.g. ``"tMap"``), or
    None when unavailable.
    """

    field_path: str
    old_value: str | None
    new_value: str | None
    component_type: str | None


# ---------------------------------------------------------------------------
# Platform section maps (hardcoded per plan; OI-05 deferred)
# ---------------------------------------------------------------------------

TALEND_COMPONENT_SECTION_MAP: dict[str, list[str]] = {
    "tMap":                     ["TRANSFORMATION_DETAIL", "DATA_FLOWS", "BUSINESS_RULES"],
    "tMSSqlInput":              ["EXTERNAL_SYSTEMS", "SOURCE_SQL", "DATA_FLOWS"],
    "tMSSqlOutput":             ["EXTERNAL_SYSTEMS", "DATA_FLOWS"],
    "tFileInputDelimited":      ["DATA_FLOWS", "SCHEMA_DICTIONARY", "EXTERNAL_SYSTEMS"],
    "tFileOutputDelimited":     ["DATA_FLOWS", "SCHEMA_DICTIONARY"],
    "tJavaRow":                 ["CUSTOM_CODE", "BUSINESS_RULES", "TRANSFORMATION_DETAIL"],
    "tJava":                    ["CUSTOM_CODE", "BUSINESS_RULES"],
    "tContextLoad":             ["ENVIRONMENT_CONFIG", "EXTERNAL_SYSTEMS"],
    "tRESTClient":              ["EXTERNAL_SYSTEMS", "DATA_FLOWS"],
    "tJMS":                     ["EXTERNAL_SYSTEMS", "SCHEDULING"],
    "tScheduler":               ["SCHEDULING"],
    "tLogCatcher":              ["ERROR_HANDLING"],
    "connection":               ["EXTERNAL_SYSTEMS", "ENVIRONMENT_CONFIG"],
    "context_var":              ["ENVIRONMENT_CONFIG"],
    "_default":                 ["COMPONENTS"],
}

WORKATO_COMPONENT_SECTION_MAP: dict[str, list[str]] = {
    "trigger":                  ["SCHEDULING", "EXTERNAL_SYSTEMS"],
    "action":                   ["DATA_FLOWS", "TRANSFORMATION_DETAIL", "EXTERNAL_SYSTEMS"],
    "lookup_table":             ["SCHEMA_DICTIONARY", "BUSINESS_RULES"],
    "formula":                  ["TRANSFORMATION_DETAIL", "BUSINESS_RULES"],
    "connection":               ["EXTERNAL_SYSTEMS", "ENVIRONMENT_CONFIG"],
    "_default":                 ["COMPONENTS"],
}

# GROUP_SUMMARY is always included so the summary index stays current.
ALWAYS_REWRITE: tuple[str, ...] = ("GROUP_SUMMARY",)

# Evidence Register heading is NEVER rewritten (G2a guardrail).
EVIDENCE_REGISTER_HEADING = "Evidence Register"


def _platform_map(platform: str) -> dict[str, list[str]]:
    """Return the component->section map for the given platform name."""
    if platform.lower() == "workato":
        return WORKATO_COMPONENT_SECTION_MAP
    return TALEND_COMPONENT_SECTION_MAP  # default for talend / generic


def sections_for_delta(
    platform: str,
    field_changes: list[FieldChange],
) -> frozenset[str]:
    """Return the set of Phase 2 section names that must be rewritten.

    Iterates ``field_changes``, looks up each ``component_type`` in the
    platform map (falling back to ``_default`` for unknown types), and unions
    all matched sections.  ``GROUP_SUMMARY`` is always added.
    """
    comp_map = _platform_map(platform)
    sections: set[str] = set(ALWAYS_REWRITE)
    for fc in field_changes:
        comp_type = fc.component_type or ""
        matched = comp_map.get(comp_type) or comp_map.get("_default") or ["COMPONENTS"]
        sections.update(matched)
    return frozenset(sections)


@lru_cache(maxsize=1)
def _load_catalog():
    """Lazily load the doc catalog (cached)."""
    from mcp_server.providers.custom.doc_catalog import load_doc_catalog
    return load_doc_catalog()


_H2_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def extract_h2_sections(text: str) -> dict[str, str]:
    """Return {heading_title: full section text (heading line included)}.

    Shared by ``true_patch_executor`` (G2/G2a invariant checks) and
    ``synthesis_impact_mapper`` (section-level impact mapping for summary
    documents) -- one canonical ``## Heading`` splitter for both consumers.
    """
    headings = list(_H2_RE.finditer(text))
    if not headings:
        return {}
    result: dict[str, str] = {}
    for idx, m in enumerate(headings):
        title = m.group(1).strip()
        start = m.start()
        end = headings[idx + 1].start() if idx + 1 < len(headings) else len(text)
        result[title] = text[start:end]
    return result


def docs_for_sections(sections: frozenset[str]) -> list[str]:
    """Return doc names (no .md suffix) whose phase2_sections intersect ``sections``.

    Synthesis docs (phase2_sections: []) are never included.
    Order follows the catalog declaration order.
    """
    catalog = _load_catalog()
    result: list[str] = []
    for entry in catalog.docs:
        if not entry.phase2_sections:
            # Synthesis docs (09_EXECUTIVE_SUMMARY, 07_QUALITY_SECURITY_RISK_SCORECARD)
            continue
        if sections & set(entry.phase2_sections):
            result.append(entry.name)
    return result
