"""Deterministic system-landscape rollup pass.

Sibling of `intodoc.lineage`: same philosophy (pure Python, no LLM, no
runtime cost, confidence-tiered, never fabricate, never silently drop,
credential-safe), applied one layer up. Where `lineage.py` builds
column-level data lineage inside and across jobs, `landscape.py` rolls
job/route-level `ProjectManifest` facts (plus the already-built
`LineageMatrix`) up into an L1/L2 systems-and-connections graph:

1. **systems[]** -- deduplicated external-node identities (databases,
   message brokers, SaaS APIs, file endpoints, ...), derived from three
   signals of decreasing specificity:
   (a) context-group names (`ProjectManifest.context_metadata`) -- the
       richest business-name signal. A context-group core name that
       recurs (after generic-suffix stripping) across >= 2 distinct
       `source_root`s is treated as strong cross-project evidence of a
       real shared system (verified against the real 6-project scan:
       this rule alone recovers Maximo, Quintiq, RHM, ActiveMQ, Centric,
       MAS, sFTP as cross-cutting business names).
   (b) `scan_result.metadata_connections` -- canonicalized by physical
       identity (`connection_type`, `host`, `database`), NOT display
       name, since two differently-named connection objects (e.g. from
       different Talend repository folders) can point at the exact same
       physical system.
   (c) `ProjectManifest.job_external_systems` -- generic per-component
       fallback labels ("REST API", "JMS Broker", ...). Used only when
       signal (b) doesn't already cover a job's usage (Talend's
       repository "Metadata Connection" objects are DB-only, so this
       fallback tier effectively covers non-DB external systems: ESB
       channels, REST/SOAP endpoints, SFTP, ...). Scoped per source_root
       -- a generic label carries zero cross-project identity evidence,
       so merging it across projects would fabricate an identity match
       this pass has no basis for.
   When a metadata-connection identity group and a context-group core
   match (same normalized core), they are merged into one system and the
   context-group's display name wins (richer business-name signal).
   Everything else stays separate -- ambiguous cases are never guessed
   into a merge.

2. **applications[]** -- one per `source_root` (from
   `ProjectManifest.source_roots`).

3. **edges[]** -- rolled-up dependencies between applications/systems:
   - **control_flow**: `ProjectManifest.artifact_relations`
     (job_calls_job / route_invokes_job) rolled up to the owning
     application. `mechanism`/`reliability` come from any matching
     `artifact_channels` record on the same artifact (jms => buffered;
     direct/direct-vm => synchronous); tRunJob/cTalendJob invocations
     with no channel evidence default to a direct, synchronous,
     in-process call (Talend's own execution semantics for both).
   - **data**: (a) `artifact_resources` read/write records rolled up to
     the resolved system, when the referenced connection resolves to a
     known system; (b) cross-job `LineageEntry` edges from the
     `LineageMatrix` (`confidence=inferred`, `source_job_name` set)
     rolled up to an application-to-application data dependency. These
     answer "if X writes bad data, who downstream consumes it" --
     distinct from control_flow's "what won't run".
   Every edge carries `provenance` (backing job/artifact/lineage-entry
   ids) so an L1 edge can be drilled down to L3/L4. An edge is never
   emitted without provenance.

4. **diagnostics[]** -- computed findings. At minimum: write-only /
   unread channels (an `artifact_channels` group with output-direction
   writers and zero input-direction readers across the whole scan --
   flagged higher severity when the channel name looks like a
   dead-letter queue, informational otherwise, since many write-only
   channels are legitimate terminal branches); unresolved cross-system
   references (`artifact_relations` entries that never resolved to a
   target job).

Anything that cannot be rolled up cleanly (an edge endpoint whose owning
application can't be found, a resource reference to an unknown
connection, ...) is recorded in `Landscape.unmapped` -- never silently
dropped, matching `lineage.py`'s `unmapped_columns`.

Output is written as `scan/landscape.json`, gated on the same
credential-filter leaf-string verification as `lineage.json` (D6,
`redaction_verifier.verify_landscape`).
"""

from __future__ import annotations

import re
import hashlib
from collections import Counter, defaultdict
from pathlib import Path

from intodoc.models import (
    Landscape,
    LandscapeApplication,
    LandscapeChannel,
    LandscapeDiagnostic,
    LandscapeEdge,
    LandscapeInterface,
    LandscapeProvenance,
    LandscapeSystem,
    LandscapeSystemEvidence,
    LineageMatrix,
    MetadataConnection,
    RedactionDescriptor,
    ScanResult,
)
from intodoc.scanner.credential_filter import CredentialFilter

# ── Shared vocabulary ─────────────────────────────────────────────────

_CHANNEL_TYPE_TO_MECHANISM = {
    "jms": "jms",
    "direct": "direct",
    "direct-vm": "direct-vm",
    "lobster_comm": "unknown",
}

# General reliability-from-mechanism mapping (used for BOTH control_flow
# and data edges -- one shared function, not a per-edge-type special case).
_RELIABILITY_BY_MECHANISM = {
    "jms": "buffered",
    "direct": "synchronous",
    "direct-vm": "synchronous",
}

_CONFIDENCE_RANK = {"certain": 3, "likely": 2, "inferred": 1, "unknown": 0}

# These labels describe a transport/component category, not the identity of
# the peer system behind it. They remain available as L2 interface/channel
# evidence, but promoting one scoped copy per source root creates meaningless
# L1 nodes such as "REST API" and "File endpoint".
_NON_IDENTITY_FALLBACK_LABELS = frozenset({
    "rest api",
    "http api",
    "soap api",
    "jms broker",
    "file endpoint",
    "messaging",
    "esb route",
    "ftp",
    "sftp",
    "email",
    "jdbc",
})

# Direction values recorded by the Talend parser (`_route_component_direction`,
# `_component_resource_direction`) are "input"/"output"/"both"/"unknown",
# component-centric. Landscape edges are always oriented application-first
# (source=application), so these translate to "in" (app reads in from the
# system) / "out" (app writes out to the system).
_DIRECTION_MAP = {"input": "in", "output": "out", "both": "both", "unknown": "unknown"}

