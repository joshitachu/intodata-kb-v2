"""The ONE door between approved scanner bytes and the agent workspace.

Every byte that reaches a documentation agent comes out of this module, and
the point of that is not tidiness: it is that "no path bypasses verification"
becomes checkable by grep instead of by review. A caller hands over a relative
path and receives parsed data or a typed refusal; it never holds a ``Path`` it
opened itself.

Three verified defects are the reason this exists, all of them found by
planting a file in a scan directory that a receipt had already approved:

**A symlink was invisible on both sides of the comparison.**
``workspace._tree_relative_paths`` skipped symlinks while walking the tree,
and the scanner skips them while writing it, so a link planted afterwards was
neither ``added`` nor ``removed``. ``scan/xslt.json`` is optional -- an estate
with no stylesheets legitimately has none -- so on such a scan the name was
free, and ``_stage_scan`` opened it by fixed filename. Reproduced: an
arbitrary host file was staged into the workspace and
``verify_scan_is_publishable`` returned with ``notices == []``.

**The membership rule came out of the file it was supposed to bind.**
``roots`` and ``excluded_prefixes`` were read from the receipt, so narrowing
``roots`` to ``["scan/jobs"]`` in a tampered receipt left every estate-level
collection outside the walk: unbound, unverified, and still staged by name.
Reproduced with a leaked password staged verbatim. The rule is now
:data:`APPROVAL_ROOTS` / :data:`APPROVAL_EXCLUDED_PREFIXES` -- values this
code owns, compared against the receipt's claim rather than taken from it.

**A recognised filename was a licence to read.** The estate collections are a
fixed list, so an optional one absent from the receipt was opened anyway if it
happened to exist on disk. Membership in the receipt is now the entry
criterion, and the filename list only decides what is *asked for*.

``providers/custom/enterprise/workspace.py:146`` already refused symlinks
correctly in the sibling lane; this converges on that behaviour rather than
inventing a second one, and adds ``O_NOFOLLOW`` plus an ``fstat`` on the open
descriptor so the check is on the file that was actually opened rather than on
a name that was resolved a moment earlier.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from mcp_server.providers.agent_workspace.contract import (
    WorkspaceStage,
    WorkspaceUnavailableError,
)

#: Scanner receipt that authorises publication of the scan's bytes, and the
#: redaction record that corroborates it. Both are the ROOT of trust rather
#: than approved artifacts: nothing can verify them against themselves, which
#: is exactly why they get their own reader with no digest step.
APPROVAL_FILENAME = "publication_approval.json"
REDACTION_REPORT_FILENAME = "redaction_report.json"

#: The one tree-digest algorithm this builder knows how to recompute. A
#: receipt naming another one is refused rather than skipped: an unverifiable
#: binding and no binding are the same thing, and only one of them looks safe.
#: Mirrors ``intodoc.cli._APPROVAL_TREE_ALGORITHM``.
APPROVAL_TREE_ALGORITHM = "sha256-relpath-pairs-1"

#: The agent-eligible membership rule, OWNED HERE. Mirrors
#: ``intodoc.cli._AGENT_ARTIFACT_ROOTS`` / ``_AGENT_ARTIFACT_EXCLUDED_PREFIXES``,
#: and the receipt's own copy is checked against it rather than believed.
#:
#: Reading the rule out of the receipt was defensible while the receipt was
#: trusted -- two copies of a membership rule do drift. It stopped being
#: defensible the moment the receipt became the thing under attack: a narrowed
#: ``roots`` shrinks the walk, and every artifact outside the narrowed walk is
#: then neither compared nor hashed while still being staged by name.
APPROVAL_ROOTS: tuple[str, ...] = ("scan",)
APPROVAL_EXCLUDED_PREFIXES: tuple[str, ...] = ("scan/evidence/", "scan/sanitized/")

#: Aggregates the receipt must hash by name. Not the bytes the workspace
#: stages -- those are the tree -- but the four the scanner's own redaction
#: verification ran against, so a receipt missing one is a receipt from a
#: different pipeline.
REQUIRED_APPROVED_ARTIFACTS: frozenset[str] = frozenset(
    {
        "scan_result.json",
        "scan/manifest.json",
        "scan/lineage.json",
        "scan/landscape.json",
    }
)

#: The minimum agent-eligible tree. Every one of these is opened by the
#: builder on every run, so a receipt that does not declare one cannot produce
#: a workspace -- and the refusal belongs at the approval boundary, where it
#: names the receipt, rather than 200 lines later as a missing file.
REQUIRED_APPROVED_TREE_FILES: frozenset[str] = frozenset(
    {
        "scan/manifest.json",
        "scan/meta.json",
        "scan/contexts.json",
        "scan/connections.json",
        "scan/routines.json",
        "scan/landscape.json",
        "scan/lineage.json",
        "scan/mapping_accounting.json",
        "scan/capability_ledger.json",
    }
)

#: Ceiling on one approved artifact, in bytes. Not a readability target (that
#: is ``workspace.MAX_STAGED_FILE_BYTES``, 2 MB, and applies to what is
#: STAGED): this is the point past which reading a file into memory is a
#: denial of service rather than a read. The largest artifact this lane is
#: known to handle is the 21 MB ``scan_result.json`` recorded in
#: ``workspace.py``'s own docstring, so the ceiling is set well above it and
#: still far below anything that would exhaust a server process.
MAX_APPROVED_ARTIFACT_BYTES = 64_000_000

#: How many offending paths a refusal names before it stops. Enough to act on,
#: bounded because the message reaches a log line and a database row.
_MAX_NAMED_OFFENDERS = 5

_READ_CHUNK_BYTES = 1024 * 1024


def offenders(label: str, paths: Iterable[str]) -> str:
    """``label: a, b, c, and N more`` -- paths only, never contents."""

    named = list(paths)
    head = ", ".join(named[:_MAX_NAMED_OFFENDERS])
    if len(named) > _MAX_NAMED_OFFENDERS:
        head += f", and {len(named) - _MAX_NAMED_OFFENDERS} more"
    return f"{label}: {head}"


def _refuse(message: str, *, stage: WorkspaceStage = "approval") -> WorkspaceUnavailableError:
    return WorkspaceUnavailableError(message, stage=stage)


def path_is_unsafe(relative: str) -> bool:
    """``True`` for an absolute path, a traversal, or an empty segment.

    A receipt is a file on disk and every field in it is DATA. This is the
    cheap syntactic half of the check; :meth:`ApprovedArtifactReader.resolve`
    is the half that involves the filesystem.
    """

    text = str(relative).strip()
    if not text:
        return True
    candidate = Path(text)
    return candidate.is_absolute() or ".." in candidate.parts or "" in candidate.parts


def resolves_outside(scan_dir: Path, relative: str) -> bool:
    """``True`` when ``relative`` lands outside ``scan_dir`` once resolved.

    The filesystem half of :func:`path_is_unsafe`, kept here for the same
    reason every other filesystem touch is: joining a receipt-supplied string
    onto the scan directory is exactly the operation this module exists to be
    the only site of.
    """

    scan_dir = Path(scan_dir)
    return not (scan_dir / relative).resolve().is_relative_to(scan_dir.resolve())


# ---------------------------------------------------------------------------
# Root of trust
# ---------------------------------------------------------------------------


def read_root_of_trust(scan_dir: Path, filename: str, *, absent_hint: str = "") -> Any:
    """Read the approval receipt or the redaction report, with no digest.

    These two files cannot be verified against anything -- they are what the
    verification is made of -- so they get the structural guards (no symlink,
    regular file, bounded size) and nothing else. They live at the scan-output
    root and are named by this module, never by data.

    ``absent_hint`` is what an operator should DO about it. Each of the two
    files is absent for its own reason and the remedies differ, so the caller
    supplies the sentence rather than this module guessing.
    """

    scan_dir = Path(scan_dir)
    path = scan_dir / filename
    if not path.is_file() or path.is_symlink():
        raise _refuse(f"the scan has no {filename}. {absent_hint}".strip())
    with _open_regular_file(path, label=filename) as handle:
        data = handle.read(MAX_APPROVED_ARTIFACT_BYTES + 1)
    if len(data) > MAX_APPROVED_ARTIFACT_BYTES:
        raise _refuse(f"{filename} is larger than this builder will read")
    return _decode_json(data, label=filename)


# ---------------------------------------------------------------------------
# The reader
# ---------------------------------------------------------------------------


class ApprovedArtifactReader:
    """Open, hash, compare and parse. Callers do none of the four.

    Constructed from a receipt that has already passed its structural checks,
    and holding the union of the receipt's ``artifacts`` map and its tree.
    Membership in that union is the entry criterion for every read: a
    recognised filename is not one, and neither is existing on disk.
    """

    def __init__(
        self,
        scan_dir: Path,
        digests: Mapping[str, str],
        *,
        max_bytes: int = MAX_APPROVED_ARTIFACT_BYTES,
    ) -> None:
        self._scan_dir = Path(scan_dir)
        self._scan_root = self._scan_dir.resolve()
        self._digests = {str(key): str(value) for key, value in digests.items()}
        self._max_bytes = int(max_bytes)

    # -- membership ------------------------------------------------------

    @property
    def approved_paths(self) -> frozenset[str]:
        """Every relative path the receipt binds. The whole readable surface."""

        return frozenset(self._digests)

    @property
    def approval_digest(self) -> str:
        """One digest over the WHOLE approved surface, paths and hashes.

        The identity of these bytes for anything that has to ask "is this the
        same scan". A rescan that reproduces every filename still changes it,
        which is the answer that keeps a previous run's drafts from being
        read as evidence about this one.
        """

        return tree_digest(self._digests)

    def has(self, relative: str) -> bool:
        """``True`` when the receipt binds this path, syntax permitting.

        Callers ask this about an OPTIONAL artifact. It deliberately does not
        touch the filesystem: "the scan has no stylesheets" and "a stylesheet
        file appeared after approval" must not be the same answer.
        """

        return not path_is_unsafe(relative) and str(relative).strip() in self._digests

    # -- reads -----------------------------------------------------------

    def read_bytes(self, relative: str) -> bytes:
        """The approved bytes of one artifact, or a refusal.

        ONE open, ONE read, and the digest is taken over the buffer that is
        returned. Hashing the path and then reading it again is two different
        files whenever anything is racing the build, and the second one is the
        one the agent would have received.
        """

        path, expected = self.resolve(relative)
        with _open_regular_file(path, label=str(relative), max_bytes=self._max_bytes) as handle:
            data = handle.read(self._max_bytes + 1)
        if len(data) > self._max_bytes:
            raise _refuse(
                f"approved artifact {relative} grew past the {self._max_bytes} byte "
                "ceiling while it was being read; the scan is being modified. Rescan."
            )
        actual = f"sha256:{hashlib.sha256(data).hexdigest()}"
        if actual != expected:
            raise _refuse(
                f"approved artifact {relative} does not match its approved digest "
                f"(approved {expected}, on disk {actual}). The scan was modified "
                "after approval; rescan before generating documentation."
            )
        return data

    def read_json(self, relative: str) -> Any:
        """Parse from the bytes that were verified, never by reopening."""

        return _decode_json(self.read_bytes(relative), label=str(relative))

    def verify(self, relative: str) -> None:
        """Prove one artifact matches its approved digest, without holding it.

        The streaming counterpart of :meth:`read_bytes`, for the whole-tree
        pass where every byte is hashed and none is kept. Same guard chain, so
        a symlinked or non-regular artifact is refused here too.
        """

        path, expected = self.resolve(relative)
        digest = hashlib.sha256()
        size = 0
        with _open_regular_file(path, label=str(relative), max_bytes=self._max_bytes) as handle:
            for chunk in iter(lambda: handle.read(_READ_CHUNK_BYTES), b""):
                size += len(chunk)
                if size > self._max_bytes:
                    raise _refuse(
                        f"approved artifact {relative} is larger than the "
                        f"{self._max_bytes} byte ceiling this builder will read"
                    )
                digest.update(chunk)
        actual = f"sha256:{digest.hexdigest()}"
        if actual != expected:
            raise _refuse(
                f"approved artifact {relative} does not match its approved digest "
                f"(approved {expected}, on disk {actual}). The scan was modified "
                "after approval; rescan before generating documentation."
            )

    # -- the guard chain -------------------------------------------------

    def resolve(self, relative: str) -> tuple[Path, str]:
        """The one place a relative path becomes a path on this host.

        Order matters. Membership first, because it is the cheapest and the
        only one that cannot be raced; then the filesystem checks, on every
        ancestor as well as on the artifact itself. A symlinked PARENT
        directory is the variant a per-file check misses, and it moves the
        whole subtree somewhere else.
        """

        text = str(relative).strip()
        if path_is_unsafe(text):
            raise _refuse(
                f"{APPROVAL_FILENAME} names an unsafe artifact path; the receipt is not trustworthy"
            )
        expected = self._digests.get(text)
        if expected is None:
            raise _refuse(
                f"{text} is not covered by the publication approval, so nothing "
                "proves what these bytes are. A file the receipt does not name is "
                "never read, whatever it is called. Rescan the project."
            )
        candidate = self._scan_dir / text
        for ancestor in _ancestors(self._scan_dir, candidate):
            if ancestor.is_symlink():
                raise _refuse(
                    f"a parent directory of the approved artifact {text} is a "
                    "symlink, so its bytes are not the ones that were approved. "
                    "The scan was modified after approval; rescan."
                )
        # `lstat`, so the answer is about the entry itself. `Path.is_file()`
        # reports a fifo as absent, which is the wrong diagnosis for an
        # operator and a dangerous one for anyone reading the refusal: the
        # name is taken, it just is not a file.
        try:
            info = candidate.lstat()
        except OSError as exc:
            raise _refuse(f"approved artifact {text} is missing from the scan output") from exc
        if stat.S_ISLNK(info.st_mode):
            raise _refuse(
                f"the approved artifact {text} is a symlink, so its bytes are not "
                "the ones that were approved. The scan was modified after "
                "approval; rescan before generating documentation."
            )
        if not stat.S_ISREG(info.st_mode):
            raise _refuse(
                f"the approved artifact {text} is not a regular file, so its bytes "
                "are not the ones that were approved. The scan was modified after "
                "approval; rescan the project."
            )
        if not candidate.resolve().is_relative_to(self._scan_root):
            raise _refuse(
                f"the approved artifact {text} resolves outside the scan directory; "
                "the scan output is not trustworthy. Rescan the project."
            )
        return candidate, expected


# ---------------------------------------------------------------------------
# The membership walk
# ---------------------------------------------------------------------------


def approved_tree_paths(scan_dir: Path) -> tuple[list[str], list[str]]:
    """Execute the OWNED membership rule over a scan directory.

    Returns ``(regular files, symlinks)``, both relative and sorted. The
    second list is the fix for the verified bypass: the scanner creates no
    symlinks, so anything returned there was planted, and the caller decides
    whether it is reachable. Dropping them on the floor -- which is what both
    sides did -- makes a planted link neither an addition nor a removal.

    Directory symlinks are not descended into (``rglob`` does not follow them),
    so a link is reported as one entry rather than as a subtree.
    """

    scan_dir = Path(scan_dir)
    found: list[str] = []
    links: list[str] = []
    for root in APPROVAL_ROOTS:
        base = scan_dir / root
        if not base.is_dir() or base.is_symlink():
            continue
        for path in sorted(base.rglob("*")):
            relative = path.relative_to(scan_dir).as_posix()
            if any(relative.startswith(prefix) for prefix in APPROVAL_EXCLUDED_PREFIXES):
                continue
            if path.is_symlink():
                links.append(relative)
            elif path.is_file():
                found.append(relative)
    return sorted(found), sorted(links)


def tree_digest(files: Mapping[str, str]) -> str:
    """Digest over ``relpath + sha256`` pairs in sorted order.

    Byte-stable by construction: sorted keys, an explicit separator between
    the path and the digest so ``a/b`` + ``c`` cannot collide with ``a`` +
    ``b/c``, and ASCII-encoded hashes.
    """

    digest = hashlib.sha256()
    for relative in sorted(files):
        digest.update(str(relative).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(files[relative]).encode("utf-8"))
        digest.update(b"\n")
    return f"sha256:{digest.hexdigest()}"


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _ancestors(root: Path, candidate: Path) -> list[Path]:
    """Every directory between ``root`` (exclusive) and ``candidate``."""

    chain: list[Path] = []
    current = candidate.parent
    while current != root and root in current.parents:
        chain.append(current)
        current = current.parent
    return chain


def _open_regular_file(path: Path, *, label: str, max_bytes: int = MAX_APPROVED_ARTIFACT_BYTES):
    """Open ``path`` for reading, refusing anything that is not a plain file.

    ``O_NOFOLLOW`` and an ``fstat`` on the returned descriptor, rather than a
    ``stat`` on the name: the check is then on the file that was actually
    opened. A fifo, a socket, a device node or a directory is refused here
    -- reading one either blocks forever or returns bytes that no digest in
    the receipt describes.
    """

    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise _refuse(
            f"the approved artifact {label} could not be opened as a plain file "
            f"({type(exc).__name__}); the scan output is not trustworthy. Rescan."
        ) from exc
    handle = os.fdopen(descriptor, "rb")
    try:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise _refuse(
                f"the approved artifact {label} is not a regular file, so its bytes "
                "are not the ones that were approved. Rescan the project."
            )
        if info.st_size > max_bytes:
            raise _refuse(
                f"approved artifact {label} is {info.st_size} bytes, past the "
                f"{max_bytes} byte ceiling this builder will read"
            )
    except BaseException:
        handle.close()
        raise
    return handle


def _decode_json(data: bytes, *, label: str) -> Any:
    try:
        return json.loads(data.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise _refuse(f"{label} is not valid UTF-8 and cannot be read as JSON") from exc
    except json.JSONDecodeError as exc:
        # Position, never the offending text: this message reaches a run log
        # and a database row.
        raise _refuse(
            f"{label} could not be read as JSON (line {exc.lineno}, column {exc.colno})"
        ) from exc
