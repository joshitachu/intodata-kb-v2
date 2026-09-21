"""Structured read-only access to the intodoc scan of a project's source.

Why this exists
---------------
Generated documentation summarises component parameters away; the scan keeps
them. Asked "what orders go through FMD?", a measured run answered "I did not
find any filter ... confirm with the functional owner". The code answers it
without ambiguity: a ``cMessagingEndpoint`` labelled ``determineFMD`` carries a
``URI`` parameter reading ``"sql:SELECT COUNT(ABAN8) FROM " +
context.jdeTableF0101 + " WHERE ABAN8=CAST(:#ADDRESS_KEY AS NUMBER) AND
ABCLASS01 = 'FMD'?dataSource=..."``. None of ``ABCLASS01``, ``determineFMD``,
``checkIfFMDNeeded`` or ``No FMD Customer`` occurs anywhere in that project's
generated documentation. A hedge was published where a predicate existed.

Structure, not files
--------------------
This is deliberately not a filesystem mount with grep and read. The scan is
minified -- one job is one line -- so a line-oriented search returns a 4.6 MB
line as a single "match", and a whole job reaches 102 components, which is more
than a turn can carry. Both capabilities therefore address structure: a search
returns the job, the component, the component's ``LABEL``, the parameter name
and the parameter value; a read returns exactly one component.

Layout
------
The scan root is a read-only mount (``INTODOC_SCAN_ROOT``, default
``/intodoc-output``; ``knowledge_base/intodoc-output`` on a developer machine)::

    <root>/<project_id>/scan_result.json     one monolith, 167 MB for pluripharm
    <root>/<project_id>/scan/jobs/**.json    the same jobs, one file each
    <root>/<project_id>/redaction_report.json

Only ``scan/jobs/**`` is read. The two representations were compared for
pluripharm and hold the same 482 jobs with identical name sets, so the sharded
tree loses nothing; what it adds is that a component read touches one file
instead of parsing 167 MB, and a search skips files whose bytes do not contain
the term. ``scan/sanitized/`` -- a 612 MB mirror of the original source files --
is not searched: it is text, not structure, and it is the thing this module
exists not to hand back raw.

Redaction
---------
Parameter values are returned verbatim, and that is safe because redaction is an
upstream property of the artifact rather than something re-implemented here.
The scan is the publication-safe representation: it is redacted at scan time and
verified there, with the report written beside it. For pluripharm,
``redaction_report.json`` records ``redaction_level: "standard"``, 49,806
redactions (7,245 sensitive component parameters, 124 connection hosts, 3,756
context-variable passwords, 4 basic-auth values), and a verification pass over
487,963 values and 2,475,452 string leaves with ``passed: true``, no findings
and no residual matches.

The property that makes these capabilities possible is that redaction removes
secrets while preserving logic: every secret-named parameter reads
``[REDACTED]`` and every URL ``<URL_REDACTED>``, yet the FMD statement above
survives intact, ``dataSource`` reference and all. Re-masking at read time would
be a second implementation of a rule that already has one, and it could only
drift from it -- so there is no masking, no secret-pattern scan and no
redaction gate in this module. If a scan ever needs different redaction, that
belongs in the scanner, next to the report that proves it ran.

Cost
----
Measured end to end through these functions, warm page cache, median of three.
Against the real pluripharm scan (482 job files, 126 MB): a term that occurs
nowhere costs 75 ms, ``determineFMD`` (2 files parsed) 75 ms, ``ABCLASS01``
(12 files parsed) 119 ms, and ``tMap`` -- which forces all 482 files through the
parser for 1,462 matches -- 401 ms. The largest mounted project,
``VW_FULL_LANDSCAPE`` at 1,345 shards, costs 464 ms on the same worst case.

The shape that makes this cheap is a byte-level prefilter: a file whose raw
bytes do not contain the term is never decoded or parsed. So there is no index,
no cache and no background build, and consequently the search can never be
stale. ``read_component`` costs 0.8 ms given a ``job_ref`` and 297 ms given a
job name, which is the whole reason a search hit publishes the ref.

The largest single job file in any mounted project is 4.6 MB. The blocking work
runs in a worker thread so a 0.4 s scan does not stall the event loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Final, NamedTuple

from src.harness.capability_runtime import (
    CapabilityExecutionContext,
    CapabilityExecutionRequest,
    CapabilityHandlerServices,
    CapabilityObservation,
)
from src.harness.models import (
    SOURCE_COMPONENT_READ_CAPABILITY,
    SOURCE_SEARCH_CAPABILITY,
    CapabilityDefinition,
    EvidenceItem,
    EvidenceQueryResult,
)

logger = logging.getLogger(__name__)

__all__ = [
    "SCAN_ROOT_ENV",
    "read_component_evidence",
    "scan_root",
    "search_source_evidence",
    "source_evidence_capabilities",
    "source_evidence_capability_handlers",
]


#: Where the read-only scan mount is expected. Read at call time rather than at
#: import so a process -- or a test -- can repoint it without a reload.
SCAN_ROOT_ENV: Final = "INTODOC_SCAN_ROOT"

#: The container mount. Wrong on a developer machine on purpose: a default that
#: happens to resolve to one checkout makes a test that forgot to set the
#: variable answer from whatever is on the machine running it.
_DEFAULT_SCAN_ROOT: Final = "/intodoc-output"

#: The sharded per-job tree, relative to ``<root>/<project_id>``.
_JOBS_RELATIVE_DIR: Final = ("scan", "jobs")

#: A project id becomes a path segment. It arrives from RBAC-derived scope
#: rather than raw user input and is validated anyway, because a traversal here
#: reads another client's scan. The leading-character rule and the explicit
#: parent-directory rejection below matter: ``^[A-Za-z0-9._-]+$`` alone accepts
#: ``..``, which is the whole of a traversal.
_SAFE_PROJECT_ID_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

#: A search hit shows enough of the value to judge it and no more. The FMD
#: statement is 190 characters; a tMap expression block runs to tens of
#: thousands, and twenty-five of those would be the whole turn. Anything cut is
#: reported as cut, and `read_component` returns the value whole.
_HIT_VALUE_MAX_CHARS: Final = 1_200

#: Per-parameter ceiling for a whole-component read. Generous -- the point of
#: this capability is the complete value -- but not unbounded.
_COMPONENT_PARAM_VALUE_MAX_CHARS: Final = 4_000

#: Ceiling for one rendered component. A component with 200 parameters exists;
#: the rendering stops here and says how many parameters it did not reach.
_COMPONENT_BODY_MAX_CHARS: Final = 24_000

#: Component `code` is a raw Java/Camel fragment and is usually empty. It is
#: shown, bounded, because it occasionally carries the logic the parameters do
#: not.
_COMPONENT_CODE_MAX_CHARS: Final = 2_000

#: How many names a "that does not exist, these do" failure lists. A job holds
#: up to 102 components, so listing all of them is the useful behaviour.
_MAX_LISTED_NAMES: Final = 150

#: How many candidate job refs an ambiguous job name reports.
_MAX_LISTED_JOBS: Final = 25

_DEFAULT_MAX_RESULTS: Final = 25


class _MatchSite(NamedTuple):
    """Where in a job's structure a term was found.

    ``where`` is published on every hit because the same term means different
    things in different positions: ``FMD`` in a parameter value is a predicate,
    ``FMD`` in a component name is a label, and a model that cannot tell them
    apart will quote one as the other.
    """

    where: str
    #: The parameter, variable or connection name the match sits on, when the
    #: match site has one. Absent for matches on a component or job name.
    key: str | None
    value: str


class _JobFile(NamedTuple):
    path: Path
    #: Path relative to the project's jobs directory, with forward slashes. This
    #: is the ``job_ref`` a hit publishes and `read_component` accepts back.
    ref: str


class _ResolvedProject(NamedTuple):
    jobs_dir: Path | None
    failure: EvidenceQueryResult | None


class _ResolvedJob(NamedTuple):
    job: _JobFile | None
    failure: EvidenceQueryResult | None


def scan_root() -> Path:
    """The mounted intodoc output root."""

    value = os.environ.get(SCAN_ROOT_ENV, "").strip()
    return Path(value or _DEFAULT_SCAN_ROOT)


# ---------------------------------------------------------------------------
# Project and path resolution
# ---------------------------------------------------------------------------


def _resolve_project(
    *,
    capability_id: str,
    project_id: str,
    project_scope: list[str] | None,
) -> _ResolvedProject:
    """Validate the caller, the id and the mount, in that order.

    Scope first, before the id is even inspected, so an unauthorized project
    learns nothing at all -- including how this tool reacts to what it was
    asked. This repeats a check the executor already made: this function is also
    reachable from tests and from any future caller that builds its own context,
    and the cost of the duplicate is one comparison.
    """

    if project_scope is not None and project_id not in project_scope:
        return _ResolvedProject(
            jobs_dir=None,
            failure=EvidenceQueryResult(
                capability_id=capability_id,
                status="failed",
                error_message="That project is not in the authorized scope for this turn.",
            ),
        )

    if not _SAFE_PROJECT_ID_RE.match(project_id) or ".." in project_id:
        # Logged because a malformed id from a trusted scope path is a defect
        # worth seeing, and a crafted one is worth seeing more.
        logger.warning("Refusing unsafe intodoc project_id %r for a scan path", project_id)
        return _ResolvedProject(
            jobs_dir=None,
            failure=EvidenceQueryResult(
                capability_id=capability_id,
                status="failed",
                error_message=(
                    "That project identifier is not a valid one. It may contain only "
                    "letters, digits, dots, dashes and underscores, and must start with "
                    "a letter or a digit."
                ),
                scope={
                    "searched_what": (
                        "Nothing was read: the identifier was rejected before any path "
                        "was built from it."
                    ),
                },
            ),
        )

    root = scan_root()
    jobs_dir = root.joinpath(project_id, *_JOBS_RELATIVE_DIR)
    if not _is_contained(jobs_dir, root):
        # Unreachable through the pattern above; kept because the pattern is the
        # kind of thing that gets relaxed by somebody who does not know why it is
        # strict, and this check does not depend on knowing that.
        logger.warning("Refusing intodoc scan path outside the scan root for %r", project_id)
        return _ResolvedProject(
            jobs_dir=None,
            failure=EvidenceQueryResult(
                capability_id=capability_id,
                status="failed",
                error_message="That project identifier does not resolve inside the scan root.",
            ),
        )

    if not jobs_dir.is_dir():
        return _ResolvedProject(
            jobs_dir=None,
            failure=EvidenceQueryResult(
                capability_id=capability_id,
                status="failed",
                error_message=(
                    f"No scanned source is mounted for project {project_id}. Nothing was "
                    "searched, so this says nothing about whether the project's code "
                    "contains what you are looking for -- say so rather than reporting "
                    "an absence."
                ),
                scope={
                    "projects": [project_id],
                    "scan_mounted": False,
                    "searched_what": (
                        "Nothing. The scanned source for this project is not present "
                        f"under the configured scan root ({SCAN_ROOT_ENV}). Either the "
                        "project has never been scanned or the scan is not mounted into "
                        "this deployment."
                    ),
                },
            ),
        )

    return _ResolvedProject(jobs_dir=jobs_dir, failure=None)


def _is_contained(candidate: Path, root: Path) -> bool:
    """Whether ``candidate`` stays inside ``root`` once both are resolved.

    ``strict=False`` on purpose: the path is checked before it is known to
    exist, and a missing directory must be refused for being outside the root,
    not accepted for being absent.
    """

    try:
        resolved = candidate.resolve(strict=False)
        resolved_root = root.resolve(strict=False)
    except OSError:
        logger.warning("Could not resolve an intodoc scan path for containment", exc_info=True)
        return False
    return resolved == resolved_root or resolved_root in resolved.parents


def _job_files(jobs_dir: Path) -> list[_JobFile]:
    """Every job shard under a project, in a stable order.

    Sorted so that two identical calls return identical results: the walk order
    of a directory is not a guarantee, and a search that reorders its hits
    between turns makes a conversation's own citations disagree.
    """

    files: list[_JobFile] = []
    for path in jobs_dir.rglob("*.json"):
        if not path.is_file():
            continue
        try:
            ref = path.relative_to(jobs_dir).as_posix()
        except ValueError:
            # rglob cannot produce this; a symlinked tree under a different root
            # could, and a ref that does not resolve back is worse than skipping.
            continue
        files.append(_JobFile(path=path, ref=ref))
    files.sort(key=lambda item: item.ref)
    return files


def _load_job(path: Path) -> dict[str, Any] | None:
    """One job shard, or None when it cannot be read or parsed.

    None rather than an exception: one corrupt shard among 482 must cost that
    shard, not the search. Every caller counts what it skipped and publishes the
    count in ``scope``, so a partial result is never presented as a complete one.
    """

    try:
        with path.open("rb") as handle:
            loaded = json.load(handle)
    except (OSError, ValueError) as error:
        # ValueError covers json.JSONDecodeError and the UnicodeDecodeError that
        # a truncated multi-byte shard raises.
        logger.warning("Unreadable intodoc job shard %s: %s", path.name, error)
        return None
    return loaded if isinstance(loaded, dict) else None


# ---------------------------------------------------------------------------
# Structure walking
# ---------------------------------------------------------------------------


def _param_items(component: dict[str, Any]) -> list[tuple[str, str]]:
    """A component's parameters as ``(name, value)``, skipping malformed entries."""

    raw = component.get("params")
    if not isinstance(raw, list):
        return []
    items: list[tuple[str, str]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        value = entry.get("value")
        if not isinstance(name, str):
            continue
        items.append((name, value if isinstance(value, str) else str(value)))
    return items


def _component_label(component: dict[str, Any]) -> str | None:
    """The component's ``LABEL`` parameter, which is what a human named it.

    ``cMessagingEndpoint_15`` is the identity; ``determineFMD`` is the intent.
    A hit that reports only the identity is a hit the reader cannot place.
    """

    for name, value in _param_items(component):
        if name == "LABEL" and value:
            return value
    return None


def _component_matches(
    component: dict[str, Any],
    needle: str,
) -> list[_MatchSite]:
    """Every place inside one component where the term appears."""

    sites: list[_MatchSite] = []
    unique_name = component.get("unique_name")
    component_type = component.get("component_type")
    if isinstance(unique_name, str) and needle in unique_name.casefold():
        sites.append(_MatchSite(where="component_name", key=None, value=unique_name))
    if isinstance(component_type, str) and needle in component_type.casefold():
        sites.append(_MatchSite(where="component_type", key=None, value=component_type))
    for name, value in _param_items(component):
        if needle in value.casefold():
            sites.append(_MatchSite(where="component_param_value", key=name, value=value))
        elif needle in name.casefold():
            # `elif`: a parameter whose name and value both match is one finding,
            # and the value is the more informative half of it.
            sites.append(_MatchSite(where="component_param_name", key=name, value=value))
    code = component.get("code")
    if isinstance(code, str) and needle in code.casefold():
        sites.append(_MatchSite(where="component_code", key=None, value=code))
    return sites


def _job_matches(job: dict[str, Any], needle: str) -> list[_MatchSite]:
    """Every place outside the component list where the term appears.

    Context variables are here rather than on a component because that is where
    the scan puts them, and they are worth matching: a Talend job's environment
    differences live entirely in them -- ``jdeTableF0101`` resolves to
    ``PRODDTA.F0101`` in PROD and ``CRPDTA.F0101`` in DEV -- so a question about
    which table is read in production is answered here and nowhere else.
    """

    sites: list[_MatchSite] = []
    for field, where in (("name", "job_name"), ("purpose", "job_purpose"), ("path", "job_path")):
        value = job.get(field)
        if isinstance(value, str) and needle in value.casefold():
            sites.append(_MatchSite(where=where, key=None, value=value))

    groups = job.get("context_groups")
    if isinstance(groups, list):
        for group in groups:
            if not isinstance(group, dict):
                continue
            group_name = group.get("name") if isinstance(group.get("name"), str) else ""
            variables = group.get("variables")
            if not isinstance(variables, list):
                continue
            for variable in variables:
                if not isinstance(variable, dict):
                    continue
                var_name = variable.get("name")
                if not isinstance(var_name, str):
                    continue
                key = f"{group_name}.{var_name}" if group_name else var_name
                values = variable.get("values")
                rendered = (
                    ", ".join(
                        f"{env}={val}"
                        for env, val in values.items()
                        if isinstance(env, str) and isinstance(val, str)
                    )
                    if isinstance(values, dict)
                    else ""
                )
                if needle in var_name.casefold():
                    sites.append(
                        _MatchSite(where="context_variable_name", key=key, value=rendered)
                    )
                elif rendered and needle in rendered.casefold():
                    sites.append(
                        _MatchSite(where="context_variable_value", key=key, value=rendered)
                    )

    connections = job.get("connections")
    if isinstance(connections, list):
        for connection in connections:
            if not isinstance(connection, dict):
                continue
            name = connection.get("name")
            if isinstance(name, str) and needle in name.casefold():
                source = connection.get("source")
                target = connection.get("target")
                sites.append(
                    _MatchSite(
                        where="connection_name",
                        key=name,
                        value=f"{source} -> {target}",
                    )
                )
    return sites


# ---------------------------------------------------------------------------
# search_source
# ---------------------------------------------------------------------------


class _Hit(NamedTuple):
    job_name: str
    job_ref: str
    job_path: str | None
    component: str | None
    component_type: str | None
    component_label: str | None
    site: _MatchSite


def _scan_project(
    *,
    jobs_dir: Path,
    needle: str,
    max_results: int,
    job_filter: str | None,
    component_type_filter: str | None,
) -> tuple[list[_Hit], int, int, int, list[str]]:
    """Walk every shard once and collect bounded hits plus honest totals.

    Blocking. Called through ``asyncio.to_thread``.

    Every candidate file is examined even after ``max_results`` is reached. The
    extra work is measured at tenths of a second and it buys the one thing a
    truncated result needs: the true total, so the caller is told "25 of 412"
    rather than "25", and cannot mistake a cap for an exhaustive answer.

    Returns the hits, the total number of matches, the number of files examined,
    the number of files opened and parsed, and the refs of files that could not
    be read.
    """

    hits: list[_Hit] = []
    total = 0
    files = _job_files(jobs_dir)
    parsed = 0
    unreadable: list[str] = []
    needle_bytes = needle.encode("utf-8", errors="ignore")

    for job_file in files:
        if job_filter is not None and not _job_filter_matches(job_file, job_filter):
            continue
        try:
            raw = job_file.path.read_bytes()
        except OSError as error:
            logger.warning("Unreadable intodoc job shard %s: %s", job_file.ref, error)
            unreadable.append(job_file.ref)
            continue
        # The prefilter is a plain case-insensitive byte scan. It is allowed to
        # over-accept -- a term that only appears inside a key the walk below
        # ignores costs one parse -- but it must never under-accept, which is why
        # it runs on the whole file rather than on any decoded subset of it.
        if needle_bytes not in raw.lower():
            continue
        try:
            job = json.loads(raw)
        except ValueError as error:
            logger.warning("Malformed intodoc job shard %s: %s", job_file.ref, error)
            unreadable.append(job_file.ref)
            continue
        if not isinstance(job, dict):
            unreadable.append(job_file.ref)
            continue
        parsed += 1

        job_name = job.get("name") if isinstance(job.get("name"), str) else job_file.ref
        job_path = job.get("path") if isinstance(job.get("path"), str) else None

        if component_type_filter is None:
            for site in _job_matches(job, needle):
                total += 1
                if len(hits) < max_results:
                    hits.append(
                        _Hit(
                            job_name=job_name,
                            job_ref=job_file.ref,
                            job_path=job_path,
                            component=None,
                            component_type=None,
                            component_label=None,
                            site=site,
                        )
                    )

        components = job.get("components")
        if not isinstance(components, list):
            continue
        for component in components:
            if not isinstance(component, dict):
                continue
            component_type = component.get("component_type")
            if component_type_filter is not None:
                if not isinstance(component_type, str):
                    continue
                if component_type_filter not in component_type.casefold():
                    continue
            sites = _component_matches(component, needle)
            if not sites:
                continue
            unique_name = component.get("unique_name")
            label = _component_label(component)
            for site in sites:
                total += 1
                if len(hits) < max_results:
                    hits.append(
                        _Hit(
                            job_name=job_name,
                            job_ref=job_file.ref,
                            job_path=job_path,
                            component=unique_name if isinstance(unique_name, str) else None,
                            component_type=(
                                component_type if isinstance(component_type, str) else None
                            ),
                            component_label=label,
                            site=site,
                        )
                    )

    examined = sum(
        1
        for job_file in files
        if job_filter is None or _job_filter_matches(job_file, job_filter)
    )
    return hits, total, examined, parsed, unreadable


def _job_filter_matches(job_file: _JobFile, job_filter: str) -> bool:
    """Whether a shard is the one a ``job`` argument names.

    Matched on the ref, which is exact, or on the filename stem, which is how a
    job name reaches the tree. A name match cannot be done here without parsing
    the file, so this is deliberately a cheap pre-filter: a `job` argument that
    names a job whose filename was truncated by the scanner still reaches the
    parse below, because the stem check is a substring test in both directions.
    """

    wanted = job_filter.casefold()
    ref = job_file.ref.casefold()
    if ref == wanted:
        return True
    stem = job_file.path.stem.casefold()
    return wanted in stem or stem.startswith(wanted)


async def search_source_evidence(
    *,
    project_id: str,
    query: str,
    project_scope: list[str] | None,
    job: str | None = None,
    component_type: str | None = None,
    max_results: int = _DEFAULT_MAX_RESULTS,
) -> EvidenceQueryResult:
    """Find a term in a project's scanned source and return the structure around it.

    Every return path carries ``scope``. A miss here is the dangerous case: the
    failure this capability was built for was an agent reporting "no filter is
    recorded" after searching a corpus that could not have contained one, so a
    result with no hits states what was searched -- how many jobs, which tree,
    which representation -- and says in words that this is not a statement about
    the code. ``scope`` also reports every cap applied, because a capped result
    that does not say it was capped is a wrong count waiting to be quoted.
    """

    capability_id = SOURCE_SEARCH_CAPABILITY.id
    resolved = _resolve_project(
        capability_id=capability_id,
        project_id=project_id,
        project_scope=project_scope,
    )
    if resolved.jobs_dir is None:
        return resolved.failure or EvidenceQueryResult(
            capability_id=capability_id,
            status="failed",
            error_message=f"The scanned source for project {project_id} could not be opened.",
        )

    needle = query.strip().casefold()
    if len(needle) < 2:
        return EvidenceQueryResult(
            capability_id=capability_id,
            status="failed",
            error_message=(
                "Send a literal term of at least two characters -- a column, a label, a "
                "table, a queue. This is a substring match over the scanned source, not "
                "a question answerer."
            ),
            scope={
                "projects": [project_id],
                "searched_what": "Nothing was searched: the term was empty or too short.",
            },
        )

    bounded_results = max(1, min(int(max_results), 100))
    job_filter = job.strip() if job and job.strip() else None
    type_filter = (
        component_type.strip().casefold()
        if component_type and component_type.strip()
        else None
    )

    try:
        hits, total, examined, parsed, unreadable = await asyncio.to_thread(
            _scan_project,
            jobs_dir=resolved.jobs_dir,
            needle=needle,
            max_results=bounded_results,
            job_filter=job_filter,
            component_type_filter=type_filter,
        )
    except OSError as error:
        # The directory walk itself failed -- an unmounted volume, a permission
        # change. Never raised into the turn: the model needs to know it did not
        # look, not to lose the turn.
        logger.exception("intodoc scan walk failed for project %s", project_id)
        return EvidenceQueryResult(
            capability_id=capability_id,
            status="failed",
            error_message=(
                f"The scanned source for project {project_id} could not be read "
                f"({type(error).__name__}). Nothing was searched."
            ),
            scope={
                "projects": [project_id],
                "searched_what": "Nothing: the scan tree became unreadable mid-request.",
            },
        )

    truncated_values = sum(1 for hit in hits if len(hit.site.value) > _HIT_VALUE_MAX_CHARS)
    scope: dict[str, Any] = {
        "projects": [project_id],
        "query": query,
        "source": "intodoc scan, sharded per-job structure (scan/jobs/**)",
        "jobs_examined": examined,
        "jobs_parsed": parsed,
        "match_count": total,
        "returned": len(hits),
        "max_results": bounded_results,
        "value_max_chars": _HIT_VALUE_MAX_CHARS,
        "values_truncated": truncated_values,
        "complete": total == len(hits),
        "matching": "case-insensitive substring, not a regular expression",
    }
    if job_filter is not None:
        scope["job_filter"] = job_filter
    if type_filter is not None:
        scope["component_type_filter"] = component_type
    if unreadable:
        # Named, not counted. "3 files were skipped" is not something a reader can
        # act on; the refs are, and a systematically corrupt shard shows up as the
        # same ref on every search.
        scope["unreadable_jobs"] = unreadable[:_MAX_LISTED_JOBS]
        scope["unreadable_job_count"] = len(unreadable)

    if not hits:
        scope["searched_what"] = (
            f"Every one of the {examined} scanned job(s) in project {project_id} was "
            f"examined for the literal term {query!r} -- component parameters and their "
            "values, component names and types, component code, context variables, "
            "connection names, job names, purposes and paths -- and it occurs in none of "
            "them. That is a fact about this term in this scan. Before reporting it as "
            "an absence, consider that the estate may spell it differently: try the "
            "column, table, queue or label name on its own rather than a phrase. The "
            "scan covers the source files that were scanned for this project and nothing "
            "outside them."
        )
        return EvidenceQueryResult(
            capability_id=capability_id,
            status="succeeded",
            scope=scope,
            items=[],
        )

    if total > len(hits):
        scope["searched_what"] = (
            f"All {examined} scanned job(s) in project {project_id} were examined and "
            f"{query!r} occurs {total} time(s); the {len(hits)} shown are the first in "
            "job order, not the best. This is a capped view: do not count from it. "
            "Narrow with 'job' or 'component_type', or raise 'max_results'."
        )
    else:
        scope["searched_what"] = (
            f"All {examined} scanned job(s) in project {project_id} were examined and "
            f"every one of the {total} occurrence(s) of {query!r} is returned here. "
            "Nothing was withheld, so this set is complete for this term. Values longer "
            f"than {_HIT_VALUE_MAX_CHARS} characters are cut and marked; read_component "
            "returns them whole."
        )

    return EvidenceQueryResult(
        capability_id=capability_id,
        status="succeeded",
        scope=scope,
        items=[
            _hit_item(project_id=project_id, hit=hit, index=index)
            for index, hit in enumerate(hits, start=1)
        ],
    )


def _hit_item(*, project_id: str, hit: _Hit, index: int) -> EvidenceItem:
    """Render one hit as the structure around it rather than as a line of text."""

    value = hit.site.value
    truncated = len(value) > _HIT_VALUE_MAX_CHARS
    shown = value[:_HIT_VALUE_MAX_CHARS] if truncated else value

    if hit.component is not None:
        heading = f"{hit.job_name} / {hit.component}"
        if hit.component_type:
            heading += f" ({hit.component_type})"
        if hit.component_label:
            heading += f' labelled "{hit.component_label}"'
    else:
        heading = hit.job_name

    lines = [heading, f"match: {hit.site.where}"]
    if hit.site.key is not None:
        lines.append(f"{hit.site.key} = {shown}")
    else:
        lines.append(shown)
    if truncated:
        lines.append(
            f"[value cut at {_HIT_VALUE_MAX_CHARS} of {len(value)} characters -- "
            f"read_component(project_id={project_id!r}, job={hit.job_ref!r}, "
            f"component={hit.component!r}) returns it whole]"
        )

    metadata: dict[str, Any] = {
        "project_id": project_id,
        "job": hit.job_name,
        # The ref, not the absolute path: it is what `read_component` accepts and
        # it does not disclose the mount layout.
        "job_ref": hit.job_ref,
        "where": hit.site.where,
        "value_chars": len(value),
        "value_truncated": truncated,
    }
    if hit.job_path is not None:
        metadata["job_design_path"] = hit.job_path
    if hit.component is not None:
        metadata["component"] = hit.component
    if hit.component_type is not None:
        metadata["component_type"] = hit.component_type
    if hit.component_label is not None:
        metadata["component_label"] = hit.component_label
    if hit.site.key is not None:
        metadata["param"] = hit.site.key

    return EvidenceItem(
        id=f"source:{project_id}:{index}",
        capability_id=SOURCE_SEARCH_CAPABILITY.id,
        source_type="intodoc_scan",
        title=heading,
        summary="\n".join(lines),
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# read_component
# ---------------------------------------------------------------------------


def _resolve_job(
    *,
    capability_id: str,
    jobs_dir: Path,
    project_id: str,
    wanted: str,
) -> _ResolvedJob:
    """Resolve a ``job`` argument to one shard, by ref or by name.

    Blocking. Called through ``asyncio.to_thread``.

    A ref resolves without reading anything, which is the path a search hit
    takes. A name costs a walk, and is supported because a model that read a job
    name in a document should not have to guess a path to open it.

    Job names are not unique: pluripharm holds 482 shards under 445 distinct
    names, because the same job exists in more than one repository. An ambiguous
    name therefore fails and lists the candidates rather than returning the
    first -- returning the first would present one repository's component as
    another's, and nothing downstream could detect it.
    """

    candidate = jobs_dir / wanted
    if wanted.endswith(".json") and _is_contained(candidate, jobs_dir) and candidate.is_file():
        return _ResolvedJob(job=_JobFile(path=candidate, ref=wanted.replace("\\", "/")), failure=None)

    needle = wanted.casefold()
    matches: list[_JobFile] = []
    names: list[str] = []
    for job_file in _job_files(jobs_dir):
        job = _load_job(job_file.path)
        if job is None:
            continue
        name = job.get("name")
        job_id = job.get("job_id")
        if isinstance(name, str):
            names.append(name)
        if (isinstance(name, str) and name.casefold() == needle) or (
            isinstance(job_id, str) and job_id.casefold() == needle
        ):
            matches.append(job_file)

    if len(matches) == 1:
        return _ResolvedJob(job=matches[0], failure=None)

    if not matches:
        return _ResolvedJob(
            job=None,
            failure=EvidenceQueryResult(
                capability_id=capability_id,
                status="failed",
                error_message=(
                    f"No scanned job named {wanted!r} in project {project_id}. The job "
                    "names that do exist are listed in the scope of this result; pass one "
                    "of those, or the 'job_ref' from a search_source hit."
                ),
                scope={
                    "projects": [project_id],
                    "job": wanted,
                    "available_jobs": sorted(set(names))[:_MAX_LISTED_NAMES],
                    "available_job_count": len(set(names)),
                    "searched_what": (
                        "Every scanned job in this project was checked by name and by "
                        "job id. Nothing was read, so this says nothing about the "
                        "project's components."
                    ),
                },
            ),
        )

    return _ResolvedJob(
        job=None,
        failure=EvidenceQueryResult(
            capability_id=capability_id,
            status="failed",
            error_message=(
                f"{wanted!r} names {len(matches)} scanned jobs in project {project_id} -- "
                "the same job exists in more than one repository, and they are not the "
                "same code. Choose one and pass its 'job_ref', listed in the scope of "
                "this result."
            ),
            scope={
                "projects": [project_id],
                "job": wanted,
                "candidates": [item.ref for item in matches[:_MAX_LISTED_JOBS]],
                "candidate_count": len(matches),
                "searched_what": (
                    "The name matched more than one shard and none was read: returning "
                    "one of them would present one repository's component as another's."
                ),
            },
        ),
    )


async def read_component_evidence(
    *,
    project_id: str,
    job: str,
    component: str,
    project_scope: list[str] | None,
) -> EvidenceQueryResult:
    """Return one component of one job whole.

    One component, never a job: a job reaches 102 components, and a capability
    that can return all of them is one that will. The completeness this offers
    is per component -- every parameter, every declared column, every edge --
    which is the unit a question about behaviour is actually asked at.
    """

    capability_id = SOURCE_COMPONENT_READ_CAPABILITY.id
    resolved = _resolve_project(
        capability_id=capability_id,
        project_id=project_id,
        project_scope=project_scope,
    )
    if resolved.jobs_dir is None:
        return resolved.failure or EvidenceQueryResult(
            capability_id=capability_id,
            status="failed",
            error_message=f"The scanned source for project {project_id} could not be opened.",
        )

    jobs_dir = resolved.jobs_dir
    try:
        resolved_job = await asyncio.to_thread(
            _resolve_job,
            capability_id=capability_id,
            jobs_dir=jobs_dir,
            project_id=project_id,
            wanted=job.strip(),
        )
    except OSError:
        logger.exception("intodoc job resolution failed for project %s", project_id)
        return EvidenceQueryResult(
            capability_id=capability_id,
            status="failed",
            error_message=(
                f"The scanned source for project {project_id} could not be read. Nothing "
                "was opened."
            ),
            scope={"projects": [project_id], "job": job},
        )
    if resolved_job.job is None:
        return resolved_job.failure or EvidenceQueryResult(
            capability_id=capability_id,
            status="failed",
            error_message=f"{job!r} could not be resolved to a scanned job.",
        )

    job_file = resolved_job.job
    loaded = await asyncio.to_thread(_load_job, job_file.path)
    if loaded is None:
        return EvidenceQueryResult(
            capability_id=capability_id,
            status="failed",
            error_message=(
                f"The scanned job {job_file.ref} exists but could not be parsed, so none "
                "of its components can be read. Nothing about this job can be confirmed "
                "or denied from the scan."
            ),
            scope={
                "projects": [project_id],
                "job_ref": job_file.ref,
                "searched_what": "The shard was opened and found to be unreadable or malformed.",
            },
        )

    job_name = loaded.get("name") if isinstance(loaded.get("name"), str) else job_file.ref
    components = loaded.get("components") if isinstance(loaded.get("components"), list) else []
    needle = component.strip().casefold()
    available: list[str] = []
    found: dict[str, Any] | None = None
    for entry in components:
        if not isinstance(entry, dict):
            continue
        unique_name = entry.get("unique_name")
        if not isinstance(unique_name, str):
            continue
        available.append(unique_name)
        if found is None and unique_name.casefold() == needle:
            found = entry

    if found is None:
        return EvidenceQueryResult(
            capability_id=capability_id,
            status="failed",
            error_message=(
                f"No component named {component!r} in job {job_name}. The "
                f"{len(available)} component(s) that job does contain are listed in the "
                "scope of this result."
            ),
            scope={
                "projects": [project_id],
                "job": job_name,
                "job_ref": job_file.ref,
                "component": component,
                "available_components": available[:_MAX_LISTED_NAMES],
                "available_component_count": len(available),
                "searched_what": (
                    "One job was opened and every component in it was checked by unique "
                    "name. No other job was read."
                ),
            },
        )

    return _component_result(
        project_id=project_id,
        job_name=job_name,
        job_file=job_file,
        job=loaded,
        component=found,
    )


def _component_result(
    *,
    project_id: str,
    job_name: str,
    job_file: _JobFile,
    job: dict[str, Any],
    component: dict[str, Any],
) -> EvidenceQueryResult:
    """Render one component and state exactly what the rendering left out."""

    unique_name = component.get("unique_name")
    unique_name = unique_name if isinstance(unique_name, str) else "(unnamed)"
    component_type = component.get("component_type")
    component_type = component_type if isinstance(component_type, str) else "(unknown type)"
    label = _component_label(component)

    params = _param_items(component)
    incoming, outgoing = _component_edges(job, component, unique_name)

    lines: list[str] = [
        f"Component {unique_name} ({component_type})",
        f"job: {job_name}",
        f"job_ref: {job_file.ref}",
    ]
    design_path = job.get("path")
    if isinstance(design_path, str) and design_path:
        lines.append(f"design path: {design_path}")
    if label:
        lines.append(f'label: "{label}"')
    if component.get("active") is False:
        # A disabled component is still in the scan. Reading its parameters as
        # live behaviour is a specific, plausible, wrong answer.
        lines.append("active: false -- this component is disabled in the job design")

    truncated_params: list[str] = []
    omitted_params = 0
    lines.append("")
    lines.append(f"parameters ({len(params)}):")
    # Running total rather than re-joining the buffer per parameter: a component
    # with 200 parameters would otherwise make the rendering quadratic in the
    # size of the thing being rendered.
    rendered_chars = sum(len(line) + 1 for line in lines)
    for name, value in params:
        if rendered_chars > _COMPONENT_BODY_MAX_CHARS:
            omitted_params += 1
            continue
        if len(value) > _COMPONENT_PARAM_VALUE_MAX_CHARS:
            truncated_params.append(name)
            value = value[:_COMPONENT_PARAM_VALUE_MAX_CHARS] + " ...[cut]"
        line = f"  {name} = {value}"
        rendered_chars += len(line) + 1
        lines.append(line)
    if omitted_params:
        lines.append(f"  ...[{omitted_params} further parameter(s) not shown: size limit]")

    lines.append("")
    lines.append(f"incoming ({len(incoming)}):")
    lines.extend(f"  {edge}" for edge in incoming or ["(none)"])
    lines.append(f"outgoing ({len(outgoing)}):")
    lines.extend(f"  {edge}" for edge in outgoing or ["(none)"])

    schema_lines = _schema_lines(component)
    if schema_lines:
        lines.append("")
        lines.extend(schema_lines)

    code = component.get("code")
    code_truncated = False
    if isinstance(code, str) and code.strip():
        code_truncated = len(code) > _COMPONENT_CODE_MAX_CHARS
        lines.append("")
        lines.append("code:")
        lines.append(code[:_COMPONENT_CODE_MAX_CHARS])
        if code_truncated:
            lines.append(f"  ...[cut at {_COMPONENT_CODE_MAX_CHARS} of {len(code)} characters]")

    body = "\n".join(lines)
    scope: dict[str, Any] = {
        "projects": [project_id],
        "job": job_name,
        "job_ref": job_file.ref,
        "component": unique_name,
        "component_type": component_type,
        "param_count": len(params),
        "incoming_count": len(incoming),
        "outgoing_count": len(outgoing),
        "param_value_max_chars": _COMPONENT_PARAM_VALUE_MAX_CHARS,
        "complete": not truncated_params and not omitted_params and not code_truncated,
        "source": "intodoc scan, sharded per-job structure (scan/jobs/**)",
    }
    if label:
        scope["component_label"] = label
    if truncated_params:
        scope["truncated_params"] = truncated_params
    if omitted_params:
        scope["omitted_param_count"] = omitted_params
    if code_truncated:
        scope["code_truncated"] = True
    scope["searched_what"] = (
        f"One component, {unique_name}, of one job, {job_name}. Every parameter it "
        f"declares is listed ({len(params)} of them) with its value, together with the "
        "edges into and out of it and any row schema it declares. Nothing else in this "
        f"job -- it has {_component_count(job)} component(s) -- and no other job was "
        "read, so nothing here supports a statement about the job as a whole."
        + (
            " Some values were cut at the size limit; the names of the cut parameters "
            "are in 'truncated_params'."
            if truncated_params or omitted_params or code_truncated
            else ""
        )
    )

    metadata: dict[str, Any] = {
        "project_id": project_id,
        "job": job_name,
        "job_ref": job_file.ref,
        "component": unique_name,
        "component_type": component_type,
        "param_count": len(params),
    }
    if label:
        metadata["component_label"] = label

    return EvidenceQueryResult(
        capability_id=SOURCE_COMPONENT_READ_CAPABILITY.id,
        status="succeeded",
        scope=scope,
        items=[
            EvidenceItem(
                id=f"source:{project_id}:{job_file.ref}:{unique_name}",
                capability_id=SOURCE_COMPONENT_READ_CAPABILITY.id,
                source_type="intodoc_scan",
                title=f"{job_name} / {unique_name}",
                summary=body,
                metadata=metadata,
            )
        ],
    )


def _component_edges(
    job: dict[str, Any],
    component: dict[str, Any],
    unique_name: str,
) -> tuple[list[str], list[str]]:
    """The edges touching one component, derived from the job's connection list.

    A component carries ``incoming_connections`` and ``outgoing_connections``
    fields, and across every mounted project they are empty on every component:
    the scanner records the wiring once, on the job, as
    ``{name, source, target, connector_name, ...}``. Reading the component
    fields alone would report every component as unwired -- exactly the kind of
    confident wrong structural claim this module exists to prevent -- so the
    job's list is the source of truth, and the component's own lists are merged
    in afterwards so a future scan that populates them is not ignored.
    """

    incoming: list[str] = []
    outgoing: list[str] = []
    connections = job.get("connections")
    if isinstance(connections, list):
        for connection in connections:
            if not isinstance(connection, dict):
                continue
            source = connection.get("source")
            target = connection.get("target")
            name = connection.get("name") or ""
            connector = connection.get("connector_name") or ""
            inactive = "" if connection.get("active", True) else " [inactive]"
            if target == unique_name and isinstance(source, str):
                incoming.append(
                    f"{source} -> {unique_name}  [{connector}] {name}{inactive}".rstrip()
                )
            if source == unique_name and isinstance(target, str):
                outgoing.append(
                    f"{unique_name} -> {target}  [{connector}] {name}{inactive}".rstrip()
                )

    for field, bucket in (
        ("incoming_connections", incoming),
        ("outgoing_connections", outgoing),
    ):
        declared = component.get(field)
        if not isinstance(declared, list):
            continue
        for entry in declared:
            rendered = entry if isinstance(entry, str) else None
            if rendered and not any(rendered in existing for existing in bucket):
                bucket.append(rendered)
    return incoming, outgoing


def _schema_lines(component: dict[str, Any]) -> list[str]:
    """Render the row schemas a component declares, columns and types.

    Included because the column list is the only place a field-level question
    can be answered from: a mapping question asked against prose gets a summary
    of the mapping, and asked here gets the columns.
    """

    schemas = component.get("schemas")
    if not isinstance(schemas, list):
        return []
    lines: list[str] = []
    for schema in schemas:
        if not isinstance(schema, dict):
            continue
        columns = schema.get("columns")
        if not isinstance(columns, list) or not columns:
            continue
        connector = schema.get("connector") or "?"
        name = schema.get("name") or "?"
        lines.append(f"schema {name} [{connector}] ({len(columns)} columns):")
        for column in columns:
            if not isinstance(column, dict):
                continue
            column_name = column.get("name")
            if not isinstance(column_name, str):
                continue
            column_type = column.get("type") or ""
            source_type = column.get("source_type") or ""
            key = " key" if column.get("key") else ""
            nullable = "" if column.get("nullable", True) else " not-null"
            lines.append(f"  {column_name}: {column_type} {source_type}{key}{nullable}".rstrip())
    return lines


def _component_count(job: dict[str, Any]) -> int:
    components = job.get("components")
    return len(components) if isinstance(components, list) else 0


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------


def source_evidence_capabilities() -> tuple[CapabilityDefinition, ...]:
    """Every capability this module owns, for registry assembly."""

    return (SOURCE_SEARCH_CAPABILITY, SOURCE_COMPONENT_READ_CAPABILITY)


class SourceEvidenceHandler:
    """Bind one scan capability to its reader.

    It does not go through ``RegisteredEvidenceHandler`` and the shared adapter
    table for two reasons. That path caps every item summary at 4,000 characters
    and filters item metadata against a fixed key allowlist, which would drop the
    ``job_ref``/``component`` pair that makes the two capabilities compose and
    silently cut long parameter values a caller asked for precisely because they
    are long. It also runs a credential-masking pass over every field, which
    would be a second implementation of a redaction rule that the scanner already
    applies and verifies -- see this module's docstring.
    """

    def __init__(self, capability: CapabilityDefinition) -> None:
        self.capability_id = capability.id
        self.input_schema = capability.input_schema
        self.output_type = capability.output_type
        self.task_types = tuple(capability.task_types)

    async def __call__(
        self,
        capability: CapabilityDefinition,
        request: CapabilityExecutionRequest,
        context: CapabilityExecutionContext,
        services: CapabilityHandlerServices,
    ) -> CapabilityObservation:
        # The reader owns its own source; no request-scoped service is involved.
        del services
        arguments = dict(request.arguments)
        project_id = str(arguments.get("project_id") or "")
        try:
            if capability.id == SOURCE_SEARCH_CAPABILITY.id:
                result = await search_source_evidence(
                    project_id=project_id,
                    query=str(arguments.get("query") or ""),
                    # Server-owned turn identity, never a model-supplied argument.
                    project_scope=context.project_scope,
                    job=_optional_str(arguments.get("job")),
                    component_type=_optional_str(arguments.get("component_type")),
                    max_results=_bounded_int(
                        arguments.get("max_results"), default=_DEFAULT_MAX_RESULTS
                    ),
                )
            else:
                result = await read_component_evidence(
                    project_id=project_id,
                    job=str(arguments.get("job") or ""),
                    component=str(arguments.get("component") or ""),
                    project_scope=context.project_scope,
                )
        except Exception:
            # The readers return failures rather than raising, so reaching here
            # is a defect in them. It still may not end the turn: a crash inside
            # a read-only tool must cost the call, not the conversation.
            logger.exception("Scan evidence read failed for %s", capability.id)
            return CapabilityObservation(
                capability_id=capability.id,
                status="failed",
                output_type=capability.output_type,
                summary="The scanned source could not answer this request.",
                error_message="The scanned source is unavailable for this request.",
                retryable=True,
                safe_details={"evidence_count": 0},
            )

        items = [item.model_dump(mode="json") for item in result.items]
        if result.status != "succeeded":
            return CapabilityObservation(
                capability_id=capability.id,
                status="failed",
                output_type=capability.output_type,
                summary="The scanned source could not answer this request.",
                error_message=result.error_message
                or "The scanned source returned no usable evidence.",
                retryable=True,
                data={"scope": result.scope} if result.scope else {},
                safe_details={"evidence_count": 0},
            )

        data: dict[str, Any] = {"items": items}
        if result.scope:
            data["scope"] = result.scope
        return CapabilityObservation(
            capability_id=capability.id,
            status="succeeded",
            output_type=capability.output_type,
            summary=(
                f"Returned {len(items)} scanned source result(s)."
                if items
                else "The term does not occur in this project's scanned source."
            ),
            data=data,
            evidence_refs=[
                {
                    "evidence_id": item.get("id"),
                    "source_type": item.get("source_type"),
                    "project_id": project_id,
                }
                for item in items
            ],
            safe_details={"evidence_count": len(items)},
        )


def source_evidence_capability_handlers() -> tuple[SourceEvidenceHandler, ...]:
    """Executable handler bindings for :func:`source_evidence_capabilities`."""

    return tuple(
        SourceEvidenceHandler(capability) for capability in source_evidence_capabilities()
    )


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _bounded_int(value: Any, *, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value
