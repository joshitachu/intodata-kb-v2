"""Stage the agent's workspace. The ONE producer of the on-disk contract.

Nexus parses, sanitises and hashes; this module turns the result into
something readable and refuses to stage anything it cannot prove is safe.
Nothing here calls a model, and nothing here decides what the estate means.

Four rules, each of which cost a failed run to learn:

**No source is staged, and the rule is on BYTES rather than on filenames.**
No ``.item``, no ``.properties``, no ``sanitized/``. That was the whole rule
once, and it was satisfied while ``_stage_designs`` copied shards carrying
every tJava body in ``components[].code`` and every routine in
``source_code``. ``projection.py`` removes those on the way in and states the
removal in place; :func:`_refuse_verbatim_code` asserts the result. The scan
is the evidence, which is why slice 0's stylesheet-discovery fix is
load-bearing: a scan-only workspace over an incomplete scan is worse than the
source tree. ``enterprise/workspace.py::stage_sanitized_workspace`` exists to
expose that tree and is deliberately NOT used here; only ``build_exact_tables``
from that module is reused, because it is the deterministic fact renderer.

**Nothing over ~2 MB, and nothing named with a leading underscore.** The
predecessor's main interface was a 38 MB JSON blob and every internal artifact
was ``_``-prefixed -- a marker meaning "never show a client", which is a
distinction the agent has no way to make. ``scan_result.json`` is 21 MB, so
the already-sharded ``scan/jobs/<group>/<design>.json`` form intodoc writes is
staged instead, with its collision hashes stripped. A list-shaped artifact
that is still too large is split into numbered parts with an index that says
so; a non-list one is staged whole and the oversize is recorded as a notice,
because silently dropping evidence is the one outcome worse than a big file.

**Sanitisation is fail-closed, and it comes from the scan's own receipt.**
``publication_approval.json`` is written by the scanner only after every final
redaction verification passed. It carries a sha256 per published artifact AND
a tree digest over the COMPLETE set of artifacts a workspace may stage; this
module re-hashes both and refuses on any disagreement, extra file, missing
file, absence, ``approved: false`` or ``allow_redaction_failures: true``. The
four-artifact map alone was not a binding: it covered the aggregates and left
every ``scan/jobs/<group>/<design>.json`` shard -- the bytes that actually
reach the agent -- free to change after the redaction verification that makes
the approval mean anything. The approved ``scan/manifest.json`` is also the
index every staged design is resolved through, so a design file the approved
manifest does not point at is never staged.

**Nothing here opens a scanner artifact.** Every read goes through
:class:`approved_reader.ApprovedArtifactReader`, which is the one place a
relative path becomes a path on this host: it refuses a path the receipt does
not name, a symlink (including a symlinked parent directory), a non-regular
file, a traversal and an oversize file, and it hashes THE BYTES IT RETURNS
rather than the name it was given. Three defects made that necessary and each
was reproduced: a symlink planted after approval was invisible on both sides
of the tree comparison and was staged; ``roots`` came out of the receipt it
was supposed to bind, so narrowing it left every estate collection unverified
and still staged; and a recognised filename was enough to open a file the
receipt never mentioned. ``scan_dir`` is therefore passed around whole and
never used to build a path in this module -- pinned by a static test.

**Absence is stated, never implied by an empty directory.** ``runtime/`` is
always written, in one of three modes. An optional artifact the scan does not
carry becomes a notice on the receipt, not a silent gap. This is the Steven
failure -- documentation claiming configuration was unavailable when it was in
the scan all along -- and the workspace is the first place it can start.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import logging
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp_server.config import config
from mcp_server.providers.agent_workspace import (
    guide,
    index_builder,
    instructions,
    ledger,
    navigation,
    projection,
    registers,
    resume,
)
from mcp_server.providers.agent_workspace.approved_reader import (
    APPROVAL_EXCLUDED_PREFIXES,
    APPROVAL_FILENAME,
    APPROVAL_ROOTS,
    APPROVAL_TREE_ALGORITHM,
    REDACTION_REPORT_FILENAME,
    REQUIRED_APPROVED_ARTIFACTS,
    REQUIRED_APPROVED_TREE_FILES,
    ApprovedArtifactReader,
    approved_tree_paths,
    offenders,
    path_is_unsafe,
    read_root_of_trust,
    resolves_outside,
    tree_digest,
)
from mcp_server.providers.agent_workspace.guide import ScopeMode
from mcp_server.providers.agent_workspace.contract import (
    AGENT_GUIDE,
    EVIDENCE_FILES,
    EVIDENCE_ROOTS,
    FACTS,
    GOAL,
    INDEX,
    INSTRUCTIONS,
    INTEGRATION_MODEL,
    NOTES,
    OUTPUT,
    PLATFORM_GUIDE_DIR,
    RUNTIME,
    SCAN,
    WorkspaceReceipt,
    WorkspaceUnavailableError,
)

logger = logging.getLogger(__name__)

#: Readability ceiling for one staged file. Not a storage limit: it is the
#: point past which an agent stops reading a file and starts sampling it.
MAX_STAGED_FILE_BYTES = 2_000_000

#: Estate-wide collections staged out of ``scan/``. ``(source, staged,
#: required)``. The optional ones are genuinely absent on estates that use
#: none of that construct; their absence becomes a receipt notice so a reader
#: can tell "this estate has no TDM maps" from "the workspace forgot them".
#:
#: Every one of them goes through :mod:`projection` on the way in -- NOT only
#: ``routines.json``, where the known body lives. A per-file allowlist
#: withholds the bodies we have already found and ships the next one the
#: scanner learns to extract.
_SCAN_COLLECTIONS: tuple[tuple[str, str, bool], ...] = (
    ("contexts.json", "contexts.json", True),
    ("connections.json", "connections.json", True),
    ("routines.json", "routines.json", True),
    ("xslt.json", "xslt.json", False),
    ("services.json", "services.json", False),
    ("structures.json", "structures.json", False),
    ("tdm_maps.json", "tdm_maps.json", False),
    ("landscape.json", "landscape.json", True),
    ("lineage.json", "lineage.json", True),
    ("mapping_accounting.json", "mapping_accounting.json", True),
    ("capability_ledger.json", "capability_ledger.json", True),
)

#: Scan artifacts that must NEVER be staged, with the reason. Enforced by
#: construction (nothing copies them) and asserted by the test suite, because
#: the failure mode of each is silent: the sanitized mirror IS the source tree
#: this lane exists to stop staging, and the rest are either enormous or an
#: internal index of the workspace's own build.
_NEVER_STAGED: tuple[str, ...] = ("sanitized", "evidence", "fact_graph.json")

#: intodoc appends ``-<12 hex>`` to a group or design segment when a composite
#: bundle could collide on the sanitized name (``cli.py::_bounded_segment``).
#: Stripped for readability, and only when the strip does not itself collide.
_COLLISION_SUFFIX_RE = re.compile(r"-[0-9a-f]{12}$")

#: Filesystem-hostile characters in a mapping-table filename.
_UNSAFE_SEGMENT_RE = re.compile(r'[/\\:*?"<>|\s]+')


@dataclass
class _Staged:
    """Accumulator for what actually landed, and what did not."""

    design_count: int = 0
    group_count: int = 0
    mapping_table_count: int = 0
    runtime_available: bool = False
    #: Verbatim code bodies the projection removed on the way in. Carried on
    #: the accumulator rather than recomputed, because the disclosure in
    #: ``facts/parser_limitations.md`` has to describe THIS staging run.
    withheld: projection.WithholdingCensus = field(default_factory=projection.WithholdingCensus)
    notices: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Sanitisation gate
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    """Hash a STAGED file. Scanner artifacts go through the reader instead."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_staged_json(path: Path) -> Any:
    """Read JSON this builder itself wrote into the workspace.

    Deliberately NOT usable for scanner output: the caller supplies a staging
    path, and everything under the scan directory is reached through
    :class:`ApprovedArtifactReader`.
    """

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkspaceUnavailableError(
            f"the staged file {path.name} could not be read as JSON", stage="projection"
        ) from exc


