"""The documentation method the orchestrating agent reads, as a staged file.

The four-phase procedure used to be a ~47 KB f-string assembled in
``providers/claude_cli.py::_build_prompt``: roughly 98 interpolated staging
paths, a phase-by-phase script, and every accumulated imperative the pipeline
had ever learned, rebuilt from Python locals on every run. That shape had three
costs worth naming, because they are why it is a file now:

* **It could not be reviewed.** The method only existed as the output of a
  function, so "what does a run actually instruct" was a question you answered
  by running the code and diffing the result.
* **Python owned the procedure.** Every step between phases was choreographed
  by the prompt builder, so changing the method meant changing the provider --
  and the two CLI transports had to be kept in step by hand.
* **Per-run FACT and standing METHOD were indistinguishable.** A path that
  genuinely varies per run (the staging directory) was interpolated beside a
  rule that has never varied (copy code verbatim, never reconstruct it), and
  both were rebuilt from scratch every time.

So the method is a file the agent reads (``data/generation_method/METHOD.md``),
and the prompt states only what a file cannot know: where this run's workspace
is, how big this estate is, what a previous attempt already finished.

Staged, never published. The copy lands in ``<staging>/_method/METHOD.md``,
beside the shared confidence model and validation checklist that
``services/investigation_method.py`` stages into ``<staging>/_method/_shared/``.
``_method/`` is removed with the other working evidence after the deterministic
post-run checks, exactly like ``_preprocess/``: method is prompt input, not a
deliverable.

Advisory on failure, like every other staging aid on this lane. A method that
could not be copied is reported and the run continues -- the alternative is a
run that writes no documents because a file copy failed, which is strictly
worse than one that writes them from the prompt's own facts.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from mcp_server.config import config
from mcp_server.services.investigation_method import STAGED_METHOD_DIRNAME

logger = logging.getLogger(__name__)

#: Directory under ``config.data_dir`` holding the method.
GENERATION_METHOD_DIRNAME = "generation_method"

#: The method file itself, named the same on disk and in staging so a log line
#: about one is a log line about the other.
METHOD_FILENAME = "METHOD.md"


def method_source_path() -> Path:
    """Where the method lives in the repository."""
    return Path(config.data_dir) / GENERATION_METHOD_DIRNAME / METHOD_FILENAME


def staged_method_path(staging_dir: Path) -> Path:
    """Where a run reads the method from (it need not exist)."""
    return Path(staging_dir) / STAGED_METHOD_DIRNAME / METHOD_FILENAME


def materialize_generation_method(staging_dir: Path) -> Path | None:
    """Copy the method into ``<staging>/_method/``; return the staged path.

    Returns ``None`` when the source is missing or the copy failed; the caller
    logs and carries on. Idempotent -- a resumed run over byte-identical
    content rewrites the same bytes and does not churn the staging tree.
    """
    source = method_source_path()
    if not source.is_file():
        logger.warning("Generation method not found at %s", source)
        return None
    target = staged_method_path(staging_dir)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    except OSError as exc:
        logger.warning("Generation method could not be staged: %s", exc)
        return None
    logger.info("Staged generation method at %s (%d bytes)", target, source.stat().st_size)
    return target
