"""The layered guidance contract: what each level may say, and how much of it.

Answering behaviour is governed by four stacked levels, most specific last:

    ORG      house rules for every project, every user   (admin-owned)
    PROJECT  this integration landscape                  (project-owned)
    PERSON   how this individual wants answers           (user-owned)
    THREAD   this conversation                           (system-owned)

Only the first three live here. THREAD is conversation history and is already
owned by the conversation layer; nothing durable is written to it.

Three properties make this a hierarchy rather than four blobs of text glued
together, and all three are enforced here rather than left to the model:

* **Authority.** ``_ALLOWED_KINDS`` says which kinds of statement each level may
  contain. A person can express a presentation preference and nothing else --
  not because the model is asked to ignore the rest, but because the row cannot
  be constructed. That is what stops "just give me your best guess" from
  defeating "never invent evidence".
* **Shape.** The PERSON level is a closed set of enums (``StylePresentation``),
  never free text. It is the level most exposed to learned write-back, so it is
  the level with no expressive room for an injected instruction.
* **Budget.** Every level carries a hard token ceiling. Guidance is re-sent on
  every model call, so an unbounded level is an unbounded per-turn tax -- and
  "the agent learns" becomes "the prompt grows forever" without one.

The organization's *invariants* are deliberately NOT here. They are code-owned
constants in :mod:`src.harness.guidance.renderer`, always rendered, never read
from the database, so a database failure degrades guidance without ever
degrading the safety floor.
"""

from __future__ import annotations

import datetime
from enum import StrEnum
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.harness.packs.models import StylePresentation

__all__ = [
    "GuidanceKind",
    "GuidanceLayer",
    "GuidanceRule",
    "GuidanceStatus",
    "LAYER_TOKEN_BUDGETS",
    "ResolvedGuidance",
    "allowed_kinds",
    "estimate_tokens",
    "style_from_payload",
    "style_to_payload",
]


class GuidanceLayer(StrEnum):
    """A level in the stack. Render order is ORG -> PROJECT -> PERSON."""

    ORG = "org"
    PROJECT = "project"
    PERSON = "person"


class GuidanceKind(StrEnum):
    """What a row says.

    ``RULE`` and ``OUTPUT_RULE`` carry free text; the rest carry a structured
    ``payload`` so the compiler can use them for more than prose (an evidence
    rule can order retrieval, a vocabulary term can seed an alias lookup).
    """

    RULE = "rule"
    VOCABULARY = "vocabulary"
    EVIDENCE_RULE = "evidence_rule"
    OUTPUT_RULE = "output_rule"
    STYLE = "style"


class GuidanceStatus(StrEnum):
    """Lifecycle. Nothing is deleted; a replaced rule is superseded.

    ``PROPOSED`` exists for learned PROJECT rules: one user's correction must
    not silently rewrite the project for everyone, so it waits for review.
    A learned PERSON rule needs no gate -- it only affects its author.
    """

    ACTIVE = "active"
    PROPOSED = "proposed"
    SUPERSEDED = "superseded"


#: The authority matrix. A layer may only contain the kinds listed for it.
#:
#: PERSON is STYLE-only on purpose -- see the module docstring. PROJECT may
#: carry free-text rules, but it renders *after* ORG and the renderer states
#: that the organization block cannot be relaxed, so a project rule can tighten
#: and never loosen.
_ALLOWED_KINDS: Final[dict[GuidanceLayer, frozenset[GuidanceKind]]] = {
    GuidanceLayer.ORG: frozenset(
        {GuidanceKind.RULE, GuidanceKind.OUTPUT_RULE, GuidanceKind.STYLE}
    ),
    GuidanceLayer.PROJECT: frozenset(
        {
            GuidanceKind.RULE,
            GuidanceKind.VOCABULARY,
            GuidanceKind.EVIDENCE_RULE,
            GuidanceKind.OUTPUT_RULE,
            GuidanceKind.STYLE,
        }
    ),
    GuidanceLayer.PERSON: frozenset({GuidanceKind.STYLE}),
}

#: Per-level ceilings, in tokens, enforced on write by the repository.
#:
#: Sized against what each level is for, not against what fits: ORG is a handful
#: of invariable house rules, PROJECT carries domain vocabulary and so needs the
#: most room, PERSON is a single structured overlay that renders in ~45 tokens.
LAYER_TOKEN_BUDGETS: Final[dict[GuidanceLayer, int]] = {
    GuidanceLayer.ORG: 400,
    GuidanceLayer.PROJECT: 600,
    GuidanceLayer.PERSON: 150,
}

#: Free-text kinds. Everything else must supply ``payload`` instead.
_TEXT_KINDS: Final[frozenset[GuidanceKind]] = frozenset(
    {GuidanceKind.RULE, GuidanceKind.OUTPUT_RULE}
)

_MAX_CONTENT_CHARS: Final = 500


def allowed_kinds(layer: GuidanceLayer) -> frozenset[GuidanceKind]:
    """The kinds ``layer`` may contain. Empty for an unknown layer, not an error."""

    return _ALLOWED_KINDS.get(layer, frozenset())


