"""Bind components to the support artifacts they name (exact tokens only).

A Talend component points at an XSLT stylesheet, a TDM map, a structure or
a WSDL through an ordinary string parameter (``RESOURCE_URI``,
``XSLT_FILE``, ``FILENAME``, ...). Nothing in the export links the two, so
without this pass the stylesheet that holds the whole transformation looks
unused and the component looks empty.

The binding rule is exact-token equality against a resource's repository
path or file name -- never similarity, never a prefix. A reference that
names a file the scan never parsed is recorded as ``unresolved`` (with the
reason), because "the transform is missing" is a finding, and silence is
not.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from intodoc.models import (
    ParsedJob,
    ServiceDefinition,
    StructureDefinition,
    TdmMap,
    XsltTransform,
)

# Suffixes that make a parameter value unambiguously a reference to a
# support artifact. A bare `.xml` is deliberately absent: schemas, samples
# and configuration all end in `.xml`, so treating those as unresolved
# resource references would manufacture findings.
_RESOURCE_SUFFIX_TYPES: dict[str, str] = {
    ".xsl": "xslt",
    ".xslt": "xslt",
    ".wsdl": "service",
}

_VERSION_SUFFIX_RE = re.compile(r"(.+?)_(\d+(?:\.\d+)*)$")


def _strip_version(stem: str) -> str:
    match = _VERSION_SUFFIX_RE.match(stem)
    return match.group(1) if match else stem


def _normalize_reference(value: str) -> str:
    """Normalize a parameter value to a comparable repository-style path.

    Only transport syntax is removed (quotes, backslashes, ``./``,
    ``file:``). The path itself is never rewritten.
    """
    text = value.strip()
    while len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1].strip()
    text = text.replace("\\", "/")
    for prefix in ("file:///", "file://", "file:"):
        if text.lower().startswith(prefix):
            text = text[len(prefix) :]
            break
    while text.startswith("./"):
        text = text[2:]
    return text.strip()


def _resource_tokens(path: str, *, name: str = "") -> list[str]:
    """The exact spellings by which a resource may legitimately be named."""
    posix = PurePosixPath(path)
    tokens = [path, posix.name, _strip_version(posix.stem)]
    if name:
        tokens.append(name)
    return [token for token in dict.fromkeys(tokens) if token]


def _build_index(
    structures: list[StructureDefinition],
    transforms: list[XsltTransform],
    services: list[ServiceDefinition],
    tdm_maps: list[TdmMap],
) -> dict[str, dict[str, list[tuple[str, str]]]]:
    """token -> [(resource_type, resource_path)], per source root."""
    index: dict[str, dict[str, list[tuple[str, str]]]] = {}

    def add(source_root: str, tokens: list[str], resource_type: str, resource: str) -> None:
        bucket = index.setdefault(source_root, {})
        for token in tokens:
            entries = bucket.setdefault(token, [])
            if (resource_type, resource) not in entries:
                entries.append((resource_type, resource))

    for transform in transforms:
        add(transform.source_root, _resource_tokens(transform.path), "xslt", transform.path)
    for structure in structures:
        add(structure.source_root, _resource_tokens(structure.path, name=structure.name), "structure", structure.path)
    for mapping in tdm_maps:
        add(mapping.source_root, _resource_tokens(mapping.path, name=mapping.name), "tdm", mapping.path)
    for service in services:
        add(service.source_root, _resource_tokens(service.path), "service", service.path)
        if service.wsdl_path and service.wsdl_path != service.path:
            add(service.source_root, _resource_tokens(service.wsdl_path), "service", service.path)
    return index


def _lookup(
    bucket: dict[str, list[tuple[str, str]]],
    value: str,
) -> tuple[list[tuple[str, str]], str]:
    """Match one normalized value. Returns (candidates, matched_token)."""
    posix = PurePosixPath(value)
    for token in (value, posix.name):
        if token and token in bucket:
            return bucket[token], token
    # A parameter may carry a longer path than the repository-relative one
    # (an absolute deployment path). Accept it only when the repository path
    # is a whole trailing path segment sequence of it.
    for token, entries in bucket.items():
        if "/" in token and value.endswith(f"/{token}"):
            return entries, token
    return [], ""


def build_resource_refs(
    jobs: list[ParsedJob],
    *,
    structures: list[StructureDefinition],
    transforms: list[XsltTransform],
    services: list[ServiceDefinition],
    tdm_maps: list[TdmMap],
) -> list[dict]:
    """Resolve every component/map reference to a support artifact.

    Resolution is scoped to the component's own source root: repository
    identities are project-local, so a stylesheet in one export can never
    satisfy a reference from another.

    Side effect: fills ``XsltTransform.referenced_by`` for exact matches,
    keeping the reference list and this ledger from ever disagreeing.
    """
    index = _build_index(structures, transforms, services, tdm_maps)
    refs: list[dict] = []
    referenced_by: dict[str, list[str]] = {}

    for job in jobs:
        bucket = index.get(job.source_root, {})
        artifact = job.job_id or job.name
        for component in job.components:
            for param in component.params:
                value = _normalize_reference(param.value)
                if not value or value.startswith("["):
                    continue
                candidates, token = _lookup(bucket, value) if bucket else ([], "")
                if len(candidates) == 1:
                    resource_type, resource = candidates[0]
                    refs.append(
                        {
                            "artifact": artifact,
                            "source_root": job.source_root,
                            "component_name": component.unique_name,
                            "param": param.name,
                            "resource_type": resource_type,
                            "resource": resource,
                            "matched_token": token,
                            "resolution": "exact",
                        }
                    )
                    if resource_type == "xslt":
                        owners = referenced_by.setdefault(resource, [])
                        owner = f"{artifact}::{component.unique_name}"
                        if owner not in owners:
                            owners.append(owner)
                    continue
                if len(candidates) > 1:
                    refs.append(
                        {
                            "artifact": artifact,
                            "source_root": job.source_root,
                            "component_name": component.unique_name,
                            "param": param.name,
                            "resource_type": candidates[0][0],
                            "resource": "",
                            "resolution": "unresolved",
                            "reason": "ambiguous_resource_name",
                            "candidates": [resource for _, resource in candidates],
                        }
                    )
                    continue
                suffix = PurePosixPath(value).suffix.lower()
                if suffix in _RESOURCE_SUFFIX_TYPES:
                    refs.append(
                        {
                            "artifact": artifact,
                            "source_root": job.source_root,
                            "component_name": component.unique_name,
                            "param": param.name,
                            "resource_type": _RESOURCE_SUFFIX_TYPES[suffix],
                            "resource": "",
                            "resolution": "unresolved",
                            "reason": "referenced_file_not_in_scan",
                        }
                    )

    # A TDM map names its structures directly in the map header, so those
    # references need no parameter scan -- but they resolve by the same rule.
    for mapping in tdm_maps:
        bucket = index.get(mapping.source_root, {})
        for slot, declared in (("inStruct", mapping.input_struct), ("outStruct", mapping.output_struct)):
            if not declared:
                continue
            candidates = [
                (resource_type, resource)
                for resource_type, resource in bucket.get(_normalize_reference(declared), [])
                if resource_type == "structure"
            ]
            base = {
                "artifact": mapping.owning_artifact,
                "source_root": mapping.source_root,
                "component_name": mapping.name or mapping.path,
                "param": slot,
                "resource_type": "structure",
            }
            if len(candidates) == 1:
                refs.append({**base, "resource": candidates[0][1], "resolution": "exact"})
            else:
                refs.append(
                    {
                        **base,
                        "resource": "",
                        "resolution": "unresolved",
                        "reason": ("ambiguous_resource_name" if candidates else "referenced_structure_not_in_scan"),
                        "declared": declared,
                    }
                )

    for transform in transforms:
        transform.referenced_by = referenced_by.get(transform.path, [])
    return refs