_EXACT_CHANNEL_TYPES = frozenset({"jms", "direct", "direct-vm", "lobster_comm"})
_PLACEHOLDER_CHANNEL_VALUES = frozenset({"", "configured channel", "configured endpoint"})


def _qualified_artifact_id(source_root: str, artifact_id: str) -> str:
    """Return a stable root-qualified artifact identity without doubling an
    already-qualified composite Talend id."""
    if source_root and artifact_id.startswith(f"{source_root}::"):
        return artifact_id
    return f"{source_root}::{artifact_id}" if source_root else artifact_id


def _provenance(
    *,
    artifact_id: str,
    source_root: str,
    component_name: str = "",
    fact_type: str,
) -> LandscapeProvenance:
    return LandscapeProvenance(
        artifact_id=artifact_id,
        qualified_artifact_id=_qualified_artifact_id(source_root, artifact_id),
        source_root=source_root,
        component_name=component_name,
        fact_type=fact_type,
    )


def _reliability_for_mechanism(mechanism: str) -> str:
    """Shared reliability derivation: jms => buffered; direct/direct-vm =>
    synchronous; anything else => unknown. Never guessed beyond this."""
    return _RELIABILITY_BY_MECHANISM.get(mechanism, "unknown")


def _safe_descriptor(value: object) -> RedactionDescriptor | None:
    """Validate scanner-provided safe URI facts without reconstructing a URI."""
    if not isinstance(value, dict):
        return None
    try:
        return RedactionDescriptor.model_validate(value)
    except (TypeError, ValueError):
        return None


# ── System-kind classification ────────────────────────────────────────

_KIND_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("database", (
        "sql server", "mssql", "mysql", "postgres", "oracle", "sqlite", "db2",
        "snowflake", "redshift", "mongodb", "cassandra", "hbase", "hive",
        "impala", "neo4j", "dynamodb", "jdbc",
    )),
    ("messaging_broker", (
        "jms", "activemq", "rabbitmq", "kafka", "service bus", "messaging",
        "mq broker",
    )),
    ("rest_api", ("rest api", "http api")),
    ("soap_api", ("soap api",)),
    ("file_endpoint", (
        "file endpoint", "ftp", "sftp", "amazon s3", "azure storage",
        "google cloud storage", "file system",
    )),
    ("external_saas", (
        "salesforce", "marketo", "netsuite", "sap", "elasticsearch",
    )),
    ("internal", ("esb route",)),
)


def _classify_kind(label: str) -> str:
    """Map a connection_type / generic external-system label to a
    LandscapeSystem.kind. First keyword match wins; never guessed beyond
    the known vocabulary -- unmatched labels are "unknown", not fabricated
    into a more specific kind."""
    lowered = label.lower()
    for kind, keywords in _KIND_KEYWORDS:
        if any(kw in lowered for kw in keywords):
            return kind
    return "unknown"


# ── Name normalization (for cross-signal system-identity merging) ─────

# Generic Talend naming suffixes stripped iteratively (longest/most-specific
# first) to recover a business-name "core" from a repository object name,
# e.g. "MaximoDBConnection" -> "Maximo", "LogDBConnection" -> "Log",
# "AssetCheckInterface_Context" -> "AssetCheck". Verified against the real
# 6-project topology scan (see module docstring).
_GENERIC_NAME_SUFFIXES: tuple[str, ...] = (
    "dbconnection", "connection", "_context", "context", "interface", "db",
)


def _strip_composite_qualifier(name: str) -> str:
    """Strip the `{source_root}::{name}` prefix `_qualify_composite_connections`
    (talend/parser.py) adds when two composite sub-projects export a
    same-named connection with different fingerprints. That qualifier is a
    filesystem-path-shaped disambiguator, never a business name -- stripping
    it for display purposes does NOT imply the two connections are the same
    system (their identity key can still differ; see `_connection_identity_key`),
    it only makes the label readable instead of a raw repository path."""
    return name.rsplit("::", 1)[-1] if "::" in name else name


def _strip_generic_suffix(name: str) -> str:
    core = _strip_composite_qualifier(name)
    changed = True
    while changed:
        changed = False
        lowered = core.lower()
        for suffix in _GENERIC_NAME_SUFFIXES:
            if lowered.endswith(suffix) and len(core) > len(suffix):
                core = core[: -len(suffix)]
                changed = True
                break
    return core.strip("_") or name


def _normalized_core(name: str) -> str:
    return _strip_generic_suffix(name).strip().lower()


def _canonical_business_name(names: list[str], core: str) -> str:
    """Pick a deterministic, human-readable display name from a group of
    raw names sharing the same normalized `core`.

    Prefers a name that already equals the core with no suffix stripped
    (the "cleanest" form, e.g. "Maximo" over "maximoContext" or
    "MaximoDBConnection") over a plain most-common vote, which would tie-
    break arbitrarily on dict/insertion order.
    """
    exact = sorted(n for n in names if n.lower() == core)
    if exact:
        return exact[0]
    counts = Counter(names)
    max_count = max(counts.values())
    candidates = sorted(n for n, c in counts.items() if c == max_count)
    return min(candidates, key=len)


_DEAD_LETTER_RE = re.compile(r"dlq|dead.?letter|error.?queue", re.IGNORECASE)

_NON_SLUG_RE = re.compile(r"[^A-Za-z0-9]+")


def _slug(text: str) -> str:
    """Sanitize free text into a stable id fragment (alnum + underscore
    only). Never embeds punctuation from a redaction placeholder or any
    other raw text verbatim into an id."""
    return _NON_SLUG_RE.sub("_", text).strip("_").lower() or "unknown"


# ── System identity building ───────────────────────────────────────────


def _has_safe_physical_discriminator(value: str) -> bool:
    """Whether a connection discriminator survived filtering concretely.

    A redaction marker and a context/dynamic expression are useful evidence
    that configuration exists, but not evidence that two projects use the
    same physical endpoint. Treating them as equality keys would turn
    redaction into an unsafe topology merge.
    """
    normalized = value.strip().lower()
    return bool(normalized) and not (
        normalized.startswith("[")
        or "redacted" in normalized
        or "configured" in normalized
        or normalized.startswith("context.")
        or normalized.startswith("globalmap")
    )