def _stageable_relative_paths(reader: ApprovedArtifactReader) -> set[str]:
    """Every path under ``scan/`` that a build could actually copy or read.

    This is the set that matters for the agent boundary, and it is derived from
    the SAME two places the staging code reads: the fixed collection filenames
    in :data:`_SCAN_COLLECTIONS`, and the ``job_files`` index of the approved
    ``scan/manifest.json``. Deriving it here rather than restating it is the
    point -- a third copy of "what gets staged" would drift from the two that
    do the work, and the drift would show up as either a staged unapproved file
    or a refused harmless one.

    Safe to read the manifest here: it is one of the four aggregates
    :func:`verify_scan_is_publishable` digest-checks BEFORE calling the tree
    verification, so by this point its bytes are proven -- and the read goes
    through the reader, which proves it again.
    """

    # `_SCAN_COLLECTIONS` is (source name, staged name, required). Only the
    # SOURCE name exists under `scan/`; the staged name is what it is called in
    # the workspace. They are identical today, and taking the source name is
    # what stays correct if they ever stop being.
    stageable = {f"scan/{source}" for source, _staged, _required in _SCAN_COLLECTIONS}
    stageable |= {"scan/manifest.json", "scan/meta.json"}
    manifest = reader.read_json("scan/manifest.json")
    job_files = manifest.get("job_files") if isinstance(manifest, dict) else None
    if isinstance(job_files, dict):
        for relative in job_files.values():
            relative = str(relative or "").strip()
            # `job_files` is data even after the digest check -- the digest
            # proves the bytes are the approved ones, not that their contents
            # are sane. A value escaping `scan/` must not enter this set.
            if not relative or path_is_unsafe(relative):
                continue
            stageable.add(f"scan/{Path(relative).as_posix()}")
    return stageable


def _verify_approval_tree(
    reader: ApprovedArtifactReader,
    scan_dir: Path,
    approval: dict[str, Any],
    notices: list[str] | None = None,
) -> None:
    """Re-bind EVERY artifact a workspace may stage, not just the four aggregates.

    ``artifacts`` hashes ``scan_result.json``, ``scan/manifest.json``,
    ``scan/lineage.json`` and ``scan/landscape.json``. The agent workspace
    stages none of those four directly: it stages each
    ``scan/jobs/<group>/<design>.json`` shard, the estate collections and the
    inventory out of ``scan/meta.json``. Editing one shard after approval was
    therefore undetectable -- the redaction verification that makes the
    approval meaningful had run against different bytes.

    Fail-closed with no degraded path: a receipt with no tree block is
    REFUSED rather than accepted on its four hashes, because "older scan" and
    "someone removed the binding" are the same file on disk.
    """

    tree = approval.get("tree")
    if not isinstance(tree, dict):
        raise WorkspaceUnavailableError(
            f"{APPROVAL_FILENAME} carries no agent-eligible artifact tree, so it "
            "binds only four aggregate files and proves nothing about the design "
            "shards and estate collections a workspace actually stages. Rescan the "
            "project with the current intodoc before generating documentation."
        )
    algorithm = str(tree.get("algorithm") or "")
    if algorithm != APPROVAL_TREE_ALGORITHM:
        raise WorkspaceUnavailableError(
            f"{APPROVAL_FILENAME} states tree algorithm {algorithm!r}, which this "
            f"builder cannot recompute (it knows {APPROVAL_TREE_ALGORITHM!r}). "
            "Rescan the project with a matching intodoc."
        )

    files = tree.get("files")
    if not isinstance(files, dict) or not files:
        raise WorkspaceUnavailableError(
            f"{APPROVAL_FILENAME} has an artifact tree with no files; it proves "
            "nothing. Rescan the project."
        )
    # The membership RULE is this builder's, not the receipt's. Reading it out
    # of the receipt was the second reproduced bypass: narrowing `roots` to
    # `["scan/jobs"]` put every estate-level collection outside the walk, so it
    # was neither compared nor hashed while `_stage_scan` still staged it by
    # name. A receipt that claims a different rule is refused rather than
    # obeyed -- it did not come from an intodoc this builder can verify.
    claimed_roots = tuple(str(item) for item in tree.get("roots") or ())
    claimed_excluded = tuple(str(item) for item in tree.get("excluded_prefixes") or ())
    if claimed_roots != APPROVAL_ROOTS or claimed_excluded != APPROVAL_EXCLUDED_PREFIXES:
        raise WorkspaceUnavailableError(
            f"{APPROVAL_FILENAME} states an agent-eligible membership rule this "
            f"builder does not enforce (roots {list(claimed_roots)}, excluded "
            f"{list(claimed_excluded)}; expected {list(APPROVAL_ROOTS)} and "
            f"{list(APPROVAL_EXCLUDED_PREFIXES)}). Rescan the project with a "
            "matching intodoc."
        )
    # The receipt is a file on disk and every field in it is DATA. A key
    # escaping the scan directory would make the re-hash below read -- and
    # implicitly vouch for -- arbitrary paths on this host.
    unsafe = sorted(
        relative
        for relative in map(str, files)
        if path_is_unsafe(relative) or resolves_outside(scan_dir, relative)
    )
    if unsafe:
        raise WorkspaceUnavailableError(
            f"{APPROVAL_FILENAME} names artifacts outside the scan directory; "
            "the receipt is not trustworthy. " + offenders("paths", unsafe)
        )
    if tree.get("file_count") != len(files):
        raise WorkspaceUnavailableError(
            f"{APPROVAL_FILENAME} states {tree.get('file_count')!r} artifact(s) but "
            f"lists {len(files)}; the receipt is internally inconsistent. Rescan."
        )
    if tree_digest({str(k): str(v) for k, v in files.items()}) != str(tree.get("digest") or ""):
        raise WorkspaceUnavailableError(
            f"{APPROVAL_FILENAME} does not match its own tree digest; the receipt "
            "was edited after the scanner wrote it. Rescan the project."
        )
    declared = {str(relative) for relative in files}
    missing_required = sorted(REQUIRED_APPROVED_TREE_FILES - declared)
    if missing_required:
        raise WorkspaceUnavailableError(
            f"{APPROVAL_FILENAME} does not bind every artifact this builder opens "
            f"({offenders('missing from the tree', missing_required)}). Rescan the "
            "project with the current intodoc."
        )

    on_disk, planted_links = approved_tree_paths(scan_dir)
    added = sorted(set(on_disk) - declared)
    removed = sorted(declared - set(on_disk))

    # `scan/` is the SCANNER's output directory, but it is not exclusively the
    # scanner's: `services/summary_service.py` writes `scan/summaries.json`
    # there as a server-side cache, after approval and repeatedly. Treating
    # every unapproved file as fatal therefore broke real projects after the
    # summary cache first appeared, even though that cache is never staged.
    #
    # The honest boundary is not "nothing appeared under scan/" -- that is the
    # scanner's business and the server legitimately writes there. It is
    # "nothing the builder can STAGE is unapproved", because staging is the only
    # way a byte reaches an agent.
    #
    # A SYMLINK is in that set and used not to be counted at all. Both sides
    # skipped links -- the scanner while writing the tree, this walk while
    # reading it -- so one planted at an optional collection's name
    # (`scan/xslt.json` on an estate with no stylesheets) was neither `added`
    # nor `removed`, and `_stage_scan` then opened it by fixed filename.
    # Reproduced: an arbitrary host file staged, `notices == []`. The scanner
    # creates no links, so every entry here was planted; a link nothing can
    # stage is still recorded rather than dropped.
    stageable = _stageable_relative_paths(reader)
    smuggled = sorted({relative for relative in (*added, *planted_links) if relative in stageable})
    ignored = sorted(
        {relative for relative in (*added, *planted_links) if relative not in stageable}
    )
    if smuggled or removed:
        detail = "; ".join(
            part
            for part in (
                offenders("not approved", smuggled) if smuggled else "",
                offenders("approved but missing", removed) if removed else "",
            )
            if part
        )
        raise WorkspaceUnavailableError(
            "the scan output no longer matches its publication approval "
            f"({detail}). The scan was modified after approval; rescan before "
            "generating documentation."
        )
    if ignored and notices is not None:
        # Recorded, never silent: a file appearing inside an approved tree is
        # worth an operator seeing even when it is provably unreachable.
        notices.append(
            f"{len(ignored)} file(s) under scan/ are not covered by the publication "
            f"approval and are not staged (first: {ignored[0]})"
        )

    # One pass, through the reader: it refuses a symlinked or non-regular
    # artifact before it hashes anything, so "the digests match" cannot be
    # true of a file that is not the file the receipt describes.
    for relative in sorted(declared):
        reader.verify(relative)


