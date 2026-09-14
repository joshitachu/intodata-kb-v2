"""Talend ESB service designs (``services/*.item`` + sibling ``*.wsdl``).

A service design is the published contract of an ESB project: it names the
port types and operations the outside world can call, and each operation is
implemented by a Route. Without it the scan can describe the routes but not
what any external consumer is actually allowed to invoke.

Only NAMES are captured (port type, operation, message). A WSDL also
carries endpoint addresses, which are connection evidence for the redaction
layers -- not service-contract structure -- so they are never lifted out
of the document here.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from pathlib import Path

from intodoc.models import ServiceDefinition, ServiceOperation
from intodoc.scanner.fswalk import child_dir, walk_files, warn_handler

# Tags that carry a port type / operation in either serialization
# (a Talend service .item or a raw WSDL).
_PORT_TYPE_TAGS: frozenset[str] = frozenset({"portType", "ServicePort", "servicePort"})
_OPERATION_TAGS: frozenset[str] = frozenset({"operation", "ServiceOperation", "serviceOperation"})

_VERSION_SUFFIX_RE = re.compile(r"(.+?)_(\d+(?:\.\d+)*)$")


def _local_tag(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _strip_version(stem: str) -> str:
    match = _VERSION_SUFFIX_RE.match(stem)
    return match.group(1) if match else stem


def _message_ref(elem: ET.Element, direction: str) -> str:
    for child in elem:
        if not isinstance(child.tag, str):
            continue
        if _local_tag(child.tag) == direction:
            return child.get("message", "") or child.get("name", "")
    return ""


def collect_operations(root: ET.Element) -> list[ServiceOperation]:
    """Collect port-type-qualified operations from a service item or WSDL.

    ElementTree exposes no parent pointers, so the port type is carried
    down explicitly instead of being matched back up by position.
    """
    operations: list[ServiceOperation] = []

    def walk(elem: ET.Element, port_type: str) -> None:
        for child in elem:
            if not isinstance(child.tag, str):
                continue
            tag = _local_tag(child.tag)
            if tag in _PORT_TYPE_TAGS:
                walk(child, child.get("name", "") or port_type)
                continue
            if tag in _OPERATION_TAGS:
                name = child.get("name", "")
                if name:
                    operations.append(
                        ServiceOperation(
                            port_type=port_type,
                            operation=name,
                            input_message=_message_ref(child, "input"),
                            output_message=_message_ref(child, "output"),
                        )
                    )
                continue
            walk(child, port_type)

    walk(root, "")
    return operations


def _find_sibling_wsdl(item_file: Path) -> Path | None:
    """The WSDL belonging to one service item, matched by exact stem.

    Talend writes ``Svc_0.1.item`` beside either ``Svc_0.1.wsdl`` or
    ``Svc.wsdl``; both are the same artifact. No other file is adopted,
    because a directory can hold several services.
    """
    exact = item_file.with_suffix(".wsdl")
    if exact.is_file():
        return exact
    unversioned = item_file.with_name(f"{_strip_version(item_file.stem)}.wsdl")
    return unversioned if unversioned.is_file() else None


def parse_services(
    talend_root: Path,
    source_root: str,
    *,
    read_text: Callable[[Path], str],
    read_sidecar: Callable[[Path], dict[str, str]],
    warn: Callable[..., None],
    latest_files: Callable[[Path, str], list[Path]] | None = None,
) -> list[ServiceDefinition]:
    """Parse the ``services/`` designs of one Talend root.

    Only the repository-layout half lives here: a service item is
    meaningless without the ``services/`` directory and the ``.properties``
    sidecar that sit around it, so it is correctly discovered per project
    root. A WSDL with no item beside it is a self-describing document and is
    discovered bundle-wide by :func:`parse_standalone_wsdls` instead.
    """
    results: list[ServiceDefinition] = []

    on_error = warn_handler(warn, "service")
    services_dir = child_dir(talend_root, "services", on_error=on_error)
    item_files: list[Path] = []
    if services_dir is not None:
        item_files = (
            latest_files(services_dir, "*.item")
            if latest_files is not None
            else sorted(walk_files(services_dir, patterns=("*.item",), on_error=on_error))
        )

    for item_file in item_files:
        sidecar = read_sidecar(item_file)
        wsdl = _find_sibling_wsdl(item_file)
        definition = ServiceDefinition(
            name=sidecar.get("label") or _strip_version(item_file.stem),
            path=item_file.relative_to(talend_root).as_posix(),
            source_root=source_root,
            property_id=sidecar.get("property_id", ""),
            version=sidecar.get("version", ""),
            wsdl_path=wsdl.relative_to(talend_root).as_posix() if wsdl else "",
        )
        outcome_messages: list[str] = []
        for candidate in [path for path in (item_file, wsdl) if path is not None]:
            try:
                definition.operations.extend(
                    collect_operations(
                        ET.fromstring(read_text(candidate)),
                    )
                )
            except ET.ParseError as exc:
                outcome_messages.append(
                    f"Service '{definition.name}' file {candidate.name} could not be parsed as XML: {exc}"
                )
        definition.operations = _dedupe_operations(definition.operations)
        if outcome_messages and not definition.operations:
            definition.parse_outcome = "unsupported"
        elif outcome_messages or not definition.operations:
            definition.parse_outcome = "partial"
            if not outcome_messages:
                outcome_messages.append(
                    f"Service '{definition.name}' declares no recognizable "
                    "portType/operation; its published contract is unknown."
                )
        for message in outcome_messages:
            warn(
                kind=("parse_error" if definition.parse_outcome == "unsupported" else "partial_parse"),
                file=item_file,
                message=message,
                phase="service",
            )
        results.append(definition)

    return sorted(results, key=lambda service: service.path)


def claimed_service_files(
    repo_root: Path, services: Iterable[ServiceDefinition],
) -> set[Path]:
    """Absolute paths already reported by a per-root :func:`parse_services`.

    Reconstructed from the definitions themselves rather than returned as a
    second output, so the two can never disagree about what was claimed.
    """
    claimed: set[Path] = set()
    for service in services:
        base = repo_root / service.source_root
        claimed.add(base / service.path)
        if service.wsdl_path:
            claimed.add(base / service.wsdl_path)
    return claimed


def parse_standalone_wsdls(
    search_root: Path,
    attribute: Callable[[Path], tuple[str, str]],
    *,
    claimed: set[Path],
    read_text: Callable[[Path], str],
    warn: Callable[..., None],
) -> list[ServiceDefinition]:
    """Parse every WSDL below ``search_root`` that no service item claims.

    A WSDL with no item beside it (routes commonly keep one under
    ``resources/``, and an estate may ship one in its own upload folder) is
    still a published contract, so it is parsed as its own definition rather
    than being inventoried and ignored. Discovery is by file type over the
    whole bundle for the same reason stylesheets are: the document is
    self-describing and needs no Talend project above it. ``attribute`` maps
    an absolute path to the ``(source_root, path)`` pair to record.
    """
    results: list[ServiceDefinition] = []
    on_error = warn_handler(warn, "service")
    for wsdl in sorted(walk_files(search_root, patterns=("*.wsdl",), on_error=on_error)):
        if wsdl in claimed:
            continue
        # Same hidden-path rule the repository walk applies, on the same
        # bundle-relative path, so this can never parse a file the inventory
        # reports as excluded.
        if any(part.startswith(".") for part in wsdl.relative_to(search_root).parts):
            continue
        source_root, relative_path = attribute(wsdl)
        definition = ServiceDefinition(
            name=_strip_version(wsdl.stem),
            path=relative_path,
            source_root=source_root,
            wsdl_path=relative_path,
        )
        try:
            definition.operations = _dedupe_operations(
                collect_operations(ET.fromstring(read_text(wsdl))),
            )
        except ET.ParseError as exc:
            definition.parse_outcome = "unsupported"
            warn(
                kind="parse_error",
                file=wsdl,
                phase="service",
                message=f"WSDL '{wsdl.name}' could not be parsed as XML: {exc}",
            )
        if definition.parse_outcome != "unsupported" and not definition.operations:
            definition.parse_outcome = "partial"
            warn(
                kind="partial_parse",
                file=wsdl,
                phase="service",
                message=(
                    f"WSDL '{wsdl.name}' declares no recognizable "
                    "portType/operation; its published contract is unknown."
                ),
            )
        results.append(definition)
    return results


def _dedupe_operations(operations: list[ServiceOperation]) -> list[ServiceOperation]:
    """Collapse the same operation seen in both the item and the WSDL.

    Order of first appearance is preserved so the list still reads like the
    contract document.
    """
    seen: set[tuple[str, str, str, str]] = set()
    unique: list[ServiceOperation] = []
    for operation in operations:
        key = (
            operation.port_type,
            operation.operation,
            operation.input_message,
            operation.output_message,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(operation)
    return unique
