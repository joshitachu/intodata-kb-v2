"""Associate prepared Tier-3 supporting evidence to the units it describes.

The composite project ZIP carries non-code artifacts -- documented field
mappings, interface specs, PDFs, notes. Slices 1-4 got that material prepared,
redacted, and read by the investigation roles, but it never reached the per-unit
documents. This module closes that gap deterministically (no model in the path):

- It associates a prepared mapping to a unit by GENERAL signals only: concrete
  identifiers -- table names, connection names, endpoints, and unit/group/
  component names -- that appear in BOTH the mapping text and the unit's own
  scanner evidence. There is no platform, domain, or filename heuristic.
- A matched mapping becomes a redacted "Documented mappings (Tier-3 context)"
  section appended to that unit's evidence bundle, and its id is recorded as a
  ``mapping_ref`` on the unit's document plan.
- Mappings are DOCUMENTED INTENT: additive context that sits ON TOP of the
  verbatim code/SQL/tMap evidence and NEVER overrides it. Where a mapping
  disagrees with the code, the code is authoritative and the mismatch is
  surfaced.
- A mapping that matches no unit is never dropped: the full mapping set is
  surfaced at the project tier so the Tier-B summary writers can still use it.

All mapping text handed to this module is already redacted (it comes from the
prepared supporting-evidence snapshot); this module never re-introduces raw
bytes and never trims verbatim code -- only the additive mapping sections are
byte-bounded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import re
from typing import Any, Mapping, Sequence

from mcp_server.experiments.application.unit_evidence import (
    HIGH_SIGNAL_PARAM_NAMES,
    HIGH_SIGNAL_PARAM_PATTERN,
    param_map,
)

logger = logging.getLogger("mcp_server.experiments.mapping_association")

MAPPING_SECTION_HEADING = "DOCUMENTED MAPPINGS (TIER-3 CONTEXT)"

# A token must start with a letter and be reasonably distinctive: short tokens
# and common SQL / documentation words are dropped so association keys on
# project-specific identifiers, not generic prose.
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}")
_MIN_TOKEN_LEN = 4
_STOPWORDS = frozenset(
    {
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
        "default", "prod", "test", "true", "false", "yes", "no",
    }
)
_SQL_TABLE_RE = re.compile(
    r"(?is)\b(?:from|join|into|update|delete\s+from|table)\s+([A-Za-z_][\w.$#]*)"
)


@dataclass(frozen=True, slots=True)
class DocumentedMapping:
    """One prepared, already-redacted Tier-3 supporting artifact."""

    evidence_id: str
    logical_identity: str
    claim_class: str
    text: str


@dataclass(frozen=True, slots=True)
class MappingAssociationPlan:
    """Deterministic mapping-to-unit association result.

    ``unit_refs`` and ``unit_matched_tokens`` drive both the per-unit evidence
    section and the ``mapping_refs`` on the unit document plan. ``project_only``
    is the set of mappings that matched no unit and therefore live only at the
    project tier -- but ``project_section`` renders the FULL set so the summary
    writers see everything regardless of association.
    """

    mappings_by_id: dict[str, DocumentedMapping]
    unit_refs: dict[str, tuple[str, ...]]
    unit_matched_tokens: dict[str, dict[str, tuple[str, ...]]]
    project_only_ids: tuple[str, ...]
    _empty: bool = field(default=False)

    @property
    def total_mappings(self) -> int:
        return len(self.mappings_by_id)

    @property
    def matched_mapping_ids(self) -> frozenset[str]:
        return frozenset(
            evidence_id
            for refs in self.unit_refs.values()
            for evidence_id in refs
        )

    @property
    def matched_unit_count(self) -> int:
        return len(self.unit_refs)

    def unit_section(self, unit_name: str, *, max_bytes: int) -> str | None:
        """Render the redacted Tier-3 section for one unit, bounded by bytes.

        Returns ``None`` when the unit has no associated mapping. Mapping text is
        additive (Tier-3) so it is the only thing truncated when the section
        exceeds ``max_bytes``; verbatim code lives in the evidence bundle and is
        never touched here.
        """

        refs = self.unit_refs.get(unit_name)
        if not refs:
            return None
        tokens_by_ref = self.unit_matched_tokens.get(unit_name, {})
        blocks = [
            _render_mapping_block(
                self.mappings_by_id[evidence_id],
                matched_tokens=tokens_by_ref.get(evidence_id, ()),
            )
            for evidence_id in refs
            if evidence_id in self.mappings_by_id
        ]
        if not blocks:
            return None
        section = f"## {MAPPING_SECTION_HEADING}\n\n{_UNIT_PREAMBLE}\n\n" + "\n\n".join(blocks)
        return _bound_text(section, max_bytes)

    def project_section(self, *, max_bytes: int) -> str | None:
        """Render the FULL redacted mapping set for the Tier-B summary writers."""

        if not self.mappings_by_id:
            return None
        matched = self.matched_mapping_ids
        blocks: list[str] = []
        for evidence_id in sorted(self.mappings_by_id):
            mapping = self.mappings_by_id[evidence_id]
            association = (
                "associated with one or more units"
                if evidence_id in matched
                else "not associated with any single unit (project-wide context)"
            )
            blocks.append(
                _render_mapping_block(mapping, matched_tokens=(), association_note=association)
            )
        section = f"# {MAPPING_SECTION_HEADING}\n\n{_PROJECT_PREAMBLE}\n\n" + "\n\n".join(blocks)
        return _bound_text(section, max_bytes)


_UNIT_PREAMBLE = (
    "The following supporting material was associated with this unit because it "
    "shares concrete identifiers (tables, connections, endpoints, unit/group/"
    "component names) with the unit's scanner evidence. It is DOCUMENTED INTENT, "
    "not implemented truth: it ADDS context on top of the verbatim code/SQL/tMap "
    "evidence above and NEVER overrides it. Where it disagrees with the code, the "
    "code is authoritative -- surface the mismatch as a documented-intent-vs-"
    "implemented-code discrepancy."
)
_PROJECT_PREAMBLE = (
    "The complete set of prepared, redacted supporting material (documented "
    "mappings, specs, notes) for this project. It is DOCUMENTED INTENT (Tier-3): "
    "additive context that never overrides the scanner facts. Use it for "
    "project-wide narrative and to flag documented-intent-vs-implemented-code "
    "discrepancies; never restate it as an implemented fact."
)


def build_mapping_association_plan(
    mappings: Sequence[DocumentedMapping],
    units: Mapping[str, Mapping[str, Any]],
    *,
    max_units_per_mapping: int = 25,
) -> MappingAssociationPlan:
    """Associate each mapping to the units that share its concrete identifiers.

    ``units`` maps unit name -> already-parsed scan unit dict. Association is a
    pure function of shared distinctive tokens, so it is deterministic and
    reproducible. Every mapping is retained; unmatched mappings are marked
    project-only. Association coverage is logged (matched vs. project-only).
    """

    unit_tokens = {name: unit_signal_tokens(unit) for name, unit in units.items()}
    unit_tokens = {name: tokens for name, tokens in unit_tokens.items() if tokens}

    mappings_by_id: dict[str, DocumentedMapping] = {}
    unit_refs: dict[str, list[str]] = {}
    unit_matched_tokens: dict[str, dict[str, tuple[str, ...]]] = {}
    project_only_ids: list[str] = []

    for mapping in sorted(mappings, key=lambda item: item.evidence_id):
        if mapping.evidence_id in mappings_by_id:
            continue
        mappings_by_id[mapping.evidence_id] = mapping
        mapping_tokens = _tokenize(mapping.text)
        if not mapping_tokens:
            project_only_ids.append(mapping.evidence_id)
            continue
        scored: list[tuple[int, str, tuple[str, ...]]] = []
        for name, tokens in unit_tokens.items():
            overlap = tokens & mapping_tokens
            if overlap:
                scored.append((len(overlap), name, tuple(sorted(overlap))))
        if not scored:
            project_only_ids.append(mapping.evidence_id)
            continue
        # Deterministic ranking: strongest overlap first, then unit name. Cap the
        # fan-out so a single mapping that mentions a very common table does not
        # attach to every unit; the full set still surfaces at the project tier.
        scored.sort(key=lambda item: (-item[0], item[1]))
        selected = scored[:max_units_per_mapping]
        if len(scored) > len(selected):
            logger.info(
                "mapping %s matched %d units; capped attachment to %d "
                "(full set still surfaced at project tier)",
                mapping.evidence_id,
                len(scored),
                len(selected),
            )
        for _score, name, overlap in selected:
            unit_refs.setdefault(name, []).append(mapping.evidence_id)
            unit_matched_tokens.setdefault(name, {})[mapping.evidence_id] = overlap

    for name in unit_refs:
        unit_refs[name].sort()

    matched_ids = {evidence_id for refs in unit_refs.values() for evidence_id in refs}
    project_only_ids = [
        evidence_id for evidence_id in project_only_ids if evidence_id not in matched_ids
    ]
    logger.info(
        "mapping association coverage: %d mapping(s), %d matched a unit, "
        "%d surfaced only at the project tier, %d unit(s) received mappings",
        len(mappings_by_id),
        len(matched_ids),
        len(project_only_ids),
        len(unit_refs),
    )

    return MappingAssociationPlan(
        mappings_by_id=mappings_by_id,
        unit_refs={name: tuple(refs) for name, refs in unit_refs.items()},
        unit_matched_tokens=unit_matched_tokens,
        project_only_ids=tuple(sorted(project_only_ids)),
        _empty=not mappings_by_id,
    )


def unit_signal_tokens(unit: Mapping[str, Any]) -> frozenset[str]:
    """Extract distinctive identifier tokens from one scan unit dict.

    Signals are all concrete project identifiers -- unit/group names, component
    unique names, SQL table names, connection endpoints, context variable names,
    and high-signal endpoint/URL/path parameter values. No platform-specific or
    business-domain assumption is made beyond the shape of these fields.
    """

    raw: list[str] = []
    raw.append(str(unit.get("name") or ""))
    raw.append(str(unit.get("group") or ""))

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

    for group in unit.get("context_groups", []) or []:
        raw.append(str(group.get("name") or ""))
        for var in group.get("variables", []) or []:
            raw.append(str(var.get("name") or ""))
            for env_value in (var.get("values", {}) or {}).values():
                raw.append(str(env_value))

    tokens: set[str] = set()
    for value in raw:
        tokens |= _tokenize(value)
    return frozenset(tokens)


def _tokenize(text: str) -> set[str]:
    tokens = set()
    for match in _TOKEN_RE.findall(text or ""):
        token = match.casefold()
        if len(token) >= _MIN_TOKEN_LEN and token not in _STOPWORDS:
            tokens.add(token)
    return tokens


def _render_mapping_block(
    mapping: DocumentedMapping,
    *,
    matched_tokens: tuple[str, ...],
    association_note: str | None = None,
) -> str:
    lines = [
        f"### Mapping: {mapping.logical_identity}  [evidence:{mapping.evidence_id}]",
        f"Claim class: {mapping.claim_class}",
    ]
    if matched_tokens:
        lines.append("Associated by shared identifiers: " + ", ".join(matched_tokens))
    if association_note is not None:
        lines.append(f"Association: {association_note}")
    lines.append("")
    lines.append(mapping.text.strip())
    return "\n".join(lines)


def _bound_text(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    notice = (
        "\n\n> NOTE: documented-mapping (Tier-3) context truncated to the "
        f"configured {max_bytes}-byte budget; verbatim scanner evidence is "
        "unaffected. See the project-tier documented mappings for the full set.\n"
    )
    budget = max(0, max_bytes - len(notice.encode("utf-8")))
    truncated = encoded[:budget].decode("utf-8", errors="ignore")
    logger.warning(
        "documented-mapping section truncated to %d bytes (was %d)",
        max_bytes,
        len(encoded),
    )
    return truncated + notice
