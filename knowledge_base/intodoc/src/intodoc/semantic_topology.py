"""Evidence-backed semantic proposals for readable topology views.

This module deliberately sits after deterministic scanning and landscape
construction. It does not rewrite scanner facts or claim organization truth.
It recognizes explicit directional language already authored in Talend
artifacts (for example ``Source2Target`` and ``Send ... to Target``), resolves
names only against vocabulary found in the same scan, and emits proposed
systems/exchanges with exact provenance.
"""

from __future__ import annotations

import hashlib
import html
import re
from collections import Counter, defaultdict

from intodoc.models import (
    IntegrationSolution,
    Landscape,
    ParsedJob,
    ScanResult,
    SemanticExchange,
    SemanticSystem,
    SemanticTopology,
    SemanticTopologyEvidence,
)


_DIRECTION_DELIMITER_RE = re.compile(
    # `_to_` is intentionally case-insensitive. CamelCase `To` is not:
    # treating lowercase "to" as a delimiter would split words such as
    # "Monitoring" into the fabricated systems "Moni" and "ring".
    r"(?i:_to_)|(?<=[A-Za-z])2(?=[A-Za-z])|(?<=[a-z])To(?=[A-Z])",
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_IDENTITY_RE = re.compile(r"[^a-z0-9]+")
_TEST_OR_COPY_RE = re.compile(
    r"(?:^|[_-])(?:copy(?:[_-]?of)?|test|mock)(?=$|[_-]|[A-Z])",
    re.IGNORECASE,
)
_OUTBOUND_LABEL_RE = re.compile(
    r"\b(?:send|write|push|post|publish|update)\b\s+(.*?)\s+\bto\s+([A-Za-z][A-Za-z0-9_-]*)",
    re.IGNORECASE,
)
_INBOUND_LABEL_RE = re.compile(
    r"\b(?:incoming|receive|read|get)\b\s+(.*?)\s+\bfrom\s+([A-Za-z][A-Za-z0-9_-]*)",
    re.IGNORECASE,
)

_PAYLOAD_TECHNICAL_TOKENS = frozenset({
    "bg",
    "background",
    "context",
    "copy",
    "job",
    "main",
    "process",
    "route",
    "sync",
    "synchronization",
    "test",
})
_PAYLOAD_ACTION_TOKENS = frozenset({
    "create",
    "fetch",
    "get",
    "import",
    "push",
    "read",
    "receive",
    "send",
    "update",
    "write",
})
_GENERIC_IDENTITY_KEYS = frozenset({
    "api",
    "csv",
    "common",
    "context",
    "data",
    "database",
    "db",
    "email",
    "esb",
    "file",
    "interface",
    "json",
    "job",
    "log",
    "logging",
    "monitoring",
    "notification",
    "references",
    "route",
    "service",
    "talend",
    "test",
    "workorder",
    "xml",
})


def _identity_key(value: str) -> str:
    return _NON_IDENTITY_RE.sub("", value.casefold())


def _stable_id(prefix: str, value: str) -> str:
    key = _identity_key(value)
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-") or "unknown"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}:{slug[:40]}:{digest}"


def _clean_label(value: str) -> str:
    cleaned = html.unescape(_HTML_TAG_RE.sub(" ", value or ""))
    cleaned = cleaned.replace("__UNIQUE_NAME__", " ")
    return re.sub(r"\s+", " ", cleaned).strip()


def _words(value: str) -> list[str]:
    spaced = _CAMEL_BOUNDARY_RE.sub(" ", value.replace("_", " ").replace("-", " "))
    return [word for word in re.split(r"\s+", spaced) if word]


def _payload_label(value: str) -> str:
    words = _words(value)
    while words and words[0].casefold() in _PAYLOAD_ACTION_TOKENS:
        words.pop(0)
    words = [
        word
        for word in words
        if word.casefold() not in _PAYLOAD_TECHNICAL_TOKENS
        and not word.isdigit()
    ]
    if not words:
        return ""
    return " ".join(word if word.isupper() else word.capitalize() for word in words)


def _raw_directional_parts(value: str) -> tuple[str, str] | None:
    """Extract conservative raw endpoint candidates for vocabulary seeding."""
    for match in _DIRECTION_DELIMITER_RE.finditer(value):
        left = value[:match.start()].rstrip("_-")
        right = value[match.end():].lstrip("_-")
        if not left or not right:
            continue
        left = re.split(r"[_-]", left)[-1]
        right = re.split(r"[_-]", right)[0]
        if len(left) > 2 and left[0] in {"r", "R"} and left[1].isupper():
            left = left[1:]
        if (
            2 <= len(_identity_key(left)) <= 40
            and 2 <= len(_identity_key(right)) <= 40
        ):
            return left, right
    return None


class _AliasRegistry:
    def __init__(self) -> None:
        self._labels: dict[str, Counter[str]] = defaultdict(Counter)

    def add(self, label: str) -> None:
        clean = _clean_label(label)
        key = _identity_key(clean)
        if (
            not clean
            or len(key) < 2
            or len(key) > 64
            or key in _GENERIC_IDENTITY_KEYS
        ):
            return
        self._labels[key][clean] += 1

    def display(self, key: str) -> str:
        labels = self._labels[key]
        return sorted(
            labels,
            key=lambda label: (-labels[label], len(label), label.casefold()),
        )[0]

    def aliases_for(self, key: str) -> list[str]:
        return sorted(self._labels[key], key=str.casefold)

    def keys_longest_first(self) -> list[str]:
        return sorted(self._labels, key=lambda key: (-len(key), key))

    def resolve_prefix(self, value: str) -> tuple[str, str] | None:
        normalized = _identity_key(value)
        for key in self.keys_longest_first():
            if normalized.startswith(key):
                return key, normalized[len(key):]
        return None

    def resolve_suffix(self, value: str) -> tuple[str, str] | None:
        normalized = _identity_key(value)
        for key in self.keys_longest_first():
            if normalized.endswith(key):
                return key, normalized[:-len(key)]
        return None

    def resolve_mention(self, value: str) -> str | None:
        normalized = _identity_key(value)
        for key in self.keys_longest_first():
            if normalized.startswith(key):
                return key
        return None


def _build_alias_registry(scan_result: ScanResult, landscape: Landscape) -> _AliasRegistry:
    aliases = _AliasRegistry()
    for application in landscape.applications:
        aliases.add(application.label)
    for system in landscape.systems:
        aliases.add(system.name)

    context_names = [
        str(metadata.get("name") or context_key)
        for context_key, metadata in scan_result.manifest.context_metadata.items()
        if isinstance(metadata, dict)
    ]
    directional_names = context_names + [
        job.name
        for job in scan_result.jobs
        if not _TEST_OR_COPY_RE.search(job.name)
    ]
    # Context and artifact names are especially useful because Talend authors
    # commonly use Source2Target_Payload naming for an integration family.
    for directional_name in directional_names:
        parts = _raw_directional_parts(directional_name)
        if parts is not None:
            for candidate in parts:
                # Prefer an already evidenced base identity over a
                # context/job-family suffix. For example, when AssetCheck is
                # already known, `Vise2AssetCheckBgProcess_Context` is
                # evidence for VISE -> AssetCheck, not proof of a separate
                # organization system called AssetCheckBgProcess.
                if aliases.resolve_prefix(candidate) is None:
                    aliases.add(candidate)
    return aliases


def _directional_identity(
    value: str,
    aliases: _AliasRegistry,
) -> tuple[str, str, str] | None:
    for match in _DIRECTION_DELIMITER_RE.finditer(value):
        left = value[:match.start()]
        right = value[match.end():]
        source = aliases.resolve_suffix(left)
        target = aliases.resolve_prefix(right)
        if source is None or target is None or source[0] == target[0]:
            continue

        target_display = aliases.display(target[0])
        target_position = _identity_key(right).find(target[0])
        payload_start = target_position + len(target[0])
        # Slice the normalized suffix only for classification; the readable
        # payload comes from the original right-hand identifier after removing
        # the matched display alias when possible.
        payload_raw = right
        display_match = re.match(re.escape(target_display), right, re.IGNORECASE)
        if display_match is not None:
            payload_raw = right[display_match.end():]
        elif payload_start < len(_identity_key(right)):
            payload_raw = target[1]
        return source[0], target[0], _payload_label(payload_raw)
    return None


def _component_mechanisms(job: ParsedJob) -> set[str]:
    mechanisms: set[str] = set()
    for component in job.components:
        component_type = component.component_type.casefold()
        if "jms" in component_type or component_type.startswith("cmq"):
            mechanisms.add("jms")
        elif "rest" in component_type or "httprequest" in component_type:
            mechanisms.add("rest")
        elif "soap" in component_type:
            mechanisms.add("soap")
        elif "file" in component_type or "ftp" in component_type:
            mechanisms.add("file")
        elif any(token in component_type for token in ("mssql", "oracle", "postgresql", "dbinput", "dboutput")):
            mechanisms.add("db")
    return mechanisms


def _component_label(component) -> str:
    for param in component.params:
        if param.name == "LABEL":
            return _clean_label(param.value)
    return ""


def _solution_for_root(landscape: Landscape) -> dict[str, IntegrationSolution]:
    by_root: dict[str, IntegrationSolution] = {}
    for application in landscape.applications:
        solution = IntegrationSolution(
            id=application.id,
            name=application.label or application.source_root,
            platform="talend",
            role=application.role,
            source_roots=application.source_roots or [application.source_root],
            artifact_count=application.artifact_count,
        )
        for source_root in solution.source_roots:
            by_root[source_root] = solution
    return by_root


def build_semantic_topology(
    scan_result: ScanResult,
    landscape: Landscape,
) -> SemanticTopology:
    """Build conservative semantic proposals from explicit scan vocabulary."""
    aliases = _build_alias_registry(scan_result, landscape)
    solution_by_root = _solution_for_root(landscape)
    solutions = {
        solution.id: solution
        for solution in solution_by_root.values()
    }

    system_kind_by_key: dict[str, str] = {}
    for technical_system in landscape.systems:
        key = _identity_key(technical_system.name)
        if key and technical_system.kind != "unknown":
            system_kind_by_key.setdefault(key, technical_system.kind)

    exchange_groups: dict[tuple[str, str], dict] = {}
    unresolved: list[dict] = []

    def add_exchange(
        *,
        source_key: str,
        target_key: str,
        payload: str,
        job: ParsedJob,
        signal_type: str,
        signal_label: str,
        component_name: str = "",
    ) -> None:
        solution = solution_by_root.get(job.source_root)
        if solution is None or solution.role != "execution":
            return
        key = (source_key, target_key)
        group = exchange_groups.setdefault(key, {
            "payloads": set(),
            "mechanisms": set(),
            "solutions": set(),
            "artifacts": set(),
            "evidence": {},
        })
        if payload:
            group["payloads"].add(payload)
        group["mechanisms"].update(_component_mechanisms(job))
        group["solutions"].add(solution.id)
        artifact_id = job.job_id or job.name
        group["artifacts"].add(artifact_id)
        evidence = SemanticTopologyEvidence(
            signal_type=signal_type,
            label=signal_label,
            artifact_id=artifact_id,
            qualified_artifact_id=(
                artifact_id
                if artifact_id.startswith(f"{job.source_root}::")
                else f"{job.source_root}::{artifact_id}"
            ),
            source_root=job.source_root,
            component_name=component_name,
        )
        group["evidence"].setdefault(evidence.model_dump_json(), evidence)

    for job in scan_result.jobs:
        artifact_name = job.name or job.job_id
        if _TEST_OR_COPY_RE.search(artifact_name):
            continue
        directional = _directional_identity(artifact_name, aliases)
        if directional is not None:
            add_exchange(
                source_key=directional[0],
                target_key=directional[1],
                payload=directional[2],
                job=job,
                signal_type="directional_artifact_name",
                signal_label=artifact_name,
            )

        solution = solution_by_root.get(job.source_root)
        solution_key = (
            aliases.resolve_mention(solution.name)
            if solution is not None
            else None
        )
        for component in job.components:
            label = _component_label(component)
            if not label or solution_key is None:
                continue
            outbound = _OUTBOUND_LABEL_RE.search(label)
            inbound = _INBOUND_LABEL_RE.search(label)
            if outbound is not None:
                target_key = aliases.resolve_mention(outbound.group(2))
                if target_key is not None and target_key != solution_key:
                    add_exchange(
                        source_key=solution_key,
                        target_key=target_key,
                        payload=_payload_label(outbound.group(1)),
                        job=job,
                        signal_type="component_label_direction",
                        signal_label=label,
                        component_name=component.unique_name,
                    )
            elif inbound is not None:
                source_key = aliases.resolve_mention(inbound.group(2))
                if source_key is not None and source_key != solution_key:
                    add_exchange(
                        source_key=source_key,
                        target_key=solution_key,
                        payload=_payload_label(inbound.group(1)),
                        job=job,
                        signal_type="component_label_direction",
                        signal_label=label,
                        component_name=component.unique_name,
                    )

    systems: dict[str, SemanticSystem] = {}
    exchanges: list[SemanticExchange] = []
    for (source_key, target_key), group in sorted(exchange_groups.items()):
        source_name = aliases.display(source_key)
        target_name = aliases.display(target_key)
        for identity_key, display_name in (
            (source_key, source_name),
            (target_key, target_name),
        ):
            system_id = _stable_id("semantic-system", identity_key)
            evidence = [
                record
                for record in group["evidence"].values()
                if identity_key in {source_key, target_key}
            ]
            current = systems.get(system_id)
            if current is None:
                systems[system_id] = SemanticSystem(
                    id=system_id,
                    name=display_name,
                    kind=system_kind_by_key.get(identity_key, "software_system"),
                    status="proposed",
                    confidence="likely",
                    aliases=aliases.aliases_for(identity_key),
                    evidence=evidence,
                )
            else:
                seen = {record.model_dump_json() for record in current.evidence}
                current.evidence.extend(
                    record for record in evidence
                    if record.model_dump_json() not in seen
                )

        payloads = sorted(group["payloads"], key=str.casefold)
        mechanisms = sorted(group["mechanisms"])
        if mechanisms == ["jms"]:
            reliability = "buffered"
        elif mechanisms and "jms" not in mechanisms:
            reliability = "synchronous"
        else:
            reliability = "unknown"
        label = (
            ", ".join(payloads[:3])
            if payloads
            else f"{source_name} to {target_name}"
        )
        exchange_key = f"{source_key}:{target_key}"
        exchanges.append(SemanticExchange(
            id=_stable_id("exchange", exchange_key),
            source_system_id=_stable_id("semantic-system", source_key),
            target_system_id=_stable_id("semantic-system", target_key),
            label=label,
            payloads=payloads,
            mechanisms=mechanisms,
            reliability=reliability,
            status="proposed",
            confidence="likely",
            implementation_solution_ids=sorted(group["solutions"]),
            artifact_ids=sorted(group["artifacts"]),
            evidence=[
                group["evidence"][key]
                for key in sorted(group["evidence"])
            ],
        ))

    return SemanticTopology(
        systems=sorted(systems.values(), key=lambda system: system.name.casefold()),
        integration_solutions=sorted(
            solutions.values(),
            key=lambda solution: solution.name.casefold(),
        ),
        exchanges=exchanges,
        unresolved=unresolved,
        summary={
            "system_count": len(systems),
            "integration_solution_count": len(solutions),
            "exchange_count": len(exchanges),
            "proposal_count": len(systems) + len(exchanges),
        },
    )
