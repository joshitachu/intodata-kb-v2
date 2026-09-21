"""What an uploaded document becomes, and where it belongs.

The upload form already asks the user what kind of thing they are handing over
-- source, reference, guidance, standard, template, policy. Until now that label
was only a search filter: every role was stored identically and differed solely
in which similarity queries could see it. A label that describes a document but
changes nothing about how it is used is not carrying its weight.

This module turns the label into a routing decision, along two axes that are
deliberately kept independent:

* **Authority** -- what force the document carries. Evidence is cited, a
  constraint is obeyed, a method is followed. :class:`MaterialKind` collapses the
  six user-facing roles onto these four, because the six exist to be meaningful
  to a person uploading a file and the four exist to be meaningful to an agent
  reading one.
* **Size** -- whether the agent can hold the whole thing or must search it.

Conflating the two is the mistake worth naming: role and size say nothing about
each other. A two-page policy and a three-hundred-page policy carry identical
authority and need entirely different handling. Retrieval by similarity is the
right answer for the second and a strictly worse ``cat`` for the first -- it
risks recall, destroys the clause ordering a policy exists to express, and gives
the agent no way to know whether it saw all of it.

The asymmetry that follows is the point of the whole module: evidence can be
pulled, because a search that misses is a search that missed. A constraint must
be pushed, because a constraint that is only sometimes found is not a constraint.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from src.document_roles import DOCUMENT_ROLES, DocumentRole

__all__ = [
    "MANIFEST_MAX_ENTRIES",
    "MANIFEST_TOKEN_BUDGET",
    "MATERIALIZE_MAX_CHARS",
    "MaterialEntry",
    "MaterialKind",
    "ProjectMaterial",
    "material_kind",
    "material_slug",
    "should_materialize",
    "workspace_relpath",
]


class MaterialKind(StrEnum):
    """What the agent should do with a document, as opposed to what it is called."""

    #: Facts about the project. Cited, never obeyed. This is the bulk of the
    #: corpus and the one kind that is searched rather than read.
    EVIDENCE = "evidence"
    #: Background that informs without proving. Read when small, searched when not.
    REFERENCE = "reference"
    #: How to do a kind of work. Picked up when that work is being done.
    METHOD = "method"
    #: Requirements the answer must respect regardless of the question asked.
    CONSTRAINT = "constraint"


_ROLE_TO_KIND: Final[dict[DocumentRole, MaterialKind]] = {
    "source": MaterialKind.EVIDENCE,
    "reference": MaterialKind.REFERENCE,
    "guidance": MaterialKind.METHOD,
    "template": MaterialKind.METHOD,
    "standard": MaterialKind.CONSTRAINT,
    "policy": MaterialKind.CONSTRAINT,
}

# Fail loudly at import if a role is ever added without deciding what it becomes.
# Falling back to a default here would silently route a new constraint into the
# evidence path, which is the one mistake this module exists to prevent.
assert set(_ROLE_TO_KIND) == set(DOCUMENT_ROLES), (
    "every document role must map to a MaterialKind; "
    f"missing: {sorted(set(DOCUMENT_ROLES) - set(_ROLE_TO_KIND))}"
)

#: Ceiling for "the agent can read this whole". Roughly 8k tokens against a
#: 128k-token context: affordable once, and small enough that reading it beats
#: searching it. Above this, similarity retrieval is the only workable access.
#: The number is a starting position, not a finding -- it deserves measuring
#: against what the agent actually opens before it is treated as tuned.
MATERIALIZE_MAX_CHARS: Final = 32_000

#: The manifest is re-sent on every turn, so it is a per-turn tax like every
#: other always-on block and gets the same treatment: a hard ceiling, with
#: overflow collapsed into a single "and N more, search them" line rather than
#: silently truncated.
MANIFEST_TOKEN_BUDGET: Final = 300
MANIFEST_MAX_ENTRIES: Final = 20

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_MAX_SLUG_CHARS: Final = 48


def material_kind(role: str) -> MaterialKind:
    """Map a stored document role onto what the agent should do with it."""

    return _ROLE_TO_KIND.get(role, MaterialKind.EVIDENCE)  # type: ignore[arg-type]


def should_materialize(role: str, char_count: int) -> bool:
    """True when a document's text is worth keeping for the agent to read whole.

    Two independent gates, and both matter. Role excludes evidence: the source
    corpus is where the volume is, it is searched by design, and persisting it
    would duplicate hundreds of megabytes to no purpose. Size excludes anything
    the agent could not hold anyway.
    """

    return material_kind(role) is not MaterialKind.EVIDENCE and 0 < char_count <= (
        MATERIALIZE_MAX_CHARS
    )


def material_slug(filename: str) -> str:
    """A stable, filesystem-safe stem for a document.

    Derived from the filename rather than the title so the same document always
    lands at the same path across turns -- the workspace write is idempotent and
    a slug that drifted would orphan the previous turn's file.
    """

    stem = filename.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
    slug = _SLUG_STRIP.sub("-", stem).strip("-")[:_MAX_SLUG_CHARS].strip("-")
    return slug or "document"


class MaterialEntry(BaseModel):
    """One project document, as the agent will meet it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: int
    filename: str = Field(max_length=500)
    role: str = Field(max_length=20)
    kind: MaterialKind
    char_count: int = 0
    #: True when the parsed text is stored and the file can be written into the
    #: workspace. False means search is the only way in, and the manifest has to
    #: say so -- an entry that points at a file which is not there costs the
    #: agent a failed read and a wasted step.
    materialized: bool = False

    @property
    def slug(self) -> str:
        return material_slug(self.filename)

    @property
    def title(self) -> str:
        """A human-readable label, for the manifest line."""

        stem = self.filename.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        return stem.replace("_", " ").replace("-", " ").strip() or self.filename


class ProjectMaterial(BaseModel):
    """Everything non-evidence a project holds, ready to render and to mount."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str | None = None
    entries: tuple[MaterialEntry, ...] = ()
    #: Documents omitted from the manifest by the entry cap. Reported as a count
    #: rather than dropped: "there are 14 more, search them" is true and useful;
    #: silence implies there is nothing else.
    overflow_count: int = 0
    cache_key: str = ""

    def of_kind(self, kind: MaterialKind) -> tuple[MaterialEntry, ...]:
        return tuple(e for e in self.entries if e.kind is kind)

    @property
    def mountable(self) -> tuple[MaterialEntry, ...]:
        """Entries whose text can be written into the workspace this turn."""

        return tuple(e for e in self.entries if e.materialized)

    @property
    def is_empty(self) -> bool:
        return not self.entries and self.overflow_count == 0


def workspace_relpath(entry: MaterialEntry) -> str:
    """Where an entry is written inside `.nexus/project/`.

    Methods become skills because that is how they are used -- picked up when
    the matching work is being done, not read on every turn. References and
    constraints keep their full text under `references/`; a constraint is also
    distilled into rules, but the document stays addressable so the rule can
    point back at what it came from.

    Every path is keyed on the document id as well as the slug. The slug alone
    is not unique: `guide.docx` and `guide.pdf` reduce to the same stem, and two
    documents sharing a path would have the manifest advertise both while only
    one text was ever written -- a citation pointing at the wrong document,
    which is the failure this whole module exists to avoid.
    """

    if entry.kind is MaterialKind.METHOD:
        return f"skills/{entry.document_id}-{entry.slug}/SKILL.md"
    return f"references/{entry.document_id}-{entry.slug}.md"