def _connection_identity_key(conn: MetadataConnection) -> tuple[str, ...]:
    """Return only evidence-backed connection identity.

    Physical type/host/database identity is allowed solely when *both* host
    and database are concrete retained values. If filtering or dynamic context
    expressions erased either discriminator, keep the repository connection
    scoped to its source root and exact repository identity instead. A later
    recurring-context rule can still make an explicit, stricter merge.
    """
    if (
        _has_safe_physical_discriminator(conn.host)
        and _has_safe_physical_discriminator(conn.database)
    ):
        return ("physical", conn.connection_type, conn.host, conn.database)
    source_root = str(conn.additional_params.get("source_root", ""))
    repository_identity = conn.xmi_id or conn.name
    return ("repository_scoped", source_root, repository_identity, conn.name)


def _build_metadata_connection_systems(
    scan_result: ScanResult,
    cred_filter: CredentialFilter,
) -> tuple[list[LandscapeSystem], dict[str, str], dict[tuple[str, ...], str]]:
    """Build one LandscapeSystem per distinct (type, host, database) group.

    Returns (systems, name_to_system_id, identity_key_to_system_id) so
    later phases can resolve an `artifact_resources` connection-name
    reference, or merge in a matching context-group core, respectively.

    `cred_filter` is re-run on every raw connection name as soon as it is
    read here (defense in depth, same as `lineage._redact_transformations`)
    -- names feed directly into system ids/labels, so filtering must happen
    before any id/core derivation, not as an afterthought on the final
    string fields only.
    """
    groups: dict[tuple[str, ...], list[MetadataConnection]] = defaultdict(list)
    for conn in scan_result.metadata_connections:
        groups[_connection_identity_key(conn)].append(conn)

    systems: list[LandscapeSystem] = []
    name_to_system_id: dict[str, str] = {}
    identity_to_system_id: dict[tuple[str, ...], str] = {}

    for index, (key, conns) in enumerate(sorted(groups.items(), key=lambda kv: kv[0])):
        connection_type = conns[0].connection_type
        raw_names = [c.name for c in conns]
        safe_names = [cred_filter.filter_text(n) if n else n for n in raw_names]
        core_counts = Counter(_strip_generic_suffix(n) for n in safe_names)
        # Prefer the most common suffix-stripped core; ties broken
        # alphabetically for determinism.
        best_core = min(
            core_counts.items(),
            key=lambda kv: (-kv[1], kv[0]),
        )[0]
        system_id = f"system:connection:{index}:{_slug(best_core)}"
        source_roots = sorted({
            c.additional_params.get("source_root", "")
            for c in conns
            if c.additional_params.get("source_root")
        })
        evidence = [
            LandscapeSystemEvidence(
                signal_type="metadata_connection",
                label=safe_name,
                detail={
                    "connection_type": c.connection_type,
                    "xmi_id": c.xmi_id,
                    "source_root": c.additional_params.get("source_root", ""),
                },
            )
            for c, safe_name in zip(conns, safe_names)
        ]
        system = LandscapeSystem(
            id=system_id,
            name=best_core,
            kind=_classify_kind(connection_type),
            evidence=evidence,
            source_roots=source_roots,
        )
        systems.append(system)
        # Keyed by the RAW connection name -- that's what
        # `artifact_resources[].resource` (== `metadata_connection_ref`)
        # actually references. Never persisted itself (internal lookup
        # only); every field that lands in `Landscape` output goes through
        # `safe_names`/`evidence` above.
        for n in raw_names:
            name_to_system_id[n] = system_id
        identity_to_system_id[key] = system_id

    return systems, name_to_system_id, identity_to_system_id


def _build_context_group_systems(
    scan_result: ScanResult,
    connection_systems: list[LandscapeSystem],
    application_labels: set[str],
    cred_filter: CredentialFilter,
) -> tuple[list[LandscapeSystem], dict[str, str]]:
    """Promote cross-project context-group core names to systems, merging
    into an existing metadata-connection system when the core matches.

    A context-group name whose core matches an application's own label is
    excluded -- that names the owning application itself (project config),
    not an external system, and creating one would fabricate a spurious
    self-referential node.

    `cred_filter` is re-run on every raw context-group name as soon as it
    is read (defense in depth, same rationale as
    `_build_metadata_connection_systems`).
    """
    context_metadata = scan_result.manifest.context_metadata
    by_core: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    for context_key, entry in context_metadata.items():
        raw_name = entry.get("name", context_key)
        name = cred_filter.filter_text(raw_name) if raw_name else raw_name
        core = _normalized_core(name)
        by_core[core].append((context_key, {**entry, "name": name}))

    connection_core_index: dict[str, LandscapeSystem] = {
        _normalized_core(sys_.name): sys_ for sys_ in connection_systems
    }
    app_label_cores = {_normalized_core(label) for label in application_labels if label}

    new_systems: list[LandscapeSystem] = []
    context_key_to_system_id: dict[str, str] = {}
    for core, entries in sorted(by_core.items()):
        if not core or core in app_label_cores:
            continue
        distinct_roots = sorted({e.get("source_root", "") for _, e in entries if e.get("source_root")})
        if len(distinct_roots) < 2:
            # Single-project context group: no cross-project evidence this
            # names a shared external system rather than a project-local
            # flow config. Not promoted -- never fabricated as a system.
            continue

        names = [e.get("name", key) for key, e in entries]
        canonical_name = _canonical_business_name(names, core)
        evidence = [
            LandscapeSystemEvidence(
                signal_type="context_group",
                label=e.get("name", key),
                detail={
                    "context_id": e.get("context_id", ""),
                    "source_root": e.get("source_root", ""),
                    "environments": e.get("environments", []),
                },
            )
            for key, e in entries
        ]

        existing = connection_core_index.get(core)
        if existing is not None:
            # Richer business-name signal wins over the connection's own
            # display name; merge evidence and source_roots in place.
            existing.name = canonical_name
            existing.evidence.extend(evidence)
            existing.source_roots = sorted(set(existing.source_roots) | set(distinct_roots))
            for context_key, _entry in entries:
                context_key_to_system_id[context_key] = existing.id
            continue

        system_id = f"system:context:{_slug(core)}"
        new_systems.append(LandscapeSystem(
            id=system_id,
            name=canonical_name,
            kind="unknown",
            evidence=evidence,
            source_roots=distinct_roots,
        ))
        for context_key, _entry in entries:
            context_key_to_system_id[context_key] = system_id

    return new_systems, context_key_to_system_id