def verify_scan_is_publishable(
    scan_dir: Path, notices: list[str] | None = None
) -> ApprovedArtifactReader:
    """Prove this scan may be exposed, or refuse to stage it.

    Returns the reader every later stage reads its evidence through. Raises
    ``WorkspaceUnavailableError`` before a single byte is written for every
    failure mode, because each alternative to stopping publishes unverified
    client material. There is no degraded path and there must not be one: a
    partially-verified redaction is indistinguishable, downstream, from a
    verified one.
    """
    scan_dir = Path(scan_dir)
    if not scan_dir.is_dir():
        raise WorkspaceUnavailableError(
            f"no scan output for this project at {scan_dir}. Scan the project before "
            "generating documentation."
        )
    approval = read_root_of_trust(
        scan_dir,
        APPROVAL_FILENAME,
        absent_hint=(
            "Either it predates publication approval or it was produced with "
            "--allow-redaction-failures, which withholds the receipt on purpose. "
            "Rescan before generating documentation."
        ),
    )
    if not isinstance(approval, dict):
        raise WorkspaceUnavailableError(f"{APPROVAL_FILENAME} is not a JSON object")
    if approval.get("approved") is not True:
        raise WorkspaceUnavailableError(
            f"{APPROVAL_FILENAME} does not approve this scan for publication "
            f"(approved={approval.get('approved')!r})"
        )
    if approval.get("allow_redaction_failures") is not False:
        raise WorkspaceUnavailableError(
            f"{APPROVAL_FILENAME} records that redaction failures were allowed; "
            "these bytes are not proven clean and will not be staged"
        )
    artifacts = approval.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        raise WorkspaceUnavailableError(
            f"{APPROVAL_FILENAME} names no artifact digests, so it proves nothing"
        )
    missing_approvals = sorted(REQUIRED_APPROVED_ARTIFACTS - set(map(str, artifacts)))
    if missing_approvals:
        raise WorkspaceUnavailableError(
            f"{APPROVAL_FILENAME} does not bind every required final scan artifact "
            f"({offenders('missing', missing_approvals)}). Rescan the project."
        )
    unsafe_artifacts = sorted(
        relative for relative in map(str, artifacts) if path_is_unsafe(relative)
    )
    if unsafe_artifacts:
        raise WorkspaceUnavailableError(
            f"{APPROVAL_FILENAME} names an unsafe final artifact path; "
            "the receipt is not trustworthy"
        )

    reader = _reader_for(scan_dir, approval)
    for relative in sorted(map(str, artifacts)):
        reader.verify(relative)

    # The four-artifact map above is kept for older receipts and for the
    # aggregates it names; this is what binds the bytes a workspace stages.
    _verify_approval_tree(reader, scan_dir, approval, notices)

    report = read_root_of_trust(
        scan_dir,
        REDACTION_REPORT_FILENAME,
        absent_hint=(
            "There is no record of what was redacted, so the approval cannot be "
            "corroborated. Rescan the project."
        ),
    )
    if not isinstance(report, dict):
        raise WorkspaceUnavailableError(f"{REDACTION_REPORT_FILENAME} is not a JSON object")
    verification = report.get("verification")
    if isinstance(verification, dict) and verification.get("passed") is False:
        raise WorkspaceUnavailableError(
            f"{REDACTION_REPORT_FILENAME} reports a failed redaction verification"
        )
    return reader


def _reader_for(scan_dir: Path, approval: dict[str, Any]) -> ApprovedArtifactReader:
    """The readable surface: the receipt's aggregates UNION its artifact tree.

    A path claimed twice with two different digests is a refusal rather than a
    choice. Picking either one makes the other half of the receipt a decoration,
    and there is no way to tell from the outside which half was believed.
    """

    tree = approval.get("tree")
    digests: dict[str, str] = {}
    conflicts: list[str] = []
    # `tree` is unvalidated at this point -- `_verify_approval_tree` runs on
    # the reader this function returns -- so it is treated as data of unknown
    # shape rather than assumed to be an object.
    for block in (approval.get("artifacts"), tree.get("files") if isinstance(tree, dict) else None):
        if not isinstance(block, dict):
            continue
        for relative, expected in block.items():
            key, value = str(relative), str(expected)
            if digests.setdefault(key, value) != value:
                conflicts.append(key)
    if conflicts:
        raise WorkspaceUnavailableError(
            f"{APPROVAL_FILENAME} binds the same artifact to two different digests; "
            "the receipt is internally inconsistent. Rescan the project. "
            + offenders("paths", sorted(set(conflicts)))
        )
    return ApprovedArtifactReader(scan_dir, digests)


# ---------------------------------------------------------------------------
# scan/
# ---------------------------------------------------------------------------


