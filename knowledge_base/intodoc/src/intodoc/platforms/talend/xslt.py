"""XSLT stylesheet parsing for Talend routes and jobs (``*.xsl`` / ``*.xslt``).

Talend routes delegate transformation to stylesheets held under
``route_resources/`` or ``resources/`` and named from a component parameter
(cXSLT ``RESOURCE_URI``, tXSLT ``XSLT_FILE``, ...). A scan that only reads
job items therefore sees a component called "transform" and no mapping at
all -- the entire business rule lives in a file it never opened.

This parser is deliberately structural: it records which templates exist
and which value-producing instruction wrote which output path from which
``select`` expression. It does not evaluate XPath, and it never guesses an
owner: ``referenced_by`` is populated only by exact-token matching in
``resource_refs.py``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path

from intodoc.models import XsltMapping, XsltTemplate, XsltTransform
from intodoc.scanner.fswalk import walk_files, warn_handler

_XSL_NAMESPACE = "http://www.w3.org/1999/XSL/Transform"

# Value-producing instructions modelled as mappings (the closed vocabulary
# of `XsltMapping.instruction`, minus "literal-element", which is any
# non-XSL result element).
_MAPPING_INSTRUCTIONS: frozenset[str] = frozenset(
    {
        "value-of",
        "copy-of",
        "copy",
        "for-each",
        "apply-templates",
        "if",
        "when",
        "otherwise",
        "attribute",
        "element",
        "text",
        "call-template",
    }
)

# Recognized declarations and helpers that are not themselves mappings.
# Anything in the XSL namespace outside these two sets is reported in
# `unmatched_instructions` so an unmodelled construct is never invisible.
_STRUCTURAL_TAGS: frozenset[str] = frozenset(
    {
        "stylesheet",
        "transform",
        "template",
        "variable",
        "param",
        "include",
        "import",
        "output",
        "key",
        "sort",
        "choose",
        "with-param",
        "strip-space",
        "preserve-space",
        "decimal-format",
        "namespace-alias",
        "attribute-set",
        "character-map",
        "output-character",
        "fallback",
        "comment",
        "processing-instruction",
        "number",
        "message",
        "otherwise",
        "import-schema",
        "function",
        "sequence",
        "for-each-group",
        "matching-substring",
    }
)


def _local_tag(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _is_xsl(elem: ET.Element) -> bool:
    return isinstance(elem.tag, str) and elem.tag.startswith(f"{{{_XSL_NAMESPACE}}}")


def parse_xslt_file(
    xsl_file: Path,
    *,
    relative_path: str,
    source_root: str,
    read_text: Callable[[Path], str],
) -> tuple[XsltTransform, str]:
    """Parse one stylesheet. Returns (transform, warning_message)."""
    transform = XsltTransform(
        name=xsl_file.name,
        path=relative_path,
        source_root=source_root,
    )
    try:
        root = ET.fromstring(read_text(xsl_file))
    except ET.ParseError as exc:
        transform.parse_outcome = "unsupported"
        return transform, f"XSLT '{xsl_file.name}' could not be parsed as XML: {exc}"

    unmatched: set[str] = set()

    def declaration(child: ET.Element, *, template_match: str) -> bool:
        """Record a top-level/in-template declaration. True when consumed."""
        tag = _local_tag(child.tag)
        if tag == "variable":
            transform.variables.append(
                {
                    "name": child.get("name", ""),
                    "select": child.get("select", ""),
                    "template_match": template_match,
                }
            )
            return True
        if tag == "param":
            transform.params.append(
                {
                    "name": child.get("name", ""),
                    "select": child.get("select", ""),
                    "template_match": template_match,
                }
            )
            return True
        if tag in {"include", "import"}:
            href = child.get("href", "")
            if href:
                transform.includes.append(href)
            return True
        return False

    def walk(elem: ET.Element, *, template_match: str, template_mode: str, output_path: str) -> None:
        for child in elem:
            if not isinstance(child.tag, str):
                continue  # comment / processing instruction node
            tag = _local_tag(child.tag)
            if not _is_xsl(child):
                # Literal result element: it is the output markup itself.
                child_path = f"{output_path}/{tag}" if output_path else tag
                transform.mappings.append(
                    XsltMapping(
                        template_match=template_match,
                        instruction="literal-element",
                        output_path=child_path,
                        mode=template_mode,
                    )
                )
                walk(child, template_match=template_match, template_mode=template_mode, output_path=child_path)
                continue
            if tag in _MAPPING_INSTRUCTIONS:
                named = child.get("name", "")
                child_path = f"{output_path}/{named}" if named and output_path else (named or output_path)
                transform.mappings.append(
                    XsltMapping(
                        template_match=template_match,
                        instruction=tag,  # type: ignore[arg-type]
                        select=child.get("select", ""),
                        test=child.get("test", ""),
                        output_path=child_path,
                        mode=child.get("mode", "") or template_mode,
                    )
                )
                walk(child, template_match=template_match, template_mode=template_mode, output_path=child_path)
                continue
            if not declaration(child, template_match=template_match) and (tag not in _STRUCTURAL_TAGS):
                unmatched.add(tag)
            walk(child, template_match=template_match, template_mode=template_mode, output_path=output_path)

    if _is_xsl(root) and _local_tag(root.tag) in {"stylesheet", "transform"}:
        for child in root:
            if not isinstance(child.tag, str) or not _is_xsl(child):
                continue
            tag = _local_tag(child.tag)
            if tag == "template":
                template = XsltTemplate(
                    match=child.get("match", ""),
                    name=child.get("name", ""),
                    mode=child.get("mode", ""),
                )
                transform.templates.append(template)
                walk(child, template_match=template.match or template.name, template_mode=template.mode, output_path="")
                continue
            if declaration(child, template_match=""):
                continue
            if tag not in _STRUCTURAL_TAGS and tag not in _MAPPING_INSTRUCTIONS:
                unmatched.add(tag)
    else:
        # A literal-result-element stylesheet (xsl:version on the output
        # root) is valid XSLT; treat the whole document as one template.
        walk(root, template_match="", template_mode="", output_path="")

    transform.includes = sorted(set(transform.includes))
    transform.unmatched_instructions = sorted(unmatched)
    if unmatched:
        transform.parse_outcome = "partial"
        return transform, (
            f"XSLT '{xsl_file.name}' uses unmodelled instructions: {', '.join(transform.unmatched_instructions)}."
        )
    return transform, ""


def parse_xslt_transforms(
    search_root: Path,
    attribute: Callable[[Path], tuple[str, str]],
    *,
    read_text: Callable[[Path], str],
    warn: Callable[..., None],
) -> list[XsltTransform]:
    """Parse every stylesheet below ``search_root``, wherever it is stored.

    Discovery is by file type over the whole source bundle, not per Talend
    project: a stylesheet is self-describing and needs neither a
    ``.properties`` sidecar nor a ``talend.project`` above it, so a search
    confined to the project roots only ever lost the ones an estate ships in
    a separately uploaded mapping folder.

    ``attribute`` maps an absolute stylesheet path to the
    ``(source_root, path)`` pair recorded on the transform. Ownership is the
    caller's decision because only the caller knows where this bundle's
    Talend projects are; discovery is this module's.
    """
    results: list[XsltTransform] = []
    on_error = warn_handler(warn, "xslt")
    for xsl_file in sorted(walk_files(search_root, patterns=("*.xsl", "*.xslt"), on_error=on_error)):
        # Same hidden-path rule the repository walk applies, and applied to
        # the same bundle-relative path, so a stylesheet can never be parsed
        # here while the file inventory reports it as excluded.
        if any(part.startswith(".") for part in xsl_file.relative_to(search_root).parts):
            continue
        source_root, relative_path = attribute(xsl_file)
        transform, message = parse_xslt_file(
            xsl_file,
            relative_path=relative_path,
            source_root=source_root,
            read_text=read_text,
        )
        if message:
            warn(
                kind=("parse_error" if transform.parse_outcome == "unsupported" else "partial_parse"),
                file=xsl_file,
                message=message,
                phase="xslt",
            )
        results.append(transform)
    return sorted(results, key=lambda t: (t.source_root, t.path))
