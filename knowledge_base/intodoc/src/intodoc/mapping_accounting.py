"""Field-level mapping accounting (deterministic, no LLM).

`intodoc.lineage` answers "where did this column come from". This pass
answers the question a reader asks first: **how much of what this mapper
was given did it actually use, and what did it write that came from
nowhere?** A tMap offering 120 input columns and writing 8 is a different
artifact from one writing 118, and neither is visible from a sample of
expressions.

One record per mapper component, covering all four Talend mapping formats
(tMap, tXMLMap, TDM map, XSLT). Every list is complete: nothing here is
truncated, and an expression that cannot be traced is classified
``dynamic`` or ``unresolved`` rather than omitted.

Reference extraction deliberately reuses `intodoc.lineage`'s own seams
(`tokenize_expression`, `_resolve_transformations`, the tree reference
indexes and the two path regexes). Re-implementing "what counts as a
reference" here would let the two passes disagree about the same
expression, which is worse than the coupling.
"""

from __future__ import annotations

import hashlib
import re

from intodoc.lineage import (
    _IDENTIFIER_FIELD_RE,
    _TDM_INPUT_REF_RE,
    _TXMLMAP_PATH_REF_RE,
    _expression_is_constant,
    _resolve_row,
    _resolve_transformations,
    _tdm_input_reference_index,
    _txmlmap_input_reference_index,
    strip_comments_and_literals,
)
from intodoc.models import (
    Component,
    MappingAccounting,
    MappingRow,
    ParsedJob,
    ScanResult,
    TdmMap,
    TMapData,
    TMapEntry,
    XsltTransform,
)
from intodoc.platforms.talend.parser import TalendParser

# Identifier namespaces whose `x.y` references are runtime lookups or
# library calls rather than a mapper input. This is Java/Talend vocabulary,
# not project data: `context.NAME` and `globalMap.get(...)` are resolved at
# execution time, and `TalendDate.getDate(...)` is a routine call. Anything
# NOT in here and not a declared input is a genuine wiring gap
# (`unresolved`), which is the distinction that makes the dynamic count
# actionable.
_RUNTIME_NAMESPACES: frozenset[str] = frozenset({
    "context", "globalMap", "globalMapUtil", "resourceMap",
    "System", "Math", "String", "Integer", "Long", "Double", "Float",
    "Boolean", "Character", "Byte", "Short", "Object", "Arrays",
    "Collections", "Pattern", "Matcher", "BigDecimal", "BigInteger",
    "Date", "Calendar", "UUID", "Thread", "Locale", "TimeZone", "Objects",
})

_XSLT_LITERAL_RE = re.compile(r"^\s*(?:'[^']*'|\"[^\"]*\"|-?\d+(?:\.\d+)?)\s*$")
# An XPath step or path: `Order/Header/Id`, `@code`, `Id`. A function call
# or predicate makes the expression compound, never a direct map.
_XPATH_STEP_RE = re.compile(r"^[@A-Za-z_][\w.:-]*(?:/[@A-Za-z_][\w.:-]*)*$")


def _table_id(unit_id: str, component_name: str, mapper_kind: str) -> str:
    raw = f"{unit_id}|{component_name}|{mapper_kind}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _runtime_identifiers(scan_result: ScanResult) -> frozenset[str]:
    """Namespaces that legitimately resolve at runtime, for this scan.

    Project routines are included by name so `MyRules.normalize(...)` is a
    routine call rather than an unresolved row reference. Derived from the
    scan itself plus Talend's own system routine set -- never a list of
    project identifiers maintained by hand.
    """
    return frozenset(
        _RUNTIME_NAMESPACES
        | set(TalendParser._SYSTEM_ROUTINES)
        | {routine.name for routine in scan_result.routines if routine.name}
    )


def _finalize(accounting: MappingAccounting) -> MappingAccounting:
    """Derive the counters from `rows` so they can never drift from them."""
    accounting.written_fields = len(accounting.rows)
    accounting.mapped_from_input = sum(
        row.classification in ("direct_map", "expression") for row in accounting.rows
    )
    accounting.constant_or_generated = sum(
        row.classification == "constant" for row in accounting.rows
    )
    accounting.lookup_derived = sum(
        row.classification == "lookup" for row in accounting.rows
    )
    accounting.dynamic_unresolved = sum(
        row.classification in ("dynamic", "unresolved") for row in accounting.rows
    )
    accounting.table_id = _table_id(
        accounting.unit_id, accounting.component_name, accounting.mapper_kind,
    )
    return accounting


def _unwritten(offered: list[str], rows: list[MappingRow]) -> list[str]:
    """Offered inputs no output expression referenced. Order is preserved."""
    used = {ref for row in rows for ref in row.source_refs}
    return [field for field in offered if field not in used]


