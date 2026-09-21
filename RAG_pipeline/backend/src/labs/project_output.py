"""Where this backend expects the documentation pipeline's per-project output.

The seam between two sub-projects, written down once.

`knowledge_base/mcp-server` owns a per-project output tree and this backend
reads two directories out of it:

    <root>/<project_id>/docs/        published documentation, mounted read-only
    <root>/<project_id>/knowledge/   the generation run's own memory

That is a filesystem coupling across a sub-project boundary, and it already
existed -- the `docs` symlink has been resolved this way since the lane was
written. What did not exist was a place to say so. Spelling
``/ project_id / "docs"`` at one call site and ``/ project_id / "knowledge"``
at another is two undeclared assumptions about somebody else's layout; this
module makes it one declared one, with a single environment variable
(``LABS_DOCS_ROOT``, a read-only bind of ``./knowledge_base/output`` in
Compose) and a single project-id validation.

It is a path and not an HTTP client because the MCP server publishes no route
for either tree. If one is ever added, this is the only file that changes.

Nothing here writes. Both directories are the other sub-project's to produce;
this backend is a reader of them and of nothing else under that root.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

__all__ = [
    "DOCS_DIRNAME",
    "KNOWLEDGE_DIRNAME",
    "project_docs_dir",
    "project_knowledge_dir",
    "project_output_dir",
]

#: Local dev default. Overridden by ``LABS_DOCS_ROOT`` everywhere else, and by
#: every test -- the default points at one developer's checkout, so a test that
#: forgot to set the variable would answer from whatever happens to be on the
#: machine running it.
_DEFAULT_DOCS_ROOT: Final = (
    "/Users/joshuakabel/Documents/repos/nexus/intodata-kb-v2/knowledge_base/output"
)

#: The published documentation the docs symlink points at.
DOCS_DIRNAME: Final = "docs"

#: The generation run's durable memory. A sibling of ``docs/`` and deliberately
#: outside it: the memory is the pipeline's working record, never part of the
#: published tree, and never pushed to the client's RAG corpus.
KNOWLEDGE_DIRNAME: Final = "knowledge"

#: A project id is about to become a path segment. It reaches these functions
#: from RBAC-derived scope rather than raw user input, and is validated anyway.
_SAFE_PROJECT_ID_RE: Final = re.compile(r"^[A-Za-z0-9._-]+$")


def _root() -> Path:
    value = os.environ.get("LABS_DOCS_ROOT", "").strip()
    return Path(value or _DEFAULT_DOCS_ROOT)


def project_output_dir(project_id: str | None) -> Path | None:
    """The project's output root, or None when the id is absent or unsafe.

    Read at call time rather than at import so a process -- or a test -- can
    change ``LABS_DOCS_ROOT`` without a reload.

    No existence check: this is the path the tree *would* live at, and the two
    functions below are the ones that answer whether it is really there.
    """

    if not project_id:
        return None
    if not _SAFE_PROJECT_ID_RE.match(project_id):
        logger.warning("Refusing unsafe labs project_id %r for an output path", project_id)
        return None
    return _root() / project_id


def project_docs_dir(project_id: str | None) -> Path | None:
    """The project's published documentation directory, if it exists."""

    output = project_output_dir(project_id)
    if output is None:
        return None
    candidate = output / DOCS_DIRNAME
    return candidate if candidate.is_dir() else None


def project_knowledge_dir(project_id: str | None) -> Path | None:
    """The project's generation-memory directory, if it exists."""

    output = project_output_dir(project_id)
    if output is None:
        return None
    candidate = output / KNOWLEDGE_DIRNAME
    return candidate if candidate.is_dir() else None