def _build_fallback_systems(
    scan_result: ScanResult,
) -> tuple[list[LandscapeSystem], dict[tuple[str, str], str]]:
    """Generic per-(source_root, label) fallback systems from
    `job_external_systems`, for labels not already covered by a database
    identity (Talend's repository connections are DB-only, so a
    database-classified label is always represented more richly by
    `_build_metadata_connection_systems` already). Scoped per source_root
    because a generic label carries no cross-project identity evidence --
    merging it across projects would fabricate a match."""
    manifest = scan_result.manifest
    job_source_roots = manifest.job_source_roots
    label_root_jobs: dict[tuple[str, str], list[str]] = defaultdict(list)

    for job_key, labels in manifest.job_external_systems.items():
        source_root = job_source_roots.get(job_key, "")
        for label in labels:
            if (
                _classify_kind(label) == "database"
                or label.strip().lower() in _NON_IDENTITY_FALLBACK_LABELS
            ):
                continue
            label_root_jobs[(label, source_root)].append(job_key)

    systems: list[LandscapeSystem] = []
    key_to_system_id: dict[tuple[str, str], str] = {}
    for (label, source_root), jobs in sorted(label_root_jobs.items()):
        system_id = f"system:fallback:{_slug(label)}:{_slug(source_root) if source_root else 'none'}"
        systems.append(LandscapeSystem(
            id=system_id,
            name=label,
            kind=_classify_kind(label),
            evidence=[LandscapeSystemEvidence(
                signal_type="job_external_system",
                label=label,
                detail={"source_root": source_root, "job_count": len(jobs)},
            )],
            source_roots=[source_root] if source_root else [],
        ))
        key_to_system_id[(label, source_root)] = system_id

    return systems, key_to_system_id


def _build_resource_systems(
    scan_result: ScanResult,
    name_to_system_id: dict[str, str],
    fallback_key_to_id: dict[tuple[str, str], str],
) -> tuple[list[LandscapeSystem], dict[tuple[str, str, str], str]]:
    """Build scoped system identities for non-repository resources.

    Workato connectors, Lobster transport channels, and Generic import
    signals are topology evidence even though they are not Talend repository
    connections. Reuse a stronger metadata/fallback identity where one
    exists; otherwise scope the resource to its source root so matching labels
    in separate projects are never merged by guesswork.
    """
    systems: list[LandscapeSystem] = []
    key_to_system_id: dict[tuple[str, str, str], str] = {}
    job_source_roots = scan_result.manifest.job_source_roots

    for resource in scan_result.manifest.artifact_resources:
        artifact = resource.get("artifact", "")
        source_root = job_source_roots.get(artifact, "")
        resource_type = resource.get("resource_type", "unknown") or "unknown"
        name = resource.get("resource", "")
        if not name:
            continue
        # A Talend repository reference is meaningful only when it maps to
        # parsed repository metadata. Do not turn a dangling reference into a
        # synthetic external system; retain it in `unmapped` downstream.
        if resource_type == "repository_connection":
            continue
        key = (source_root, resource_type, name)
        if key in key_to_system_id:
            continue

        existing = name_to_system_id.get(name)
        if existing is None:
            existing = fallback_key_to_id.get((name, source_root))
        if existing is not None:
            key_to_system_id[key] = existing
            continue

        system_id = (
            f"system:resource:{_slug(resource_type)}:{_slug(name)}:"
            f"{_slug(source_root) if source_root else 'none'}"
        )
        key_to_system_id[key] = system_id
        systems.append(LandscapeSystem(
            id=system_id,
            name=name,
            kind=_classify_kind(f"{resource_type} {name}"),
            evidence=[LandscapeSystemEvidence(
                signal_type="artifact_resource",
                label=name,
                detail={
                    "resource_type": resource_type,
                    "source_root": source_root,
                    "artifact_count": 1,
                },
            )],
            source_roots=[source_root] if source_root else [],
        ))

    return systems, key_to_system_id


# ── Applications ────────────────────────────────────────────────────────


def _build_applications(
    scan_result: ScanResult,
) -> tuple[list[LandscapeApplication], dict[str, str]]:
    roots = scan_result.manifest.source_roots
    jobs_by_root: dict[str, list] = defaultdict(list)
    for job in scan_result.jobs:
        jobs_by_root[job.source_root].append(job)

    grouped_roots: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for root in roots:
        root_id = root.get("id") or root.get("path", "")
        if not root_id:
            continue
        project_id = root.get("project_id", "")
        identity = ("project", project_id) if project_id else ("root", root_id)
        grouped_roots[identity].append(root)

    applications: list[LandscapeApplication] = []
    root_to_application_id: dict[str, str] = {}
    for (identity_kind, identity_value), identity_roots in sorted(grouped_roots.items()):
        source_roots = sorted({
            root.get("path") or root.get("id", "")
            for root in identity_roots
            if root.get("path") or root.get("id")
        })
        if not source_roots:
            continue
        labels = sorted({
            root.get("label", "") for root in identity_roots if root.get("label")
        })
        descriptions = sorted({
            root.get("description", "")
            for root in identity_roots
            if root.get("description")
        })
        if identity_kind == "project":
            label_core = _slug(labels[0] if labels else "talend_project")
            digest = hashlib.sha256(identity_value.encode("utf-8")).hexdigest()[:12]
            application_id = f"talend-project:{label_core}:{digest}"
            project_id = identity_value
        else:
            application_id = identity_value
            project_id = ""

        # Identical Talend projects are frequently bundled alongside several
        # importing projects. Count their designs once by stable repository
        # path/version instead of multiplying the same seven designs by the
        # number of uploaded ZIPs that happened to contain them.
        unique_jobs: dict[tuple[str, str, str, str], object] = {}
        for source_root in source_roots:
            for job in jobs_by_root.get(source_root, []):
                key = (
                    job.artifact_kind,
                    job.source_design,
                    job.path,
                    job.version,
                )
                unique_jobs.setdefault(key, job)
        kinds_breakdown = dict(Counter(
            job.artifact_kind for job in unique_jobs.values()
        ))
        root_roles = {
            root.get("role", "execution") for root in identity_roots
        }
        role = (
            "shared_project"
            if len(source_roots) > 1
            else next(iter(root_roles), "execution")
        )
        applications.append(LandscapeApplication(
            id=application_id,
            source_root=source_roots[0],
            source_roots=source_roots,
            project_id=project_id,
            label=labels[0] if labels else "",
            description=descriptions[0] if descriptions else "",
            role=role,
            artifact_count=len(unique_jobs),
            kinds_breakdown=kinds_breakdown,
        ))
        for source_root in source_roots:
            root_to_application_id[source_root] = application_id
    return applications, root_to_application_id


