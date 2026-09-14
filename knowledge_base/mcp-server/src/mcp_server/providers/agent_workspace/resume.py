"""Prove that reusable state belongs to THIS run, or start clean.

``output/``, ``notes/`` and ``notes/integration_model.json`` were preserved
across every build of a staging directory, unconditionally
(``workspace.py:1155-1171`` before this module existed). The intent was
right -- a restart's whole point is that finished work survives -- but nothing
tied that work to the run it was about. A new run over a preserved staging
directory silently inherited another run's drafts, another run's notes and
another run's accumulated integration model, and then published them as its
own. Nothing in the receipt said so, because nothing knew.

The fix is a value object, not a flag. :class:`ResumeIdentity` is the set of
immutable facts that make reuse safe, and comparison returns the FIELD NAMES
that differ so the receipt can say *what* changed rather than *that* something
did. ``providers/coding_agent/models.py::InvestigationFingerprints`` already
solved exactly this for the predecessor lane, including the ``differences()``
method; this mirrors it deliberately -- a third spelling of "is this
checkpoint mine" is how two of them end up disagreeing.

**Only ``build_workspace`` consults it.** An internal restart inside one
active generation shares the workspace by design: the runner composes a fresh
session over the same directory and must find the previous attempt's output
exactly where it left it. Running this check per attempt would discard the
work that a restart exists to keep, so the check happens once, where the
workspace is built.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mcp_server.providers.agent_workspace.contract import (
    CONTRACT,
    COVERAGE_ROSTER,
    INTEGRATION_MODEL,
    NOTES,
    OUTPUT,
    RESUME_IDENTITY,
    WorkspaceUnavailableError,
)

#: What each field is, in a sentence an operator reads in a run log. The
#: notice has to name the field that changed; a bare "identity mismatch" sends
#: somebody to diff two hashes.
_FIELD_MEANING: dict[str, str] = {
    "project": "the project",
    "scan": "the approved scan",
    "goal": "the goal",
    "method": "the instructions and investigation method",
    "contract": "the workspace contract version",
    "knowledge_model": "the integration-model schema",
}

_FIELD_ORDER: tuple[str, ...] = (
    "project",
    "scan",
    "goal",
    "method",
    "contract",
    "knowledge_model",
)


def _digest(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


class ResumeIdentity(BaseModel):
    """The facts that make ``output/`` and ``notes/`` safe to reuse.

    Every field is a sha256 rather than the value it stands for. A goal is
    free text a client wrote and an instruction set is ~120 KB; neither
    belongs in a control file that survives the run, and a digest answers the
    only question asked of it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Which project's estate this workspace describes.
    project: str = Field(pattern=r"^[a-f0-9]{64}$")
    #: The publication approval's complete artifact binding. A rescan changes
    #: it even when the file names are identical, which is the point: drafts
    #: written against last week's estate are not evidence about this one.
    scan: str = Field(pattern=r"^[a-f0-9]{64}$")
    #: The goal, whitespace-normalised before hashing.
    goal: str = Field(pattern=r"^[a-f0-9]{64}$")
    #: The staged instructions and the agent guide, as bytes.
    method: str = Field(pattern=r"^[a-f0-9]{64}$")
    #: ``contract.CONTRACT``. A lane version bump invalidates reuse.
    contract: str = Field(pattern=r"^[a-f0-9]{64}$")
    #: ``IntegrationKnowledgeBase.schema_version``. A persisted model from an
    #: older schema does not validate against the new one, and a crash there
    #: would be a confusing way to learn that.
    knowledge_model: str = Field(pattern=r"^[a-f0-9]{64}$")

    @classmethod
    def build(
        cls,
        *,
        project_id: str,
        scan_digest: str,
        goal: str,
        method_digest: str,
        contract_version: str = CONTRACT,
        knowledge_model_version: str,
    ) -> ResumeIdentity:
        return cls(
            project=_digest(project_id),
            scan=_digest(scan_digest),
            goal=_digest(" ".join(str(goal).split())),
            method=_digest(method_digest),
            contract=_digest(contract_version),
            knowledge_model=_digest(knowledge_model_version),
        )

    def differences(self, other: ResumeIdentity) -> list[str]:
        """The field names that differ, in a fixed order."""

        return [name for name in _FIELD_ORDER if getattr(self, name) != getattr(other, name)]

    # -- persistence -----------------------------------------------------

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.model_dump(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    @classmethod
    def load(cls, path: Path) -> ResumeIdentity | None:
        """The stored identity, or ``None`` when there is no usable one.

        Corrupt, truncated and absent are ONE answer on purpose. Each of them
        means the same thing -- nothing here proves the reusable state belongs
        to this run -- and the safe handling of all three is identical.
        """

        path = Path(path)
        if not path.is_file() or path.is_symlink():
            return None
        try:
            return cls.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, ValidationError):
            return None


@dataclass(frozen=True)
class ResumeDecision:
    """What was done with the reusable state, and why."""

    preserved: bool
    #: Field names from :data:`_FIELD_ORDER`. Empty on a preserve, and empty
    #: on a first build (there is nothing to compare, which is not a change).
    changed_fields: tuple[str, ...] = ()
    #: One client-safe sentence for the receipt notices. Empty when the
    #: decision is uninteresting (an exact match on a first-ever build).
    notice: str = ""


