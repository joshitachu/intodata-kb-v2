"""Per-project documentation memory that SURVIVES a run.

Why this exists
---------------
Every documentation run used to start from nothing. It did not know what had
changed in the scan since the last run, what a human had corrected, or that
``VISE2AssetCheck`` and ``Vise2AssetCheck`` are two genuinely different Talend
groups rather than a typo. The project was re-taught from scratch every time
and nothing the agent worked out last time survived.

``docs_staging/`` is destroyed and atomically swapped on every run, so memory
cannot live only there. The layout is therefore two-sided:

* **durable home** -- ``output/{project_id}[/{subproject}]/knowledge/``. The
  record. It is a sibling of ``docs/``, not inside it, so it is outside the
  published tree entirely. Never pushed to RAG, never deleted by this module.
* **staged copy** -- ``docs_staging/_knowledge/``. The working copy the agent
  reads AND writes during the run. :func:`persist_project_memory` copies it
  back into the durable home and then REMOVES it from staging.

The staged name is underscore-prefixed for a verified reason, and the two
names are deliberately different. ``RagBridge._read_nested_md_files`` and
``api/routes/projects.py::_count_staged_docs`` both admit a nested markdown
file unless a path segment starts with ``_`` or ``.``. A staged
``knowledge/open-questions.md`` would therefore be pushed into the CLIENT's
RAG corpus and counted as a delivered document -- the agent's private working
notes and unresolved questions are exactly what must never reach a client.
``_knowledge/`` cannot publish by construction, rather than relying on a later
cleanup step. The removal in :func:`persist_project_memory` is then hygiene
and lane-consistency (``_knowledge`` is not on Phase 4's published-staging
allowlist, so the custom lane deletes it anyway; removing it ourselves makes
every lane behave identically and stops a stale staged copy from shadowing the
record), not the leak control.

Design rules
------------
* **Seed honestly.** On a first run the files exist and say, in words, that
  they are empty because this is the first run. Nothing is ever fabricated.
* **A deterministic continuity note.** ``_since_last_run.md`` is written by
  Python, with no model call, and states what changed in the scan since the
  last SUCCESSFUL run. It names designs and groups and reports structural
  counts; it never reproduces a configuration value, so a changed parameter
  shows up as a changed design and not as its new value. That is what keeps a
  credential out of memory even though the note is derived from scan data.
* **Staleness, not blind trust.** Every durable entry carries the scan
  fingerprint it was written against (same idea as the enterprise lane's
  ``phase_state.json`` fingerprint: a changed fingerprint invalidates carried
  state instead of silently reusing it). When the fingerprint changes the
  STAGED copy is annotated for review. The durable file is never annotated, so
  the record stays byte-exact and the annotation can never accumulate.
* **Append-only where several writers share a file.** :func:`append_entry`
  writes one complete block through a single ``O_APPEND`` write, which is the
  primitive that does not lose a concurrent writer's entry.
* **Human corrections outrank agent conclusions.** An entry's ``author``
  field distinguishes ``human`` from ``agent``. A human entry is annotated
  with a softer note that restates its authority, and no code path in this
  module rewrites an entry body.

Not a provider dependency: this module imports nothing from
``mcp_server.providers``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

#: The durable home's directory name, under the project output root and a
#: sibling of ``docs/`` -- outside the published tree.
DURABLE_DIRNAME = "knowledge"

#: The staged working copy's directory name, under ``docs_staging/``.
#: Underscore-prefixed on purpose: see the module docstring. Both names are
#: fixed by contract -- the doc-writer prompts reference these exact paths.
STAGED_DIRNAME = "_knowledge"

#: The six memory files. Order is the order an agent should read them in.
MEMORY_FILES: tuple[str, ...] = (
    "key-notes.md",
    "decisions.md",
    "lessons-learned.md",
    "terminology.md",
    "open-questions.md",
    "known-limitations.md",
)

#: Python-written, run-local, never copied back into the durable home.
SINCE_LAST_RUN_FILENAME = "_since_last_run.md"

#: Durable-only bookkeeping. Underscore-prefixed so that even if a staged copy
#: ever escaped into ``docs/`` the RAG nested-glob would skip it.
SNAPSHOT_FILENAME = "_scan_snapshot.json"

AUTHOR_AGENT = "agent"
AUTHOR_HUMAN = "human"

#: Everything above this line in a memory file is the platform's instructions;
#: everything below it is entries.
ENTRIES_MARKER = "--- entries below; append only, never edit above this line ---"

#: Annotations this module injects into the STAGED copy and strips on the way
#: back. They are a presentation layer, not part of the record.
REVIEW_PREFIX = "> [MEMORY REVIEW]"
NOTE_PREFIX = "> [MEMORY NOTE]"

#: How many items of any one kind the continuity note lists before it
#: summarises the remainder. A 429-design estate must not produce a note that
#: crowds out the evidence it is supposed to point at.
MAX_LISTED_ITEMS = 150

_ENTRY_HEADER_RE = re.compile(r"^###[ \t]+(?P<rest>\S.*?)[ \t]*$")
_FIELD_RE = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_-]*):[ \t]*(?P<value>.*)$")
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z?$")


# --------------------------------------------------------------------------- #
# ASCII discipline
# --------------------------------------------------------------------------- #

def ascii_only(text: str) -> str:
    """Return ``text`` with every non-ASCII codepoint escaped.

    Applied to every string this module writes into staging. The model mirrors
    what it is shown: a published run once leaked CJK characters into a quoted
    XML namespace, turning a real identifier into a false one, and a sibling
    artifact taught the em dash by printing one. A design name is client data
    and may legitimately be non-ASCII, so it is escaped rather than dropped --
    ``backslashreplace`` is reversible and visibly flags the character.
    """
    return text.encode("ascii", "backslashreplace").decode("ascii")


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")


def _atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` via a temp file in the same directory.

    A half-written memory file is worse than a missing one: the next run would
    parse it and trust the fragment.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #

def durable_memory_dir(output_dir: Path) -> Path:
    """The record: ``output/{project_id}[/{subproject}]/knowledge/``."""
    return Path(output_dir) / DURABLE_DIRNAME


def staged_memory_dir(staging_dir: Path) -> Path:
    """The working copy: ``docs_staging/_knowledge/``."""
    return Path(staging_dir) / STAGED_DIRNAME


# --------------------------------------------------------------------------- #
# Scan facts, fingerprint and snapshot
# --------------------------------------------------------------------------- #

class UnitFacts(BaseModel):
    """Structural facts about one scanned unit (a Talend design, a job).

    Counts only. No parameter value, no connection string, no context value
    ever enters this model, which is what makes the derived continuity note
    safe to write even though it is derived from scan data.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    digest: str
    group: str
    components: int = 0
    tmaps: int = 0
    context_groups: int = 0
    calls: int = 0

    def structural_counts(self) -> dict[str, int]:
        return {
            "components": self.components,
            "tmaps": self.tmaps,
            "context_groups": self.context_groups,
            "calls": self.calls,
        }


