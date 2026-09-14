"""Capture field-level evidence out of Talend job/route designs (``*.item``).

This exists because the scanner drops it. ``Component`` in
``intodoc/src/intodoc/models.py`` carries params and code but has no schema
field, and the parser never reads ``<metadata>/<column>``. Measured on one real
landscape: 687,294 column declarations across 4,254 designs, none retained.
Those declarations ARE the field-level interface contract, so no question about
which fields cross a boundary is answerable without them.

Captured per design:
  * every component's row schema, per connector, in declared order
  * tMap / tXMLMap output expressions (target field <- source expression)
  * tWriteJSONField write trees and tExtractJSONFields read paths -- the real
    payload shape, which routinely differs from the row schema feeding it
  * inline Java (tJava/tJavaRow/tJavaFlex) as a transformation expression
  * the flow graph, used to place each schema at a stage

Stage is derived from graph position rather than from a component-name
allowlist: a node with no inbound flow is offering data, a node with no
outbound flow is receiving it, everything between is mid-transformation. A name
allowlist silently misclassifies the custom joblets that real projects are full
of.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from ..models import (
    ArtifactRecord, Confidence, Direction, FieldRecord, Stage,
    TransformKind, Unresolved,
)

# Params worth keeping as evidence of what a component talks to.
_ENDPOINT_PARAMS = (
    "URI", "QUERY", "TABLE", "FILENAME", "DESTINATION", "XSLT_FILE",
    "SELECTED_JOB_NAME", "LOOP_QUERY", "JSON_LOOP_QUERY", "METHOD",
    "CONNECTION_FACOTRY_CONFIGURATION", "EXPRESSION", "IMPORT", "TYPE",
)
_CODE_PARAMS = ("CODE", "CODE_START", "CODE_MAIN", "CODE_END", "CODE_ONCE")
_LABEL_PARAMS = ("LABEL",)

# Components whose OUTPUT is a transformation result rather than a pass-through.
_TRANSFORM_COMPONENTS = frozenset({
    "tMap", "tXMLMap", "tJavaRow", "tJavaFlex", "tJava", "tXSLT",
    "tWriteJSONField", "tExtractJSONFields", "tExtractXMLField",
    "tConvertType", "tAggregateRow", "tNormalize", "tDenormalize",
    "tReplace", "tSplitRow", "tUnite", "cProcessor", "cSetBody", "cSetHeader",
})

_VERSIONED = re.compile(r"^(?P<base>.+)_(?P<major>\d+)\.(?P<minor>\d+)\.item$")
_DESIGN_DIRS = ("process", "routes", "joblets", "routelets")


@dataclass
class _Node:
    unique_name: str
    component_type: str
    params: dict[str, str]
    schemas: list[tuple[str, tuple[tuple[str, str], ...]]]  # (connector, ((col,type),...))
    json_tree: list[tuple[str, str]]                        # (path, column)


def latest_designs(repo_root: Path) -> dict[Path, tuple[int, int]]:
    """Newest version of each design, keyed by path.

    Talend keeps every historical version side by side; on one landscape 5,320
    superseded versions sat next to 1,345 live artifacts. Reading all of them
    would multiply the contract by its own edit history.
    """
    best: dict[tuple[str, str], tuple[tuple[int, int], Path]] = {}
    for path in repo_root.rglob("*.item"):
        rel = path.as_posix()
        if any(part.startswith(".") for part in rel.split("/")):
            continue
        parts = set(path.relative_to(repo_root).parts)
        if not (parts & set(_DESIGN_DIRS)):
            continue
        m = _VERSIONED.match(path.name)
        if not m:
            continue
        key = (path.parent.as_posix(), m.group("base"))
        ver = (int(m.group("major")), int(m.group("minor")))
        cur = best.get(key)
        if cur is None or ver > cur[0]:
            best[key] = (ver, path)
    return {p: v for v, p in best.values()}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _read_nodes(root: ET.Element) -> list[_Node]:
    nodes: list[_Node] = []
    for el in root.iter("node"):
        params: dict[str, str] = {}
        json_tree: list[tuple[str, str]] = []
        unique = ""
        for p in el.findall("elementParameter"):
            name = p.get("name") or ""
            val = p.get("value")
            if name == "UNIQUE_NAME":
                unique = val or ""
            if val:
                params[name] = val
            # tWriteJSONField / tAdvancedFileOutputXML express the real wire
            # shape as ROOT/LOOP/GROUP tables, which is NOT the row schema.
            if name in ("ROOT", "LOOP", "GROUP"):
                for ev in p.findall("elementValue"):
                    if ev.get("elementRef") == "PATH":
                        json_tree.append((ev.get("value") or "", ""))
                    elif ev.get("elementRef") == "COLUMN" and json_tree:
                        path, _ = json_tree[-1]
                        json_tree[-1] = (path, ev.get("value") or "")
        schemas: list[tuple[str, tuple[tuple[str, str], ...]]] = []
        for md in el.findall("metadata"):
            cols = tuple(
                (c.get("name") or "", c.get("type") or "")
                for c in md.findall("column")
            )
            if cols:
                schemas.append((md.get("connector") or md.get("name") or "", cols))
        nodes.append(_Node(
            unique_name=unique or el.get("componentName") or "",
            component_type=el.get("componentName") or "",
            params=params,
            schemas=schemas,
            json_tree=[t for t in json_tree if t[1]],
        ))
    return nodes


def _flow_graph(root: ET.Element) -> tuple[set[str], set[str]]:
    """Return (nodes with inbound flow, nodes with outbound flow)."""
    has_in: set[str] = set()
    has_out: set[str] = set()
    for c in root.findall("connection"):
        # lineStyle 0 is a data (row) link; triggers carry no fields.
        if (c.get("lineStyle") or "0") != "0":
            continue
        src, tgt = c.get("source"), c.get("target")
        if src:
            has_out.add(src)
        if tgt:
            has_in.add(tgt)
    return has_in, has_out


def _stage_for(node: _Node, has_in: set[str], has_out: set[str]) -> tuple[str, str]:
    """(stage, confidence) from graph position, falling back to component role."""
    n = node.unique_name
    inbound, outbound = n in has_in, n in has_out
    if not inbound and outbound:
        return Stage.OFFERED, Confidence.LIKELY
    if inbound and not outbound:
        return Stage.WRITTEN, Confidence.LIKELY
    if node.component_type in _TRANSFORM_COMPONENTS:
        return Stage.MAPPED, Confidence.LIKELY
    if inbound and outbound:
        return Stage.MAPPED, Confidence.INFERRED
    # Isolated node: declared but not wired into any flow.
    return Stage.MAPPED, Confidence.UNRESOLVED


def _map_expressions(root: ET.Element) -> list[tuple[str, str, str, str]]:
    """(container_name, target_field, expression, container_kind) for tMap/tXMLMap."""
    out: list[tuple[str, str, str, str]] = []
    for nd in root.iter("nodeData"):
        for container in nd:
            kind = _local(container.tag)
            if kind not in ("outputTables", "outputTrees", "varTables"):
                continue
            cname = container.get("name") or ""
            for entry in container.iter():
                name = entry.get("name")
                expr = entry.get("expression")
                if name and expr:
                    out.append((cname, name, expr, kind))
    return out


def _endpoints_and_refs(node: _Node) -> tuple[list[str], list[str]]:
    endpoints, refs = [], []
    for key in ("URI", "DESTINATION", "FILENAME", "TABLE", "QUERY"):
        v = node.params.get(key)
        if v:
            endpoints.append(f"{key}={v[:300]}")
    for key in ("XSLT_FILE", "SELECTED_JOB_NAME"):
        v = node.params.get(key)
        if v:
            refs.append(v)
    return endpoints, refs


def capture_design(
    path: Path,
    repo_root: Path,
    artifact_kind: str = "job",
) -> tuple[ArtifactRecord, list[FieldRecord], list[Unresolved]]:
    """Parse one design into an artifact record plus its field records."""
    rel = path.relative_to(repo_root).as_posix()
    name = _VERSIONED.match(path.name).group("base")  # caller guarantees match
    unresolved: list[Unresolved] = []

    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        return (
            ArtifactRecord(artifact_id=rel, name=name, kind=artifact_kind,
                           path=rel, notes=f"parse_error: {exc}"),
            [],
            [Unresolved("unparsed_design", rel, str(exc), rel)],
        )

    nodes = _read_nodes(root)
    has_in, has_out = _flow_graph(root)
    by_name = {n.unique_name: n for n in nodes}

    fields: list[FieldRecord] = []
    endpoints: list[str] = []
    mapping_refs: list[str] = []
    labels: list[str] = []

    # 1. schema declarations -------------------------------------------------
    for node in nodes:
        stage, conf = _stage_for(node, has_in, has_out)
        eps, refs = _endpoints_and_refs(node)
        endpoints.extend(eps)
        mapping_refs.extend(refs)
        if node.params.get("LABEL"):
            labels.append(node.params["LABEL"])

        for connector, cols in node.schemas:
            for col, ctype in cols:
                fields.append(FieldRecord(
                    interface_id=rel,
                    artifact_id=rel,
                    direction=Direction.INTERNAL,
                    stage=stage,
                    field_path=col,
                    field_type=ctype,
                    transform_kind=TransformKind.DECLARED,
                    evidence_file=rel,
                    evidence_locator=f"{node.unique_name}[{node.component_type}]:{connector}",
                    confidence=conf,
                ))

        # 2. the real wire shape, where it differs from the row schema -------
        for jpath, column in node.json_tree:
            fields.append(FieldRecord(
                interface_id=rel,
                artifact_id=rel,
                direction=Direction.OUTBOUND,
                stage=Stage.WRITTEN,
                field_path=jpath,
                source_fields=(column,),
                source_expr=column,
                transform_kind=TransformKind.DIRECT,
                evidence_file=rel,
                evidence_locator=f"{node.unique_name}[{node.component_type}]:wire",
                confidence=Confidence.CERTAIN,
                notes="serialised payload path",
            ))

        # 3. inline code as a transformation --------------------------------
        code = "\n".join(node.params[k] for k in _CODE_PARAMS if k in node.params)
        if code.strip():
            fields.append(FieldRecord(
                interface_id=rel,
                artifact_id=rel,
                direction=Direction.INTERNAL,
                stage=Stage.MAPPED,
                field_path=f"({node.unique_name} code)",
                source_expr=code,
                transform_kind=TransformKind.EXPRESSION,
                evidence_file=rel,
                evidence_locator=f"{node.unique_name}[{node.component_type}]:code",
                confidence=Confidence.CERTAIN,
                notes="inline code block",
            ))

        # 4. an unreadable external mapping is a recorded gap, not a silence -
        xslt = node.params.get("XSLT_FILE")
        if xslt and xslt.startswith("context."):
            unresolved.append(Unresolved(
                "mapping_via_context_variable",
                f"{rel}::{node.unique_name}",
                f"XSLT_FILE={xslt} -- the mapping file is chosen at runtime; "
                "resolve the context value to read the field mapping",
                rel,
            ))

    # 5. tMap / tXMLMap expressions ----------------------------------------
    for container, target, expr, kind in _map_expressions(root):
        srcs = _source_refs(expr)
        fields.append(FieldRecord(
            interface_id=rel,
            artifact_id=rel,
            direction=Direction.INTERNAL,
            stage=Stage.MAPPED,
            field_path=f"{container}/{target}" if container else target,
            source_expr=expr,
            source_fields=srcs,
            transform_kind=_transform_kind(expr),
            evidence_file=rel,
            evidence_locator=f"{kind}:{container}",
            confidence=Confidence.CERTAIN,
        ))

    artifact = ArtifactRecord(
        artifact_id=rel,
        name=name,
        kind=artifact_kind,
        group=_group_of(path, repo_root),
        source_root=_source_root_of(path, repo_root),
        path=rel,
        purpose="; ".join(dict.fromkeys(labels))[:400],
        component_types=tuple(sorted({n.component_type for n in nodes if n.component_type})),
        endpoints=tuple(sorted(set(endpoints))),
        mapping_refs=tuple(sorted(set(mapping_refs))),
        calls=tuple(sorted({
            n.params["SELECTED_JOB_NAME"] for n in nodes
            if n.params.get("SELECTED_JOB_NAME")
        })),
        name_resolved=True,
    )
    return artifact, fields, unresolved


_REF = re.compile(r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)")
# Prefixes that are plumbing, not a data source: keeping them would drown the
# real upstream field references. Mirrors the scanner lineage pass, which
# records these as dynamic refs rather than as sources.
_NON_SOURCE_PREFIXES = frozenset({
    "globalMap", "context", "System", "TalendDate", "StringHandling",
    "Math", "Integer", "String", "Double", "Long", "Boolean", "Arrays",
    "Base64", "UUID", "exchange", "output_row", "input_row", "java", "javax",
})


def _source_refs(expr: str) -> tuple[str, ...]:
    """`row1.WONUM` style upstream references, plumbing prefixes removed."""
    refs = {
        f"{a}.{b}" for a, b in _REF.findall(expr)
        if a not in _NON_SOURCE_PREFIXES
    }
    return tuple(sorted(refs, key=lambda s: (s.casefold(), s)))


def _transform_kind(expr: str) -> str:
    e = expr.strip()
    if not e:
        return TransformKind.UNKNOWN
    if re.fullmatch(r'"[^"]*"|\d+(\.\d+)?', e):
        return TransformKind.CONSTANT
    if re.fullmatch(r"[A-Za-z_]\w*\.[A-Za-z_]\w*\s*;?", e):
        return TransformKind.DIRECT
    if "?" in e and ":" in e:
        return TransformKind.CONDITIONAL
    if e.count("+") >= 2 and '"' in e:
        return TransformKind.CONCAT
    return TransformKind.EXPRESSION


def _source_root_of(path: Path, repo_root: Path) -> str:
    parts = path.relative_to(repo_root).parts
    for i, p in enumerate(parts):
        if p in _DESIGN_DIRS:
            return "/".join(parts[:i])
    return ""


def _group_of(path: Path, repo_root: Path) -> str:
    parts = path.relative_to(repo_root).parts
    for i, p in enumerate(parts):
        if p in _DESIGN_DIRS:
            rest = parts[i + 1:-1]
            return "/".join(rest)
    return ""