def integration_model_schema_version() -> str:
    """The persisted knowledge model's declared schema version.

    Read off the model class rather than duplicated here: the two would drift,
    and the drift would show up as a resume that should have been refused.
    """

    from mcp_server.providers.agent_workspace.integration_model import (
        IntegrationKnowledgeBase,
    )

    field = IntegrationKnowledgeBase.model_fields.get("schema_version")
    version = getattr(field, "default", None) if field is not None else None
    if not isinstance(version, str) or not version:
        raise WorkspaceUnavailableError(
            "the integration knowledge model declares no schema version, so reusable "
            "agent state cannot be bound to it",
            stage="projection",
        )
    return version


def reconcile(staging_dir: Path, identity: ResumeIdentity) -> ResumeDecision:
    """Keep the agent's directories only when they provably belong here.

    Preserve on an exact match; otherwise remove ``output/``, ``notes/`` and
    the integration model, and say which field differed. Discarding is the
    safe direction and it is also the cheap one -- the run regenerates the
    documents -- whereas merging a stranger's drafts into this run's
    publication is neither detectable nor reversible.
    """

    staging_dir = Path(staging_dir)
    stored = ResumeIdentity.load(staging_dir / RESUME_IDENTITY)
    reusable = _has_reusable_state(staging_dir)

    # An unwritable or half-removed staging directory is a workspace failure
    # like any other, and it has to reach the operator through the one
    # exception family with a stage on it. A bare OSError from `rmtree` escapes
    # the runner's single `except` and becomes an unqualified "Generation
    # failed" -- and it does so AFTER the evidence has been staged, which is
    # the most expensive place in the build to be uninformative.
    try:
        if stored is not None and not stored.differences(identity):
            identity.save(staging_dir / RESUME_IDENTITY)
            return ResumeDecision(preserved=True)

        changed = tuple(stored.differences(identity)) if stored is not None else ()
        notice = ""
        if reusable and stored is None:
            notice = (
                "previous work in output/ and notes/ was discarded: no resume identity "
                "records which run produced it"
            )
        elif reusable:
            meanings = ", ".join(_FIELD_MEANING.get(name, name) for name in changed)
            notice = f"previous work in output/ and notes/ was discarded because {meanings} changed"

        _clear(staging_dir)
        identity.save(staging_dir / RESUME_IDENTITY)
    except OSError as exc:
        raise WorkspaceUnavailableError(
            "the agent's own directories could not be reconciled with this run "
            f"({type(exc).__name__}); the staging directory is not usable",
            stage="projection",
        ) from exc
    return ResumeDecision(preserved=False, changed_fields=changed, notice=notice)


def _has_reusable_state(staging_dir: Path) -> bool:
    """``True`` when there is something a mismatch would actually discard.

    Only the notice depends on this; the clear happens either way. It matters
    because a notice that fires on every rebuild is a notice an operator stops
    reading, and three things in these directories are NEXUS-seeded rather than
    agent work:

    * ``output/reference/`` is the deterministic compiler's, rebuilt from the
      evidence on every build and again before every publication;
    * ``notes/coverage/roster.jsonl`` is the builder's own projection of the
      parsed designs, re-seeded on every build. The classifications log beside
      it is the agent's and does count;
    * a ``notes/integration_model.json`` that is still byte-identical to the
      empty starting point this builder writes. The model IS reusable state
      once the agent has put something in it, which is why the comparison is
      against the default rather than against the file's existence.

    Walks FILES recursively rather than iterating the top level. A top-level
    iteration counted the ``notes/coverage/`` DIRECTORY as agent work the
    moment the roster was seeded into it, so every second build of an unchanged
    project reported that it had discarded work the agent never did.
    """

    from mcp_server.providers.agent_workspace.registers import REFERENCE_DIR_NAME

    output = staging_dir / OUTPUT
    if output.is_dir() and any(entry.name != REFERENCE_DIR_NAME for entry in output.iterdir()):
        return True
    notes = staging_dir / NOTES
    if not notes.is_dir():
        return False
    model = staging_dir / INTEGRATION_MODEL
    roster = staging_dir / COVERAGE_ROSTER
    for path in notes.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        if path == roster:
            continue
        if path == model and _is_the_empty_model(model):
            continue
        return True
    return False


def _is_the_empty_model(path: Path) -> bool:
    """``True`` when the persisted model is the starting point, not a record.

    Compared against a freshly-serialised default rather than against a
    remembered byte string: the schema belongs to ``integration_model.py`` and
    a copy of its serialisation here would be a second definition of "empty".
    """

    from mcp_server.providers.agent_workspace.integration_model import (
        IntegrationKnowledgeBase,
    )

    try:
        return path.read_text(encoding="utf-8") == IntegrationKnowledgeBase().to_json()
    except (OSError, ValueError):
        return False


def _clear(staging_dir: Path) -> None:
    for relative in (OUTPUT, NOTES):
        path = staging_dir / relative
        if path.is_symlink():
            path.unlink(missing_ok=True)
        elif path.exists():
            shutil.rmtree(path)
    # Removed explicitly as well as with `notes/`. The model lives under the
    # scratch root today; a future move must not silently turn this into a
    # no-op, and removing an absent file costs nothing.
    model = staging_dir / INTEGRATION_MODEL
    if model.is_file() or model.is_symlink():
        model.unlink(missing_ok=True)