# ── Edge aggregation ────────────────────────────────────────────────────


class _EdgeAccumulator:
    """Aggregates repeated edge observations into one LandscapeEdge per
    (source, target, source_kind, target_kind, dependency_type, mechanism)
    group, tracking evidence_count/provenance/direction/confidence."""

    def __init__(self) -> None:
        self._groups: dict[tuple, dict] = {}

    def add(
        self,
        *,
        source: str,
        target: str,
        source_kind: str,
        target_kind: str,
        dependency_type: str,
        mechanism: str,
        direction: str,
        confidence: str,
        provenance: LandscapeProvenance | list[LandscapeProvenance],
        evidence_count: int = 1,
    ) -> None:
        key = (source, target, source_kind, target_kind, dependency_type, mechanism)
        group = self._groups.get(key)
        if group is None:
            group = {
                "directions": set(),
                "confidence": confidence,
                "provenance": {},
                "count": 0,
            }
            self._groups[key] = group
        group["directions"].add(direction)
        if _CONFIDENCE_RANK.get(confidence, 0) < _CONFIDENCE_RANK.get(group["confidence"], 0):
            group["confidence"] = confidence
        records = provenance if isinstance(provenance, list) else [provenance]
        for record in records:
            group["provenance"].setdefault(record.model_dump_json(), record)
        group["count"] += max(evidence_count, 1)

    def edges(self) -> list[LandscapeEdge]:
        result: list[LandscapeEdge] = []
        for key, group in sorted(self._groups.items()):
            source, target, source_kind, target_kind, dependency_type, mechanism = key
            directions = group["directions"]
            direction = next(iter(directions)) if len(directions) == 1 else "both" if directions - {"unknown"} else "unknown"
            result.append(LandscapeEdge(
                source=source,
                target=target,
                source_kind=source_kind,
                target_kind=target_kind,
                dependency_type=dependency_type,
                mechanism=mechanism,
                direction=direction,
                reliability=_reliability_for_mechanism(mechanism),
                confidence=group["confidence"],
                evidence_count=group["count"],
                provenance=[group["provenance"][key] for key in sorted(group["provenance"])],
            ))
        return result


def _remap_application_edges(
    edges: list[LandscapeEdge],
    root_to_application_id: dict[str, str],
) -> list[LandscapeEdge]:
    """Collapse source-root endpoints onto stable Talend project identities.

    Edge builders deliberately remain source-root based because provenance is
    recorded at that exact physical location. This final projection changes
    only graph endpoints, keeps the original provenance, merges duplicate
    observations, and drops self-loops created solely by duplicate project
    copies in a composite bundle.
    """
    acc = _EdgeAccumulator()
    for edge in edges:
        source = (
            root_to_application_id.get(edge.source, edge.source)
            if edge.source_kind == "application"
            else edge.source
        )
        target = (
            root_to_application_id.get(edge.target, edge.target)
            if edge.target_kind == "application"
            else edge.target
        )
        if (
            source == target
            and edge.source_kind == "application"
            and edge.target_kind == "application"
        ):
            continue
        acc.add(
            source=source,
            target=target,
            source_kind=edge.source_kind,
            target_kind=edge.target_kind,
            dependency_type=edge.dependency_type,
            mechanism=edge.mechanism,
            direction=edge.direction,
            confidence=edge.confidence,
            provenance=edge.provenance,
            evidence_count=edge.evidence_count,
        )
    return acc.edges()


# ── Control-flow edges ───────────────────────────────────────────────────


def _channel_mechanism_for_relation(
    channels_by_artifact_component: dict[tuple[str, str], str],
    source_job: str,
    component_name: str,
    provenance: str,
) -> str:
    if component_name:
        found = channels_by_artifact_component.get((source_job, component_name))
        if found is not None:
            return _CHANNEL_TYPE_TO_MECHANISM.get(found, "unknown")
    if provenance in ("tRunJob", "cTalendJob"):
        # In-process Java call -- Talend's own execution semantics for
        # both a tRunJob subjob call and a route's cTalendJob invocation.
        return "direct"
    return "unknown"


def _build_control_flow_edges(
    scan_result: ScanResult,
    unmapped: list[dict],
) -> list[LandscapeEdge]:
    manifest = scan_result.manifest
    job_source_roots = manifest.job_source_roots
    channels_by_artifact_component: dict[tuple[str, str], str] = {}
    for ch in manifest.artifact_channels:
        component_name = ch.get("component_name", "")
        if component_name:
            channels_by_artifact_component[(ch.get("artifact", ""), component_name)] = ch.get("channel_type", "")

    acc = _EdgeAccumulator()
    for relation in manifest.artifact_relations:
        target = relation.get("target", "")
        if not target:
            # Unresolved / ambiguous: never fabricate an edge to nothing.
            # Surfaced separately as an "unresolved_reference" diagnostic.
            unmapped.append({
                "reason": "unresolved_artifact_relation",
                "relation": relation,
            })
            continue
        source_job = relation.get("source", "")
        source_root = job_source_roots.get(source_job)
        target_root = job_source_roots.get(target)
        if source_root is None or target_root is None:
            unmapped.append({
                "reason": "artifact_relations endpoint not found in job_source_roots",
                "relation": relation,
            })
            continue
        if source_root == target_root:
            # This is valuable L3 call evidence, but not an L1 relationship
            # between applications. The unrolled manifest retains it for
            # drill-down without cluttering the landscape with self-loops.
            continue

        mechanism = _channel_mechanism_for_relation(
            channels_by_artifact_component,
            source_job,
            relation.get("component_name", ""),
            relation.get("provenance", ""),
        )
        acc.add(
            source=source_root,
            target=target_root,
            source_kind="application",
            target_kind="application",
            dependency_type="control_flow",
            mechanism=mechanism,
            direction="out",
            confidence="certain",
            provenance=[
                _provenance(
                    artifact_id=source_job,
                    source_root=source_root,
                    component_name=relation.get("component_name", ""),
                    fact_type="artifact_relation_source",
                ),
                _provenance(
                    artifact_id=target,
                    source_root=target_root,
                    fact_type="artifact_relation_target",
                ),
            ],
        )
    return acc.edges()


