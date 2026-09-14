"""Talend Data Mapper structure parsing (``Structures/**/*.xml``).

A Structure is the schema half of TDM: a ``TdmMap`` names an input and an
output structure, and without them a map's ``in$/KTA/WID`` expression
references a field nobody can describe. Structures are independently
versioned repository artifacts with a ``.properties`` sidecar, so they are
parsed per Talend root exactly like jobs, contexts and routines.

Two rules govern this parser:

* **Never fail the scan.** A malformed or unrecognized structure file is
  recorded with ``parse_outcome="unsupported"`` plus a ``ScanWarning``; it
  is never dropped, because a missing structure silently turns a mapped
  field into an unexplained one.
* **Never guess.** Element attributes are copied verbatim, and any element
  tag this parser does not model is listed in ``unmatched_tags`` (making
  the outcome ``partial``) rather than being skipped quietly.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path

from intodoc.models import StructureDefinition, StructureElement
from intodoc.scanner.fswalk import child_dir, walk_files, warn_handler

# Tags the structure format is known to use. `child`/`rootElem` carry the
# element tree; the rest are metadata Talend Studio needs to redraw the
# structure and hold no field semantics, so they are recognized-and-ignored
# rather than reported as gaps.
_KNOWN_TAGS: frozenset[str] = frozenset(
    {
        "root",
        "rootElem",
        "child",
        "structNode",
        "namespaceContainers",
        "elemNode",
        "defaultRep",
    }
)

_TREE_TAGS: frozenset[str] = frozenset({"rootElem", "child"})


def _local_tag(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _optional_bool(value: str | None) -> bool | None:
    """Parse a serialized boolean, leaving absence as unknown.

    An omitted ``nullable`` is not evidence of ``nullable=false``; the
    export simply did not say.
    """
    if value is None:
        return None
    normalized = value.strip().casefold()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    return None


def _element_from(elem: ET.Element, path: str) -> StructureElement:
    return StructureElement(
        name=elem.get("name", ""),
        path=path,
        data_type=elem.get("dataType", "") or elem.get("type", ""),
        occur_max_times=elem.get("occurMaxTimes", ""),
        min_size=elem.get("minSize", ""),
        max_size=elem.get("maxSize", ""),
        nullable=_optional_bool(elem.get("nullable")),
        group_type=elem.get("groupType", ""),
    )


def parse_structure_file(
    xml_file: Path,
    *,
    relative_path: str,
    source_root: str,
    read_text: Callable[[Path], str],
    sidecar: dict[str, str],
) -> tuple[StructureDefinition, str]:
    """Parse one structure file. Returns (definition, warning_message).

    ``warning_message`` is empty when nothing needs reporting; the caller
    owns warning emission so this function stays pure enough to unit test.
    """
    name = sidecar.get("label") or xml_file.stem
    definition = StructureDefinition(
        name=name,
        path=relative_path,
        source_root=source_root,
        property_id=sidecar.get("property_id", ""),
        version=sidecar.get("version", ""),
    )
    try:
        root = ET.fromstring(read_text(xml_file))
    except ET.ParseError as exc:
        definition.parse_outcome = "unsupported"
        return definition, f"Structure '{name}' could not be parsed as XML: {exc}"

    unmatched: set[str] = set()
    for elem in root.iter():
        tag = _local_tag(elem.tag)
        if tag not in _KNOWN_TAGS:
            unmatched.add(tag)

    elements: list[StructureElement] = []

    def walk(elem: ET.Element, prefix: str) -> None:
        for child in elem:
            if _local_tag(child.tag) not in _TREE_TAGS:
                continue
            child_name = child.get("name", "")
            path = f"{prefix}/{child_name}" if prefix else child_name
            elements.append(_element_from(child, path))
            walk(child, path)

    root_elems = [e for e in root if _local_tag(e.tag) == "rootElem"]
    if not root_elems:
        # Some structure exports nest rootElem below a wrapper element; look
        # deeper before declaring the file unsupported.
        root_elems = [e for e in root.iter() if _local_tag(e.tag) == "rootElem"]
    for root_elem in root_elems:
        root_name = root_elem.get("name", "")
        if not definition.root_element:
            definition.root_element = root_name
        elements.append(_element_from(root_elem, root_name))
        walk(root_elem, root_name)

    definition.elements = elements
    definition.element_count = len(elements)
    definition.unmatched_tags = sorted(unmatched)
    if not root_elems:
        definition.parse_outcome = "unsupported"
        return definition, (
            f"Structure '{name}' contains no rootElem; its element tree is invisible to downstream analysis."
        )
    if unmatched:
        definition.parse_outcome = "partial"
        return definition, (
            f"Structure '{name}' contains unmodelled element tags: {', '.join(definition.unmatched_tags)}."
        )
    return definition, ""


def parse_structures(
    talend_root: Path,
    source_root: str,
    *,
    read_text: Callable[[Path], str],
    read_sidecar: Callable[[Path], dict[str, str]],
    warn: Callable[..., None],
    latest_files: Callable[[Path, str], list[Path]] | None = None,
) -> list[StructureDefinition]:
    """Parse every structure under ``Structures/`` for one Talend root.

    ``latest_files`` receives (directory, glob) and returns the highest
    export version per artifact -- the same rule jobs and contexts use, so
    a structure saved five times is one artifact here too.
    """
    on_error = warn_handler(warn, "structure")
    structures_dir = child_dir(talend_root, "Structures", on_error=on_error)
    if structures_dir is None:
        return []

    files = (
        latest_files(structures_dir, "*.xml")
        if latest_files is not None
        else sorted(walk_files(structures_dir, patterns=("*.xml",), on_error=on_error))
    )
    results: list[StructureDefinition] = []
    for xml_file in files:
        definition, message = parse_structure_file(
            xml_file,
            relative_path=xml_file.relative_to(talend_root).as_posix(),
            source_root=source_root,
            read_text=read_text,
            sidecar=read_sidecar(xml_file),
        )
        if message:
            warn(
                kind=("parse_error" if definition.parse_outcome == "unsupported" else "partial_parse"),
                file=xml_file,
                message=message,
                phase="structure",
            )
        results.append(definition)
    return results