# ── tMap ─────────────────────────────────────────────────────────────


def _component_schema_columns(component: Component) -> tuple[list[str], bool]:
    """Column names a component's FLOW schema offers, and its enumerability.

    The FLOW connector describes the row leaving the component; a REJECT
    schema describes a different row and must not be added to it. When the
    export names no connector at all, every schema is taken -- there is
    nothing to distinguish them by.
    """
    flow = [
        schema for schema in component.schemas
        if schema.connector.upper() in ("", "FLOW")
    ] or component.schemas
    columns = [
        column.name
        for schema in flow for column in schema.columns
        if column.name and not column.dynamic
    ]
    return columns, not any(schema.dynamic for schema in flow)


def _tmap_offered(
    job: ParsedJob, tmap: TMapData,
) -> tuple[list[str], list[str]]:
    """(offered 'table.column' list, unenumerable input table names).

    Two independent sources are unioned: the tMap's own declared input
    table entries, and the row schema of the component wired into that
    input. Either can be the complete one -- Talend stores the input
    columns on the tMap for a built-in schema, and on the upstream
    component for a repository schema.
    """
    offered: list[str] = []
    unenumerable: list[str] = []
    by_name = {component.unique_name: component for component in job.components}
    for detail in tmap.table_details:
        if detail.get("kind") != "inputTables":
            continue
        table = str(detail.get("attributes", {}).get("name", ""))
        if not table:
            continue
        table_enumerable = True
        for entry in detail.get("entries", []):
            name = str(entry.get("name", ""))
            if not name:
                continue
            if str(entry.get("type", "")).strip().casefold() == "id_dynamic":
                table_enumerable = False
                continue
            reference = f"{table}.{name}"
            if reference not in offered:
                offered.append(reference)
        resolution = _resolve_row(job, table, side="input")
        component = (
            by_name.get(resolution.component_unique_name)
            if resolution is not None else None
        )
        if component is not None:
            columns, enumerable = _component_schema_columns(component)
            table_enumerable = table_enumerable and enumerable
            for column in columns:
                reference = f"{table}.{column}"
                if reference not in offered:
                    offered.append(reference)
        if not table_enumerable:
            unenumerable.append(table)
    return offered, unenumerable


def _classify_tmap_row(
    entry: TMapEntry,
    resolved: frozenset[tuple[str, str]],
    dynamic: frozenset[str],
    lookup_tables: frozenset[str],
    runtime_identifiers: frozenset[str],
) -> str:
    if not entry.expression.strip():
        # Talend writes an output column with an empty expression when it is
        # declared but never mapped: the column IS written (as null), and
        # its source is genuinely unknown.
        return "unresolved"
    if not resolved and not dynamic:
        return "constant"
    if not resolved:
        return (
            "dynamic"
            if all(ref.split(".", 1)[0] in runtime_identifiers for ref in dynamic)
            else "unresolved"
        )
    if not dynamic and all(identifier in lookup_tables for identifier, _ in resolved):
        return "lookup"
    if len(resolved) == 1 and not dynamic:
        (identifier, field), = resolved
        stripped = strip_comments_and_literals(entry.expression).strip()
        if stripped == f"{identifier}.{field}":
            return "direct_map"
    return "expression"


def _tmap_accounting(
    job: ParsedJob, tmap: TMapData, runtime_identifiers: frozenset[str],
) -> MappingAccounting:
    input_tables = set(tmap.input_tables)
    # Talend convention (shared with `lineage`): the first declared input
    # table is the main flow, every later one is a lookup join.
    lookup_tables: frozenset[str] = (
        frozenset(tmap.input_tables[1:]) if len(tmap.input_tables) > 1 else frozenset()
    )
    var_lookup = {entry.column_name: entry for entry in tmap.var_entries}
    offered, unenumerable = _tmap_offered(job, tmap)

    accounting = MappingAccounting(
        unit_id=job.job_id or job.name,
        component_name=tmap.component_name,
        mapper_kind="tmap",
        offered_fields=len(offered),
        offered_enumerable=not unenumerable,
        unenumerable_inputs=unenumerable,
    )

    for detail in tmap.table_details:
        attributes = {
            str(key): str(value)
            for key, value in dict(detail.get("attributes", {})).items()
        }
        name = attributes.get("name", "")
        expression_filter = attributes.get("expressionFilter", "").strip()
        if expression_filter and attributes.get("activateExpressionFilter", "true").casefold() != "false":
            accounting.filters.append(expression_filter)
        if detail.get("kind") == "inputTables" and name in lookup_tables:
            accounting.lookup_tables.append({
                "name": name,
                "match_model": attributes.get("matchingMode", ""),
                "load_model": attributes.get("lookupMode", ""),
                "inner_join": attributes.get("innerJoin", ""),
                "expression_filter": expression_filter,
            })
        if detail.get("kind") == "outputTables" and name and any(
            attributes.get(flag, "false").casefold() == "true"
            for flag in ("reject", "rejectInnerJoin", "isErrorRejectTable")
        ):
            accounting.reject_outputs.append(name)

    for entry in tmap.entries:
        resolution = _resolve_transformations(
            entry, input_tables, lookup_tables, var_lookup,
        )
        classification = _classify_tmap_row(
            entry, resolution.resolved_refs, resolution.dynamic_refs,
            lookup_tables, runtime_identifiers,
        )
        accounting.rows.append(MappingRow(
            output_table=entry.table_name,
            output_field=entry.column_name,
            expression=entry.expression,
            classification=classification,  # type: ignore[arg-type]
            source_refs=sorted(
                f"{identifier}.{field}" for identifier, field in resolution.resolved_refs
            ),
            dynamic_refs=sorted(resolution.dynamic_refs),
        ))

    accounting.unwritten_offered = _unwritten(offered, accounting.rows)
    return _finalize(accounting)