# ── Data edges ────────────────────────────────────────────────────────


def _build_resource_data_edges(
    scan_result: ScanResult,
    name_to_system_id: dict[str, str],
    resource_system_ids: dict[tuple[str, str, str], str],
    unmapped: list[dict],
) -> list[LandscapeEdge]:
    manifest = scan_result.manifest
    job_source_roots = manifest.job_source_roots
    acc = _EdgeAccumulator()

    for resource in manifest.artifact_resources:
        job_key = resource.get("artifact", "")
        source_root = job_source_roots.get(job_key)
        connection_name = resource.get("resource", "")
        resource_type = resource.get("resource_type", "unknown") or "unknown"
        system_id = name_to_system_id.get(connection_name)
        if system_id is None:
            system_id = resource_system_ids.get(
                (source_root or "", resource_type, connection_name),
            )
        if source_root is None or system_id is None:
            unmapped.append({
                "reason": "artifact_resources connection reference not resolved to a system",
                "resource": resource,
            })
            continue

        direction = _DIRECTION_MAP.get(resource.get("direction", "unknown"), "unknown")
        if resource_type == "repository_connection":
            mechanism = "db"
            dependency_type = "data"
            confidence = "certain"
        elif resource_type in {"ftp", "sftp", "file", "s3", "gcs"}:
            mechanism = "file"
        elif resource_type in {"http", "rest", "soap"}:
            mechanism = resource_type
        elif "message" in resource_type or "jms" in resource_type or "mq" in resource_type:
            mechanism = "jms"
        else:
            # Workato connector actions and Generic imports do not expose a
            # reliable wire protocol in the scanned artifacts.
            mechanism = "unknown"
        if resource_type != "repository_connection":
            dependency_type = "integration"
            confidence = "likely"
        acc.add(
            source=source_root,
            target=system_id,
            source_kind="application",
            target_kind="system",
            dependency_type=dependency_type,
            mechanism=mechanism,
            direction=direction,
            confidence=confidence,
            provenance=_provenance(
                artifact_id=job_key,
                source_root=source_root,
                component_name=resource.get("component_name", ""),
                fact_type="artifact_resource",
            ),
        )
    return acc.edges()


def _build_fallback_integration_edges(
    scan_result: ScanResult,
    name_to_system_id: dict[str, str],
    fallback_key_to_id: dict[tuple[str, str], str],
    resource_system_ids: dict[tuple[str, str, str], str],
    unmapped: list[dict],
) -> list[LandscapeEdge]:
    """Connect fallback system evidence when no concrete resource supersedes it."""
    manifest = scan_result.manifest
    job_source_roots = manifest.job_source_roots
    concrete_pairs: set[tuple[str, str]] = set()
    for resource in manifest.artifact_resources:
        artifact = resource.get("artifact", "")
        source_root = job_source_roots.get(artifact)
        resource_type = resource.get("resource_type", "unknown") or "unknown"
        resource_name = resource.get("resource", "")
        system_id = name_to_system_id.get(resource_name) or resource_system_ids.get(
            (source_root or "", resource_type, resource_name),
        )
        if source_root is not None and system_id is not None:
            concrete_pairs.add((source_root, system_id))

    acc = _EdgeAccumulator()
    for artifact, labels in manifest.job_external_systems.items():
        source_root = job_source_roots.get(artifact)
        if source_root is None:
            unmapped.append({
                "reason": "job_external_system artifact not found in job_source_roots",
                "artifact": artifact,
                "labels": labels,
            })
            continue
        for label in labels:
            system_id = fallback_key_to_id.get((label, source_root))
            if system_id is None:
                # Database-labelled fallbacks are intentionally omitted by
                # system construction; a real repository resource is needed.
                continue
            if (source_root, system_id) in concrete_pairs:
                continue
            acc.add(
                source=source_root,
                target=system_id,
                source_kind="application",
                target_kind="system",
                dependency_type="integration",
                mechanism="unknown",
                direction="unknown",
                confidence="likely",
                provenance=_provenance(
                    artifact_id=artifact,
                    source_root=source_root,
                    fact_type="job_external_system",
                ),
            )
    return acc.edges()


