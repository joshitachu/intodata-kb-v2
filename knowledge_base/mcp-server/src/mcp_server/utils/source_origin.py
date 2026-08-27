"""Origin markers that record where a scanned source tree came from.

A scan's ``source_root`` paths carry a marker segment naming the sub-project the
units under it belong to. Two origins write such a marker today:

* :data:`ZIP_MARKER` -- a nested archive inside an uploaded ZIP bundle, written
  by ``api/routes/projects._extract_zip_bundle``.
* :data:`REPO_MARKER` -- a member repository of a multi-repo project, written by
  the multi-repo clone path.

Both are **sub-project boundaries**: the partition planner turns each marked
segment into its own scoped generation, so one repo (or one inner zip) gets its
own doc set and its own model-call budget.

Only the ZIP origin leaves a raw archive artifact beside the extraction
directory, so archive-specific handling (excluding the ``X.zip`` that produced
``X.__intodoc_zip__``, in ``providers/custom/noncode_evidence``) keys on
:data:`ARCHIVE_MARKERS` rather than on the full marker set.

Matching is **containment**, not suffix. The extraction path disambiguates a
name collision by appending a digest (``X.__intodoc_zip__-a1b2c3d4e5``), so a
marked segment does not necessarily *end* with its marker. Every consumer must
go through the helpers here; ``segment.endswith(MARKER)`` silently drops
collision directories into the shared partition.
"""

from __future__ import annotations

# A nested archive expanded out of an uploaded ZIP bundle.
ZIP_MARKER = ".__intodoc_zip__"

# A member repository cloned for a multi-repo project.
REPO_MARKER = ".__intodoc_repo__"

# Every marker that denotes a sub-project boundary.
ORIGIN_MARKERS: tuple[str, ...] = (ZIP_MARKER, REPO_MARKER)

# Markers whose extraction directory has a raw archive sibling on disk.
ARCHIVE_MARKERS: tuple[str, ...] = (ZIP_MARKER,)


def marker_in_segment(segment: str) -> str | None:
    """Return the origin marker *segment* carries, or ``None``.

    Markers are checked longest-first so an unambiguous answer is returned even
    if a future marker is a prefix of another.
    """
    text = str(segment or "")
    for marker in sorted(ORIGIN_MARKERS, key=len, reverse=True):
        if marker in text:
            return marker
    return None


def origin_stem(segment: str) -> str | None:
    """Return the sub-project name a marked *segment* declares, or ``None``.

    ``Alpha.__intodoc_repo__`` and ``Alpha.__intodoc_zip__-a1b2c3d4e5`` both
    yield ``Alpha``. An unmarked segment yields ``None``; a segment that is
    *only* a marker yields an empty string, which callers treat as
    unattributable.
    """
    marker = marker_in_segment(segment)
    if marker is None:
        return None
    return str(segment).split(marker, 1)[0]


def strip_origin_marker(segment: str) -> str:
    """Return *segment* with any origin marker and disambiguating suffix removed.

    Used for display: operators refer to the repo or zip, never to the physical
    extraction directory. Unmarked segments are returned unchanged.
    """
    stem = origin_stem(segment)
    return str(segment) if stem is None else stem


def marker_dir_name(stem: str, marker: str, *, digest: str | None = None) -> str:
    """Build the extraction/clone directory name for *stem* under *marker*."""
    if marker not in ORIGIN_MARKERS:
        raise ValueError(f"Unknown origin marker: {marker!r}")
    name = f"{stem}{marker}"
    return f"{name}-{digest}" if digest else name


def subproject_key(source_root: str) -> str | None:
    """Return the sub-project a *source_root* belongs to, or ``None``.

    The first marked path segment wins, so a shared tree nested *inside* a
    marked directory (``.../Alpha.__intodoc_repo__/REFERENCES``) folds into
    ``Alpha`` instead of becoming its own sub-project. Returns ``None`` when no
    segment is marked -- a single-root scan, which callers treat as one whole
    partition.
    """
    normalized = str(source_root or "").replace("\\", "/")
    for segment in normalized.split("/"):
        stem = origin_stem(segment)
        if stem is not None:
            return stem
    return None


def is_composite_root(source_root: str) -> bool:
    """True when *source_root* carries any origin marker."""
    return subproject_key(source_root) is not None


def archive_marker_in_segment(segment: str) -> str | None:
    """Return the *archive* marker *segment* carries, or ``None``.

    Distinct from :func:`marker_in_segment`: a cloned repo has no archive
    sibling to exclude, so archive handling must not fire for it.
    """
    text = str(segment or "")
    for marker in sorted(ARCHIVE_MARKERS, key=len, reverse=True):
        if marker in text:
            return marker
    return None
