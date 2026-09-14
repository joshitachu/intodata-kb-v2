"""Publish ``output/`` into the staged docs root, and the ONLY validation left.

Four checks, and deliberately nothing else:

1. ``output/`` exists and holds at least one non-empty, readable ``.md``.
2. No path escape; every published path is a ``.md`` under ``output/``.
3. No credential leak.
4. Internal-path or pipeline-vocabulary leaks are reported as warnings.

What is **not** here is the point of the module. Nothing compares the agent's
prose to a parser identity, a fact id, an internal project id, a required
section heading, a document filename or a mapping row. Those comparisons are
what this lane removes, not relocates. Prior runs had already written valid
deliverables and were then failed because a client-facing project name differed
from an internal parser identifier. Such naming differences no longer block
otherwise safe documents, and a regression test pins that behavior.

Check 3 did not exist anywhere in the codebase before this module: across the
~70 finding codes in the lane it replaces there were zero credential checks.
The gates enforced internal identity, which is not a safety property.

Publication is two-phase, and the order is load-bearing.
:func:`publish_agent_output` validates ``output/`` and holds the accepted
content in memory, the topology compiler runs against the still-intact
``output/``, and :func:`finalize_publication` assembles the deliverable.

**Publication is a CLEAN ROOM, not a cleanup.** Finalization used to delete the
roots ``contract.py`` declares from ``staging_dir`` and then write the accepted
documents into that same directory, which ``services/atomic_swap.py`` renames
to ``docs/`` verbatim. So anything the agent had written ANYWHERE ELSE in
staging survived unvalidated and shipped -- verified by planting
``docs_staging/rogue.md`` and ``docs_staging/rogue-visible/extra.md`` before
finalization: both were present in the published tree, and the first was also
accepted by ``is_published_doc_filename`` and pushed to RAG. Publishing by
survival makes every future directory added anywhere in the pipeline a
publication decision that nobody made.

Finalization now BUILDS a new directory from trusted inputs only -- the
in-memory accepted snapshot plus an explicit, enumerated set of Nexus-owned
internal roots (:data:`NEXUS_OWNED_ROOTS`) -- re-checks the complete result,
and swaps it into place. Nothing reaches ``docs/`` by not having been named in
a cleanup list.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from mcp_server.providers.agent_workspace import contract
from mcp_server.providers.custom.enterprise.client_language import leakage_findings
from mcp_server.providers.custom.source_context import CREDENTIAL_KEY_TOKENS

# Reused, not re-derived: the compiled secret shapes that already fail a
# published topology closed. A second copy of these patterns would be free to
# drift, and a drift means one surface blocks what the other ships.
from mcp_server.services.business_topology.validation import (
    _BEARER_RE,
    _CREDENTIAL_URL_RE,
    _PRIVATE_KEY_RE,
)

logger = logging.getLogger(__name__)

#: Workspace paths that must never appear in client prose. Retargeted from the
#: enterprise ``INTERNAL_PATH_MARKERS`` (``_workspace``, ``_analysis``,
#: ``_exact``, ...) onto this lane's directory names. Every entry is a literal
#: path fragment rather than a bare word: a document may legitimately discuss a
#: job's *output*, so ``output/`` alone would be a false positive generator,
#: while ``notes/progress.md`` cannot be anything but a leak.
WORKSPACE_PATH_MARKERS: tuple[str, ...] = (
    "AGENT_GUIDE.md",
    "GOAL.md",
    "INDEX.md",
    "instructions/",
    "scan/jobs/",
    "facts/navigation/",
    "facts/mappings/",
    "facts/units.json",
    "facts/dependencies.json",
    "facts/interfaces.json",
    "facts/configuration.json",
    "parser_limitations.md",
    "notes/progress.md",
    # The agent's working layers. A client document citing its own scratch
    # analysis or its knowledge-model file is the same leak as citing
    # `notes/progress.md`, and these two were added to the workspace after
    # this list was written -- the exact way a marker list goes stale.
    "notes/analysis/",
    "notes/integrations/",
    "notes/coverage/",
    "notes/completion.json",
    "capability_ledger.json",
)

#: Values that name a credential without being one. Matched case-insensitively
#: against the whole captured value.
_PLACEHOLDER_VALUES = frozenset(
    {
        "changeme",
        "configured",
        "hidden",
        "masked",
        "none",
        "null",
        "omitted",
        "placeholder",
        "provided",
        "redacted",
        "removed",
        "required",
        "secret",
        "set",
        "unknown",
        "unset",
        "value",
    }
)

#: A credential assignment: a key from the shared token list, then a value that
#: has the shape of a literal rather than a reference. Precision is chosen over
#: recall on purpose -- this check BLOCKS a run, and the failure mode of a
#: false positive (a paid estate's documentation held over the word
#: "password") is worse than the failure mode of a missed low-entropy secret,
#: which the scanner-side credential filter already removes upstream.
#: The lookbehind excludes only LETTERS AND DIGITS, deliberately not ``_`` or
#: ``.``. Excluding those too was the original spelling and it silently dropped
#: every compound key -- ``sftp_password``, ``mssql_password``, ``db_secret``,
#: ``client_token``, ``context.ftp_password`` -- which is precisely how real
#: estates name these. Compound credential keys are common; a bare
#: ``password =`` is the exception, not the rule. A leading letter still
#: blocks the match, so ``notapassword`` does not fire.
_CREDENTIAL_ASSIGNMENT_RE = re.compile(
    rf"(?<![A-Za-z0-9])(?P<key>[A-Za-z0-9_.]*?(?:{CREDENTIAL_KEY_TOKENS}))\s*[:=]\s*"
    r"[\"'`]?(?P<value>[^\s\"'`|,;<>\)\]]{6,120})",
    re.IGNORECASE,
)

#: References, not secrets: an environment variable, a context variable, a
#: template slot, a routine call, a path, a sentence continuation.
_REFERENCE_VALUE_RE = re.compile(
    r"^(?:\$|%|@|\{|\(|<|context\.|globalMap|routines\.|env\.|process\.env|"
    r"\.{3}|-{3,}|\*{3,}|x{3,}$)",
    re.IGNORECASE,
)

#: A literal secret has entropy that an English word does not. Requiring a
#: digit or a base64/token symbol is what keeps ``password: configured`` and
#: ``token: refreshed hourly`` out of the blocking set.
_SECRETLIKE_VALUE_RE = re.compile(r"[0-9]|[+/=]")

# High-precision provider credential shapes. Same class as the private-key
# block ``redact_secretlike`` already knows, and both are unambiguous: no
# English prose produces them.
_AWS_ACCESS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")

_NAMED_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("credential in a connection URL", _CREDENTIAL_URL_RE),
    ("bearer token", _BEARER_RE),
    ("private key block", _PRIVATE_KEY_RE),
    ("AWS access key id", _AWS_ACCESS_KEY_RE),
    ("JSON web token", _JWT_RE),
)

#: The ONLY staging entries carried into the clean publication besides the
#: accepted documents. Enumerated rather than derived from a rule, because the
#: rule the previous implementation used -- "whatever the cleanup list forgot"
#: -- is what let ``rogue.md`` publish.
#:
#: Each entry is here because a named consumer reads it back out of ``docs/``
#: after the swap. Verified by grep across ``src/`` on 2026-09-14:
#:
#: * ``_topology`` -- ``api/routes/projects.py:4633`` resolves
#:   ``output/{pid}/docs/_topology`` for the org/landscape view, and
#:   ``4889`` for the per-sub-project evidence. Written by
#:   ``topology.compile_agent_topology`` between the two publication phases.
#: * ``_audit`` -- the run's forensic record. ``opencode_cli`` writes
#:   ``opencode_errors.json``, ``opencode_attempt_ledger.json``,
#:   ``opencode_events.json`` and ``opencode_interrupted_usage/`` under
#:   ``{workspace}/_audit``; ``api/routes/projects.py:2329`` writes the plugin
#:   pack execution receipt into the staged ``_audit`` AFTER this function
#:   returns, and ``_load_enterprise_gate_results`` reads
#:   ``_audit/enterprise_gates.json`` from the same directory.
#: * ``_transient`` -- ``providers/custom/runtime_evidence.py`` caches the
#:   fetched runtime payload at ``_transient/runtime/runtime_evidence.json``.
#:   No producer in THIS lane writes it today; it is carried so that wiring one
#:   in does not silently lose it, which is the exact failure the OpenCode
#:   engine had when it reused a consumer without its producer.
#:
#: DELIBERATELY ABSENT: ``contract.CONTROL`` (``_agent_control/``). It holds the
#: runner's restart checkpoint and the run-bound resume identity, both of which
#: describe an IN-FLIGHT run. A published tree is the end of that run, so the
#: state is meaningless there and it has no consumer -- it survived only
#: because it was not on a removal list, which is the defect class this whole
#: function replaces. Being underscore-prefixed keeps it out of RAG and out of
#: every client listing, but it still landed on the client's disk.
#:
#: Every entry here IS invisible to clients by construction:
#: ``is_published_doc_filename``, ``_count_staged_docs``, the PDF export and
#: the dashboard document list all reject an underscore-prefixed segment. That
#: is a necessary condition for membership, never a sufficient one -- a root is
#: carried because something reads it back, or it is not carried.
NEXUS_OWNED_ROOTS: tuple[Path, ...] = (
    Path("_audit"),
    Path("_topology"),
    Path("_transient"),
)

#: Carried internal artifacts are scanned for credentials up to this size. A
#: provider event log is unbounded and is not client-facing, so scanning all of
#: it would cost more than the finding is worth.
_INTERNAL_SCAN_MAX_BYTES = 2_000_000

#: Suffixes of carried internal artifacts worth scanning at all. A binary
#: carried under ``_audit`` produces only mojibake findings.
_INTERNAL_SCAN_SUFFIXES = frozenset({".md", ".json", ".jsonl", ".txt", ".yaml", ".yml"})


@dataclass(frozen=True)
class PublicationOutcome:
    """What publication accepted, and why it refused if it did.

    ``blocking`` non-empty means nothing will be written: the run fails and
    staging is preserved as a draft, so the operator can read the offending
    file at the path the finding names.

    ``documents`` holds the accepted content in memory between the two phases;
    it is not part of the reported result.
    """

    ok: bool
    published: tuple[str, ...] = ()
    blocking: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    documents: dict[str, str] = field(default_factory=dict, compare=False, repr=False)

    @property
    def doc_count(self) -> int:
        return len(self.published)


def credential_findings(text: str) -> list[str]:
    """Return one finding per credential-shaped span, naming no secret value.

    A finding carries the line number and the matched KEY or pattern name and
    never the value: this string ends up in a run log, an error message and a
    database row, so quoting the match would copy the leak into three more
    places.
    """

    findings: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for label, pattern in _NAMED_SECRET_PATTERNS:
            if pattern.search(line):
                findings.append(f"line {line_number}: {label}")
        for match in _CREDENTIAL_ASSIGNMENT_RE.finditer(line):
            value = match.group("value").strip().strip("\"'`")
            if not value or value.casefold() in _PLACEHOLDER_VALUES:
                continue
            if "redacted" in value.casefold() or _REFERENCE_VALUE_RE.match(value):
                continue
            if not _SECRETLIKE_VALUE_RE.search(value):
                continue
            findings.append(f"line {line_number}: literal value assigned to {match.group('key')!r}")
    return sorted(dict.fromkeys(findings))


def internal_leak_findings(text: str) -> list[str]:
    """Internal paths, pipeline vocabulary and internal identifiers.

    ``leakage_findings`` is reused whole rather than re-implemented; its marker
    set covers the underscore-prefixed staging directories and the pipeline
    phrases (``scanner detail is limited``, an Evidence Register, a raw
    ``mapping_table:`` reference), and this lane adds its own workspace paths on
    top. Note the reused function also treats an HTML comment as a leak; that
    is deliberate and inherited -- a client document should not ship commented
    scaffolding.
    """

    findings = list(leakage_findings(text))
    findings.extend(
        f"workspace path {marker!r}" for marker in WORKSPACE_PATH_MARKERS if marker in text
    )
    return sorted(dict.fromkeys(findings))


def _relative_publish_path(path: Path, output_root: Path) -> Path | None:
    """Return the path relative to ``output/``, or ``None`` if it escapes.

    ``resolve()`` on both sides is what makes this a containment check and not
    just a string check. It is defence in depth behind :func:`_walk_regular`,
    which refuses a symlink before this is ever reached.
    """

    try:
        return path.resolve(strict=True).relative_to(output_root)
    except (OSError, ValueError):
        return None


def _is_visible(relative: Path) -> bool:
    return all(not part.startswith(("_", ".")) for part in relative.parts)


def _walk_regular(root: Path) -> tuple[list[Path], list[Path]]:
    """Recurse ``root`` WITHOUT following symlinks. Returns (files, symlinks).

    Explicit rather than ``rglob``, because ``rglob``'s symlink behaviour is a
    function of the interpreter version and this project supports 3.11+:
    ``**`` descended into symlinked directories up to 3.12 and stopped doing so
    in 3.13. Measured here on 3.14.6 -- ``Path('out').rglob('*')`` over an
    ``out/linkdir -> /tmp/target`` yields ``linkdir`` and NOT
    ``linkdir/inside.md``. So on 3.11 a symlinked directory inside ``output/``
    published its target's contents, and on 3.14 the same link is silently
    skipped with no finding at all. Both are wrong in different directions,
    which is precisely why the walk is written out.

    A symlink is returned separately rather than resolved: the caller refuses
    it. ``providers/custom/enterprise/workspace.py:146`` already refuses
    symlinks on the input side, and this converges on that behaviour rather
    than inventing a second one.
    """

    files: list[Path] = []
    symlinks: list[Path] = []
    stack = [root]
    seen: set[tuple[int, int]] = set()
    while stack:
        current = stack.pop()
        try:
            entries = sorted(os.scandir(current), key=lambda entry: entry.name)
        except OSError:
            continue
        for entry in entries:
            path = Path(entry.path)
            if entry.is_symlink():
                symlinks.append(path)
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    # Hardlinked-directory loops are not creatable on the
                    # platforms this runs on, but the guard costs one stat and
                    # a walk that never terminates is not a recoverable error.
                    key = (entry.stat(follow_symlinks=False).st_dev, entry.inode())
                    if key in seen:
                        continue
                    seen.add(key)
                    stack.append(path)
                elif entry.is_file(follow_symlinks=False):
                    files.append(path)
            except OSError:
                continue
    return sorted(files), sorted(symlinks)


def evidence_tampering_findings(staging_dir: Path, expected_digest: str) -> list[str]:
    """Blocking finding when the evidence changed while the agent was running.

    This is the enforcement half of the write boundary. Path-scoped write
    permissions are inert in this runtime (measured -- see
    ``contract.writable_globs``), so ``edit`` is granted workspace-wide and
    the evidence is protected by comparing digests instead.

    An empty ``expected_digest`` skips the check: this function stays a pure
    check over what it is given, and supplying the baseline is the RUNNER's
    job -- it always has one, because the builder returns it on every
    receipt. ``test_the_runner_always_supplies_an_evidence_baseline`` is what
    stops that from silently regressing into an unchecked production path.
    """

    from mcp_server.providers.agent_workspace.workspace import evidence_digest

    if not expected_digest:
        return []
    actual = evidence_digest(Path(staging_dir))
    if actual == expected_digest:
        return []
    return [
        (
            "the evidence workspace was modified during the run "
            f"(expected {expected_digest[:12]}, found {actual[:12]}); "
            "scan/, facts/, instructions/ and runtime/ are read-only inputs"
        )
    ]


def publish_agent_output(
    staging_dir: Path, *, expected_evidence_digest: str = ""
) -> PublicationOutcome:
    """Run the checks over ``output/**/*.md`` and accept what passes.

    Nothing is written here; :func:`finalize_publication` does that. Relative
    paths are preserved, so ``output/integrations/x.md`` becomes
    ``docs/integrations/x.md`` once the staging directory is swapped in. The
    same relative path is the RAG filename, preserving nested client-facing
    document organization without involving legacy partitioning logic.
    """

    staging_dir = Path(staging_dir)
    output_dir = staging_dir / contract.OUTPUT
    if not output_dir.is_dir():
        return PublicationOutcome(
            ok=False,
            blocking=("the agent produced no output/ directory",),
        )
    output_root = output_dir.resolve()

    blocking: list[str] = []
    warnings: list[str] = []
    #: relative path -> content, staged in memory so a blocking finding in the
    #: last document cannot leave the first ones already copied.
    accepted: dict[Path, str] = {}

    files, symlinks = _walk_regular(output_dir)
    for link in symlinks:
        # BLOCKING, and for a link that points inside ``output/`` too. The
        # target is not the only problem: the link is re-pointable between the
        # moment it is validated and the moment it is read, and a symlinked
        # DIRECTORY changes what the walk even sees depending on the
        # interpreter version. There is no case in which the agent needs one.
        try:
            label = link.relative_to(output_dir).as_posix()
        except ValueError:  # pragma: no cover - link is always under output_dir
            label = link.name
        blocking.append(f"output/{label}: symbolic link; symlinks are never published")

    for path in files:
        relative = _relative_publish_path(path, output_root)
        if relative is None:
            # Named, not silently dropped: a path escape is the one file-level
            # problem an operator cannot see by listing the directory.
            blocking.append(f"output/{path.name}: resolves outside output/ and was not published")
            continue
        if path.suffix.lower() != ".md":
            continue
        if not _is_visible(relative):
            warnings.append(
                f"output/{relative.as_posix()}: skipped, a path segment is marked internal"
            )
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            warnings.append(f"output/{relative.as_posix()}: unreadable ({type(exc).__name__})")
            continue
        if not content.strip():
            warnings.append(f"output/{relative.as_posix()}: empty, not published")
            continue
        label = relative.as_posix()
        blocking.extend(f"{label}: {finding}" for finding in credential_findings(content))
        # ADVISORY, not blocking. A section heading, a term we happen to use
        # internally, or a stray path is a cosmetic problem in a document that
        # is otherwise correct -- and failing a finished paid run over one is
        # the exact behaviour this lane exists to remove. Run 241 wrote four
        # good documents and was refused for the phrase "Scanner-Limited",
        # which our own instructions had told it to use as a heading.
        #
        # Credentials stay blocking. That is a safety property; naming is not.
        warnings.extend(f"{label}: {finding}" for finding in internal_leak_findings(content))
        accepted[relative] = content

    if not accepted and not blocking:
        blocking.append("output/ contains no non-empty readable Markdown document")
    # The write boundary, enforced. `edit` is granted workspace-wide because
    # path-scoped rules are inert in this runtime, so the evidence is
    # protected here instead -- and by digest, which catches a modification
    # by any means rather than only one the permission layer recognised.
    blocking.extend(evidence_tampering_findings(staging_dir, expected_evidence_digest))
    if blocking:
        return PublicationOutcome(
            ok=False,
            blocking=tuple(sorted(dict.fromkeys(blocking))),
            warnings=tuple(warnings),
        )

    documents = {relative.as_posix(): content for relative, content in accepted.items()}
    return PublicationOutcome(
        ok=True,
        published=tuple(sorted(documents)),
        warnings=tuple(warnings),
        documents=documents,
    )


@dataclass(frozen=True)
class PublicationAssembly:
    """What the clean room contains, and what it refused to let in.

    ``ok`` false means the staging directory was NOT replaced: the draft is
    intact, the previously published ``docs/`` is untouched, and the caller
    must not swap.
    """

    ok: bool
    published: tuple[str, ...] = ()
    carried: tuple[str, ...] = ()
    excluded: tuple[str, ...] = ()
    blocking: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def _carry_nexus_state(staging_dir: Path, clean_dir: Path) -> tuple[list[str], list[str]]:
    """Copy the enumerated Nexus-owned roots into the clean publication.

    Regular files only, symlinks never followed, and every destination checked
    to still be inside ``clean_dir`` after resolution -- the sources are
    directories the agent could also have written into, so this is a copy
    across a trust boundary even though the roots themselves are Nexus's.
    """

    carried: list[str] = []
    warnings: list[str] = []
    for root in NEXUS_OWNED_ROOTS:
        source = staging_dir / root
        if source.is_symlink() or not source.is_dir():
            continue
        files, symlinks = _walk_regular(source)
        for link in symlinks:
            warnings.append(
                f"{root.as_posix()}/{link.name}: symbolic link in Nexus state, not carried"
            )
        for path in files:
            relative = root / path.relative_to(source)
            destination = clean_dir / relative
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
            except OSError as exc:
                warnings.append(f"{relative.as_posix()}: not carried ({type(exc).__name__})")
                continue
            carried.append(relative.as_posix())
    return carried, warnings


def _final_publication_findings(clean_dir: Path) -> tuple[list[str], list[str]]:
    """Credential scan over the COMPLETE assembled publication.

    Two severities, and the split is deliberate:

    * A **client-facing** path (no underscore- or dot-prefixed segment) is what
      a client reads and what RAG ingests, so a finding there BLOCKS. These
      bytes were already scanned in :func:`publish_agent_output`; re-scanning
      them here is what makes "every published byte was checked" a property of
      the assembled directory rather than of one code path.
    * A **carried internal** artifact is visible to operators only -- no client
      surface, no RAG layer, no PDF export accepts an underscore segment -- and
      it legitimately quotes provider error text. A finding there is a warning.
      Blocking on it would let one quoted connection string in a provider error
      event hold a finished paid run, which is the failure mode this lane
      exists to remove.
    """

    blocking: list[str] = []
    warnings: list[str] = []
    files, _symlinks = _walk_regular(clean_dir)
    for path in files:
        relative = path.relative_to(clean_dir)
        label = relative.as_posix()
        client_facing = _is_visible(relative)
        if not client_facing:
            if path.suffix.lower() not in _INTERNAL_SCAN_SUFFIXES:
                continue
            try:
                if path.stat().st_size > _INTERNAL_SCAN_MAX_BYTES:
                    continue
            except OSError:
                continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            if client_facing:
                blocking.append(f"{label}: unreadable in the assembled publication")
            continue
        findings = [f"{label}: {finding}" for finding in credential_findings(content)]
        if client_facing:
            blocking.extend(findings)
        else:
            warnings.extend(f"internal artifact {item}" for item in findings)
    return blocking, warnings


def _replace_staging(staging_dir: Path, clean_dir: Path) -> None:
    """Swap the clean publication in for the staging draft.

    Two renames with a rollback, the same shape as ``services/atomic_swap``:
    a crash between them leaves both directories on disk under names an
    operator can see, rather than leaving nothing where ``docs_staging`` was.
    ``swap_docs`` then renames the result to ``docs/`` exactly as before -- this
    function deliberately does not touch ``docs/`` itself, so the one place
    that replaces published documentation stays one place.
    """

    discarded = staging_dir.parent / f"{staging_dir.name}.discarded"
    if discarded.exists():
        shutil.rmtree(discarded, ignore_errors=True)
    os.rename(staging_dir, discarded)
    try:
        os.rename(clean_dir, staging_dir)
    except OSError:
        if not staging_dir.exists():
            os.rename(discarded, staging_dir)
        raise
    shutil.rmtree(discarded, ignore_errors=True)


def finalize_publication(staging_dir: Path, outcome: PublicationOutcome) -> PublicationAssembly:
    """Assemble a NEW publication directory and swap it in for the draft.

    The clean room, in the order the plan states it:

    1. accepted content is already an immutable in-memory snapshot
       (``outcome.documents``, produced and validated by
       :func:`publish_agent_output`);
    2. a new empty directory is created beside the staging draft;
    3. only accepted Markdown is written into it, at its ``output/``-relative
       path, so ``output/integrations/orders.md`` becomes
       ``integrations/orders.md``;
    4. the deterministic reference registers ride in with it -- they are
       compiler-authored Markdown under ``output/reference/`` and the runner
       rebuilds them from the scan before this phase, so they are trusted code
       output that passed the same checks;
    5. the compiled topology and the rest of :data:`NEXUS_OWNED_ROOTS` are
       copied from staging;
    6. the complete assembled directory is re-checked
       (:func:`_final_publication_findings`);
    7. the draft is atomically replaced by the clean directory, and
       ``services/atomic_swap.swap_docs`` renames that to ``docs/``.

    The evidence workspace is never published: it is simply not copied. That
    is the whole change. The previous implementation deleted
    ``contract.EVIDENCE_ROOTS`` and ``contract.WRITABLE_ROOTS`` from staging
    and wrote into what was left, so a file the list did not name shipped --
    reproduced with ``docs_staging/rogue.md`` and
    ``docs_staging/rogue-visible/extra.md``, both of which reached the
    published tree, the first of them also reaching RAG.

    Never raises for a blocked assembly: it returns ``ok=False`` with the
    staging draft untouched. It still raises ``ValueError`` when handed an
    outcome that was already blocked, because that is a caller bug.
    """

    staging_dir = Path(staging_dir)
    if not outcome.ok:
        raise ValueError("refusing to finalize a publication that was blocked")

    clean_dir = staging_dir.parent / f"{staging_dir.name}.clean"
    if clean_dir.exists():
        shutil.rmtree(clean_dir, ignore_errors=True)
    clean_dir.mkdir(parents=True)

    warnings: list[str] = []
    try:
        for relative_name, content in sorted(outcome.documents.items()):
            destination = clean_dir / relative_name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")

        carried, carry_warnings = _carry_nexus_state(staging_dir, clean_dir)
        warnings.extend(carry_warnings)

        blocking, scan_warnings = _final_publication_findings(clean_dir)
        warnings.extend(scan_warnings)
        if blocking:
            return PublicationAssembly(
                ok=False,
                blocking=tuple(sorted(dict.fromkeys(blocking))),
                warnings=tuple(dict.fromkeys(warnings)),
            )

        excluded = _excluded_staging_entries(staging_dir)
        _replace_staging(staging_dir, clean_dir)
    except Exception:
        shutil.rmtree(clean_dir, ignore_errors=True)
        raise

    return PublicationAssembly(
        ok=True,
        published=tuple(sorted(outcome.documents)),
        carried=tuple(sorted(carried)),
        excluded=excluded,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _excluded_staging_entries(staging_dir: Path) -> tuple[str, ...]:
    """Top-level staging entries the clean room did not carry.

    Reported rather than silently dropped. The evidence roots and the agent's
    scratch are expected here; anything ELSE in this list is a file the agent
    wrote outside ``output/``, which is exactly the signal an operator needs
    and never got before -- ``rogue.md`` used to publish without a line in the
    log.
    """

    carried_names = {root.parts[0] for root in NEXUS_OWNED_ROOTS}
    try:
        entries = sorted(entry.name for entry in os.scandir(staging_dir))
    except OSError:
        return ()
    return tuple(name for name in entries if name not in carried_names)