def _build_channel_records_and_edges(
    scan_result: ScanResult,
    unmapped: list[dict],
) -> tuple[list[LandscapeChannel], list[LandscapeEdge]]:
    """Expose exact named channels and connect only observed producers/readers."""
    job_source_roots = scan_result.manifest.job_source_roots
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    channels: list[LandscapeChannel] = []

    for channel in scan_result.manifest.artifact_channels:
        channel_type = channel.get("channel_type", "")
        channel_name = channel.get("channel", "")
        artifact = channel.get("artifact", "")
        source_root = job_source_roots.get(artifact)
        if channel_type not in _EXACT_CHANNEL_TYPES:
            continue
        if source_root is None:
            unmapped.append({
                "reason": "artifact_channel artifact not found in job_source_roots",
                "channel": channel,
            })
            continue
        descriptor = _safe_descriptor(channel.get("channel_descriptor"))
        channels.append(LandscapeChannel(
            channel_type=channel_type,
            channel=channel_name,
            direction=channel.get("direction", "unknown"),
            artifact_id=artifact,
            qualified_artifact_id=_qualified_artifact_id(source_root, artifact),
            source_root=source_root,
            component_name=channel.get("component_name", ""),
            redaction_descriptor=descriptor,
        ))
        # A redacted/unnamed channel remains meaningful L2 evidence when it
        # has a structural descriptor, but must never be used to correlate
        # producer/consumer records into a fabricated peer edge.
        if channel_name not in _PLACEHOLDER_CHANNEL_VALUES:
            groups[(channel_type, channel_name)].append({**channel, "source_root": source_root})

    acc = _EdgeAccumulator()
    for (channel_type, _channel_name), records in groups.items():
        producers = [record for record in records if record.get("direction") in {"output", "both"}]
        consumers = [record for record in records if record.get("direction") in {"input", "both"}]
        for producer in producers:
            for consumer in consumers:
                producer_artifact = producer.get("artifact", "")
                consumer_artifact = consumer.get("artifact", "")
                producer_root = producer["source_root"]
                consumer_root = consumer["source_root"]
                if producer_root == consumer_root:
                    # Preserve both exact L2 channel records, but do not
                    # render an application self-loop for an internal flow.
                    continue
                acc.add(
                    source=producer_root,
                    target=consumer_root,
                    source_kind="application",
                    target_kind="application",
                    dependency_type="integration",
                    mechanism=_CHANNEL_TYPE_TO_MECHANISM.get(channel_type, "unknown"),
                    direction="out",
                    confidence="certain",
                    provenance=[
                        _provenance(
                            artifact_id=producer_artifact,
                            source_root=producer_root,
                            component_name=producer.get("component_name", ""),
                            fact_type="artifact_channel_producer",
                        ),
                        _provenance(
                            artifact_id=consumer_artifact,
                            source_root=consumer_root,
                            component_name=consumer.get("component_name", ""),
                            fact_type="artifact_channel_consumer",
                        ),
                    ],
                )
    return sorted(channels, key=lambda item: (item.channel_type, item.channel, item.qualified_artifact_id)), acc.edges()


def _build_interface_records(
    scan_result: ScanResult,
    unmapped: list[dict],
) -> list[LandscapeInterface]:
    """Expose endpoint facts without fabricating a calling/peer system."""
    job_source_roots = scan_result.manifest.job_source_roots
    interfaces: list[LandscapeInterface] = []
    for endpoint in scan_result.manifest.artifact_endpoints:
        artifact = endpoint.get("artifact", "")
        source_root = job_source_roots.get(artifact)
        if source_root is None:
            unmapped.append({
                "reason": "artifact_endpoint artifact not found in job_source_roots",
                "endpoint": endpoint,
            })
            continue
        interfaces.append(LandscapeInterface(
            endpoint_type=endpoint.get("endpoint_type", "unknown"),
            direction=endpoint.get("direction", "unknown"),
            artifact_id=artifact,
            qualified_artifact_id=_qualified_artifact_id(source_root, artifact),
            source_root=source_root,
            component_name=endpoint.get("component_name", ""),
            redaction_descriptor=_safe_descriptor(endpoint.get("endpoint_descriptor")),
        ))
    return sorted(interfaces, key=lambda item: (item.endpoint_type, item.qualified_artifact_id, item.component_name))


def _build_context_config_edges(
    scan_result: ScanResult,
    context_key_to_system_id: dict[str, str],
    unmapped: list[dict],
) -> list[LandscapeEdge]:
    """Roll exact usage of promoted shared contexts into config associations."""
    acc = _EdgeAccumulator()
    job_source_roots = scan_result.manifest.job_source_roots
    for context_key, artifacts in scan_result.manifest.context_used_by.items():
        system_id = context_key_to_system_id.get(context_key)
        if system_id is None:
            # Context identity did not pass the strict promotion rule, so it
            # cannot safely become an L1 relationship.
            continue
        for artifact in artifacts:
            source_root = job_source_roots.get(artifact)
            if source_root is None:
                unmapped.append({
                    "reason": "context_used_by artifact not found in job_source_roots",
                    "context_key": context_key,
                    "artifact": artifact,
                })
                continue
            acc.add(
                source=source_root,
                target=system_id,
                source_kind="application",
                target_kind="system",
                dependency_type="config",
                mechanism="context",
                direction="unknown",
                confidence="certain",
                provenance=_provenance(
                    artifact_id=artifact,
                    source_root=source_root,
                    fact_type="context_reference",
                ),
            )
    return acc.edges()


_LINEAGE_MECHANISM_BY_TRANSFORM_TYPE = {
    "table_identity": "db",
}


def _build_lineage_data_edges(
    scan_result: ScanResult,
    lineage_matrix: LineageMatrix,
    unmapped: list[dict],
) -> list[LandscapeEdge]:
    job_source_roots = scan_result.manifest.job_source_roots
    acc = _EdgeAccumulator()

    for entry in lineage_matrix.entries:
        if not entry.source_job_name:
            continue  # intra-job entry, not a job-boundary data edge
        source_job = entry.source_job_name
        target_job = entry.job_name
        source_root = job_source_roots.get(source_job)
        target_root = job_source_roots.get(target_job)
        if source_root is None or target_root is None:
            unmapped.append({
                "reason": "lineage cross-job entry endpoint not found in job_source_roots",
                "source_job_name": source_job,
                "job_name": target_job,
            })
            continue
        if source_root == target_root:
            # Same application data flow belongs to the L3/L4 drill-down,
            # not an L1 application dependency.
            continue

        transform_type = entry.transformations[0].transformation_type if entry.transformations else ""
        mechanism = _LINEAGE_MECHANISM_BY_TRANSFORM_TYPE.get(transform_type, "unknown")
        acc.add(
            source=source_root,
            target=target_root,
            source_kind="application",
            target_kind="application",
            dependency_type="data",
            mechanism=mechanism,
            direction="out",
            confidence=entry.confidence.value,
            provenance=[
                _provenance(
                    artifact_id=source_job,
                    source_root=source_root,
                    fact_type="lineage_source",
                ),
                _provenance(
                    artifact_id=target_job,
                    source_root=target_root,
                    fact_type="lineage_target",
                ),
            ],
        )
    return acc.edges()


# ── Diagnostics ──────────────────────────────────────────────────────────

