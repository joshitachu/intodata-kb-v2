"""Deterministic column/field lineage extraction pass.

Implements the S4 scanner-side slice of the topology/lineage architecture
(see `RAG_pipeline/docs/topology-lineage-runtime-architecture.md`, sec 2.2
and decisions D2/D5/D6): no LLM, no runtime cost. For each `TMapEntry`
expression captured by the Talend parser, this module:

1. Tokenizes the expression (comment/string-literal stripped) into
   candidate `identifier.field` pairs, keeping only references that are
   the tMap's own declared `input_tables` or `Var`. Everything else
   (`globalMap.get(...)`, `context.*`, routine calls, casts) is recorded
   as an unresolved *dynamic* reference -- never silently dropped.
2. Resolves `Var.X` references through `var_entries` (recursively, cycle
   safe) so the emitted edge points at the true source column(s), with
   the Var hop recorded in `transformations`.
3. Resolves each input/output row name to its upstream/downstream
   component via the job's `connections` (never the unreliable
   per-component incoming/outgoing lists).
4. Classifies each edge (`direct_map` / `expression` / `constant` /
   `lookup` / `dynamic`) with a `certain | likely | dynamic | opaque`
   confidence tier.
5. (S5 second increment) Stitches job-boundary edges across the intra-job
   graph built by steps 1-4, from two independent, deliberately
   conservative signals -- never guessed when either side is ambiguous:
   (a) a parent job's `trunjob_refs[].context_params` override that a
   child job's tMap expressions reference as `context.<name>` (recorded
   as a dynamic ref by step 1), and (b) an output component in one job
   and an input component in another job that share both a resolved
   `metadata_connection_ref` and the same table-identifying param value.
   Both emit `confidence: inferred` edges, always distinguishable from
   the S4 intra-job tiers via that confidence value plus a populated
   `source_job_name` (see `models.LineageEntry`).

Populates the (previously dead-code) `LineageMatrix` / `LineageEntry` /
`ColumnTransformation` models from `intodoc.models` -- see D5. Output is
written as `scan/lineage.json`, run through the same credential-filter
defense-in-depth gate as `scan_result.json` (D6, `redaction_verifier`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from intodoc.models import (
    Component,
    ColumnTransformation,
    Connection,
    LineageConfidence,
    LineageEntry,
    LineageMatrix,
    ParsedJob,
    ScanResult,
    TXMLMapData,
    TMapEntry,
    TdmMap,
    TRunJobRef,
)
from intodoc.scanner.credential_filter import CredentialFilter

# Talend's tMap always names the variable table "Var"; it is never a real
# connection row and is resolved through `var_entries`, not `connections`.
_VAR_TABLE_NAME = "Var"

# ── Expression tokenizer (architecture doc sec 2.2, step 1) ─────────────

_STRING_LITERAL_RE = re.compile(r'"(?:\\.|[^"\\])*"')
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"//[^\n]*")
_IDENTIFIER_FIELD_RE = re.compile(r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\b")
# tXMLMap expressions can use either Talend's ``tree.field`` spelling or a
# captured XPath-like ``tree/path`` spelling.  Do not turn arbitrary slash
# text into a source reference: a candidate must exactly match a captured
# input-tree xpath below.
_TXMLMAP_PATH_REF_RE = re.compile(
    r"(?<![\w./])([A-Za-z_][\w.-]*(?:/[A-Za-z_][\w.-]*)+)(?![\w./])"
)
# TDM's parsed export evidence currently exposes input references in this
# form.  Additional syntaxes must be added only after parser evidence exists.
_TDM_INPUT_REF_RE = re.compile(
    r"(?<![\w$])(in\$/[A-Za-z_][\w.-]*(?:/[A-Za-z_][\w.-]*)*)"
)
_CONSTANT_EXPRESSION_RE = re.compile(
    r"^\s*(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|-?\d+(?:\.\d+)?|true|false|null)\s*$",
    re.IGNORECASE,
)


def _job_identity(job: ParsedJob) -> str:
    """Canonical job identity, preserving historic plain-name scans."""
    return job.job_id or job.name


def strip_comments_and_literals(expression: str) -> str:
    """Remove string literals and comments from a tMap expression.

    Applied before `identifier.field` tokenization so literal text (which
    may contain arbitrary punctuation, including dots) and comments never
    produce spurious matches. String literals collapse to `""` (not
    removed entirely) so surrounding structure/spacing is preserved for
    the bare-reference check.
    """
    text = _BLOCK_COMMENT_RE.sub(" ", expression)
    text = _LINE_COMMENT_RE.sub(" ", text)
    text = _STRING_LITERAL_RE.sub('""', text)
    return text


@dataclass(frozen=True)
class TokenizedExpression:
    """Result of tokenizing one expression against a set of allowed identifiers."""

    resolved_refs: frozenset[tuple[str, str]]  # {(identifier, field), ...}
    dynamic_refs: frozenset[str]  # {"identifier.field", ...}
    stripped: str


def tokenize_expression(expression: str, allowed_identifiers: set[str]) -> TokenizedExpression:
    """Extract `identifier.field` pairs, splitting them into resolved vs dynamic.

    `allowed_identifiers` should be the tMap's declared `input_tables`;
    `Var` is always implicitly allowed (Talend's variable table).
    """
    allowed = set(allowed_identifiers) | {_VAR_TABLE_NAME}
    stripped = strip_comments_and_literals(expression)
    resolved: set[tuple[str, str]] = set()
    dynamic: set[str] = set()
    for identifier, fld in _IDENTIFIER_FIELD_RE.findall(stripped):
        if identifier in allowed:
            resolved.add((identifier, fld))
        else:
            dynamic.add(f"{identifier}.{fld}")
    return TokenizedExpression(resolved_refs=frozenset(resolved), dynamic_refs=frozenset(dynamic), stripped=stripped)


def _is_bare_reference(stripped: str, table: str, column: str) -> bool:
    """True when the whole (stripped) expression is exactly `table.column`."""
    return stripped.strip() == f"{table}.{column}"


# ── Edge classification (architecture doc sec 2.2, step 4) ──────────────


def _classify_hop(
    stripped: str,
    resolved: frozenset[tuple[str, str]],
    dynamic: frozenset[str],
    lookup_tables: frozenset[str],
) -> str:
    """Classify a single expression hop: direct_map / expression / constant / lookup / dynamic."""
    if not resolved and not dynamic:
        return "constant"
    if not resolved:
        return "dynamic"
    if len(resolved) == 1 and not dynamic:
        (identifier, fld), = resolved
        if identifier in lookup_tables:
            return "lookup"
        if _is_bare_reference(stripped, identifier, fld):
            return "direct_map"
    return "expression"


def _confidence_for_entry(
    final_resolved: frozenset[tuple[str, str]],
    final_dynamic: frozenset[str],
    hop_types: list[str],
) -> LineageConfidence:
    """Confidence for the fully Var-resolved edge (architecture doc D2)."""
    if not final_resolved and not final_dynamic:
        return LineageConfidence.CERTAIN  # pure constant, nothing ambiguous
    if not final_resolved:
        return LineageConfidence.DYNAMIC  # dynamic-only, no static signal at all
    if final_dynamic:
        return LineageConfidence.LIKELY  # mixed: some static signal, some dynamic noise
    if all(t == "direct_map" for t in hop_types):
        return LineageConfidence.CERTAIN  # clean passthrough end to end
    return LineageConfidence.LIKELY  # multi-ref expression and/or a lookup join


# ── Var chaining (architecture doc sec 2.2, step 2) ──────────────────────


@dataclass(frozen=True)
class _ResolvedExpression:
    resolved_refs: frozenset[tuple[str, str]]
    dynamic_refs: frozenset[str]
    # Hops in data-flow order: the Var-defining expression (if any) first,
    # the entry's own expression last.
    transformations: list[ColumnTransformation] = field(default_factory=list)


def _resolve_transformations(
    entry: TMapEntry,
    input_tables: set[str],
    lookup_tables: frozenset[str],
    var_lookup: dict[str, TMapEntry],
    *,
    _visited: frozenset[str] = frozenset(),
) -> _ResolvedExpression:
    """Recursively resolve one TMapEntry's expression, chaining through Var.

    Cycle-safe: a Var reference already on the recursion path is recorded
    as an unresolved dynamic reference rather than looping forever.
    """
    tokenized = tokenize_expression(entry.expression, input_tables)
    resolved: set[tuple[str, str]] = set()
    dynamic: set[str] = set(tokenized.dynamic_refs)
    upstream_transformations: list[ColumnTransformation] = []

    for identifier, fld in tokenized.resolved_refs:
        if identifier != _VAR_TABLE_NAME:
            resolved.add((identifier, fld))
            continue
        cycle_key = f"{_VAR_TABLE_NAME}.{fld}"
        var_entry = var_lookup.get(fld)
        if cycle_key in _visited or var_entry is None:
            # A Var self/mutual-reference cycle, or a Var referenced but
            # never defined -- never silently drop it, flag as dynamic.
            dynamic.add(cycle_key)
            continue
        hop = _resolve_transformations(
            var_entry, input_tables, lookup_tables, var_lookup,
            _visited=_visited | {cycle_key},
        )
        resolved |= hop.resolved_refs
        dynamic |= hop.dynamic_refs
        for transform in hop.transformations:
            if transform not in upstream_transformations:
                upstream_transformations.append(transform)

    this_hop_type = _classify_hop(tokenized.stripped, tokenized.resolved_refs, tokenized.dynamic_refs, lookup_tables)
    this_transform = ColumnTransformation(transformation_type=this_hop_type, expression=entry.expression)
    return _ResolvedExpression(
        resolved_refs=frozenset(resolved),
        dynamic_refs=frozenset(dynamic),
        transformations=upstream_transformations + [this_transform],
    )


# ── Row -> component resolution (architecture doc sec 2.2, step 3) ──────


def _find_connection(connections: list[Connection], name: str) -> Connection | None:
    for conn in connections:
        if conn.name == name:
            return conn
    return None


def _component_by_name(components: list[Component], unique_name: str) -> Component | None:
    for comp in components:
        if comp.unique_name == unique_name:
            return comp
    return None


@dataclass(frozen=True)
class _RowResolution:
    component_unique_name: str
    metadata_connection_ref: str


def _resolve_row(job: ParsedJob, table_name: str, *, side: str) -> _RowResolution | None:
    """Resolve a tMap input/output row name to its upstream/downstream component.

    side="input"  -> the row flows INTO the tMap; the connection SOURCE is upstream.
    side="output" -> the row flows OUT of the tMap; the connection TARGET is downstream.
    Returns None when no connection with that row name exists (unwired/unknown --
    never guessed).
    """
    conn = _find_connection(job.connections, table_name)
    if conn is None:
        return None
    other = conn.source if side == "input" else conn.target
    component = _component_by_name(job.components, other)
    return _RowResolution(
        component_unique_name=other,
        metadata_connection_ref=component.metadata_connection_ref if component else "",
    )


# ── Matrix construction ──────────────────────────────────────────────────


def _redact_transformations(
    transformations: list[ColumnTransformation],
    cred_filter: CredentialFilter,
) -> list[ColumnTransformation]:
    """Re-run the text-level credential filter on expression strings.

    Defense in depth (matches `post_filter.py`'s "re-run text filter as
    catch-all" pattern): `TMapEntry.expression` is already filtered at
    Layer 1 (raw XML text, before parsing), but `lineage.json` is a new
    assembled artifact and must independently carry no residual secrets.
    """
    redacted: list[ColumnTransformation] = []
    for t in transformations:
        expr = cred_filter.filter_text(t.expression) if t.expression else t.expression
        redacted.append(
            ColumnTransformation(
                transformation_type=t.transformation_type,
                expression=expr,
                human_explanation=t.human_explanation,
            )
        )
    return redacted


def _redacted_dynamic_refs(refs: set[str], cred_filter: CredentialFilter) -> list[str]:
    """Apply the same defense-in-depth filter to non-expression refs."""
    return sorted(cred_filter.filter_text(ref) for ref in refs)


@dataclass(frozen=True)
class _TreeFieldReference:
    tree_name: str
    path: str
    name: str


def _add_unique_reference(
    index: dict[str, list[_TreeFieldReference]],
    reference: str,
    candidate: _TreeFieldReference,
) -> None:
    if reference:
        # Keep duplicate captures too: identical-looking field nodes are still
        # two possible sources, so their spelling is ambiguous rather than
        # silently collapsed by set semantics.
        index.setdefault(reference, []).append(candidate)


def _txmlmap_input_reference_index(txmap: TXMLMapData) -> dict[str, list[_TreeFieldReference]]:
    """Index only source spellings actually represented in captured trees.

    A duplicate field name or xpath intentionally makes that spelling
    ambiguous.  We retain it in ``dynamic_refs``/``unmapped_columns`` instead
    of selecting an arbitrary branch of a nested XML tree.
    """
    index: dict[str, list[_TreeFieldReference]] = {}
    for tree in txmap.input_trees:
        for tree_field in tree.fields:
            path = tree_field.xpath.strip().strip("/") or tree_field.name
            candidate = _TreeFieldReference(tree.name, path, tree_field.name)
            if tree.name and tree_field.name:
                _add_unique_reference(index, f"{tree.name}.{tree_field.name}", candidate)
            if tree_field.xpath:
                _add_unique_reference(index, tree_field.xpath.strip().strip("/"), candidate)
    return index


def _expression_is_constant(expression: str) -> bool:
    return bool(_CONSTANT_EXPRESSION_RE.match(expression))


def _mapping_shape(
    expression: str,
    resolved_count: int,
    dynamic_refs: set[str],
    resolved_tokens: set[str],
) -> tuple[str, LineageConfidence]:
    """Classify a non-tMap mapping without pretending unknown syntax is data."""
    if not resolved_count:
        if not dynamic_refs and _expression_is_constant(expression):
            return "constant", LineageConfidence.CERTAIN
        return "dynamic", LineageConfidence.DYNAMIC
    if not dynamic_refs and len(resolved_tokens) == 1 and expression.strip() in resolved_tokens:
        return "direct_map", LineageConfidence.CERTAIN
    return "expression", LineageConfidence.LIKELY


def _unmapped_mapping_field(
    unmapped_columns: list[dict],
    *,
    job_name: str,
    table: str,
    column: str,
    reason: str,
    reference: str = "",
    mapping_type: str,
) -> None:
    item = {
        "job_name": job_name,
        "table": table,
        "column": column,
        "reason": reason,
        "mapping_type": mapping_type,
    }
    if reference:
        item["reference"] = reference
    unmapped_columns.append(item)


def _build_txmlmap_entries(
    job: ParsedJob,
    cred_filter: CredentialFilter,
    unmapped_columns: list[dict],
) -> list[LineageEntry]:
    """Emit conservative lineage from captured tXMLMap tree expressions.

    The parser preserves positional ``raw_connections`` but cannot safely turn
    those EMF proxies into source-component wiring. The enclosing node's
    ``UNIQUE_NAME`` is separately captured, so it is retained as the exact
    target component while the unresolved upstream side remains explicit in
    ``unmapped_columns``.
    """
    entries: list[LineageEntry] = []
    job_id = _job_identity(job)
    for txmap in job.txmlmap_data:
        input_index = _txmlmap_input_reference_index(txmap)
        wiring_reason = (
            "tXMLMap raw connections are positional and upstream component wiring cannot be resolved"
            if txmap.raw_connections
            else "tXMLMap does not contain resolved upstream component wiring"
        )
        _unmapped_mapping_field(
            unmapped_columns,
            job_name=job_id,
            table="",
            column="",
            reason=wiring_reason,
            mapping_type="tXMLMap",
        )
        for output_tree in txmap.output_trees:
            for output_field in output_tree.fields:
                expression = output_field.expression
                target_column = output_field.xpath or output_field.name
                tokens = set(_IDENTIFIER_FIELD_RE.findall(strip_comments_and_literals(expression)))
                ref_tokens = {f"{identifier}.{field}" for identifier, field in tokens}
                ref_tokens |= set(_TXMLMAP_PATH_REF_RE.findall(strip_comments_and_literals(expression)))

                resolved: set[_TreeFieldReference] = set()
                dynamic: set[str] = set()
                resolved_tokens: set[str] = set()
                for ref in sorted(ref_tokens):
                    candidates = input_index.get(ref, [])
                    if len(candidates) == 1:
                        resolved.update(candidates)
                        resolved_tokens.add(ref)
                    else:
                        dynamic.add(ref)
                        _unmapped_mapping_field(
                            unmapped_columns,
                            job_name=job_id,
                            table=output_tree.name,
                            column=target_column,
                            reason=(
                                "tXMLMap expression reference is ambiguous across captured input-tree paths"
                                if candidates else "tXMLMap expression reference is not a captured input-tree path"
                            ),
                            reference=ref,
                            mapping_type="tXMLMap",
                        )
                if not expression:
                    dynamic.add("tXMLMap:output_field_without_expression")
                    _unmapped_mapping_field(
                        unmapped_columns,
                        job_name=job_id,
                        table=output_tree.name,
                        column=target_column,
                        reason="tXMLMap output field has no captured expression",
                        mapping_type="tXMLMap",
                    )

                transformation_type, confidence = _mapping_shape(
                    expression, len(resolved), dynamic, resolved_tokens,
                )
                transformations = _redact_transformations([
                    ColumnTransformation(
                        transformation_type=transformation_type,
                        expression=expression,
                    )
                ], cred_filter)
                dynamic_refs = _redacted_dynamic_refs(dynamic, cred_filter)
                if not resolved:
                    entries.append(LineageEntry(
                        transformations=transformations,
                        target_table=output_tree.name,
                        target_column=target_column,
                        job_name=job_id,
                        confidence=confidence,
                        target_component=txmap.component_name,
                        resolved=False,
                        dynamic_refs=dynamic_refs,
                    ))
                    continue
                for source in sorted(resolved, key=lambda item: (item.tree_name, item.path, item.name)):
                    entries.append(LineageEntry(
                        source_table=source.tree_name,
                        source_column=source.path,
                        transformations=transformations,
                        target_table=output_tree.name,
                        target_column=target_column,
                        job_name=job_id,
                        confidence=confidence,
                        target_component=txmap.component_name,
                        resolved=True,
                        dynamic_refs=dynamic_refs,
                    ))
    return entries


def _tdm_input_reference_index(tdm: TdmMap) -> dict[str, list[_TreeFieldReference]]:
    index: dict[str, list[_TreeFieldReference]] = {}
    if tdm.input_tree is None:
        return index
    for tree_field in tdm.input_tree.fields:
        path = tree_field.path.strip().strip("/")
        if path:
            index.setdefault(path, []).append(
                _TreeFieldReference(tdm.input_tree.name, path, tree_field.name)
            )
    return index


def _build_tdm_entries(
    scan_result: ScanResult,
    cred_filter: CredentialFilter,
    unmapped_columns: list[dict],
) -> list[LineageEntry]:
    """Emit TDM lineage only where its directory ownership is exact.

    TDM's ``in$/...`` references are the only source syntax currently
    evidenced by the parser.  A missing/duplicate input path is surfaced; it
    never falls back to map-name or component-name similarity.
    """
    entries: list[LineageEntry] = []
    jobs_by_id = {_job_identity(job): job for job in scan_result.jobs}
    for tdm in scan_result.tdm_maps:
        if tdm.association not in {"directory_jobs", "directory_routes"} or not tdm.owning_artifact:
            _unmapped_mapping_field(
                unmapped_columns,
                job_name=tdm.owning_artifact,
                table=tdm.name,
                column="",
                reason="TDM map ownership is not an exact Maps/Jobs or Maps/Routes association",
                mapping_type="TDM",
            )
            continue
        owner = jobs_by_id.get(tdm.owning_artifact)
        if owner is None:
            _unmapped_mapping_field(
                unmapped_columns,
                job_name=tdm.owning_artifact,
                table=tdm.name,
                column="",
                reason="TDM owning artifact is not present in this scan",
                mapping_type="TDM",
            )
            continue
        if tdm.input_tree is None or tdm.output_tree is None:
            _unmapped_mapping_field(
                unmapped_columns,
                job_name=tdm.owning_artifact,
                table=tdm.name,
                column="",
                reason="TDM map is missing a captured input or output tree",
                mapping_type="TDM",
            )
            continue

        input_index = _tdm_input_reference_index(tdm)
        _unmapped_mapping_field(
            unmapped_columns,
            job_name=tdm.owning_artifact,
            table=tdm.name,
            column="",
            reason="TDM map does not expose resolved component wiring",
            mapping_type="TDM",
        )
        for output_field in tdm.output_tree.fields:
            expression = output_field.expression
            target_column = output_field.path or output_field.name
            refs = set(_TDM_INPUT_REF_RE.findall(expression))
            resolved: set[_TreeFieldReference] = set()
            dynamic: set[str] = set()
            resolved_tokens: set[str] = set()
            for ref in sorted(refs):
                path = ref.removeprefix("in$/")
                candidates = input_index.get(path, [])
                if len(candidates) == 1:
                    resolved.update(candidates)
                    resolved_tokens.add(ref)
                else:
                    dynamic.add(ref)
                    _unmapped_mapping_field(
                        unmapped_columns,
                        job_name=tdm.owning_artifact,
                        table=tdm.output_tree.name,
                        column=target_column,
                        reason=(
                            "TDM input path is ambiguous in the captured input tree"
                            if candidates else "TDM input path is not present in the captured input tree"
                        ),
                        reference=ref,
                        mapping_type="TDM",
                    )
            if not expression:
                dynamic.add("TDM:output_field_without_expression")
                _unmapped_mapping_field(
                    unmapped_columns,
                    job_name=tdm.owning_artifact,
                    table=tdm.output_tree.name,
                    column=target_column,
                    reason="TDM output field has no captured expression",
                    mapping_type="TDM",
                )
            elif not refs and not _expression_is_constant(expression):
                # The parser does not establish any alternate TDM input-ref
                # grammar. Retain the redacted expression as dynamic evidence
                # rather than parsing or guessing a different syntax.
                dynamic.add(f"TDM:unrecognized_expression:{expression}")

            transformation_type, confidence = _mapping_shape(
                expression, len(resolved), dynamic, resolved_tokens,
            )
            transformations = _redact_transformations([
                ColumnTransformation(
                    transformation_type=transformation_type,
                    expression=expression,
                )
            ], cred_filter)
            dynamic_refs = _redacted_dynamic_refs(dynamic, cred_filter)
            if not resolved:
                entries.append(LineageEntry(
                    transformations=transformations,
                    target_table=tdm.output_tree.name,
                    target_column=target_column,
                    job_name=_job_identity(owner),
                    confidence=confidence,
                    resolved=False,
                    dynamic_refs=dynamic_refs,
                ))
                continue
            for source in sorted(resolved, key=lambda item: (item.path, item.name)):
                entries.append(LineageEntry(
                    source_table=tdm.input_tree.name,
                    source_column=source.path or source.name,
                    transformations=transformations,
                    target_table=tdm.output_tree.name,
                    target_column=target_column,
                    job_name=_job_identity(owner),
                    confidence=confidence,
                    resolved=True,
                    dynamic_refs=dynamic_refs,
                ))
    return entries


def _build_job_entries(
    job: ParsedJob,
    cred_filter: CredentialFilter,
    unmapped_columns: list[dict],
) -> list[LineageEntry]:
    entries: list[LineageEntry] = []
    job_id = _job_identity(job)

    for tmap in job.tmap_data:
        input_tables = set(tmap.input_tables)
        # Talend convention: the first declared input table is the main
        # flow; any additional input tables are LOOKUP joins.
        lookup_tables: frozenset[str] = (
            frozenset(tmap.input_tables[1:]) if len(tmap.input_tables) > 1 else frozenset()
        )
        var_lookup = {ve.column_name: ve for ve in tmap.var_entries}

        for out_entry in tmap.entries:
            resolution = _resolve_transformations(out_entry, input_tables, lookup_tables, var_lookup)
            transformations = _redact_transformations(resolution.transformations, cred_filter)
            hop_types = [t.transformation_type for t in resolution.transformations]
            confidence = _confidence_for_entry(resolution.resolved_refs, resolution.dynamic_refs, hop_types)
            dynamic_refs = sorted(cred_filter.filter_text(ref) for ref in resolution.dynamic_refs)

            target_resolution = _resolve_row(job, out_entry.table_name, side="output")
            target_component = target_resolution.component_unique_name if target_resolution else ""
            if target_resolution is None:
                unmapped_columns.append({
                    "job_name": job_id,
                    "table": out_entry.table_name,
                    "column": out_entry.column_name,
                    "reason": "no matching connection for output row name",
                })

            if not resolution.resolved_refs:
                # Constant or dynamic-only: never silently dropped, emitted
                # with an empty source and resolved=False.
                entries.append(LineageEntry(
                    source_table="",
                    source_column="",
                    transformations=transformations,
                    target_table=out_entry.table_name,
                    target_column=out_entry.column_name,
                    job_name=job_id,
                    confidence=confidence,
                    source_component="",
                    target_component=target_component,
                    resolved=False,
                    dynamic_refs=dynamic_refs,
                ))
                continue

            for identifier, fld in sorted(resolution.resolved_refs):
                source_resolution = _resolve_row(job, identifier, side="input")
                source_component = source_resolution.component_unique_name if source_resolution else ""
                source_system = source_resolution.metadata_connection_ref if source_resolution else ""
                if source_resolution is None:
                    unmapped_columns.append({
                        "job_name": job_id,
                        "table": identifier,
                        "column": fld,
                        "reason": "no matching connection for input row name",
                    })
                entries.append(LineageEntry(
                    source_system=source_system,
                    source_table=identifier,
                    source_column=fld,
                    transformations=transformations,
                    target_table=out_entry.table_name,
                    target_column=out_entry.column_name,
                    job_name=job_id,
                    confidence=confidence,
                    source_component=source_component,
                    target_component=target_component,
                    resolved=True,
                    dynamic_refs=dynamic_refs,
                ))

    return entries


# ── Cross-job stitching (architecture doc sec 2.2, step 5 / S5) ─────────
#
# Both stitchers below only ever *add* new `confidence: inferred` entries
# on top of the intra-job entries `_build_job_entries` already produced --
# they never mutate or replace an S4 entry, so a dynamic/unresolved
# intra-job entry stays visible alongside whatever cross-job edge is
# inferred for it (never silently dropped, matching the rest of this
# module's philosophy).

_CONTEXT_PARAM_TRANSFORM_TYPE = "trunjob_context_param"
_TABLE_IDENTITY_TRANSFORM_TYPE = "table_identity"

# Talend serializes DB-style table/query params under one of these names
# depending on component family (verified: tMSSqlInput/tMSSqlOutput use
# "TABLE"); checked in order, first non-empty value wins. Never guessed
# beyond this: a component exposing none of these never participates.
_TABLE_IDENTITY_PARAM_NAMES: tuple[str, ...] = ("TABLE", "TABLE_NAME", "TABLENAME")


def _context_ref(param_name: str) -> str:
    """The dynamic-ref spelling a tokenized `context.<name>` reference takes
    (see `tokenize_expression`) -- shared so the stitcher's lookup key
    always matches what step 1 actually recorded."""
    return f"context.{param_name}"


def _stitch_trunjob_context_params(
    scan_result: ScanResult,
    entries_by_job: dict[str, list[LineageEntry]],
    cred_filter: CredentialFilter,
    unmapped_columns: list[dict],
) -> list[LineageEntry]:
    """(a) Parent job passes a context param into a child job (`trunjob_refs`).

    If the child's tMap expressions reference that param as `context.<name>`
    (already recorded as a `dynamic_refs` entry by `_build_job_entries`),
    emit an `inferred` job-boundary edge from the parent job into the
    child's target column for every such match. The child job must be
    present in this same scan -- an unresolvable child is surfaced via
    `unmapped_columns`, never guessed.
    """
    jobs_by_id = {_job_identity(job): job for job in scan_result.jobs}
    stitched: list[LineageEntry] = []

    for parent in scan_result.jobs:
        ref: TRunJobRef
        for ref in parent.trunjob_refs:
            if not ref.context_params:
                continue
            # Only a confirmed resolved ID may cross a composite-project
            # boundary.  Historic scans have no resolved_job_id, so retain the
            # old child-name lookup only when it maps uniquely.
            child_id = ref.resolved_job_id
            if not child_id:
                legacy = [job for job in scan_result.jobs if job.name == ref.child_job]
                child_id = _job_identity(legacy[0]) if len(legacy) == 1 else ""
            child = jobs_by_id.get(child_id)
            if child is None:
                unmapped_columns.append({
                    "job_name": _job_identity(parent),
                    "table": ref.child_job,
                    "column": "",
                    "reason": "trunjob child job not found in scan for context-param stitching",
                })
                continue

            child_entries = entries_by_job.get(_job_identity(child), [])
            for param in ref.context_params:
                context_ref = _context_ref(param.name)
                matches = [e for e in child_entries if context_ref in e.dynamic_refs]
                if not matches:
                    continue
                value_expr = cred_filter.filter_text(param.value) if param.value else param.value
                # Multiple tMap entries in the child can reference the same
                # context param; each gets its own edge (never collapsed --
                # they may land on different target columns/components).
                for match in matches:
                    stitched.append(LineageEntry(
                        source_table="",
                        source_column=param.name,
                        transformations=[ColumnTransformation(
                            transformation_type=_CONTEXT_PARAM_TRANSFORM_TYPE,
                            expression=f"{_job_identity(parent)}:tRunJob({_job_identity(child)}) context.{param.name} = {value_expr}",
                        )],
                        target_table=match.target_table,
                        target_column=match.target_column,
                        job_name=_job_identity(child),
                        source_job_name=_job_identity(parent),
                        confidence=LineageConfidence.INFERRED,
                        source_component="",
                        target_component=match.target_component,
                        resolved=True,
                        dynamic_refs=[],
                    ))
    return stitched


def _table_identity_param(component: Component) -> str:
    """First non-empty table-identifying param value on a component
    (quote-stripped -- Talend serializes these as Java string literals,
    e.g. `"Processes"`). Empty when the component carries none of the
    known param names, meaning it never participates in table-identity
    stitching."""
    params = component.compact_params()
    for name in _TABLE_IDENTITY_PARAM_NAMES:
        value = params.get(name, "")
        if value:
            return value.strip().strip('"').strip("'")
    return ""


def _component_direction(component_type: str) -> str:
    """"write" for *Output components, "read" for *Input components, ""
    otherwise. Talend's naming convention is consistent across DB/file
    component families (tMSSqlInput/tMSSqlOutput, tDBInput/tDBOutput, ...)."""
    if "Output" in component_type:
        return "write"
    if "Input" in component_type:
        return "read"
    return ""


@dataclass(frozen=True)
class _TableEndpoint:
    job_name: str
    component_unique_name: str
    connection_ref: str
    table: str


def _table_endpoints(scan_result: ScanResult, direction: str) -> list[_TableEndpoint]:
    endpoints: list[_TableEndpoint] = []
    for job in scan_result.jobs:
        for component in job.components:
            if _component_direction(component.component_type) != direction:
                continue
            connection_ref = component.metadata_connection_ref
            table = _table_identity_param(component)
            # Never guess: both a resolved connection ref and a table value
            # are required. Two components with equally-empty refs are NOT
            # a match -- that would fabricate edges between every unresolved
            # component in the project.
            if not connection_ref or not table:
                continue
            endpoints.append(_TableEndpoint(_job_identity(job), component.unique_name, connection_ref, table))
    return endpoints


def _stitch_table_identity(scan_result: ScanResult) -> list[LineageEntry]:
    """(b) Job A's output component writes table T (connection ref + table
    param); job B's input component reads table T -> an `inferred`
    job-boundary edge, at table granularity (no column data is available
    for a plain read/write pair, unlike the tMap-derived S4 edges).
    """
    writers = _table_endpoints(scan_result, "write")
    readers = _table_endpoints(scan_result, "read")
    stitched: list[LineageEntry] = []

    for writer in writers:
        for reader in readers:
            if writer.job_name == reader.job_name:
                continue  # same-job identity is not a job-boundary hop
            if (writer.connection_ref, writer.table) != (reader.connection_ref, reader.table):
                continue
            stitched.append(LineageEntry(
                source_table=writer.table,
                source_column="",
                transformations=[ColumnTransformation(
                    transformation_type=_TABLE_IDENTITY_TRANSFORM_TYPE,
                    expression=f"{writer.connection_ref}.{writer.table}",
                )],
                target_table=reader.table,
                target_column="",
                job_name=reader.job_name,
                source_job_name=writer.job_name,
                confidence=LineageConfidence.INFERRED,
                source_component=writer.component_unique_name,
                target_component=reader.component_unique_name,
                resolved=True,
                dynamic_refs=[],
            ))
    return stitched


def build_lineage_matrix(scan_result: ScanResult, cred_filter: CredentialFilter) -> LineageMatrix:
    """Build a `LineageMatrix` from a (post_filter-redacted) `ScanResult`.

    Must be called after `filter_scan_result` / `verify_scan_result` have
    already run over `scan_result` (see `cli.py::scan`) so `TMapEntry`
    expressions have already passed Layer 1+2 redaction; this function
    additionally re-runs the text filter on every expression it copies out
    (Layer 2 equivalent for the new artifact) -- see `_redact_transformations`.
    """
    entries: list[LineageEntry] = []
    entries_by_job: dict[str, list[LineageEntry]] = {}
    unmapped_columns: list[dict] = []
    tmap_components_processed = 0
    txmlmap_components_processed = 0

    for job in scan_result.jobs:
        tmap_components_processed += len(job.tmap_data)
        txmlmap_components_processed += len(job.txmlmap_data)
        job_entries = _build_job_entries(job, cred_filter, unmapped_columns)
        job_entries.extend(_build_txmlmap_entries(job, cred_filter, unmapped_columns))
        entries_by_job[_job_identity(job)] = job_entries
        entries.extend(job_entries)

    tdm_entries = _build_tdm_entries(scan_result, cred_filter, unmapped_columns)
    entries.extend(tdm_entries)
    for entry in tdm_entries:
        entries_by_job.setdefault(entry.job_name, []).append(entry)

    cross_job_entries = _stitch_trunjob_context_params(
        scan_result, entries_by_job, cred_filter, unmapped_columns,
    )
    cross_job_entries.extend(_stitch_table_identity(scan_result))
    entries.extend(cross_job_entries)

    confidence_counts: dict[str, int] = {}
    for e in entries:
        confidence_counts[e.confidence.value] = confidence_counts.get(e.confidence.value, 0) + 1

    summary = {
        "total_entries": len(entries),
        "resolved_entries": sum(1 for e in entries if e.resolved),
        "unresolved_entries": sum(1 for e in entries if not e.resolved),
        "tmap_components_processed": tmap_components_processed,
        "jobs_with_tmap": sum(1 for job in scan_result.jobs if job.tmap_data),
        "txmlmap_components_processed": txmlmap_components_processed,
        "jobs_with_txmlmap": sum(1 for job in scan_result.jobs if job.txmlmap_data),
        "tdm_maps_processed": len(scan_result.tdm_maps),
        "tdm_entries": len(tdm_entries),
        "confidence_counts": confidence_counts,
        "cross_job_entries": len(cross_job_entries),
    }

    return LineageMatrix(entries=entries, unmapped_columns=unmapped_columns, summary=summary)


def save_lineage_matrix(lineage_matrix: LineageMatrix, output_dir: Path) -> Path:
    """Persist `scan/lineage.json`. Caller must gate this on `verify_lineage_matrix`
    passing (or an explicit `--allow-redaction-failures` opt-in), mirroring the
    `scan_result.json` gate in `cli.py::scan`."""
    scan_dir = output_dir / "scan"
    scan_dir.mkdir(parents=True, exist_ok=True)
    path = scan_dir / "lineage.json"
    path.write_text(lineage_matrix.model_dump_json(indent=2), encoding="utf-8")
    return path