def _write_json(path: Path, payload: Any) -> int:
    """Write staged evidence JSON: UTF-8, source key order, stable bytes.

    ``ensure_ascii=False`` deliberately. The ASCII rule in this repo is about
    generated MARKDOWN on Windows cp1252 terminals; the scanner's own JSON is
    raw UTF-8, and every artifact here is now reserialized through this
    function rather than copied. Escaping to ``\\u00e9`` would keep the bytes
    valid and make ``grep`` for the name the agent read in ``INDEX.md`` miss
    the shard that holds it.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=False, default=str, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


def _write_collection(target: Path, payload: Any, staged: _Staged, *, label: str) -> None:
    """Write one staged collection, splitting it when it is too large to read.

    A list becomes numbered parts with an index in its place, so the agent
    opens a small file that tells it there are more and nothing was dropped.
    Anything else stays whole with a notice: there is no general way to split
    an object without inventing a structure, and a stated big file beats a
    missing one.

    The payload is passed in rather than read back off disk. Re-reading what
    this function has just written is a second parse of bytes that are already
    in memory, and it made "what was staged" and "what was split" two
    independently-derived answers to one question.
    """
    size = _write_json(target, payload)
    if size <= MAX_STAGED_FILE_BYTES:
        return
    if not isinstance(payload, list):
        staged.notices.append(
            f"{label} is {size / 1_000_000:.1f} MB, above the "
            f"{MAX_STAGED_FILE_BYTES / 1_000_000:.0f} MB readability target, and has "
            "no list structure to split on; it is staged whole"
        )
        return

    stem = target.stem
    parts_dir = target.with_name(stem)
    if parts_dir.exists():
        shutil.rmtree(parts_dir)
    parts_dir.mkdir(parents=True)
    # Rows per part come from the mean row size so parts land near the byte
    # ceiling rather than at an arbitrary count: a 40-row file of huge rows and
    # a 400,000-row file of tiny ones both want bounded BYTES.
    per_part = max(1, int(len(payload) * MAX_STAGED_FILE_BYTES / max(size, 1)))
    names: list[str] = []
    for index, start in enumerate(range(0, len(payload), per_part), start=1):
        name = f"part_{index:03d}.json"
        _write_json(parts_dir / name, payload[start : start + per_part])
        names.append(f"{stem}/{name}")
    _write_json(
        target,
        {
            "split": True,
            "row_count": len(payload),
            "parts": names,
            "note": (
                f"This collection is {size / 1_000_000:.1f} MB, too large to read in "
                "one file. The rows are in the listed parts, in order, with nothing "
                "dropped."
            ),
        },
    )
    staged.notices.append(f"{label} was split into {len(names)} part(s); the rows are complete")


def _readable_segment(segment: str, taken: set[str]) -> str:
    """Strip intodoc's collision hash, but never at the cost of a collision.

    Reservation is CASE-INSENSITIVE and that is the whole point. Talend groups
    that differ only in case are real and common -- the reference estate has
    both ``Monitoring`` and ``monitoring`` -- and on macOS or Windows those are
    one directory. Reserving case-sensitively staged them under two names that
    the filesystem then merged, so one group's designs landed in the other's
    directory and the group count was quietly short by one. The hashed form
    intodoc emits is unique by construction, so falling back to it is always
    safe; the numeric tail is a last resort that cannot loop.
    """
    for candidate in (_COLLISION_SUFFIX_RE.sub("", segment), segment):
        if candidate and candidate.casefold() not in taken:
            taken.add(candidate.casefold())
            return candidate
    suffix = 2
    while f"{segment}_{suffix}".casefold() in taken:
        suffix += 1
    taken.add(f"{segment}_{suffix}".casefold())
    return f"{segment}_{suffix}"


def _stage_designs(reader: ApprovedArtifactReader, target: Path, staged: _Staged) -> dict[str, str]:
    """Stage one JSON per design, resolved through the APPROVED manifest.

    Returns ``{unit key: staged path relative to the workspace root}`` so the
    fact tables can point at the file that exists here instead of the
    ``_workspace/`` path they were written for.

    Two bindings, not one. ``scan/manifest.json`` carries its digest in the
    publication approval, so its ``job_files`` index decides WHICH designs are
    staged; the reader decides whether each named shard may be opened at all.
    The index alone was not enough -- it is data, and a value pointing at
    ``sanitized/...`` or at a shard the tree never covered resolved inside
    ``scan/`` and was staged on that basis.
    """
    manifest = reader.read_json("scan/manifest.json")
    job_files = manifest.get("job_files") if isinstance(manifest, dict) else None
    if not isinstance(job_files, dict) or not job_files:
        raise WorkspaceUnavailableError(
            "the approved scan manifest carries no job_files index, so no design can "
            "be staged from it. Rescan the project with the current intodoc.",
            stage="projection",
        )

    groups: dict[str, str] = {}
    group_names: set[str] = set()
    per_group_names: dict[str, set[str]] = {}
    missing: list[str] = []
    staged_paths: dict[str, str] = {}
    for job_key in sorted(job_files):
        relative = str(job_files[job_key] or "").strip()
        approved = f"scan/{Path(relative).as_posix()}" if relative else ""
        if not approved or not reader.has(approved):
            missing.append(str(job_key))
            continue
        raw_group = Path(relative).parent.name
        if raw_group not in groups:
            groups[raw_group] = _readable_segment(raw_group, group_names)
        group_dir = target / groups[raw_group]
        group_dir.mkdir(parents=True, exist_ok=True)
        names = per_group_names.setdefault(raw_group, set())
        name = _readable_segment(Path(relative).stem, names)
        # Projected, never copied. The shard the scanner wrote carries the
        # tJava bodies in `components[].code` and in the CODE parameters, so
        # `shutil.copy2` handed the agent verbatim source through the one path
        # the `.item`/`sanitized/` exclusions do not cover.
        _write_json(
            group_dir / f"{name}.json",
            projection.project_scan_payload(reader.read_json(approved), census=staged.withheld),
        )
        staged.design_count += 1
        staged_paths[str(job_key)] = f"{SCAN.as_posix()}/jobs/{groups[raw_group]}/{name}.json"

    staged.group_count = len(groups)
    if missing:
        staged.notices.append(
            f"{len(missing)} design(s) named by the scan manifest are not covered by "
            f"the publication approval and were not staged (first: {missing[0]})"
        )
    if staged.design_count == 0:
        raise WorkspaceUnavailableError(
            "the scan manifest names designs but none of them is an approved artifact; "
            "the scan output is incomplete. Rescan the project.",
            stage="projection",
        )
    return staged_paths


def _stage_scan(reader: ApprovedArtifactReader, target: Path, staged: _Staged) -> dict[str, str]:
    target.mkdir(parents=True, exist_ok=True)
    staged_paths = _stage_designs(reader, target / "jobs", staged)
    for filename, staged_name, required in _SCAN_COLLECTIONS:
        source = f"scan/{filename}"
        # Membership in the receipt, NOT existence on disk. The two came apart
        # exactly where it mattered: an optional collection absent at scan time
        # leaves its name free, and `source.is_file()` then said yes to
        # whatever had been put there since.
        if not reader.has(source):
            if required:
                raise WorkspaceUnavailableError(
                    f"the publication approval does not cover scan/{filename}; rescan "
                    "the project with the current intodoc before generating "
                    "documentation",
                    stage="projection",
                )
            staged.notices.append(f"scan/{filename} is absent from this scan")
            continue
        # Every collection goes through the projection, not just
        # `routines.json` where the known body lives. A per-file allowlist
        # would withhold the bodies we have already found and ship the next
        # one the scanner learns to extract; the walk is a no-op on a payload
        # that carries none.
        _write_collection(
            target / staged_name,
            projection.project_scan_payload(reader.read_json(source), census=staged.withheld),
            staged,
            label=f"scan/{staged_name}",
        )

    # The file inventory lives inside meta.json beside the scan stats. Lifted
    # out rather than staging meta.json whole: the rest of that file is scan
    # bookkeeping, and the inventory is the artifact a reader needs to tell
    # "not in the estate" from "the scanner never opened it".
    meta = reader.read_json("scan/meta.json")
    inventory = meta.get("source_inventory") if isinstance(meta, dict) else None
    if not isinstance(inventory, list):
        raise WorkspaceUnavailableError(
            "the scan carries no file inventory (scan/meta.json::source_inventory); "
            "without it the workspace cannot state what the scanner did not read",
            stage="projection",
        )
    _write_collection(target / "inventory.json", inventory, staged, label="scan/inventory.json")
    return staged_paths


# ---------------------------------------------------------------------------
# facts/
# ---------------------------------------------------------------------------


def _mapping_table_name(table: dict[str, Any], units: dict[str, Any], taken: set[str]) -> str:
    """``<design>__<mapper>.md``, deterministic and collision-free.

    The rendered table already carries "Design: `x`; mapper: `y`" in its
    header; this reads the same pair from the accounting row rather than
    parsing the Markdown back. The design is named by its DISPLAY name where
    that is unambiguous, because the scanner identity of a name-colliding
    design is a path with a hash on the end and putting that in a filename is
    exactly the opaqueness this rename exists to remove. Ambiguity falls back
    to the identity, then to the scanner's table id -- both unique by
    construction. Reservation is case-insensitive; two mappers differing only
    in case are one file on macOS and Windows.
    """
    unit_id = str(table.get("unit_id") or "")
    unit = units.get(unit_id) if isinstance(units, dict) else None
    display = str((unit or {}).get("display_name") or "") if isinstance(unit, dict) else ""
    mapper = _UNSAFE_SEGMENT_RE.sub("_", str(table.get("component_name") or "mapper")).strip("_")
    mapper = mapper or "mapper"
    for design in (display, unit_id, "unknown"):
        safe = _UNSAFE_SEGMENT_RE.sub("_", design).strip("_")
        if not safe:
            continue
        base = f"{safe}__{mapper}"
        if base.casefold() not in taken:
            taken.add(base.casefold())
            return f"{base}.md"
    base = f"{mapper}__{table.get('table_id') or len(taken)}"
    taken.add(base.casefold())
    return f"{base}.md"


def _repoint_units(units_path: Path, staged_paths: dict[str, str]) -> None:
    """Replace ``workspace_path`` with the file that exists in THIS workspace.

    ``build_exact_tables`` writes ``_workspace/<source_root>/process/<x>.item``
    -- a sanitized source tree this lane deliberately does not stage. Left
    alone it would send the agent hunting for a directory that is not there
    and teach it an underscore-prefixed internal name at the same time. A unit
    the design staging did not produce loses the key entirely rather than
    keeping a dangling one.
    """
    payload = _read_staged_json(units_path)
    units = payload.get("units") if isinstance(payload, dict) else None
    if not isinstance(units, dict):
        return
    for key, unit in units.items():
        if not isinstance(unit, dict):
            continue
        unit.pop("workspace_path", None)
        scan_path = staged_paths.get(str(key))
        if scan_path:
            unit["scan_path"] = scan_path
    _write_json(units_path, payload)


#: What ``build_exact_tables`` opens, by name. It takes a DIRECTORY rather
#: than a reader -- it is the legacy lane's deterministic renderer and is
#: shared with it -- so the boundary is re-established at the call site: every
#: one of these is proved through the reader immediately before the call, which
#: is the narrowest window this module can offer without owning that function.
#: Read off ``providers/custom/enterprise/workspace.py::build_exact_tables``.
_EXACT_TABLE_INPUTS: tuple[str, ...] = (
    "scan_result.json",
    "scan/manifest.json",
    "scan/mapping_accounting.json",
    "scan/capability_ledger.json",
)


def _stage_facts(
    scan_dir: Path,
    reader: ApprovedArtifactReader,
    target: Path,
    staged: _Staged,
    staged_paths: dict[str, str],
) -> dict[str, str]:
    """Render the deterministic fact tables into ``facts/``.

    Returns ``{table id: staged mapping-table path}``. The navigation view has
    to carry the path the file ACTUALLY got: ``_mapping_table_name`` renames a
    mapper whose name collides case-insensitively with another, and a
    navigation row pointing at the name it would otherwise have had sends the
    agent to a path that does not exist.

    ``build_exact_tables`` is called, not reimplemented -- it is the existing
    deterministic renderer and re-deriving dependencies, interfaces and
    configuration would be a second implementation free to disagree with the
    first. It writes to ``_analysis/_exact/`` with names fixed by
    ``enterprise/layout.py``, so it runs against a throwaway directory and the
    results are moved under the underscore-free names this workspace uses.

    It is also the ONE reader of scanner output this module does not own: its
    signature takes a directory, and it is shared with the legacy lane. Its
    four inputs are re-proved through the reader immediately before the call,
    so the guarantee holds at the call site rather than 400 lines upstream.
    """
    from mcp_server.providers.custom.enterprise.workspace import (
        EnterpriseWorkspaceError,
        build_exact_tables,
    )

    for relative in _EXACT_TABLE_INPUTS:
        reader.verify(relative)

    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="agent-workspace-facts-") as scratch:
        scratch_dir = Path(scratch)
        try:
            tables = build_exact_tables(scan_dir, scratch_dir)
        except EnterpriseWorkspaceError as exc:
            raise WorkspaceUnavailableError(
                f"the deterministic fact tables could not be built from this scan: {exc}",
                stage="projection",
            ) from exc

        exact = scratch_dir / "_analysis" / "_exact"
        for name in ("units.json", "dependencies.json", "interfaces.json", "configuration.json"):
            source = exact / name
            if not source.is_file():  # defensive: the renderer writes all four
                raise WorkspaceUnavailableError(
                    f"the deterministic fact renderer produced no {name}", stage="projection"
                )
            shutil.move(str(source), str(target / name))

        mappings_dir = target / "mappings"
        mappings_dir.mkdir(parents=True, exist_ok=True)
        taken: set[str] = set()
        mapping_paths: dict[str, str] = {}
        by_id = {
            str(row.get("table_id") or ""): row for row in tables.mappings if isinstance(row, dict)
        }
        for table_id in sorted(by_id):
            source = exact / "mapping_tables" / f"{table_id}.md"
            if not source.is_file():
                continue
            name = _mapping_table_name(by_id[table_id], tables.units, taken)
            shutil.move(str(source), str(mappings_dir / name))
            mapping_paths[table_id] = f"{FACTS.as_posix()}/mappings/{name}"
            staged.mapping_table_count += 1

    _repoint_units(target / "units.json", staged_paths)
    (target / "parser_limitations.md").write_text(
        render_parser_limitations(reader, withheld=staged.withheld), encoding="utf-8"
    )
    return mapping_paths


def render_parser_limitations(
    reader: ApprovedArtifactReader, *, withheld: projection.WithholdingCensus | None = None
) -> str:
    """What the scanner could not read, in prose, from the ledger and inventory.

    This file exists so a document can disclose rather than deny. The verified
    failure it guards against: generated documentation asserting that
    configuration was unavailable while the scan held it all along. Every gap
    below is a row somebody can check, not a hedge.

    ``withheld`` is the OTHER kind of boundary and belongs in the same file:
    the scanner read those custom-code bodies perfectly well and this lane
    removed them on the way to the agent. An agent told only "the scanner
    could not read X" would report code it cannot see as absent, which is the
    same denial in a new place.
    """
    ledger = reader.read_json("scan/capability_ledger.json")
    meta = reader.read_json("scan/meta.json")
    constructs = ledger.get("constructs") if isinstance(ledger, dict) else []
    outcomes = ledger.get("file_outcomes") if isinstance(ledger, dict) else {}
    inventory = meta.get("source_inventory") if isinstance(meta, dict) else []
    warnings = meta.get("warnings") if isinstance(meta, dict) else []

    lines = [
        "# What the scanner could not read",
        "",
        "Everything named here is a boundary of the evidence, not a statement about",
        "the estate. A construct the scanner cannot model still exists and still runs.",
        "Say so in that shape: name the gap, say what it would take to close it, and",
        "never let it become 'this project has no X'.",
        "",
        "## Files",
        "",
    ]

    if isinstance(outcomes, dict) and outcomes:
        lines += ["| Outcome | Files | Meaning |", "|---|---|---|"]
        meanings = {
            "parsed": "fully extracted; the evidence is in `scan/`",
            "partial_or_failed": "recognised but not fully extracted; see the warnings below",
            "superseded": "an older export version of an artifact whose latest version was used",
            "indexed": "recognised as a known kind and listed, but not extracted",
            "unsupported": "not a file kind this scanner parses (binaries, payloads, docs)",
            "excluded": "hidden path, symlink, or an unreadable directory",
        }
        for outcome in sorted(outcomes):
            lines.append(
                f"| {outcome} | {outcomes[outcome]} | "
                f"{meanings.get(outcome, 'see the scanner documentation')} |"
            )
        lines.append("")

    indexed = [
        row for row in inventory if isinstance(row, dict) and row.get("outcome") == "indexed"
    ]
    if indexed:
        kinds: dict[str, int] = {}
        for row in indexed:
            kind = str(row.get("kind") or "unknown")
            kinds[kind] = kinds.get(kind, 0) + 1
        lines += [
            f"{len(indexed)} file(s) were recognised by kind and listed in "
            "`scan/inventory.json` without being extracted. By kind: "
            + ", ".join(f"{kind} ({count})" for kind, count in sorted(kinds.items()))
            + ".",
            "",
            (
                "Their names are known and their contents are not. Naming one in a "
                "document is honest; describing what is inside it is not."
            ),
            "",
        ]

    unscanned = [
        row
        for row in constructs
        if isinstance(row, dict) and row.get("category") == "unscanned_directory"
    ]
    if unscanned:
        lines += ["## Directories", ""]
        for row in unscanned:
            lines.append(f"- `{row.get('construct')}` -- {row.get('note') or 'not parsed'}")
        lines.append("")

    limited = [
        row
        for row in constructs
        if isinstance(row, dict)
        and row.get("category") != "unscanned_directory"
        and str(row.get("status")) in {"partial", "unresolved", "dynamic", "unsupported"}
        and row.get("count")
    ]
    if limited:
        lines += [
            "## Constructs",
            "",
            "| Construct | Status | Count | Why it is a limit |",
            "|---|---|---|---|",
        ]
        for row in sorted(
            limited, key=lambda item: (str(item.get("category")), str(item.get("construct")))
        ):
            note = str(row.get("note") or "").replace("|", "/") or "not modelled by this scanner"
            lines.append(
                f"| {row.get('category')}.{row.get('construct')} | {row.get('status')} | "
                f"{row.get('count')} | {note} |"
            )
        lines.append("")

    parse_warnings = [
        warning
        for warning in warnings
        if isinstance(warning, dict)
        and str(warning.get("kind")) in {"parse_error", "partial_parse", "unreadable_directory"}
    ]
    if parse_warnings:
        lines += [
            "## Individual files that did not parse cleanly",
            "",
        ]
        for warning in parse_warnings[:200]:
            lines.append(f"- `{warning.get('file')}` -- {warning.get('message')}")
        if len(parse_warnings) > 200:
            lines.append(
                f"- ... and {len(parse_warnings) - 200} more; the full list is in the scan warnings."
            )
        lines.append("")

    if len(lines) <= 8:
        lines.append("The scanner reported no limitations for this scan.")
        lines.append("")

    lines += _withheld_code_section(withheld)
    return "\n".join(lines).encode("ascii", errors="replace").decode("ascii")


def _withheld_code_section(
    withheld: projection.WithholdingCensus | None,
) -> list[str]:
    """State the code boundary WITH its population, in both directions.

    "No code bodies were withheld" is a fact worth writing down: without it an
    agent that finds no custom code cannot tell whether the estate has none or
    whether this lane removed it, and the safe reading of that ambiguity is
    the wrong one on most Talend estates.
    """

    lines = ["## Custom code", ""]
    if withheld is None or withheld.bodies == 0:
        return lines + [
            "No component or routine in this scan carried a custom-code body, so",
            "nothing was withheld on that account. Absence here is a fact about the",
            "estate.",
            "",
        ]
    return lines + [
        (
            "This lane never receives verbatim source. "
            f"**{withheld.bodies:,} custom-code body/bodies "
            f"({withheld.lines:,} lines, {withheld.characters:,} characters)** carried "
            f"inside the parsed components and routines of {withheld.artifacts:,} staged "
            "file(s) were removed before staging and replaced, in place, by a"
        ),
        "`code_withheld` block stating the size, a sha256 of the body, the",
        "identifiers it named (routines, context variables, globalMap keys), and",
        "a non-verbatim structural behavior summary (branching, exception handling,",
        "shared-state access, logging, and termination signals).",
        "",
        "Business rules implemented in those bodies are therefore NOT in your",
        "evidence, and you cannot recover them from anything here. Write that as",
        "what it is: naming the component, its inputs and outputs, and saying the",
        "logic is implemented in custom code this evidence set does not carry, is",
        "honest. Structural labels narrow the review; they do not reveal hidden",
        "conditions, values, or business meaning.",
        "",
    ]


# ---------------------------------------------------------------------------
# runtime/
# ---------------------------------------------------------------------------


def _fetch_runtime(project_id: str, units: list[tuple[str, str]]):
    """Run the async runtime producer from a synchronous builder.

    ``build_workspace`` is called from inside the runner's coroutine, so
    ``asyncio.run`` here would raise on a running loop. A one-shot worker
    thread with its own loop is the whole trick; the fetch is bounded by the
    runtime client's own timeouts.
    """
    from mcp_server.providers.custom.runtime_evidence import fetch_runtime_evidence

    def _run() -> Any:
        return asyncio.run(fetch_runtime_evidence(project_id, units))

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_run).result()


def _stage_runtime(
    project_id: str, units: list[tuple[str, str]], target: Path, staged: _Staged
) -> None:
    """Write ``runtime/``, in every case, saying which case it is.

    An empty directory would be read as "this estate never runs". Absence has
    three distinct meanings here -- switched off, mirror unreachable, no
    mirror connected -- and the rollup renderer states whichever applies.
    Runtime evidence never fails a run: it is observation, not a gate.
    """
    from mcp_server.providers.custom.runtime_evidence import (
        RuntimeEvidenceStore,
        RuntimeMode,
        project_rollup,
    )

    target.mkdir(parents=True, exist_ok=True)
    try:
        store = _fetch_runtime(project_id, units)
    except Exception as exc:
        logger.exception("agent workspace: runtime fetch failed for %s", project_id)
        staged.notices.append(f"runtime evidence could not be fetched: {type(exc).__name__}")
        store = RuntimeEvidenceStore(
            window_days=config.runtime_evidence_window_days,
            mode=RuntimeMode.UNAVAILABLE,
            requested_units=len(units),
            unretrieved_units=tuple(name for name, _ in units),
        )

    (target / "profile.md").write_text(
        project_rollup(store, max_bytes=config.phase3_runtime_profile_bytes),
        encoding="utf-8",
    )
    # The profile is an orientation view and may deliberately omit low-ranked
    # rows to stay readable.  Preserve the complete, already-redacted store as
    # structured evidence so the investigator can open the affected unit on
    # demand.  Without this file a project-wide byte ceiling silently became
    # an evidence ceiling for the independent lane.
    _write_json(target / "evidence.json", store.to_json())
    _write_json(target / "coverage.json", store.coverage_payload())
    staged.runtime_available = store.mode is RuntimeMode.DATA and bool(store.by_unit)
    if not staged.runtime_available:
        # Say which absence it is. "The mirror answered and matched nothing"
        # and "there is no mirror" are different facts about the estate, and
        # a receipt that flattens them to one boolean loses the distinction
        # the rollup itself is careful to keep.
        staged.notices.append(
            f"runtime evidence: mode {store.mode.value}, "
            f"{len(store.by_unit)} of {store.requested_units} design(s) matched"
        )


# ---------------------------------------------------------------------------
# Digest
# ---------------------------------------------------------------------------


def evidence_digest(root: Path) -> str:
    """Public alias: the integrity baseline publication re-checks after the run.

    Writes cannot be path-scoped in this runtime (see
    ``contract.writable_globs``), so the evidence roots are protected by
    comparing this digest before and after the agent runs rather than by a
    permission rule the provider ignores.
    """

    return _content_digest(root)


def _digest_tree(root: Path, relatives: tuple[Path, ...]) -> str:
    """Digest of the staged files under ``relatives``, path-and-content.

    Deterministic: sorted paths, the relative path folded in beside its own
    content so a rename is a change, and nothing iterates a set.
    """

    digest = hashlib.sha256()
    for relative_root in relatives:
        base = root / relative_root
        paths = sorted(base.rglob("*")) if base.is_dir() else ([base] if base.is_file() else [])
        for path in paths:
            if not path.is_file():
                continue
            digest.update(path.relative_to(root).as_posix().encode("utf-8"))
            digest.update(_sha256_file(path).encode("ascii"))
    return digest.hexdigest()


def _content_digest(root: Path) -> str:
    """Digest of the evidence and method, excluding the writable roots.

    ``output/`` and ``notes/`` are excluded because they are the agent's, not
    the builder's: including them would make the digest of an unchanged scan
    depend on how far a previous attempt got. ``_agent_control/`` is excluded
    for the mirror-image reason: it is Nexus's, and the resume identity inside
    it changes whenever the run does.
    """

    # `EVIDENCE_FILES` rather than an AGENT_GUIDE/GOAL pair: `INDEX.md` is the
    # compact map the whole investigation routes through, so an agent that
    # rewrote it to say the estate was smaller than it is would be tampering
    # with evidence, and a hardcoded pair would not have noticed.
    return _digest_tree(root, (*EVIDENCE_ROOTS, *EVIDENCE_FILES))


def _method_digest(root: Path) -> str:
    """Digest of the instructions and the agent guide, as staged.

    The method half of the resume identity, taken from the BYTES that were
    written rather than from the platform name that produced them. An edited
    instruction file, a new platform guide and a changed investigation method
    all move it; ``GOAL.md`` and ``INDEX.md`` are deliberately outside it
    because they carry the goal and the scan, which are their own fields.
    """

    return _digest_tree(root, (INSTRUCTIONS, AGENT_GUIDE))


def _apply_resume_identity(
    staging_dir: Path,
    *,
    project_id: str,
    reader: ApprovedArtifactReader,
    goal: str,
    staged: _Staged,
) -> None:
    """Keep ``output/`` and ``notes/`` only if they provably belong to this run.

    Called once per build, and deliberately NOT once per attempt: the runner's
    restarts share this workspace on purpose, and running the check per
    attempt would delete the work a restart exists to keep.
    """

    identity = resume.ResumeIdentity.build(
        project_id=project_id,
        scan_digest=reader.approval_digest,
        goal=goal,
        method_digest=_method_digest(staging_dir),
        knowledge_model_version=resume.integration_model_schema_version(),
    )
    decision = resume.reconcile(staging_dir, identity)
    if decision.notice:
        staged.notices.append(decision.notice)
    for relative in (OUTPUT, NOTES):
        (staging_dir / relative).mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def build_workspace(
    *,
    project_id: str,
    staging_dir: Path,
    platform: str,
    goal: str,
    scope_mode: ScopeMode | None = None,
) -> WorkspaceReceipt:
    """Stage the whole workspace. Implements ``contract.WorkspaceBuilder``.

    ``scope_mode`` is the operator's DECLARATION of what the run is
    accountable for, and it reaches every renderer that states an obligation:
    ``GOAL.md``, the core instruction files and ``INDEX.md``. All four have to
    state one scope or the bootstrap contradicts itself -- which is what
    happened while ``INDEX.md`` carried its own unconditional copy of the
    estate-wide rule. ``None`` means undeclared and resolves to
    ``whole_estate``; narrowing is never inferred.

    The order below is load-bearing in one place. Evidence and method are
    staged first; the resume decision is taken next, because it is the thing
    that may DELETE ``output/``; and the compiler-owned reference shelf is
    written after it, because that shelf lives in ``output/`` and a clear that
    ran later would take it with it. Everything else is free ordering.
    """
    scan_dir = config.intodoc_output_dir / project_id
    staged = _Staged()
    reader = verify_scan_is_publishable(scan_dir, staged.notices)

    staging_dir = Path(staging_dir)

    # Evidence and method are rebuilt from scratch on every call so a resumed
    # run cannot inherit a stale artifact from an older scan. `output/` and
    # `notes/` are decided separately, below, by the resume identity.
    for relative in EVIDENCE_ROOTS:
        path = staging_dir / relative
        if path.exists():
            shutil.rmtree(path)
    for relative in EVIDENCE_ROOTS:
        (staging_dir / relative).mkdir(parents=True, exist_ok=True)

    staged_paths = _stage_scan(reader, staging_dir / SCAN, staged)
    mapping_paths = _stage_facts(scan_dir, reader, staging_dir / FACTS, staged, staged_paths)

    _stage_runtime(
        project_id,
        _unit_requests(staging_dir / FACTS / "units.json"),
        staging_dir / RUNTIME,
        staged,
    )

    # Built LAST of the evidence, and from the staged tree rather than from
    # the scan output: a navigation row that describes a file the stager
    # renamed, split or skipped is worse than no row.
    navigation_source = navigation.load_source(staging_dir, mapping_paths)
    navigation_rows = navigation.write_navigation(staging_dir, navigation_source)

    # The searchable index layer, built from the same staged tree. It answers
    # the questions the navigation views cannot: which elements are
    # integration-BEARING, what points at what (including the reverse edge,
    # which is the whole of "what breaks if I change this"), and where a field
    # came from. Deterministic Python throughout -- the agent explains these
    # rows, it does not have to find them, which is the split run 250 got
    # wrong when a coordinator opened 8 of 142 designs and wrote about those.
    index_rows = index_builder.build_indexes(staging_dir)

    # The goal reaches the instructions, not just `GOAL.md`. Without it a
    # focused run received a focused goal statement and whole-estate method
    # files -- the contradiction the scope split exists to remove, reassembled
    # at the call site.
    instruction_files = instructions.write_instructions(
        staging_dir / INSTRUCTIONS, platform=platform, goal=goal, scope_mode=scope_mode
    )
    # The guide directory itself, not the `instructions/` root that contains
    # it. `write_platform_guides` tolerates either spelling, but tolerance is
    # not the contract: `PLATFORM_GUIDE_DIR` is where the constant says the
    # guides live, and passing anything else leaves two callers free to mean
    # different things by one path.
    platform_guides = instructions.write_platform_guides(
        staging_dir / PLATFORM_GUIDE_DIR, platform=platform
    )
    (staging_dir / AGENT_GUIDE).write_text(guide.render_guide(platform=platform), encoding="utf-8")
    (staging_dir / GOAL).write_text(
        guide.render_goal(
            goal=goal,
            project_id=project_id,
            platform=platform,
            design_count=staged.design_count,
            group_count=staged.group_count,
        ),
        encoding="utf-8",
    )
    # Reconciled BEFORE the roster is seeded and before `INDEX.md` is
    # rendered. `resume._clear` removes the whole of `notes/` and `output/`
    # when reused state cannot prove it belongs to this run, so seeding first
    # would write the roster and then delete it -- leaving the agent a map that
    # describes a file that is not there. Nothing written above this point
    # lives under either of those roots, so the move costs nothing.
    _apply_resume_identity(
        staging_dir,
        project_id=project_id,
        reader=reader,
        goal=goal,
        staged=staged,
    )

    # The population this run is accountable for, projected from
    # `facts/navigation/units/`. Re-seeded on every build so it stays in step
    # with the evidence; the agent's own `classifications.jsonl` beside it is
    # never touched, which is what lets a resume keep its accumulated
    # decisions.
    #
    # The roster is a CONVENIENCE. It sits under agent-writable `notes/`, so
    # the accountability gate never trusts it: `ledger.read_ledger` recomputes
    # the population from the digest-protected evidence and measures the
    # agent's classifications against that. Deleting rows here cannot shrink
    # what the run owes.
    coverage_candidates = ledger.seed_roster(staging_dir)

    (staging_dir / INDEX).write_text(
        navigation.render_index(
            project_id=project_id,
            platform=platform,
            workspace=staging_dir,
            source=navigation_source,
            navigation_rows=navigation_rows,
            index_rows=index_rows,
            scope_mode=guide.scope_mode_for_goal(goal, declared=scope_mode),
            coverage_candidates=coverage_candidates,
        ),
        encoding="utf-8",
    )

    # A restart keeps the agent's accumulated model.  A new run gets the
    # smallest valid provider-neutral starting point; no project candidates or
    # fixed partitions are imposed before investigation.
    integration_model_path = staging_dir / INTEGRATION_MODEL
    if not integration_model_path.is_file():
        from mcp_server.providers.agent_workspace.integration_model import (
            IntegrationKnowledgeBase,
        )

        IntegrationKnowledgeBase().save(integration_model_path)

    # Exact, exhaustive facts belong in deterministic client-facing registers,
    # not in an LLM's context window or copy loop.  They are staged before the
    # agent starts so it can explain and link to them.  The runner rebuilds the
    # same shelf immediately before publication, making these files compiler-
    # owned even though ``output/`` is otherwise writable by the agent.
    reference = registers.compile_reference_registers(staging_dir)

    _refuse_forbidden_artifacts(staging_dir)
    _refuse_verbatim_code(staging_dir)
    staged.notices.extend(_oversize_notices(staging_dir))
    return WorkspaceReceipt(
        project_id=project_id,
        platform=platform,
        workspace_path=staging_dir,
        design_count=staged.design_count,
        group_count=staged.group_count,
        mapping_table_count=staged.mapping_table_count,
        instruction_files=tuple(instruction_files),
        platform_guides=tuple(platform_guides),
        navigation_rows=dict(navigation_rows),
        index_rows=dict(index_rows),
        coverage_candidates=coverage_candidates,
        reference_registers={
            "units": reference.unit_count,
            "dependencies": reference.dependency_count,
            "unresolved_dependencies": reference.unresolved_dependency_count,
            "interfaces": reference.interface_count,
            "channels": reference.channel_count,
            "configuration_variables": reference.configuration_variable_count,
            "mapping_tables": reference.mapping_table_count,
            "mapping_rows": reference.mapping_row_count,
            "runtime_units": reference.runtime_unit_count,
            "integrations": reference.integration_count,
            "journeys": reference.journey_count,
        },
        runtime_available=staged.runtime_available,
        content_digest=_content_digest(staging_dir),
        notices=tuple(staged.notices),
    )


def _unit_requests(units_path: Path) -> list[tuple[str, str]]:
    """Design identities for the runtime lookup, from the staged fact table.

    Both keys per design, as a PAIR rather than two entries: the scanner
    identity is what everything joins on, a runtime source schedules tasks
    under the display name, and on a real estate the two differ for a fifth of
    the designs. Flattening them into separate requests would also inflate the
    coverage denominator, so the rollup would report more units in scope than
    the estate contains.
    """
    try:
        payload = json.loads(units_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    units = payload.get("units") if isinstance(payload, dict) else None
    if not isinstance(units, dict):
        return []
    return [
        (str(key), str((unit or {}).get("display_name") or "") if isinstance(unit, dict) else "")
        for key, unit in sorted(units.items())
    ]


def _refuse_forbidden_artifacts(staging_dir: Path) -> None:
    """Fail rather than ship one of ``_NEVER_STAGED``.

    Nothing copies these, so this can only fire on a future edit -- which is
    exactly when it is worth having. Each of them fails silently: staging the
    sanitized mirror puts the source tree back, and staging the fact graph or
    the monolithic scan puts a multi-megabyte blob back as the main interface.
    """
    offenders = sorted(
        path.relative_to(staging_dir).as_posix()
        for relative in EVIDENCE_ROOTS
        for name in _NEVER_STAGED
        for path in (staging_dir / relative).rglob(name)
    )
    if offenders:
        raise WorkspaceUnavailableError(
            "the workspace staged artifacts that must never reach an agent: "
            + ", ".join(offenders),
            stage="projection",
        )


def _refuse_verbatim_code(staging_dir: Path) -> None:
    """Assert the source boundary on the bytes that actually landed.

    The sibling of :func:`_refuse_forbidden_artifacts`, and the same kind of
    check: nothing staged here can carry a body, so this only fires on a
    future edit -- which is exactly when it is worth having. The previous
    version of this boundary was a filename rule (no ``.item``, no
    ``sanitized/``), and it was satisfied while every tJava body in the estate
    was being copied through ``components[].code``.

    Findings name the JSON path and never the value: this message reaches a
    run log and a database row, and a code body is exactly what must not be
    copied into either.
    """

    findings: list[str] = []
    for relative in EVIDENCE_ROOTS:
        base = staging_dir / relative
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue  # not this check's job; the readers below will say so
            findings.extend(
                projection.verbatim_code_findings(
                    payload, label=path.relative_to(staging_dir).as_posix()
                )
            )
    if findings:
        raise WorkspaceUnavailableError(
            "the workspace staged verbatim source code, which no documentation "
            "agent may receive: " + offenders("locations", sorted(findings)),
            stage="projection",
        )


def _oversize_notices(staging_dir: Path) -> list[str]:
    """Name every staged file past the readability ceiling, so none is silent."""
    notices: list[str] = []
    for relative in EVIDENCE_ROOTS:
        base = staging_dir / relative
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file() and path.stat().st_size > MAX_STAGED_FILE_BYTES:
                notices.append(
                    f"{path.relative_to(staging_dir).as_posix()} is "
                    f"{path.stat().st_size / 1_000_000:.1f} MB, above the "
                    f"{MAX_STAGED_FILE_BYTES / 1_000_000:.0f} MB readability target"
                )
    return notices