def _unread_channel_diagnostics(scan_result: ScanResult) -> list[LandscapeDiagnostic]:
    manifest = scan_result.manifest
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for ch in manifest.artifact_channels:
        channel = ch.get("channel", "")
        if channel in _PLACEHOLDER_CHANNEL_VALUES:
            # No distinguishing identity to dedupe / correlate readers on.
            continue
        groups[(ch.get("channel_type", ""), channel)].append(ch)

    diagnostics: list[LandscapeDiagnostic] = []
    for (channel_type, channel), records in sorted(groups.items()):
        directions = {r.get("direction", "unknown") for r in records}
        has_writer = "output" in directions or "both" in directions
        has_reader = "input" in directions or "both" in directions
        if not has_writer or has_reader:
            continue
        severity = "warning" if _DEAD_LETTER_RE.search(channel) else "info"
        evidence = sorted({r.get("artifact", "") for r in records})
        diagnostics.append(LandscapeDiagnostic(
            kind="unread_channel",
            description=(
                f"{channel_type} channel '{channel}' has writer(s) but no reader "
                f"anywhere in the scan ({len(records)} writer record(s))."
            ),
            evidence=evidence,
            severity=severity,
        ))
    return diagnostics


def _unresolved_reference_diagnostics(scan_result: ScanResult) -> list[LandscapeDiagnostic]:
    manifest = scan_result.manifest
    groups: dict[str, list[dict]] = defaultdict(list)
    for relation in manifest.artifact_relations:
        if relation.get("target", ""):
            continue
        target_name = relation.get("target_name", "") or "(unknown)"
        groups[target_name].append(relation)

    diagnostics: list[LandscapeDiagnostic] = []
    for target_name, relations in sorted(groups.items()):
        sources = sorted({r.get("source", "") for r in relations})
        resolutions = sorted({r.get("resolution", "") for r in relations})
        diagnostics.append(LandscapeDiagnostic(
            kind="unresolved_reference",
            description=(
                f"{len(relations)} reference(s) to '{target_name}' could not be "
                f"resolved to a target artifact (resolution: {', '.join(resolutions)})."
            ),
            evidence=sources,
            severity="info",
        ))
    return diagnostics


# ── Top-level builder ────────────────────────────────────────────────────


def build_landscape(
    scan_result: ScanResult,
    lineage_matrix: LineageMatrix,
    cred_filter: CredentialFilter,
) -> Landscape:
    """Build a `Landscape` from a (post_filter-redacted) `ScanResult` and
    its already-built `LineageMatrix`. Mirrors `lineage.build_lineage_matrix`:
    must be called after the Layer 1+2 redaction passes have already run
    over `scan_result`."""
    unmapped: list[dict] = []

    applications, root_to_application_id = _build_applications(scan_result)
    application_labels = {app.label for app in applications if app.label}

    connection_systems, name_to_system_id, _identity_to_system_id = (
        _build_metadata_connection_systems(scan_result, cred_filter)
    )
    context_systems, context_key_to_system_id = _build_context_group_systems(
        scan_result, connection_systems, application_labels, cred_filter,
    )
    fallback_systems, fallback_key_to_id = _build_fallback_systems(scan_result)
    resource_systems, resource_system_ids = _build_resource_systems(
        scan_result, name_to_system_id, fallback_key_to_id,
    )
    systems = connection_systems + context_systems + fallback_systems + resource_systems

    control_flow_edges = _build_control_flow_edges(scan_result, unmapped)
    resource_data_edges = _build_resource_data_edges(
        scan_result, name_to_system_id, resource_system_ids, unmapped,
    )
    fallback_integration_edges = _build_fallback_integration_edges(
        scan_result, name_to_system_id, fallback_key_to_id, resource_system_ids, unmapped,
    )
    channels, channel_integration_edges = _build_channel_records_and_edges(scan_result, unmapped)
    interfaces = _build_interface_records(scan_result, unmapped)
    config_edges = _build_context_config_edges(scan_result, context_key_to_system_id, unmapped)
    lineage_data_edges = _build_lineage_data_edges(scan_result, lineage_matrix, unmapped)
    edges = _remap_application_edges((
        control_flow_edges
        + resource_data_edges
        + fallback_integration_edges
        + channel_integration_edges
        + config_edges
        + lineage_data_edges
    ), root_to_application_id)

    diagnostics = _unread_channel_diagnostics(scan_result) + _unresolved_reference_diagnostics(scan_result)

    # Defense in depth (matches `lineage._redact_transformations`): re-run
    # the text filter over every assembled string field this pass produced
    # from scratch, even though the inputs were already Layer 1/2 filtered.
    for system in systems:
        system.name = cred_filter.filter_text(system.name) if system.name else system.name
        for ev in system.evidence:
            ev.label = cred_filter.filter_text(ev.label) if ev.label else ev.label
    for diag in diagnostics:
        diag.description = cred_filter.filter_text(diag.description)

    summary = {
        "system_count": len(systems),
        "application_count": len(applications),
        "edge_count": len(edges),
        "control_flow_edge_count": sum(
            edge.dependency_type == "control_flow" for edge in edges
        ),
        "data_edge_count": sum(edge.dependency_type == "data" for edge in edges),
        "integration_edge_count": sum(
            edge.dependency_type == "integration" for edge in edges
        ),
        "config_edge_count": sum(edge.dependency_type == "config" for edge in edges),
        "channel_count": len(channels),
        "interface_count": len(interfaces),
        "diagnostic_count": len(diagnostics),
        "unmapped_count": len(unmapped),
    }

    return Landscape(
        systems=systems,
        applications=applications,
        edges=edges,
        channels=channels,
        interfaces=interfaces,
        diagnostics=diagnostics,
        unmapped=unmapped,
        summary=summary,
    )


def save_landscape(landscape: Landscape, output_dir: Path) -> Path:
    """Persist `scan/landscape.json`. Caller must gate this on
    `redaction_verifier.verify_landscape` passing (or an explicit
    `--allow-redaction-failures` opt-in), mirroring the `lineage.json` gate
    in `cli.py::scan`."""
    scan_dir = output_dir / "scan"
    scan_dir.mkdir(parents=True, exist_ok=True)
    path = scan_dir / "landscape.json"
    path.write_text(landscape.model_dump_json(indent=2), encoding="utf-8")
    return path