class ScanSnapshot(BaseModel):
    """The fingerprint snapshot persisted in the durable home."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    project_id: str = ""
    platform: str = ""
    partition: str | None = None
    fingerprint: str = ""
    scan_fingerprint: str = ""
    captured_at: float = 0.0
    captured_at_iso: str = ""
    run_id: int | None = None
    units: dict[str, UnitFacts] = Field(default_factory=dict)

    @property
    def groups(self) -> list[str]:
        return sorted({facts.group for facts in self.units.values()})


class UnitChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit: str
    group: str
    previous_group: str = ""
    dimensions: list[str] = Field(default_factory=list)


class ScanDelta(BaseModel):
    """What changed in the scan since the last successful run."""

    model_config = ConfigDict(extra="forbid")

    first_run: bool = True
    previous_fingerprint: str = ""
    current_fingerprint: str = ""
    previous_captured_at_iso: str = ""
    previous_run_id: int | None = None
    shared_artifacts_changed: bool = False
    unit_count_before: int = 0
    unit_count_after: int = 0
    group_count_before: int = 0
    group_count_after: int = 0
    units_added: list[str] = Field(default_factory=list)
    units_removed: list[str] = Field(default_factory=list)
    units_modified: list[UnitChange] = Field(default_factory=list)
    units_moved: list[UnitChange] = Field(default_factory=list)
    groups_added: list[str] = Field(default_factory=list)
    groups_removed: list[str] = Field(default_factory=list)

    @property
    def unit_level_change(self) -> bool:
        return bool(
            self.units_added
            or self.units_removed
            or self.units_modified
            or self.units_moved
            or self.groups_added
            or self.groups_removed
        )

    @property
    def changed(self) -> bool:
        return (
            self.first_run
            or self.unit_level_change
            or self.shared_artifacts_changed
            or self.previous_fingerprint != self.current_fingerprint
        )


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return default


def _unit_facts_from_job(job: dict) -> tuple[str, UnitFacts]:
    """Canonical unit name plus its structural facts.

    The name rule is the same one ``ScannerService.get_chunk_definitions``
    uses (``job_id`` falling back to ``name``), because a memory keyed on the
    display name would conflate the composite-ZIP copies of a shared
    REFERENCES job. The group rule matches too (empty group => ``Global``).
    """
    name = str(job.get("job_id") or job.get("name") or "unknown")
    group = str(job.get("group") or "Global")
    tmaps = job.get("tmap_data") or []
    xmlmaps = job.get("txmlmap_data") or []
    facts = UnitFacts(
        digest=hashlib.sha256(_canonical_json(job)).hexdigest()[:16],
        group=group,
        components=len(job.get("components") or []),
        tmaps=len(tmaps) + len(xmlmaps),
        context_groups=len(job.get("context_groups") or []),
        calls=len(job.get("trunjob_refs") or []),
    )
    return name, facts


def compute_unit_facts(
    scan_output_dir: Path,
    unit_allowlist: set[str] | frozenset[str] | None = None,
) -> dict[str, UnitFacts]:
    """Read the scan cache and return ``{unit_name: UnitFacts}``.

    Prefers the per-unit ``scan/jobs/{group}/*.json`` layout and falls back to
    the monolithic ``scan_result.json``. ``unit_allowlist`` scopes a composite
    sub-project run exactly as the preprocess lane does, so a partition's
    memory never reports a sibling sub-project's designs as added or removed.
    """
    scan_output_dir = Path(scan_output_dir)
    jobs_dir = scan_output_dir / "scan" / "jobs"
    facts: dict[str, UnitFacts] = {}

    if jobs_dir.is_dir():
        for group_dir in sorted(jobs_dir.iterdir()):
            if not group_dir.is_dir():
                continue
            for job_file in sorted(group_dir.glob("*.json")):
                job = _load_json(job_file, None)
                if not isinstance(job, dict):
                    logger.warning(
                        "Project memory: unreadable scan unit %s -- skipped", job_file
                    )
                    continue
                name, unit = _unit_facts_from_job(job)
                if unit_allowlist is not None and name not in unit_allowlist:
                    continue
                facts[name] = unit
        return facts

    mono = scan_output_dir / "scan_result.json"
    if not mono.is_file():
        return facts
    payload = _load_json(mono, None)
    if not isinstance(payload, dict):
        return facts
    for job in payload.get("jobs") or []:
        if not isinstance(job, dict):
            continue
        name, unit = _unit_facts_from_job(job)
        if unit_allowlist is not None and name not in unit_allowlist:
            continue
        facts[name] = unit
    return facts


#: Shared scan artifacts that change without any single unit changing: context
#: groups, connection metadata, routines, the landscape and the file index. A
#: run must be told when only these moved, or a doc writer concludes "nothing
#: changed" while the environment underneath it did.
_SHARED_SCAN_ARTIFACTS: tuple[str, ...] = (
    "scan/manifest.json",
    "scan/contexts.json",
    "scan/connections.json",
    "scan/routines.json",
    "scan/landscape.json",
    "scan/lineage.json",
    "scan/services.json",
    "scan/structures.json",
    "scan/tdm_maps.json",
)


def compute_shared_artifact_fingerprint(scan_output_dir: Path) -> str:
    """Hash the scan artifacts that are not attributable to a single unit.

    Returns ``""`` when none of them exist (a monolithic-only scan cache), in
    which case shared-artifact drift is reported as unknown rather than as
    "no change".
    """
    scan_output_dir = Path(scan_output_dir)
    digest = hashlib.sha256()
    found = False
    for relative in _SHARED_SCAN_ARTIFACTS:
        path = scan_output_dir / relative
        if not path.is_file():
            continue
        payload = _load_json(path, None)
        if payload is None:
            continue
        found = True
        digest.update(relative.encode("ascii"))
        digest.update(b"\0")
        digest.update(_canonical_json(payload))
        digest.update(b"\0")
    return digest.hexdigest() if found else ""


def compute_memory_fingerprint(
    units: dict[str, UnitFacts],
    *,
    platform: str,
    partition_name: str | None,
    shared_fingerprint: str = "",
) -> str:
    """The fingerprint every durable entry is stamped with.

    Sensitive to the partition scope and to the shared artifacts, not only to
    the units: a remembered conclusion about a design whose context group
    changed underneath it is exactly as stale as one about a design whose
    components changed.
    """
    digest = hashlib.sha256()
    digest.update(_canonical_json({
        "schema_version": SCHEMA_VERSION,
        "platform": platform,
        "partition": partition_name,
        "shared": shared_fingerprint,
        "units": {
            name: units[name].model_dump() for name in sorted(units)
        },
    }))
    return digest.hexdigest()


def short_fingerprint(fingerprint: str) -> str:
    return fingerprint[:12]


def same_fingerprint(left: str, right: str) -> bool:
    """True when two fingerprints agree, tolerating the short display form."""
    if not left or not right:
        return False
    shortest = min(len(left), len(right))
    return left[:shortest] == right[:shortest]


def load_snapshot(durable_dir: Path) -> ScanSnapshot | None:
    path = Path(durable_dir) / SNAPSHOT_FILENAME
    if not path.is_file():
        return None
    payload = _load_json(path, None)
    if not isinstance(payload, dict):
        logger.warning("Project memory: unreadable snapshot %s -- treating as absent", path)
        return None
    try:
        return ScanSnapshot.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 - a bad snapshot is not a run failure
        logger.warning("Project memory: invalid snapshot %s (%s) -- treating as absent", path, exc)
        return None


def save_snapshot(durable_dir: Path, snapshot: ScanSnapshot) -> Path:
    path = Path(durable_dir) / SNAPSHOT_FILENAME
    _atomic_write_text(
        path, json.dumps(snapshot.model_dump(), indent=2, sort_keys=True) + "\n"
    )
    return path


def build_snapshot(
    units: dict[str, UnitFacts],
    *,
    project_id: str,
    platform: str,
    partition_name: str | None,
    shared_fingerprint: str,
    run_id: int | None,
) -> ScanSnapshot:
    now = time.time()
    return ScanSnapshot(
        project_id=project_id,
        platform=platform,
        partition=partition_name,
        fingerprint=compute_memory_fingerprint(
            units,
            platform=platform,
            partition_name=partition_name,
            shared_fingerprint=shared_fingerprint,
        ),
        scan_fingerprint=shared_fingerprint,
        captured_at=now,
        captured_at_iso=_utc_now_iso(),
        run_id=run_id,
        units=dict(units),
    )


def diff_snapshots(previous: ScanSnapshot | None, current: ScanSnapshot) -> ScanDelta:
    """Compare two snapshots. Pure function, no I/O."""
    current_groups = set(current.groups)
    if previous is None:
        return ScanDelta(
            first_run=True,
            current_fingerprint=current.fingerprint,
            unit_count_after=len(current.units),
            group_count_after=len(current_groups),
        )

    previous_groups = set(previous.groups)
    previous_names = set(previous.units)
    current_names = set(current.units)

    modified: list[UnitChange] = []
    moved: list[UnitChange] = []
    for name in sorted(previous_names & current_names):
        before = previous.units[name]
        after = current.units[name]
        if before.group != after.group:
            moved.append(
                UnitChange(
                    unit=ascii_only(name),
                    group=ascii_only(after.group),
                    previous_group=ascii_only(before.group),
                )
            )
        if before.digest == after.digest:
            continue
        dimensions: list[str] = []
        before_counts = before.structural_counts()
        after_counts = after.structural_counts()
        for key in ("components", "tmaps", "context_groups", "calls"):
            if before_counts[key] != after_counts[key]:
                dimensions.append(f"{key} {before_counts[key]} -> {after_counts[key]}")
        if not dimensions:
            # Same structural counts, different content: a rewritten
            # expression, a renamed component, a changed parameter. Named as
            # "content" rather than guessed at, and deliberately without the
            # value that changed.
            dimensions.append("content changed with no change in structural counts")
        modified.append(
            UnitChange(
                unit=ascii_only(name),
                group=ascii_only(after.group),
                previous_group=ascii_only(before.group),
                dimensions=dimensions,
            )
        )

    shared_changed = bool(
        previous.scan_fingerprint
        and current.scan_fingerprint
        and previous.scan_fingerprint != current.scan_fingerprint
    )

    return ScanDelta(
        first_run=False,
        previous_fingerprint=previous.fingerprint,
        current_fingerprint=current.fingerprint,
        previous_captured_at_iso=previous.captured_at_iso,
        previous_run_id=previous.run_id,
        shared_artifacts_changed=shared_changed,
        unit_count_before=len(previous.units),
        unit_count_after=len(current.units),
        group_count_before=len(previous_groups),
        group_count_after=len(current_groups),
        units_added=[ascii_only(n) for n in sorted(current_names - previous_names)],
        units_removed=[ascii_only(n) for n in sorted(previous_names - current_names)],
        units_modified=modified,
        units_moved=moved,
        groups_added=[ascii_only(g) for g in sorted(current_groups - previous_groups)],
        groups_removed=[ascii_only(g) for g in sorted(previous_groups - current_groups)],
    )


# --------------------------------------------------------------------------- #
# The continuity note
# --------------------------------------------------------------------------- #

def _listed(items: Iterable[str]) -> list[str]:
    values = list(items)
    lines: list[str] = []
    for value in values[:MAX_LISTED_ITEMS]:
        lines.append(f"- {value}")
    remainder = len(values) - MAX_LISTED_ITEMS
    if remainder > 0:
        lines.append(f"- ... and {remainder} more (full set in {SNAPSHOT_FILENAME})")
    return lines


def render_since_last_run(
    delta: ScanDelta,
    *,
    project_id: str,
    platform: str,
    partition_name: str | None,
) -> str:
    """Render the continuity note. Deterministic, ASCII, no model call."""
    scope = partition_name or "whole project"
    scan_unreadable = (
        not delta.first_run
        and delta.unit_count_after == 0
        and delta.unit_count_before > 0
    )
    lines: list[str] = [
        "# Since The Last Run",
        "",
        "Written by the platform in Python before any model call. No model",
        "produced this file and it contains no interpretation.",
        "",
        f"- Project: {ascii_only(project_id)}",
        f"- Platform: {ascii_only(platform)}",
        f"- Scope: {ascii_only(scope)}",
        f"- Current scan fingerprint: {short_fingerprint(delta.current_fingerprint)}",
    ]
    if delta.first_run:
        lines.append("- Previous successful run: none")
    else:
        previous = delta.previous_captured_at_iso or "unknown time"
        run_note = (
            f", run {delta.previous_run_id}" if delta.previous_run_id is not None else ""
        )
        lines.append(
            f"- Previous successful run: {ascii_only(previous)}{run_note} "
            f"(fingerprint {short_fingerprint(delta.previous_fingerprint)})"
        )

    lines += ["", "## Verdict", ""]
    if delta.first_run:
        lines += [
            "FIRST RUN for this scope. There is no previous successful",
            "documentation run to compare against, so nothing can be reported as",
            "changed and nothing is remembered yet. Every design is new to this",
            "project's memory. Do not describe anything as 'unchanged', 'as",
            "before' or 'still' -- there is no before.",
            "",
            (
                f"Current inventory: {delta.unit_count_after} designs across "
                f"{delta.group_count_after} groups."
            ),
        ]
    elif not delta.changed:
        lines += [
            "NO CHANGE. The scan is identical to the one the last successful run",
            "read. Memory recorded against fingerprint",
            f"{short_fingerprint(delta.previous_fingerprint)} still applies, and",
            "this run should spend its effort on the gaps recorded in",
            "open-questions.md rather than re-deriving the estate.",
        ]
    elif scan_unreadable:
        # An estate does not usually vanish. A scan cache that could not be
        # read looks identical to one, and the difference matters: reporting
        # 142 deletions to a doc writer is a fabricated finding, not a cautious
        # one.
        lines += [
            "SCAN UNREADABLE OR EMPTY. The last successful run saw",
            f"{delta.unit_count_before} designs and this run found none. That is",
            "far more likely to be an unreadable or mis-scoped scan cache than a",
            "deleted estate. Do NOT report any design as removed on the strength",
            "of this file, and do not treat the absence as evidence. Raise it as",
            "a run problem instead.",
        ]
    else:
        summary = (
            f"SCAN CHANGED. {len(delta.units_added)} design(s) added, "
            f"{len(delta.units_removed)} removed, "
            f"{len(delta.units_modified)} modified, "
            f"{len(delta.units_moved)} moved between groups; "
            f"{len(delta.groups_added)} group(s) added, "
            f"{len(delta.groups_removed)} removed."
        )
        lines.append(summary)
        if delta.shared_artifacts_changed and not delta.unit_level_change:
            lines += [
                "",
                "No design-level change was detected, but the shared scan",
                "artifacts did change (context groups, connection metadata,",
                "routines, landscape or lineage). Treat configuration and",
                "environment statements in the existing documents as unverified",
                "for this run; design-level conclusions still hold.",
            ]
        elif delta.shared_artifacts_changed:
            lines += [
                "",
                "The shared scan artifacts changed as well (context groups,",
                "connection metadata, routines, landscape or lineage), so",
                "configuration and environment statements need re-checking even",
                "for designs that are not listed below.",
            ]

    lines += [
        "",
        "## Counts",
        "",
        "| Dimension | Previous | Current |",
        "|-----------|----------|---------|",
        (
            f"| Designs | {'n/a' if delta.first_run else delta.unit_count_before} "
            f"| {delta.unit_count_after} |"
        ),
        (
            f"| Groups | {'n/a' if delta.first_run else delta.group_count_before} "
            f"| {delta.group_count_after} |"
        ),
    ]

    if not delta.first_run and not scan_unreadable:
        sections: list[tuple[str, list[str]]] = [
            ("Groups added", _listed(delta.groups_added)),
            ("Groups removed", _listed(delta.groups_removed)),
            ("Designs added", _listed(delta.units_added)),
            ("Designs removed", _listed(delta.units_removed)),
        ]
        for title, body in sections:
            lines += ["", f"## {title}", ""]
            lines += body or ["None."]

        lines += ["", "## Designs modified", ""]
        if delta.units_modified:
            lines += [
                "| Design | Group | What changed |",
                "|--------|-------|--------------|",
            ]
            for change in delta.units_modified[:MAX_LISTED_ITEMS]:
                detail = "; ".join(change.dimensions) or "content"
                lines.append(f"| {change.unit} | {change.group} | {detail} |")
            remainder = len(delta.units_modified) - MAX_LISTED_ITEMS
            if remainder > 0:
                lines.append(f"| ... | ... | and {remainder} more |")
        else:
            lines.append("None.")

        lines += ["", "## Designs moved between groups", ""]
        if delta.units_moved:
            lines += [
                "| Design | Previous group | Current group |",
                "|--------|----------------|---------------|",
            ]
            for change in delta.units_moved[:MAX_LISTED_ITEMS]:
                lines.append(f"| {change.unit} | {change.previous_group} | {change.group} |")
            remainder = len(delta.units_moved) - MAX_LISTED_ITEMS
            if remainder > 0:
                lines.append(f"| ... | ... | and {remainder} more |")
        else:
            lines.append("None.")

    lines += ["", "## How to use this file", ""]
    if scan_unreadable:
        lines += [
            "Nothing here is usable as evidence. Report the run problem; do not",
            "document an estate this run could not read, and do not describe any",
            "design as removed.",
        ]
    elif delta.first_run:
        lines += [
            "Nothing is listed above because there is nothing to compare against.",
            "Work the estate from the evidence, and record what you establish in",
            "the memory files beside this one so the next run does not have to",
            "establish it again. open-questions.md is where an uncertainty goes;",
            "it does not have to become confident prose in a document.",
        ]
    elif not delta.changed:
        lines += [
            "No design needs re-deriving. Spend this run on the gaps already",
            "recorded in open-questions.md and known-limitations.md, and on the",
            "memory entries flagged for review, rather than re-reading an estate",
            "that has not moved.",
        ]
    else:
        lines += [
            "Start with the designs named above. They are the only part of the",
            "estate that provably needs fresh analysis; anything not listed was",
            "byte-identical to the scan the last successful run read. Memory",
            "entries stamped with an older fingerprint are annotated in the memory",
            "files themselves -- re-verify those before reusing them.",
        ]
    lines += [
        "",
        "## What this file does not tell you",
        "",
        "It names designs and groups and reports structural counts only. It",
        "never reproduces a parameter value, a connection string, a context",
        "value or a credential, so a changed configuration value appears here as",
        "a changed design and not as its new value. If you need the value, read",
        "the redacted evidence for that design.",
        "",
    ]
    return ascii_only("\n".join(lines))


# --------------------------------------------------------------------------- #
# Memory files: seeding, entries, annotation
# --------------------------------------------------------------------------- #

_SEED_PURPOSE: dict[str, tuple[str, tuple[str, ...]]] = {
    "key-notes.md": (
        "Key Notes",
        (
            "Durable facts about this project that are expensive to re-derive and",
            "do not belong in a client document: which estate this is, how its",
            "groups relate, which designs are the spine of the integration, which",
            "parts of the scan are thin and why.",
            "",
            "Write a note here when you spent real effort establishing something",
            "that the next run would otherwise have to establish again.",
        ),
    ),
    "decisions.md": (
        "Decisions",
        (
            "Decisions taken about how this project is documented, and the reason",
            "for each one: scope boundaries, how an ambiguous design was",
            "classified, which of two contradictory sources was treated as",
            "authoritative.",
            "",
            "A decision recorded by a human outranks a decision recorded by an",
            "agent. Stamp your entry with 'author: human' when a person made the",
            "call. No run rewrites an entry in this file.",
        ),
    ),
    "lessons-learned.md": (
        "Lessons Learned",
        (
            "Corrections. What a previous run got wrong, what the right answer",
            "was, and what to do differently. This is where a human's correction",
            "lands, and it is the highest-authority file in this directory.",
            "",
            "A lesson recorded by a human outranks anything an agent concluded.",
            "Stamp your entry with 'author: human' when a person wrote it. No run",
            "rewrites an entry in this file.",
        ),
    ),
    "terminology.md": (
        "Terminology",
        (
            "The project's own vocabulary: business object names, system aliases,",
            "and the names that look like typos but are not.",
            "",
            "Worked example of why this file exists: on one Talend estate",
            "'VISE2AssetCheck' and 'Vise2AssetCheck' are two genuinely different",
            "groups, not one group spelled two ways. Normalising them would have",
            "merged two separate integrations. Record that kind of distinction",
            "here, with the evidence that it is a distinction.",
        ),
    ),
    "open-questions.md": (
        "Open Questions",
        (
            "Questions this project has not answered yet, and things you were not",
            "sure about. This file is the channel for doubt: an uncertainty",
            "recorded here does not have to be laundered into confident prose in",
            "a client document.",
            "",
            "Several agents append to this file at the same time during a run.",
            "Append a complete entry at the end of the file in one write. Never",
            "edit or delete an existing entry, and never rewrite the whole file.",
        ),
    ),
    "known-limitations.md": (
        "Known Limitations",
        (
            "What cannot be known about this project from the evidence available,",
            "and why: what the scanner does not capture for this platform, which",
            "runtime facts no source has, which questions need a person to answer.",
            "",
            "A limitation recorded here is a reason a document may state that",
            "something is unavailable. It is not a defect and it is not a licence",
            "to guess.",
        ),
    ),
}


def _seed_text(filename: str, *, fingerprint: str, first_run: bool) -> str:
    title, purpose = _SEED_PURPOSE[filename]
    lines = [f"# {title}", "", "WHAT THIS IS", ""]
    lines += [f"  {line}" if line else "" for line in purpose]
    lines += [
        "",
        "HOW TO WRITE HERE",
        "",
        "  Append a new entry at the end of this file. One entry is a header",
        "  line followed by free text:",
        "",
        "    ### <UTC timestamp> | author: agent | scan: <fingerprint>",
        "    <your text, as many lines as you need>",
        "",
        "  Use 'author: human' only for text a person wrote. The fingerprint is",
        f"  the scan this run read: {short_fingerprint(fingerprint) or 'unknown'}.",
        "  Leaving it out is allowed; the platform fills it in, which is why an",
        "  entry can always be told apart from a stale one later.",
        "",
        "STATUS",
        "",
    ]
    if first_run:
        lines += [
            "  Empty. This is the first documentation run for this project, so",
            "  there is nothing remembered yet. Do not invent history and do not",
            "  refer to a previous run.",
        ]
    else:
        lines += [
            "  Seeded during a later run because the file was missing. Any entry",
            "  written before this run was not recovered; treat the absence as",
            "  absence of a record, not as absence of a finding.",
        ]
    lines += ["", ENTRIES_MARKER, ""]
    return ascii_only("\n".join(lines))


def seed_memory(
    durable_dir: Path, *, fingerprint: str = "", first_run: bool = True
) -> list[str]:
    """Create any missing memory file. Never touches an existing one."""
    durable_dir = Path(durable_dir)
    durable_dir.mkdir(parents=True, exist_ok=True)
    seeded: list[str] = []
    for filename in MEMORY_FILES:
        path = durable_dir / filename
        if path.exists():
            continue
        _atomic_write_text(
            path, _seed_text(filename, fingerprint=fingerprint, first_run=first_run)
        )
        seeded.append(filename)
    return seeded


@dataclass(frozen=True)
class MemoryEntry:
    """One parsed entry. ``line`` is 1-based within the whole file."""

    timestamp: str
    author: str
    scan_fingerprint: str
    title: str
    line: int
    body: str
    header: str = ""

    @property
    def human_authored(self) -> bool:
        return self.author.lower() == AUTHOR_HUMAN

    @property
    def identity(self) -> tuple[str, str, str, str, str]:
        """What makes two entries the same entry.

        Deliberately the parsed fields and not the raw header line: the two
        sides of a merge may have rendered the same entry with fields in a
        different order, and treating that as two entries would duplicate it
        on every run.
        """
        return (
            self.timestamp,
            self.author.lower(),
            short_fingerprint(self.scan_fingerprint),
            self.title,
            self.body.strip(),
        )

    def render(self) -> str:
        header = self.header or entry_header(
            timestamp=self.timestamp,
            author=self.author,
            scan_fingerprint=self.scan_fingerprint,
            title=self.title,
        )
        return f"{header}\n{self.body}\n" if self.body else f"{header}\n"


def split_preamble(text: str) -> tuple[str, str]:
    """Split a memory file into (preamble including the marker line, entries)."""
    index = text.find(ENTRIES_MARKER)
    if index == -1:
        return "", text
    newline = text.find("\n", index)
    if newline == -1:
        return text, ""
    return text[: newline + 1], text[newline + 1 :]


def _parse_header(rest: str) -> tuple[str, str, str, str]:
    """Parse an entry header body into (timestamp, author, scan, title)."""
    timestamp = ""
    author = ""
    scan = ""
    titles: list[str] = []
    for raw in rest.split("|"):
        field = raw.strip()
        if not field:
            continue
        match = _FIELD_RE.match(field)
        if match:
            key = match.group("key").lower()
            value = match.group("value").strip()
            if key == "author":
                author = value
                continue
            if key == "scan":
                scan = value
                continue
            titles.append(field)
            continue
        if not timestamp and _ISO_RE.match(field):
            timestamp = field
            continue
        titles.append(field)
    return timestamp, author, scan, " | ".join(titles)


def parse_entries(text: str) -> list[MemoryEntry]:
    """Parse the entries region of a memory file.

    Tolerant by design: a header missing ``author`` or ``scan`` still parses,
    because the alternative is discarding something an agent recorded.
    """
    preamble, entries_text = split_preamble(text)
    offset = preamble.count("\n")
    lines = entries_text.split("\n")
    entries: list[MemoryEntry] = []
    pending: list[tuple[int, str, str, str, str, str]] = []
    bodies: list[list[str]] = []
    for index, line in enumerate(lines):
        match = _ENTRY_HEADER_RE.match(line)
        if match:
            timestamp, author, scan, title = _parse_header(match.group("rest"))
            pending.append((offset + index + 1, timestamp, author, scan, title, line))
            bodies.append([])
        elif pending:
            bodies[-1].append(line)
    for (line_no, timestamp, author, scan, title, header), body in zip(pending, bodies):
        entries.append(
            MemoryEntry(
                timestamp=timestamp,
                author=author or AUTHOR_AGENT,
                scan_fingerprint=scan,
                title=title,
                line=line_no,
                body="\n".join(body).strip("\n"),
                header=header,
            )
        )
    return entries


def lead_text(text: str) -> str:
    """Text between the entries marker and the first entry header.

    Anything a writer put there is unstructured but is still something it
    chose to record, so the merge preserves it rather than dropping it.
    """
    _, entries_text = split_preamble(text)
    lines: list[str] = []
    for line in entries_text.split("\n"):
        if _ENTRY_HEADER_RE.match(line):
            break
        lines.append(line)
    return "\n".join(lines).strip("\n")


def merge_memory_text(durable_text: str, staged_text: str, *, fingerprint: str) -> str:
    """Union the staged copy INTO the record; never subtract from the record.

    An overwrite is wrong here, and was a real defect: a human can write a
    correction straight into the durable file at any time, including while a
    run is in flight, and a blind copy-back of the staged mirror silently
    discards it. So:

    * every entry already in the record stays, in its original order;
    * every staged entry the record does not have is appended;
    * a deletion in the staged copy does NOT propagate -- these files are
      append-only by contract, so a missing entry means "this writer did not
      have it", not "remove it";
    * the record's preamble wins, so an agent cannot rewrite the instructions;
    * unstructured staged text is preserved as an explicit entry rather than
      dropped.

    Entry identity is the parsed fields (see :attr:`MemoryEntry.identity`), so
    re-merging the same pair is idempotent.
    """
    staged_text = normalize_entries(strip_annotations(staged_text), fingerprint=fingerprint)
    if not durable_text.strip():
        return staged_text

    durable_text = strip_annotations(durable_text)
    preamble, _ = split_preamble(durable_text)
    durable_entries = parse_entries(durable_text)
    staged_entries = parse_entries(staged_text)
    known = {entry.identity for entry in durable_entries}

    parts: list[str] = [preamble]
    durable_lead = lead_text(durable_text)
    if durable_lead:
        parts.append(durable_lead + "\n\n")
    for entry in durable_entries:
        parts.append(entry.render() + "\n")
    for entry in staged_entries:
        if entry.identity in known:
            continue
        known.add(entry.identity)
        parts.append(entry.render() + "\n")

    staged_lead = lead_text(staged_text)
    if staged_lead and staged_lead not in durable_lead:
        recovered = MemoryEntry(
            timestamp=_utc_now_iso(),
            author=AUTHOR_AGENT,
            scan_fingerprint=fingerprint,
            title="unstructured note recovered from the run",
            line=0,
            body=staged_lead,
        )
        if recovered.identity not in known:
            parts.append(recovered.render() + "\n")

    return "".join(parts)


def entry_header(
    *, timestamp: str, author: str, scan_fingerprint: str, title: str = ""
) -> str:
    header = f"### {timestamp} | author: {author} | scan: {short_fingerprint(scan_fingerprint)}"
    if title:
        header += f" | {title}"
    return ascii_only(header)


def append_entry(
    path: Path,
    body: str,
    *,
    author: str = AUTHOR_AGENT,
    scan_fingerprint: str = "",
    title: str = "",
    timestamp: str | None = None,
) -> None:
    """Append one complete entry through a single ``O_APPEND`` write.

    Several sub-agents append to ``open-questions.md`` concurrently. One
    ``os.write`` of the whole block under ``O_APPEND`` is the primitive that
    does not interleave or lose an entry; a read-modify-write would drop
    whichever writer lost the race.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    block = (
        entry_header(
            timestamp=timestamp or _utc_now_iso(),
            author=author,
            scan_fingerprint=scan_fingerprint,
            title=title,
        )
        + "\n"
        + ascii_only(body.strip("\n"))
        + "\n\n"
    )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, block.encode("ascii"))
    finally:
        os.close(fd)


def strip_annotations(text: str) -> str:
    """Remove the annotations this module injects into the staged copy."""
    kept = [
        line
        for line in text.split("\n")
        if not line.lstrip().startswith((REVIEW_PREFIX, NOTE_PREFIX))
    ]
    return "\n".join(kept)


def annotate_for_review(text: str, current_fingerprint: str) -> tuple[str, int]:
    """Annotate the STAGED copy; return ``(text, flagged_count)``.

    A remembered conclusion about a design that has since changed is worse than
    no memory, so an entry written against a different scan fingerprint is
    marked rather than silently trusted. Bodies are never touched and the
    durable file is never annotated, so the record stays byte-exact.
    """
    text = strip_annotations(text)
    preamble, entries_text = split_preamble(text)
    lines = entries_text.split("\n")
    out: list[str] = []
    flagged = 0
    current_short = short_fingerprint(current_fingerprint)
    for line in lines:
        out.append(line)
        match = _ENTRY_HEADER_RE.match(line)
        if not match:
            continue
        _, author, scan, _ = _parse_header(match.group("rest"))
        author = (author or AUTHOR_AGENT).lower()
        if scan and same_fingerprint(scan, current_fingerprint):
            continue
        flagged += 1
        if not scan:
            out.append(
                f"{NOTE_PREFIX} This entry carries no scan fingerprint, so it cannot "
                f"be checked against the current scan ({current_short}). Confirm it "
                "against current evidence before relying on it."
            )
        elif author == AUTHOR_HUMAN:
            out.append(
                f"{NOTE_PREFIX} Human-authored against scan {short_fingerprint(scan)}; "
                f"the current scan is {current_short}. A human correction outranks an "
                "agent conclusion, so treat this as authoritative unless current "
                "evidence directly contradicts it -- and say so explicitly if it does."
            )
        else:
            out.append(
                f"{REVIEW_PREFIX} Written against scan {short_fingerprint(scan)}; the "
                f"current scan is {current_short}. Re-verify against current evidence "
                "before reusing this, and do not cite it as established fact until you "
                "have."
            )
    return preamble + "\n".join(out), flagged


def normalize_entries(text: str, *, fingerprint: str, timestamp: str | None = None) -> str:
    """Complete entry headers that the writer left partial.

    An existing ``scan:`` value is never overwritten, so re-persisting is
    idempotent and an old entry keeps the fingerprint it was actually written
    against.
    """
    preamble, entries_text = split_preamble(text)
    stamp = timestamp or _utc_now_iso()
    out: list[str] = []
    for line in entries_text.split("\n"):
        match = _ENTRY_HEADER_RE.match(line)
        if not match:
            out.append(line)
            continue
        parsed_ts, author, scan, title = _parse_header(match.group("rest"))
        out.append(
            entry_header(
                timestamp=parsed_ts or stamp,
                author=author or AUTHOR_AGENT,
                scan_fingerprint=scan or fingerprint,
                title=title,
            )
        )
    return preamble + "\n".join(out)


# --------------------------------------------------------------------------- #
# Stage / persist
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class MemoryStageResult:
    durable_dir: Path
    staged_dir: Path
    fingerprint: str
    snapshot: ScanSnapshot
    delta: ScanDelta
    seeded_files: tuple[str, ...]
    entries_total: int
    entries_flagged: int
    adopted_orphan: bool
    unit_names: tuple[str, ...]
    groups: tuple[str, ...]

    def summary_line(self) -> str:
        if self.delta.first_run:
            change = "first run for this scope, nothing remembered yet"
        elif not self.delta.changed:
            change = "scan unchanged since the last successful run"
        else:
            change = (
                f"{len(self.delta.units_added)} design(s) added, "
                f"{len(self.delta.units_removed)} removed, "
                f"{len(self.delta.units_modified)} modified"
            )
        return (
            f"Project memory staged ({short_fingerprint(self.fingerprint)}): "
            f"{self.entries_total} entr{'y' if self.entries_total == 1 else 'ies'}, "
            f"{self.entries_flagged} flagged for review; {change}"
        )


@dataclass(frozen=True)
class MemoryPersistResult:
    durable_dir: Path
    persisted_files: tuple[str, ...]
    entries_before: int
    entries_after: int
    snapshot_advanced: bool
    staged_removed: bool

    @property
    def new_entries(self) -> int:
        return max(0, self.entries_after - self.entries_before)

    def summary_line(self) -> str:
        return (
            f"Project memory persisted: {len(self.persisted_files)} file(s), "
            f"{self.new_entries} new entr{'y' if self.new_entries == 1 else 'ies'}, "
            f"snapshot {'advanced' if self.snapshot_advanced else 'unchanged'}"
        )


def _memory_markdown_files(directory: Path) -> list[Path]:
    """Memory documents in ``directory``, excluding run-local internals.

    Any ``*.md`` that is not underscore-prefixed counts, so a memory file an
    agent invents (``supplier-quirks.md``) persists instead of being silently
    dropped at the end of the run.
    """
    if not directory.is_dir():
        return []
    return sorted(
        path
        for path in directory.glob("*.md")
        if path.is_file() and not path.name.startswith(("_", "."))
    )


def _count_entries(directory: Path) -> int:
    total = 0
    for path in _memory_markdown_files(directory):
        try:
            total += len(parse_entries(path.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            continue
    return total


def _copy_back(staged_dir: Path, durable_dir: Path, *, fingerprint: str) -> list[str]:
    """Merge the staged memory files into the durable home.

    Merge, not copy: see :func:`merge_memory_text`. The record is only ever
    added to.
    """
    durable_dir.mkdir(parents=True, exist_ok=True)
    persisted: list[str] = []
    for path in _memory_markdown_files(staged_dir):
        try:
            staged_text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("Project memory: cannot read staged %s (%s)", path, exc)
            continue
        target = durable_dir / path.name
        durable_text = ""
        if target.is_file():
            try:
                durable_text = target.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                logger.warning(
                    "Project memory: cannot read durable %s (%s) -- the staged copy "
                    "is written as-is rather than merged",
                    target,
                    exc,
                )
        merged = merge_memory_text(durable_text, staged_text, fingerprint=fingerprint)
        _atomic_write_text(target, ascii_only(merged))
        persisted.append(path.name)
    return persisted


def stage_project_memory(
    *,
    output_dir: Path,
    staging_dir: Path,
    project_id: str,
    scan_output_dir: Path,
    platform: str,
    partition_name: str | None = None,
    unit_allowlist: set[str] | frozenset[str] | None = None,
    run_id: int | None = None,
) -> MemoryStageResult:
    """Seed, stage and annotate this project's memory for one run.

    Steps, in order:

    1. adopt an orphaned staged copy left by a run that died between the
       provider returning and the persist step (resume safety: adopting is
       idempotent because the staged copy is the durable copy plus appends,
       so it cannot duplicate an entry);
    2. seed any missing memory file, honestly;
    3. compute the current scan facts and fingerprint and diff them against
       the persisted snapshot;
    4. write the annotated staged copies and the continuity note.

    The snapshot is NOT advanced here. A failed run must not move the baseline
    and lose the delta; :func:`persist_project_memory` advances it on success.
    """
    output_dir = Path(output_dir)
    staging_dir = Path(staging_dir)
    durable_dir = durable_memory_dir(output_dir)
    staged_dir = staged_memory_dir(staging_dir)

    previous = load_snapshot(durable_dir)

    adopted = False
    orphans = _memory_markdown_files(staged_dir)
    if orphans:
        adopted = True
        logger.warning(
            "Project memory: adopting %d orphaned staged file(s) from %s left by a "
            "previous run before re-staging",
            len(orphans),
            staged_dir,
        )
        _copy_back(
            staged_dir,
            durable_dir,
            fingerprint=previous.fingerprint if previous else "",
        )
    if staged_dir.exists():
        shutil.rmtree(staged_dir, ignore_errors=True)

    units = compute_unit_facts(scan_output_dir, unit_allowlist=unit_allowlist)
    shared_fingerprint = compute_shared_artifact_fingerprint(Path(scan_output_dir))
    snapshot = build_snapshot(
        units,
        project_id=project_id,
        platform=platform,
        partition_name=partition_name,
        shared_fingerprint=shared_fingerprint,
        run_id=run_id,
    )
    delta = diff_snapshots(previous, snapshot)

    seeded = seed_memory(
        durable_dir, fingerprint=snapshot.fingerprint, first_run=previous is None
    )

    staged_dir.mkdir(parents=True, exist_ok=True)
    entries_total = 0
    entries_flagged = 0
    for path in _memory_markdown_files(durable_dir):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("Project memory: cannot read durable %s (%s)", path, exc)
            continue
        entries_total += len(parse_entries(text))
        annotated, flagged = annotate_for_review(text, snapshot.fingerprint)
        entries_flagged += flagged
        _atomic_write_text(staged_dir / path.name, ascii_only(annotated))

    _atomic_write_text(
        staged_dir / SINCE_LAST_RUN_FILENAME,
        render_since_last_run(
            delta,
            project_id=project_id,
            platform=platform,
            partition_name=partition_name,
        ),
    )

    return MemoryStageResult(
        durable_dir=durable_dir,
        staged_dir=staged_dir,
        fingerprint=snapshot.fingerprint,
        snapshot=snapshot,
        delta=delta,
        seeded_files=tuple(seeded),
        entries_total=entries_total,
        entries_flagged=entries_flagged,
        adopted_orphan=adopted,
        unit_names=tuple(sorted(units)),
        groups=tuple(snapshot.groups),
    )


def persist_project_memory(
    *,
    output_dir: Path,
    staging_dir: Path,
    snapshot: ScanSnapshot | None = None,
    success: bool = False,
) -> MemoryPersistResult:
    """Copy the staged memory back into the durable home and clear staging.

    Always copies back, on success and on failure alike -- what the agent
    learned on a run that later failed is still learned. Only a SUCCESSFUL run
    advances the snapshot, so a failed run leaves the continuity baseline where
    it was and the next run reports the same delta.

    Always removes ``docs_staging/knowledge/``. See the module docstring: the
    RAG nested-document glob skips only ``_``-prefixed segments, so a surviving
    ``knowledge/`` directory would be published into the client corpus.
    """
    output_dir = Path(output_dir)
    staging_dir = Path(staging_dir)
    durable_dir = durable_memory_dir(output_dir)
    staged_dir = staged_memory_dir(staging_dir)

    fingerprint = snapshot.fingerprint if snapshot is not None else ""
    entries_before = _count_entries(durable_dir)

    persisted: list[str] = []
    if _memory_markdown_files(staged_dir):
        persisted = _copy_back(staged_dir, durable_dir, fingerprint=fingerprint)
    elif staged_dir.exists():
        logger.info(
            "Project memory: nothing staged under %s to persist (the run's cleanup "
            "phase removes unpublished staging directories); the durable home at %s "
            "is left as it is",
            staged_dir,
            durable_dir,
        )
    else:
        logger.info(
            "Project memory: %s is absent, so nothing was copied back; the durable "
            "home at %s is left as it is",
            staged_dir,
            durable_dir,
        )

    entries_after = _count_entries(durable_dir)

    snapshot_advanced = False
    if success and snapshot is not None:
        save_snapshot(durable_dir, snapshot)
        snapshot_advanced = True

    staged_removed = False
    if staged_dir.exists():
        shutil.rmtree(staged_dir, ignore_errors=True)
        staged_removed = not staged_dir.exists()

    return MemoryPersistResult(
        durable_dir=durable_dir,
        persisted_files=tuple(persisted),
        entries_before=entries_before,
        entries_after=entries_after,
        snapshot_advanced=snapshot_advanced,
        staged_removed=staged_removed,
    )