# ── tXMLMap / TDM (tree mappers) ─────────────────────────────────────


def _tree_reference_tokens(expression: str, path_pattern: re.Pattern[str]) -> set[str]:
    """The reference spellings a tree-mapper expression can contain.

    Same two-rule extraction `lineage` uses: Talend's ``tree.field``
    spelling plus a path spelling, both taken from comment/literal-stripped
    text so quoted content never becomes a reference.
    """
    stripped = strip_comments_and_literals(expression)
    tokens = {
        f"{identifier}.{field}"
        for identifier, field in _IDENTIFIER_FIELD_RE.findall(stripped)
    }
    tokens |= set(path_pattern.findall(stripped))
    return tokens


def _classify_tree_row(
    expression: str,
    resolved_tokens: set[str],
    dynamic_tokens: set[str],
    runtime_identifiers: frozenset[str],
) -> str:
    if not expression.strip():
        return "unresolved"
    if not resolved_tokens and not dynamic_tokens:
        return "constant" if _expression_is_constant(expression) else "dynamic"
    if not resolved_tokens:
        return (
            "dynamic"
            if all(
                token.split(".", 1)[0].split("/", 1)[0] in runtime_identifiers
                for token in dynamic_tokens
            )
            else "unresolved"
        )
    if not dynamic_tokens and len(resolved_tokens) == 1:
        if expression.strip() == next(iter(resolved_tokens)):
            return "direct_map"
    return "expression"


def _txmlmap_accounting(
    job: ParsedJob, txmap, runtime_identifiers: frozenset[str],
) -> MappingAccounting:
    index = _txmlmap_input_reference_index(txmap)
    offered: list[str] = []
    for tree in txmap.input_trees:
        for field in tree.fields:
            reference = (
                f"{tree.name}.{field.name}" if tree.name and field.name
                else (field.xpath or field.name)
            )
            if reference and reference not in offered:
                offered.append(reference)

    accounting = MappingAccounting(
        unit_id=job.job_id or job.name,
        component_name=txmap.component_name,
        mapper_kind="txmlmap",
        offered_fields=len(offered),
        # The captured input trees ARE the declared field list; a tXMLMap
        # has no dynamic-column equivalent.
        offered_enumerable=True,
    )

    for tree in txmap.output_trees:
        for field in tree.fields:
            # An output tree node without an expression is a structural
            # element, not a mapping target; the complete tree stays in
            # `ScanResult.jobs[].txmlmap_data`.
            if not field.expression.strip():
                continue
            resolved: list[str] = []
            dynamic: set[str] = set()
            source_refs: list[str] = []
            for token in sorted(_tree_reference_tokens(field.expression, _TXMLMAP_PATH_REF_RE)):
                candidates = index.get(token, [])
                if len(candidates) == 1:
                    resolved.append(token)
                    reference = f"{candidates[0].tree_name}.{candidates[0].name}"
                    if reference not in source_refs:
                        source_refs.append(reference)
                else:
                    dynamic.add(token)
            accounting.rows.append(MappingRow(
                output_table=tree.name,
                output_field=field.xpath or field.name,
                expression=field.expression,
                classification=_classify_tree_row(  # type: ignore[arg-type]
                    field.expression, set(resolved), dynamic, runtime_identifiers,
                ),
                source_refs=sorted(source_refs),
                dynamic_refs=sorted(dynamic),
            ))

    accounting.unwritten_offered = _unwritten(offered, accounting.rows)
    return _finalize(accounting)