def estimate_tokens(text: str) -> int:
    """Token count for a rendered fragment, for budget accounting.

    Uses the same encoding the RAG prompt path already warms
    (``prompt_builder.get_encoding``), so a budget here means the same thing a
    context budget means elsewhere. Falls back to a conservative characters/4
    estimate if tiktoken cannot load -- a budget that fails open on an
    unavailable vocabulary file would be no budget at all.
    """

    if not text:
        return 0
    try:
        from src.generation.prompt_builder import get_encoding

        return len(get_encoding().encode(text))
    except Exception:  # noqa: BLE001 - any tiktoken failure must not break a turn
        return (len(text) + 3) // 4


def style_from_payload(payload: dict[str, Any]) -> StylePresentation:
    """Hydrate a ``StylePresentation`` from a stored JSON payload.

    ``StrictPackModel`` sets ``strict=True``, so ``StylePresentation`` will not
    accept ``"business"`` where ``StyleAudience.BUSINESS`` is declared. That is
    right for a pack file authored in Python and wrong for every value that has
    been through JSONB, where an enum is a string by the time it comes back.
    Validating in non-strict mode coerces the member names without loosening
    anything else: an unknown value still raises, and ``extra="forbid"`` still
    rejects a field the model does not declare.

    Every read of a STYLE payload goes through here so the coercion lives in one
    place rather than being rediscovered at each call site.
    """

    return StylePresentation.model_validate(payload, strict=False)


def style_to_payload(style: StylePresentation) -> dict[str, Any]:
    """Serialize a ``StylePresentation`` for storage, round-trip safe."""

    return style.model_dump(mode="json")


class GuidanceRule(BaseModel):
    """One statement at one level.

    Validation here is the authority matrix and the scope shape, so the same
    rules apply whether a row arrives from the admin API, from learned
    write-back, or from a database read. The repository adds the budget check,
    which needs to see the other rows at the same level and so cannot live on a
    single instance.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int | None = None
    layer: GuidanceLayer
    kind: GuidanceKind
    project_id: str | None = Field(default=None, max_length=255)
    user_id: int | None = None
    content: str | None = Field(default=None, max_length=_MAX_CONTENT_CHARS)
    payload: dict[str, Any] | None = None
    status: GuidanceStatus = GuidanceStatus.ACTIVE
    #: ``admin_ui`` or ``learned:conversation:<id>`` -- provenance for review.
    source: str = Field(default="admin_ui", max_length=64)
    supersedes_id: int | None = None
    created_by: int | None = None
    created_at: datetime.datetime | None = None

    @model_validator(mode="after")
    def _validate_authority_and_scope(self) -> GuidanceRule:
        if self.kind not in allowed_kinds(self.layer):
            raise ValueError(
                f"layer {self.layer.value!r} may not contain kind {self.kind.value!r}; "
                f"allowed: {sorted(k.value for k in allowed_kinds(self.layer))}"
            )

        # The scope columns are what make a row addressable at its level. A
        # mismatch is not a cosmetic problem: an org row carrying a project_id
        # would render for one project and silently vanish for the rest.
        if self.layer is GuidanceLayer.ORG:
            if self.project_id is not None or self.user_id is not None:
                raise ValueError("an org rule is unscoped: project_id and user_id must be null")
        elif self.layer is GuidanceLayer.PROJECT:
            if not self.project_id:
                raise ValueError("a project rule requires project_id")
            if self.user_id is not None:
                raise ValueError("a project rule applies to everyone: user_id must be null")
        else:  # PERSON
            if not self.project_id or self.user_id is None:
                raise ValueError("a person rule requires both project_id and user_id")

        if self.kind in _TEXT_KINDS:
            if not (self.content or "").strip():
                raise ValueError(f"kind {self.kind.value!r} requires content")
        elif self.payload is None:
            raise ValueError(f"kind {self.kind.value!r} requires payload")

        if self.kind is GuidanceKind.STYLE:
            # Parsed, not merely present: an unparseable style payload must fail
            # at the boundary rather than render as an empty overlay later.
            style_from_payload(self.payload or {})
        return self

    @property
    def style(self) -> StylePresentation | None:
        """The payload as a ``StylePresentation``, for STYLE rules only."""

        if self.kind is not GuidanceKind.STYLE or self.payload is None:
            return None
        return style_from_payload(self.payload)


class ResolvedGuidance(BaseModel):
    """The compiled stack for one (project, user), ready to render.

    ``cache_key`` is a digest of the inputs that produced this object. The
    render is pure, so an unchanged key means an unchanged prompt prefix -- the
    property the whole volatility ordering exists to protect.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str | None = None
    user_id: int | None = None
    org_rules: tuple[GuidanceRule, ...] = ()
    project_rules: tuple[GuidanceRule, ...] = ()
    #: Merged org default -> project default -> person override, whitelist-bounded.
    person_style: StylePresentation | None = None
    #: Human-readable statements where two levels pull apart. Rendered, never
    #: silently resolved: a dropped rule the user cannot see is unexplainable.
    conflicts: tuple[str, ...] = ()
    cache_key: str = ""

    def rules_for(self, layer: GuidanceLayer) -> tuple[GuidanceRule, ...]:
        if layer is GuidanceLayer.ORG:
            return self.org_rules
        if layer is GuidanceLayer.PROJECT:
            return self.project_rules
        return ()

    @property
    def is_empty(self) -> bool:
        """True when nothing beyond the code-owned invariants would render."""

        return not (self.org_rules or self.project_rules or self.person_style)
