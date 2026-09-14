"""Census real message populations -- structure and fill rate, never the payload.

Answers "what does this application actually put on the wire, before any
mapping?" and, joined against the declared contract, "which declared fields are
never populated?". On one landscape that join showed 19 of 52 elements in a
partner's outbound contract were never populated in any observed message --
which is not derivable from code at all.

Deliberately a census and not a copy, for two reasons. Volume: these sample
directories run to thousands of messages. And exposure: production message
samples carry personal data (technician names and numbers, customer addresses,
coordinates), so the layer records paths, counts and bounded value domains --
and a value domain is suppressed entirely once it looks high-cardinality or
free-text, which is where personal data lives.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

from ..models import PayloadFieldRecord, Unresolved

# A value domain is only useful when it is a small closed set (a status or type
# vocabulary). Beyond this it is data, not metadata -- and increasingly likely
# to be personal data.
_MAX_DOMAIN = 25
_MAX_VALUE_LEN = 40


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else "?"


def _leaf_paths(root: ET.Element) -> list[tuple[str, str]]:
    """(path, text) for every leaf element, path relative to the root."""
    out: list[tuple[str, str]] = []

    def walk(el: ET.Element, prefix: str) -> None:
        children = [c for c in el if isinstance(c.tag, str)]
        if not children:
            out.append((prefix, (el.text or "").strip()))
            return
        for c in children:
            walk(c, f"{prefix}/{_local(c.tag)}" if prefix else _local(c.tag))

    for c in root:
        if isinstance(c.tag, str):
            walk(c, _local(c.tag))
    if not out:
        out.append((_local(root.tag), (root.text or "").strip()))
    return out


def census_directory(
    directory: Path,
    repo_root: Path,
    source_id: str | None = None,
    max_messages: int = 200,
    patterns: tuple[str, ...] = ("*.xml",),
) -> tuple[list[PayloadFieldRecord], list[Unresolved]]:
    """Census one directory of sample messages.

    ``max_messages`` bounds the read: on this data 40-75 messages per interface
    was enough to settle both the structure and the value domains, so reading
    thousands buys nothing and multiplies the personal-data exposure.
    """
    sid = source_id or directory.relative_to(repo_root).as_posix()
    files: list[Path] = []
    for pat in patterns:
        files.extend(sorted(directory.glob(pat)))
    files = files[:max_messages]

    if not files:
        return [], [Unresolved("empty_payload_source", sid,
                               "no sample messages matched", sid)]

    per_root_seen: Counter[str] = Counter()
    seen: dict[tuple[str, str], int] = defaultdict(int)
    populated: dict[tuple[str, str], int] = defaultdict(int)
    domains: dict[tuple[str, str], set[str]] = defaultdict(set)
    domain_blown: set[tuple[str, str]] = set()
    unresolved: list[Unresolved] = []
    example: dict[str, str] = {}

    for path in files:
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as exc:
            unresolved.append(Unresolved(
                "unparsed_payload",
                path.relative_to(repo_root).as_posix(),
                str(exc), sid,
            ))
            continue
        rname = _local(root.tag)
        per_root_seen[rname] += 1
        example.setdefault(rname, path.relative_to(repo_root).as_posix())
        for fpath, text in _leaf_paths(root):
            key = (rname, fpath)
            seen[key] += 1
            if text:
                populated[key] += 1
                if key not in domain_blown:
                    if len(text) > _MAX_VALUE_LEN:
                        domain_blown.add(key)
                    else:
                        domains[key].add(text)
                        if len(domains[key]) > _MAX_DOMAIN:
                            domain_blown.add(key)

    records: list[PayloadFieldRecord] = []
    for (rname, fpath), n_seen in seen.items():
        blown = (rname, fpath) in domain_blown
        vals = () if blown else tuple(
            sorted(domains[(rname, fpath)], key=lambda s: (s.casefold(), s))
        )
        records.append(PayloadFieldRecord(
            source_id=sid,
            message_root=rname,
            field_path=fpath,
            messages_seen=per_root_seen[rname],
            messages_populated=populated[(rname, fpath)],
            value_domain=vals,
            value_domain_truncated=blown,
            evidence_file=example.get(rname, ""),
        ))
    return records, unresolved


def discover_payload_dirs(
    repo_root: Path, min_files: int = 3, patterns: tuple[str, ...] = ("*.xml",)
) -> list[Path]:
    """Directories that look like a sample-message population.

    Discovered, not configured: a fixed directory list would miss every
    landscape shaped differently from the one it was written against. Any
    directory holding at least ``min_files`` matching messages qualifies.
    """
    hits: list[Path] = []
    for directory in {p.parent for pat in patterns for p in repo_root.rglob(pat)}:
        rel = directory.relative_to(repo_root).as_posix()
        if any(part.startswith(".") for part in rel.split("/")):
            continue
        # Skip Talend project internals: those are designs, not messages.
        if {"process", "routes", "joblets", "routelets", "metadata", "context",
            "code", "sqlPatterns", "TDQ_Libraries"} & set(directory.parts):
            continue
        count = sum(1 for pat in patterns for _ in directory.glob(pat))
        if count >= min_files:
            hits.append(directory)
    return sorted(hits)
