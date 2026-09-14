"""Capture field mappings out of XSL/XSLT stylesheets.

The scanner never sees these files: ``resources/``, ``services/`` and
``route_resources/`` are absent from the Talend classification ruleset, so the
stylesheets -- which ARE the field mapping for every ``tXSLT``-based interface --
fall out of the index along with the ``.xsd`` and ``.wsdl`` contracts beside
them.

For each literal result element that produces a value, this emits the target
path, the source expression(s) driving it, and a transform kind. Conditional
structures (``xsl:choose``, ``xsl:if``) are flattened into one record per
target with every branch's select expression retained, because the branch
condition is usually where the business rule lives -- collapsing to the first
branch would discard exactly the thing worth reading.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from ..models import (
    ArtifactRecord, Confidence, Direction, FieldRecord, Stage,
    TransformKind, Unresolved,
)

_XSL = "{http://www.w3.org/1999/XSL/Transform}"
_AVT = re.compile(r"\{([^}]+)\}")
_SUFFIXES = (".xsl", ".xslt")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _is_xsl(el: ET.Element) -> bool:
    return isinstance(el.tag, str) and el.tag.startswith(_XSL)


def _collect_values(el: ET.Element) -> tuple[list[str], list[str], bool]:
    """Recursively gather (select expressions, branch conditions, saw_literal).

    Descends through xsl control flow only; stops at a nested literal result
    element, which is a target of its own and handled by the caller.
    """
    selects: list[str] = []
    conditions: list[str] = []
    saw_literal = False

    for child in el:
        if not isinstance(child.tag, str):
            continue
        if not _is_xsl(child):
            saw_literal = True
            continue
        name = _local(child.tag)
        if name == "value-of":
            sel = child.get("select")
            if sel:
                selects.append(sel.strip())
        elif name == "text":
            txt = (child.text or "").strip()
            if txt:
                selects.append(f'"{txt}"')
        elif name in ("choose", "if", "when", "otherwise", "for-each",
                      "copy-of", "attribute", "element", "variable",
                      "apply-templates", "call-template", "sequence"):
            cond = child.get("test") or child.get("select")
            if name in ("if", "when") and cond:
                conditions.append(cond.strip())
            if name == "copy-of" and cond:
                selects.append(f"copy-of({cond.strip()})")
            s, c, lit = _collect_values(child)
            selects.extend(s)
            conditions.extend(c)
            saw_literal = saw_literal or lit
    return selects, conditions, saw_literal


def _kind(selects: list[str], conditions: list[str], literal_text: str) -> str:
    if conditions:
        return TransformKind.CONDITIONAL
    if not selects:
        return TransformKind.CONSTANT if literal_text else TransformKind.UNKNOWN
    if len(selects) > 1:
        return TransformKind.CONCAT
    only = selects[0]
    if only.startswith('"'):
        return TransformKind.CONSTANT
    if re.fullmatch(r"[\w:./@\-]+", only):
        return TransformKind.DIRECT
    return TransformKind.EXPRESSION


def capture_stylesheet(
    path: Path, repo_root: Path
) -> tuple[ArtifactRecord, list[FieldRecord], list[Unresolved]]:
    rel = path.relative_to(repo_root).as_posix()
    unresolved: list[Unresolved] = []

    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        return (
            ArtifactRecord(artifact_id=rel, name=path.name, kind="mapping",
                           path=rel, notes=f"parse_error: {exc}"),
            [],
            [Unresolved("unparsed_mapping", rel, str(exc), rel)],
        )

    fields: list[FieldRecord] = []
    source_roots: set[str] = set()

    # Stylesheet-level variables and parameters. These are derivation steps in
    # their own right and frequently hold the load-bearing logic -- a
    # `tokenize(key, '_')` that splits a composite identifier lives here, not
    # at the field that consumes it, so a field-only capture never sees it.
    for tag, kind_label in ((f"{_XSL}variable", "variable"),
                            (f"{_XSL}param", "param")):
        for var in root.iter(tag):
            vname = var.get("name") or ""
            sel = (var.get("select") or "").strip()
            if not vname or not sel:
                continue
            fields.append(FieldRecord(
                interface_id=rel, artifact_id=rel,
                direction=Direction.INTERNAL, stage=Stage.MAPPED,
                field_path=f"${vname}",
                source_expr=sel,
                source_fields=(sel,),
                transform_kind=_kind([sel], [], ""),
                evidence_file=rel,
                evidence_locator=f"xsl:{kind_label}[{vname}]",
                confidence=Confidence.CERTAIN,
                notes=f"stylesheet {kind_label}",
            ))

    for template in root.iter(f"{_XSL}template"):
        match = template.get("match") or template.get("name") or ""
        if match:
            source_roots.add(match)

        # Walk every literal result element inside this template.
        stack: list[tuple[ET.Element, str]] = [(template, "")]
        while stack:
            node, prefix = stack.pop()
            for child in node:
                if not isinstance(child.tag, str):
                    continue
                if _is_xsl(child):
                    # descend through control flow, keeping the same prefix
                    stack.append((child, prefix))
                    continue

                target = _local(child.tag)
                tpath = f"{prefix}/{target}" if prefix else target
                selects, conditions, has_child_literal = _collect_values(child)
                literal_text = (child.text or "").strip()

                # Attribute value templates carry mappings too.
                for aname, aval in child.attrib.items():
                    for expr in _AVT.findall(aval):
                        fields.append(FieldRecord(
                            interface_id=rel, artifact_id=rel,
                            direction=Direction.OUTBOUND, stage=Stage.MAPPED,
                            field_path=f"{tpath}@{_local(aname)}",
                            source_expr=expr.strip(),
                            source_fields=(expr.strip(),),
                            transform_kind=TransformKind.DIRECT,
                            evidence_file=rel,
                            evidence_locator=f"template[{match}]",
                            confidence=Confidence.CERTAIN,
                        ))

                if selects or conditions or literal_text:
                    expr_parts = list(conditions) + selects
                    fields.append(FieldRecord(
                        interface_id=rel, artifact_id=rel,
                        direction=Direction.OUTBOUND, stage=Stage.MAPPED,
                        field_path=tpath,
                        source_expr=" | ".join(expr_parts) or literal_text,
                        source_fields=tuple(selects),
                        transform_kind=_kind(selects, conditions, literal_text),
                        evidence_file=rel,
                        evidence_locator=f"template[{match}]",
                        confidence=Confidence.CERTAIN,
                        notes=(f"{len(conditions)} branch condition(s)"
                               if conditions else ""),
                    ))

                if has_child_literal:
                    stack.append((child, tpath))

    if not fields:
        unresolved.append(Unresolved(
            "mapping_yielded_no_fields", rel,
            "stylesheet parsed but produced no target fields -- likely an "
            "identity or include-only transform",
            rel,
        ))

    artifact = ArtifactRecord(
        artifact_id=rel,
        name=path.name,
        kind="mapping",
        path=rel,
        purpose=_header_comment(path),
        source_root="/".join(path.relative_to(repo_root).parts[:-1]),
        endpoints=tuple(sorted(_declared_endpoints(path))),
        notes=f"matches: {', '.join(sorted(source_roots))}"[:400],
    )
    return artifact, fields, unresolved


def _header_comment(path: Path, limit: int = 400) -> str:
    """The leading XML comment, which in practice names the functional design."""
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        return ""
    m = re.search(r"<!--(.*?)-->", head, re.DOTALL)
    if not m:
        return ""
    text = re.sub(r"\s+", " ", m.group(1)).strip()
    return text[:limit]


_URL = re.compile(r"https?://[^\s\"'<>]+")


def _declared_endpoints(path: Path) -> set[str]:
    """Endpoints hardcoded in the stylesheet.

    Worth surfacing on its own: a mapping that names an environment is a
    deployment hazard, and on real data these turn out to be test addresses
    embedded in production mappings.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    return {u.rstrip(".,;") for u in _URL.findall(text)}


def find_stylesheets(repo_root: Path) -> list[Path]:
    out: list[Path] = []
    for path in repo_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in _SUFFIXES:
            continue
        rel = path.relative_to(repo_root).as_posix()
        if any(part.startswith(".") for part in rel.split("/")):
            continue
        out.append(path)
    return sorted(out)