def _tdm_accounting(
    mapping: TdmMap, runtime_identifiers: frozenset[str],
) -> MappingAccounting:
    index = _tdm_input_reference_index(mapping)
    offered = [
        field.path or field.name
        for field in (mapping.input_tree.fields if mapping.input_tree else [])
        if (field.path or field.name)
    ]
    accounting = MappingAccounting(
        # A map with no Maps/Jobs|Routes/<name>/ scoping has no owner in the
        # export; inventing one from name similarity is exactly what the
        # parser refuses to do, so this stays empty.
        unit_id=mapping.owning_artifact,
        component_name=mapping.name or mapping.path,
        mapper_kind="tdm",
        offered_fields=len(offered),
        offered_enumerable=mapping.input_tree is not None,
        unenumerable_inputs=[] if mapping.input_tree is not None else [mapping.input_struct or mapping.name],
    )
    for field in (mapping.output_tree.fields if mapping.output_tree else []):
        if not field.expression.strip():
            continue
        resolved: list[str] = []
        dynamic: set[str] = set()
        source_refs: list[str] = []
        for token in sorted(_TDM_INPUT_REF_RE.findall(field.expression)):
            candidates = index.get(token.removeprefix("in$/"), [])
            if len(candidates) == 1:
                resolved.append(token)
                if candidates[0].path not in source_refs:
                    source_refs.append(candidates[0].path)
            else:
                dynamic.add(token)
        accounting.rows.append(MappingRow(
            output_table=mapping.output_tree.name if mapping.output_tree else "",
            output_field=field.path or field.name,
            expression=field.expression,
            classification=_classify_tree_row(  # type: ignore[arg-type]
                field.expression, set(resolved), dynamic, runtime_identifiers,
            ),
            source_refs=sorted(source_refs),
            dynamic_refs=sorted(dynamic),
        ))
    accounting.unwritten_offered = _unwritten(offered, accounting.rows)
    return _finalize(accounting)


# ── XSLT ─────────────────────────────────────────────────────────────

# An XSLT stylesheet declares no input schema, so "offered" has no value to
# report -- stated explicitly rather than published as a misleading 0/0.
_XSLT_NO_INPUT_SCHEMA = "xslt_input_document_has_no_declared_schema"


def _classify_xslt_row(select: str) -> str:
    if not select.strip():
        return "unresolved"
    if _XSLT_LITERAL_RE.match(select):
        return "constant"
    if _XPATH_STEP_RE.match(select.strip()):
        return "direct_map"
    return "expression"


def _xslt_accounting(transform: XsltTransform) -> MappingAccounting:
    accounting = MappingAccounting(
        # Attributed to the referencing unit only when exactly one component
        # names this stylesheet; a shared transform stays unattributed
        # rather than being assigned to an arbitrary caller.
        unit_id=(
            transform.referenced_by[0].split("::", 1)[0]
            if len(transform.referenced_by) == 1 else ""
        ),
        component_name=transform.path,
        mapper_kind="xslt",
        offered_fields=0,
        offered_enumerable=False,
        unenumerable_inputs=[_XSLT_NO_INPUT_SCHEMA],
    )
    for mapping in transform.mappings:
        if mapping.instruction not in ("value-of", "copy-of"):
            continue
        classification = _classify_xslt_row(mapping.select)
        accounting.rows.append(MappingRow(
            output_table=mapping.template_match,
            output_field=mapping.output_path,
            expression=mapping.select,
            classification=classification,  # type: ignore[arg-type]
            source_refs=(
                [mapping.select.strip()]
                if classification in ("direct_map", "expression") and mapping.select.strip()
                else []
            ),
            dynamic_refs=[] if mapping.select.strip() else ["xslt:instruction_without_select"],
        ))
    return _finalize(accounting)


# ── Entry point ──────────────────────────────────────────────────────


def build_mapping_accounting(scan_result: ScanResult) -> list[MappingAccounting]:
    """One accounting record per mapper component in the scan.

    Order is deterministic: job order, then component order within a job,
    then standalone TDM maps, then XSLT transforms.
    """
    runtime_identifiers = _runtime_identifiers(scan_result)
    records: list[MappingAccounting] = []
    for job in scan_result.jobs:
        for tmap in job.tmap_data:
            records.append(_tmap_accounting(job, tmap, runtime_identifiers))
        for txmap in job.txmlmap_data:
            records.append(_txmlmap_accounting(job, txmap, runtime_identifiers))
    for mapping in scan_result.tdm_maps:
        records.append(_tdm_accounting(mapping, runtime_identifiers))
    for transform in scan_result.xslt_transforms:
        records.append(_xslt_accounting(transform))
    return records
