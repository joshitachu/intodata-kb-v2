"""Bounded, role-local evidence access for Grounded agent runtimes.

This module deliberately knows nothing about a live repository.  It builds a
read-only view from the *already materialised* role packet, which is the
runtime boundary between Nexus orchestration and an agent.  A role therefore
cannot use these helpers to discover a sibling role's packet, the candidate
workspace, or the original uploaded project.

``FACT_LEDGER.json`` is exposed as a normal immutable evidence item and is
also used to derive a small, deterministic relationship graph.  Supporting
evidence is treated generically: prepared representations are just immutable
files under ``input/``; their type and authority are described by the index
that Nexus put in the packet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
import mimetypes
from pathlib import Path, PurePosixPath
from typing import Any


# These ceilings are deliberately independent of an agent-provided request.
# The per-role packet budget remains the outer cap.
MAX_LIST_ITEMS = 200
MAX_SEARCH_RESULTS = 50
MAX_READ_BYTES = 512 * 1024
MAX_SEARCH_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ScopedPacketEvidenceItem:
    """One evidence item which is readable by exactly one role packet."""

    evidence_id: str
    logical_name: str
    path: Path
    media_type: str
    sha256: str
    byte_size: int
    kind: str

    def summary(self) -> dict[str, object]:
        return {
            "byte_size": self.byte_size,
            "evidence_id": self.evidence_id,
            "kind": self.kind,
            "logical_name": self.logical_name,
            "media_type": self.media_type,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class ScopedPacketRelationship:
    """A scanner-derived relation, with the ledger as its evidence locator."""

    source_subject_id: str
    predicate: str
    target_subject_id: str
    evidence_ids: tuple[str, ...]

    def payload(self) -> dict[str, object]:
        return {
            "evidence_ids": list(self.evidence_ids),
            "predicate": self.predicate,
            "source_subject_id": self.source_subject_id,
            "target_subject_id": self.target_subject_id,
        }


@dataclass(frozen=True, slots=True)
class ScopedGroundedEvidence:
    """Immutable evidence and technical relations visible to one role only."""

    packet_root: Path
    items: tuple[ScopedPacketEvidenceItem, ...]
    relationships: tuple[ScopedPacketRelationship, ...]
    max_read_bytes: int
    max_search_bytes: int
    # Mutable on purpose despite the frozen dataclass: frozen only blocks
    # attribute *reassignment*, not mutation of a field's own contents, and
    # this needs per-attempt state to de-duplicate repeat reads. One binding
    # (and this evidence view) is constructed fresh per role attempt -- see
    # ``OpenAIAgentsGroundedTaskBuilder.build()`` -- so this can never leak
    # across attempts or roles.
    _seen_evidence_ids: set[str] = field(default_factory=set, repr=False, compare=False)

    @classmethod
    def from_packet(
        cls,
        packet_root: Path,
        *,
        max_context_bytes: int,
    ) -> "ScopedGroundedEvidence":
        """Construct a view from safe, immutable packet input roots only.

        Packet construction is responsible for populating these roots from a
        verified project-evidence snapshot.  We do not follow symlinks and do
        not include role prompts, candidate output paths, or arbitrary files
        placed next to the role packet.
        """

        if max_context_bytes < 1:
            raise ValueError("Grounded role context budget must be positive")
        root = packet_root.resolve(strict=True)
        candidates: list[Path] = []
        for root_name in ("input", "topology"):
            directory = root / root_name
            if not directory.exists():
                continue
            if directory.is_symlink() or not directory.is_dir():
                raise ValueError("Grounded evidence root must be a regular directory")
            for path in directory.rglob("*"):
                if path.is_symlink():
                    raise ValueError("Grounded evidence must not contain symlinks")
                if path.is_file():
                    candidates.append(path)

        items: list[ScopedPacketEvidenceItem] = []
        seen: set[str] = set()
        total_bytes = 0
        for path in sorted(candidates, key=lambda value: value.as_posix()):
            logical_name = _contained_logical_name(path, root)
            if logical_name in seen:
                raise ValueError("Grounded evidence logical names must be unique")
            content = _read_regular(path, root)
            total_bytes += len(content)
            if total_bytes > max_context_bytes:
                raise ValueError("Grounded evidence packet exceeds the role context budget")
            seen.add(logical_name)
            items.append(
                ScopedPacketEvidenceItem(
                    evidence_id=logical_name,
                    logical_name=logical_name,
                    path=path,
                    media_type=_media_type(logical_name),
                    sha256=sha256(content).hexdigest(),
                    byte_size=len(content),
                    kind=_kind(logical_name),
                )
            )

        if not any(item.logical_name == "input/FACT_LEDGER.json" for item in items):
            raise ValueError("Grounded role packet is missing input/FACT_LEDGER.json")
        return cls(
            packet_root=root,
            items=tuple(items),
            relationships=_relationships(items, root),
            max_read_bytes=min(MAX_READ_BYTES, max_context_bytes),
            max_search_bytes=min(MAX_SEARCH_BYTES, max_context_bytes),
        )

    def list(self, *, limit: int = MAX_LIST_ITEMS) -> dict[str, object]:
        bounded = _bounded_limit(limit, MAX_LIST_ITEMS, "limit")
        selected = self.items[:bounded]
        return {
            "items": [item.summary() for item in selected],
            "truncated": len(selected) < len(self.items),
        }

    def search(self, *, query: str, limit: int = 20) -> dict[str, object]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        bounded_limit = _bounded_limit(limit, MAX_SEARCH_RESULTS, "limit")
        needle = query.casefold()
        scanned = 0
        matches: list[dict[str, object]] = []
        for item in self.items:
            if scanned >= self.max_search_bytes or len(matches) >= bounded_limit:
                break
            content = _read_regular(item.path, self.packet_root)
            remaining = self.max_search_bytes - scanned
            bounded = content[:remaining]
            scanned += len(bounded)
            text = bounded.decode("utf-8", errors="replace")
            offset = text.casefold().find(needle)
            if offset < 0:
                continue
            snippet = _snippet(text, offset, len(query))
            matches.append({**item.summary(), "snippet": snippet})
        return {
            "items": matches,
            "scanned_bytes": scanned,
            "truncated": scanned < sum(item.byte_size for item in self.items),
        }

    def read(self, *, evidence_id: str, max_bytes: int) -> dict[str, object]:
        if not isinstance(evidence_id, str) or not evidence_id:
            raise ValueError("evidence_id must be a non-empty string")
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 1:
            raise ValueError("max_bytes must be a positive integer")
        if max_bytes > self.max_read_bytes:
            raise ValueError(
                f"max_bytes exceeds this role's read limit of {self.max_read_bytes}"
            )
        item = next((candidate for candidate in self.items if candidate.evidence_id == evidence_id), None)
        if item is None:
            raise PermissionError("evidence is outside this Grounded role packet")
        if evidence_id in self._seen_evidence_ids:
            # Already sent this evidence_id's full content earlier in this same
            # attempt. Returning it again would just resend bytes the model
            # already has, and every subsequent model turn resends the whole
            # conversation so far -- repeat reads of the same large evidence
            # were the dominant driver of a role attempt's cumulative token
            # cost (observed: the same ~500KB+ bundle read many times over).
            return {
                **item.summary(),
                "content": "",
                "returned_bytes": 0,
                "truncated": False,
                "already_read": True,
                "notice": (
                    "Already read earlier this attempt; content omitted to save "
                    "context. Re-read only if you need to re-verify exact bytes."
                ),
            }
        content = _read_regular(item.path, self.packet_root)
        if len(content) != item.byte_size or sha256(content).hexdigest() != item.sha256:
            raise PermissionError("Grounded evidence changed after packet binding")
        bounded = content[:max_bytes]
        self._seen_evidence_ids.add(evidence_id)
        return {
            **item.summary(),
            "content": bounded.decode("utf-8", errors="replace"),
            "returned_bytes": len(bounded),
            "truncated": len(bounded) < len(content),
        }

    def traverse(self, *, subject_id: str, limit: int = 100) -> dict[str, object]:
        if not isinstance(subject_id, str) or not subject_id:
            raise ValueError("subject_id must be a non-empty string")
        bounded = _bounded_limit(limit, 200, "limit")
        related = [
            relation.payload()
            for relation in self.relationships
            if subject_id in {relation.source_subject_id, relation.target_subject_id}
        ][:bounded]
        return {
            "relationships": related,
            "truncated": len(related) < sum(
                subject_id in {relation.source_subject_id, relation.target_subject_id}
                for relation in self.relationships
            ),
        }


def _relationships(
    items: list[ScopedPacketEvidenceItem],
    root: Path,
) -> tuple[ScopedPacketRelationship, ...]:
    ledger = next((item for item in items if item.logical_name == "input/FACT_LEDGER.json"), None)
    if ledger is None:
        return ()
    try:
        payload = json.loads(_read_regular(ledger.path, root).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("Grounded FACT_LEDGER.json must be valid JSON") from None
    if not isinstance(payload, dict):
        raise ValueError("Grounded FACT_LEDGER.json must be an object")

    relations: set[tuple[str, str, str]] = set()
    for item in _objects(payload.get("call_graph")):
        caller = _text(item.get("caller"))
        for callee in _strings(item.get("callees")):
            relations.add((_unit(caller), "invokes", _unit(callee)))
    for item in _objects(payload.get("connections")):
        connection = _text(item.get("name"))
        for unit in _strings(item.get("used_by")):
            relations.add((_unit(unit), "uses_connection", _connection(connection)))
    for unit, names in _string_mapping(payload.get("job_external_systems")).items():
        for name in names:
            relations.add((_unit(unit), "uses_external_system", _external_system(name)))
    for routine, units in _string_mapping(payload.get("routines_used_by")).items():
        for unit in units:
            relations.add((_unit(unit), "uses_routine", _routine(routine)))
    for group in _objects(payload.get("groups")):
        group_name = _text(group.get("name"))
        for unit in _strings(group.get("units")):
            relations.add((_unit(unit), "belongs_to_group", _group(group_name)))

    return tuple(
        ScopedPacketRelationship(source, predicate, target, (ledger.evidence_id,))
        for source, predicate, target in sorted(relations)
        if source and target
    )


def _contained_logical_name(path: Path, root: Path) -> str:
    resolved = path.resolve(strict=True)
    try:
        relative = resolved.relative_to(root)
    except ValueError as error:
        raise ValueError("Grounded evidence escapes its packet") from error
    value = relative.as_posix()
    parts = PurePosixPath(value).parts
    if not parts or parts[0] not in {"input", "topology"} or ".." in parts:
        raise ValueError("Grounded evidence is outside an authorized input root")
    return value


def _read_regular(path: Path, root: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Grounded evidence item must be a regular file")
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ValueError("Grounded evidence item escapes its packet") from error
    return resolved.read_bytes()


def _kind(logical_name: str) -> str:
    if logical_name == "input/FACT_LEDGER.json":
        return "scanner_fact_ledger"
    if logical_name.startswith("input/project_evidence/items/"):
        return "prepared_supporting_evidence"
    if logical_name.startswith("input/accepted/"):
        return "accepted_upstream_artifact"
    if logical_name.startswith("topology/"):
        return "topology_evidence"
    return "role_input"


def _media_type(logical_name: str) -> str:
    guessed, _ = mimetypes.guess_type(logical_name)
    return guessed or "application/octet-stream"


def _bounded_limit(value: int, maximum: int, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    if value > maximum:
        raise ValueError(f"{name} exceeds the maximum of {maximum}")
    return value


def _snippet(text: str, start: int, query_size: int) -> str:
    left = max(0, start - 120)
    right = min(len(text), start + query_size + 240)
    prefix = "…" if left else ""
    suffix = "…" if right < len(text) else ""
    return prefix + text[left:right].replace("\n", " ") + suffix


def _objects(value: object) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, dict))


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def _string_mapping(value: object) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, dict):
        return {}
    return {
        key.strip(): _strings(items)
        for key, items in value.items()
        if isinstance(key, str) and key.strip()
    }


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _unit(value: str) -> str:
    return f"unit:{value}" if value else ""


def _connection(value: str) -> str:
    return f"connection:{value}" if value else ""


def _external_system(value: str) -> str:
    return f"external_system:{value}" if value else ""


def _routine(value: str) -> str:
    return f"routine:{value}" if value else ""


def _group(value: str) -> str:
    return f"group:{value}" if value else ""
