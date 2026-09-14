"""Resilient, case-exact filesystem traversal for every scanner walk.

Two rules live here because both were learned the hard way on a real customer
upload, and both must hold at every walk site rather than at one of them.

**A single unreadable directory must not abort a scan.** `Path.rglob` has no
error hook in 3.11, so one `OSError` anywhere below the root propagates out of
the whole generator and kills the scan. That is not hypothetical: on a Docker
Desktop macOS bind mount (`type fakeowner`) a recursive enumeration
intermittently returns `ENOENT` for a directory that `stat`, `listdir` and
`scandir` all resolve correctly moments later, and one such hiccup aborted a
7,295-file Talend scan at the XSLT discovery pass. Permission-denied
directories and directories removed by a concurrent process fail the same way.
`walk_files` retries a failing directory once, then reports it and continues,
so the outcome is a named warning about one subtree instead of no scan at all.

**A directory name must match the platform convention exactly.** macOS and
Windows mounts are case-insensitive, so `(root / "process").is_dir()` is True
for a customer data folder called `Process`. That is how
`.../WorkOrderData/Process/Processed` came to be registered as a Talend
execution root. `child_dir` compares against the real name returned by
`scandir`, and `case_variant_names` lets a caller say out loud that a
differently-cased directory was found and deliberately not treated as the
real one.
"""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

#: Called with the directory that could not be read and the error it raised.
WalkErrorHandler = Callable[[Path, OSError], None]


def warn_handler(warn: Callable[..., None], phase: str) -> WalkErrorHandler:
    """Adapt a parser ``_warn`` into an ``on_error`` handler.

    One definition of the message, so every traversal reports an unreadable
    directory the same way and none of them can quietly report nothing.
    """

    def handle(directory: Path, exc: OSError) -> None:
        warn(
            kind="unreadable_directory",
            file=directory,
            message=(
                f"Directory could not be enumerated ({type(exc).__name__}: "
                f"errno {exc.errno}); its contents are absent from this scan."
            ),
            phase=phase,
        )

    return handle


def _scandir_entries(
    directory: Path, on_error: WalkErrorHandler | None,
) -> list[os.DirEntry[str]]:
    """Entries of one directory; a persistent failure is reported, not raised.

    The single retry is deliberate and bounded. A transient bind-mount ENOENT
    succeeds on the second call, and recovering the real files is better than
    warning about evidence that is actually present. A directory that fails
    twice is reported once and skipped.
    """
    for attempt in (0, 1):
        try:
            with os.scandir(directory) as iterator:
                return list(iterator)
        except OSError as exc:
            if attempt == 0:
                continue
            if on_error is not None:
                on_error(directory, exc)
            return []
    return []


def walk_files(
    root: Path,
    *,
    patterns: Sequence[str] = ("*",),
    on_error: WalkErrorHandler | None = None,
) -> Iterator[Path]:
    """Yield every file below ``root`` whose name matches one of ``patterns``.

    Directory symlinks are not followed, matching ``Path.rglob``. File symlinks
    are yielded so a caller can classify or exclude them itself. Yield order is
    unspecified; callers that need determinism sort the result, as they already
    did with ``rglob``.
    """
    root = Path(root)
    if not root.is_dir():
        return
    pending: list[Path] = [root]
    while pending:
        current = pending.pop()
        for entry in _scandir_entries(current, on_error):
            child = current / entry.name
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                # An entry whose type cannot be determined is not silently
                # dropped: treat it as a file and let the caller classify it.
                is_dir = False
            if is_dir:
                pending.append(child)
                continue
            if any(fnmatch.fnmatch(entry.name, pattern) for pattern in patterns):
                yield child


def walk_dirs(
    root: Path, *, on_error: WalkErrorHandler | None = None,
) -> Iterator[Path]:
    """Yield every directory below ``root``, tolerating unreadable subtrees."""
    root = Path(root)
    if not root.is_dir():
        return
    pending: list[Path] = [root]
    while pending:
        current = pending.pop()
        for entry in _scandir_entries(current, on_error):
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            child = current / entry.name
            yield child
            pending.append(child)


def child_dirs(
    parent: Path, *, on_error: WalkErrorHandler | None = None,
) -> list[Path]:
    """Immediate subdirectories of ``parent``, tolerating an unreadable parent."""
    return [
        Path(parent) / entry.name
        for entry in _scandir_entries(Path(parent), on_error)
        if _entry_is_dir(entry)
    ]


def child_dir(
    parent: Path, *names: str, on_error: WalkErrorHandler | None = None,
) -> Path | None:
    """Resolve ``parent/names...`` only if every component matches exactly.

    Returns ``None`` when any component is absent, is not a directory, or
    exists only under a different capitalisation. Used for platform-convention
    directories (Talend's ``process``, ``routes``, ``code/routines``), where a
    case-insensitive match against a customer folder is a false positive.
    """
    current = Path(parent)
    for name in names:
        entries = _scandir_entries(current, on_error)
        match = next(
            (
                entry
                for entry in entries
                if entry.name == name and _entry_is_dir(entry)
            ),
            None,
        )
        if match is None:
            return None
        current = current / match.name
    return current


def child_file(parent: Path, name: str) -> bool:
    """Whether ``parent`` holds a file named exactly ``name``."""
    for entry in _scandir_entries(Path(parent), None):
        if entry.name == name:
            try:
                return entry.is_file()
            except OSError:
                return False
    return False


def case_variant_names(parent: Path, *names: str) -> list[str]:
    """Real directory names under ``parent`` that differ from ``names`` only by case.

    The material for an explicit warning: the caller skipped this directory on
    purpose, and saying so is better than a silently smaller scan.
    """
    wanted = {name.casefold() for name in names}
    exact = set(names)
    return sorted(
        entry.name
        for entry in _scandir_entries(Path(parent), None)
        if _entry_is_dir(entry)
        and entry.name not in exact
        and entry.name.casefold() in wanted
    )


def _entry_is_dir(entry: os.DirEntry[str]) -> bool:
    try:
        return entry.is_dir()
    except OSError:
        return False
