"""Resolves where a project's generated documentation lives on disk.

A project's ``output/`` tree has one of two shapes and every reader must handle
both:

* **flat** -- ``output/{project_id}/docs`` plus ``output/{project_id}/pdf``.
  Produced by a single-run project (the ``__all__`` partition).
* **partitioned** -- ``output/{project_id}/{subproject}/docs`` per sub-project.
  Produced by :mod:`mcp_server.services.partition_orchestrator` for a composite
  ZIP upload, one sub-project per partition.

Every caller used to glob ``output/{pid}/docs/*.md`` directly, which is blind to
the second shape. The visible symptom was a partitioned project reporting N
documents in the picker -- the RAG count, which sees the ``{subproject}/{name}``
rows the orchestrator pushes -- and listing zero in the doc viewer, because the
flat directory it globbed does not exist. This module is the one place that
knows the answer; readers ask it instead of assembling paths themselves.

Identity note: within a project a document is identified by
``(subproject, name)``, never by ``name`` alone. Every partition emits the same
ten enterprise document names (``00_AGENT_BRIEF.md`` ... ), so a bare filename
is ambiguous the moment a project is partitioned.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from mcp_server.config import config

logger = logging.getLogger(__name__)

PUBLISHED = "published"
STAGING = "staging"

_DIR_FOR_SOURCE = {PUBLISHED: "docs", STAGING: "docs_staging"}

# Directories under ``output/{project_id}/`` that are never a sub-project. The
# ``{sub}/docs`` existence test already excludes all of these in practice; the
# explicit set stops a future sibling artifact directory that happens to grow a
# ``docs/`` child from being presented to operators as a sub-project.
_RESERVED_SUBDIRS = frozenset({
    "docs",
    "docs_staging",
    "pdf",
    "logs",
    "topology",
    "experiments",
    "supporting-evidence",
    "analysis_cache",
})


@dataclass(frozen=True)
class DocLocation:
    """One documentation tree belonging to a project.

    ``subproject`` is ``None`` for a flat project and the partition's output
    sub-directory name for a partitioned one. It doubles as the RAG filename
    prefix, matching ``partition_orchestrator._push_partition_docs``.
    """

    subproject: str | None
    docs_dir: Path

    @property
    def pdf_dir(self) -> Path:
        """Sibling ``pdf/`` directory.

        Derived rather than stored so it always agrees with
        ``PdfService.convert_file``, which writes to ``md_path.parent.parent /
        "pdf"`` and therefore keeps a partition's PDFs inside that partition.
        """
        return self.docs_dir.parent / "pdf"


def project_output_root(project_id: str) -> Path:
    """Root of a project's generated output tree."""
    return config.project_root / "output" / project_id


def _source_dirname(source: str) -> str:
    try:
        return _DIR_FOR_SOURCE[source]
    except KeyError:
        raise ValueError(
            f"Unknown doc source {source!r}; expected one of {sorted(_DIR_FOR_SOURCE)}"
        ) from None


def doc_locations(project_id: str, source: str = PUBLISHED) -> list[DocLocation]:
    """Every documentation tree for a project, in stable display order.

    Returns an empty list when the project has generated nothing yet.

    A partitioned project shadows the flat directory: when any sub-project tree
    exists, the top-level one is not returned. The orchestrator never removes a
    pre-existing flat ``docs/``, so a project that was first generated as a
    single run and later re-uploaded as a composite ZIP keeps stale
    pre-partition documents on disk. Listing them next to current ones with no
    sub-project label attached is how an operator ends up reading a superseded
    document believing it is live.
    """
    dirname = _source_dirname(source)
    root = project_output_root(project_id)

    partitioned = [
        DocLocation(subproject=child.name, docs_dir=child / dirname)
        for child in sorted(root.iterdir(), key=lambda p: p.name.lower())
        if child.is_dir()
        # A symlinked child can point outside the project's output root, where
        # the containment check in PdfService.get_doc_path correctly refuses to
        # serve it. Listing it anyway would advertise documents that then 404.
        and not child.is_symlink()
        and child.name not in _RESERVED_SUBDIRS
        and not child.name.startswith((".", "_"))
        and (child / dirname).is_dir()
    ] if root.is_dir() else []

    if partitioned:
        flat = root / dirname
        if flat.is_dir():
            logger.info(
                "Project %s has both a flat %s/ directory and %d sub-project "
                "tree(s); listing sub-projects only (the flat tree predates "
                "partitioning)",
                project_id,
                dirname,
                len(partitioned),
            )
        return partitioned

    flat = root / dirname
    return [DocLocation(subproject=None, docs_dir=flat)] if flat.is_dir() else []


def is_safe_path_segment(segment: str) -> bool:
    """True when ``segment`` is usable as a single path component.

    Shared by every caller that accepts a filename or sub-project from a
    request, so the traversal rule has one definition rather than one per
    endpoint.
    """
    return bool(segment) and not (
        ".." in segment or "/" in segment or "\\" in segment or segment.startswith(".")
    )


def resolve_doc_location(
    project_id: str, subproject: str | None, source: str = PUBLISHED
) -> DocLocation | None:
    """Resolve one caller-supplied sub-project to its documentation tree.

    ``None`` when the sub-project is unsafe, unknown, or has no tree for
    ``source``. A ``subproject`` of ``None`` selects the sole location of a flat
    project and is rejected for a partitioned one -- a bare filename cannot
    identify a document there.
    """
    if subproject is not None and not is_safe_path_segment(subproject):
        return None
    for location in doc_locations(project_id, source=source):
        if location.subproject == subproject:
            return location
    return None
