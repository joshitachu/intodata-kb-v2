"""Turning the resolved guidance stack into the prefix every model turn carries.

This is where layering stops being data and starts being behaviour, so three
properties matter more than the formatting:

* **The safety floor is code, not data.** ``ORG_INVARIANTS`` is never read from
  the database. A failed guidance query, an empty table or a half-applied
  migration may cost a project its vocabulary and a person their presentation
  preference; it must never cost an answer its honesty rules. Everything else
  here can be empty and the organization block still renders in full.
* **Byte-stability.** The rendered text is the prefix of every call in a
  conversation, and a prefix that differs by one byte is a prompt-cache miss for
  the whole turn. Every list is therefore ordered on a total key derived from
  the row itself, never on database insertion order -- which is neither stable
  across replicas nor reproducible after an edit.
* **Silence over empty scaffolding.** A section with nothing to say is omitted
  entirely. A ``## Project: x`` heading followed by nothing tells the model the
  project has no rules worth honouring, which is worse than not raising it.

The render is pure: no database, no I/O, no async, no clock. Budgets are
enforced by the repository on write; ``layer_token_costs`` here only reports
what a layer actually costs once rendered.
"""

from __future__ import annotations

import json
import logging
from typing import Final

from pydantic import ValidationError

from src.generation.prompt_builder import presentation_instruction
from src.harness.guidance.material import (
    MANIFEST_TOKEN_BUDGET,
    MaterialEntry,
    MaterialKind,
    ProjectMaterial,
    workspace_relpath,
)
from src.harness.guidance.models import (
    GuidanceKind,
    GuidanceLayer,
    GuidanceRule,
    GuidanceStatus,
    ResolvedGuidance,
    estimate_tokens,
)
from src.harness.packs.models import EvidenceRankingRule, VocabularyTerm

logger = logging.getLogger(__name__)

__all__ = [
    "CONFLICTS_SECTION_HEADING",
    "MANIFEST_SECTION_HEADING",
    "MATERIAL_KIND_PRIORITY",
    "MATERIAL_PATH_PREFIX",
    "ORG_INVARIANTS",
    "ORG_SECTION_HEADING",
    "PROJECT_SECTION_PREFIX",
    "layer_token_costs",
    "render_guidance",
    "render_invariants",
    "render_layer",
    "render_manifest",
]


#: The organization's non-negotiable house rules, owned by code.
#:
#: Deliberately not a table: these are the statements whose absence is unsafe,
#: and a row that can be deleted is a row that eventually will be. Between them
#: they cover all nine ``ProtectedInvariant`` members in
#: :mod:`src.harness.packs.models`. The enum is not imported -- this is a
#: coverage map for review, not a runtime dependency:
#:
#:   1. no invented identifiers        -> ENTITY_SEMANTICS, EVIDENCE_POLICY
#:   2. no guessed values              -> EVIDENCE_POLICY, QUALITY_THRESHOLDS
#:   3. confirmed vs inferred          -> FINDINGS, REQUIRED_ANALYSIS
#:   4. report tool failure honestly   -> CAPABILITIES, QUALITY_THRESHOLDS
#:   5. stay in authorized scope       -> PERMISSIONS, SECURITY_REDACTION
#:   6. no legal/regulatory conclusion -> RISK_TIERS
ORG_INVARIANTS: Final[tuple[str, ...]] = (
    (
        "Never invent a file path, job name, component name, system name or project id. "
        "If you did not read it from evidence or receive it from a tool, you do not know it."
    ),
    "Never guess at a number, a date, a count or a status. Report what the evidence returned.",
    "Distinguish clearly between what is confirmed by evidence and what is inferred.",
    (
        'If a tool fails or returns nothing, say so. An honest "no evidence in scope" is a '
        "correct answer; a plausible fabrication is not."
    ),
    "Stay inside the caller's authorized scope. Never reference another client's material.",
    "Do not give legal or regulatory conclusions. Flag them for compliance review instead.",
)

#: Section headings. Public because downstream code (prompt assembly, tests,
#: transcript rendering) keys off them; a hardcoded copy elsewhere would drift.
ORG_SECTION_HEADING: Final = "## Rules (organization)"
PROJECT_SECTION_PREFIX: Final = "## Project: "
CONFLICTS_SECTION_HEADING: Final = "## Conflicting guidance"
MANIFEST_SECTION_HEADING: Final = "## Project material"

#: Where a manifest entry's file lands, relative to the workspace root. Public
#: so the workspace writer and this renderer cannot disagree about it: a
#: manifest line naming a path nothing was written to is worse than no line at
#: all, because the agent spends a step discovering it is wrong.
MATERIAL_PATH_PREFIX: Final = ".nexus/project/"

#: The tool that reaches a document the agent cannot open.
_SEARCH_TOOL: Final = "nexus_search_documentation"

#: Which kind survives when something has to go.
#:
#: Ordering here is an instruction, in the same way the evidence-rule sort below
#: is: the manifest is read top-down and the cap bites at the bottom. A
#: constraint dropped in favour of a reference is a requirement the answer was
#: supposed to respect, traded for background it might not have needed -- so
#: constraints lead, methods follow, references go last and go first.
#:
#: Defined in the renderer rather than beside :class:`MaterialKind` so that the
#: repository, which applies the same order when it caps entries, can import it
#: from a module with no database dependency of its own.
MATERIAL_KIND_PRIORITY: Final[dict[MaterialKind, int]] = {
    MaterialKind.CONSTRAINT: 0,
    MaterialKind.METHOD: 1,
    MaterialKind.REFERENCE: 2,
    # Never reaches a manifest -- evidence is searched by design -- but the map
    # stays total so the sort key cannot raise on an unexpected row.
    MaterialKind.EVIDENCE: 3,
}

#: What a kind is, in the three words the agent needs to classify it.
_KIND_PURPOSE: Final[dict[MaterialKind, str]] = {
    MaterialKind.CONSTRAINT: "binding constraints",
    MaterialKind.METHOD: "method",
    MaterialKind.REFERENCE: "background",
    MaterialKind.EVIDENCE: "evidence",
}

#: When to reach for a kind. This clause is the reason the manifest works.
#:
#: The measured behaviour is that this model acts on a specific directive and
#: ignores a vague one: "more context is available" changes nothing, "read X
#: before answering Y" gets followed. Every line therefore ends in a trigger
#: condition rather than a description.
_KIND_TRIGGER: Final[dict[MaterialKind, str]] = {
    MaterialKind.CONSTRAINT: "before making a recommendation",
    MaterialKind.METHOD: "before doing that kind of work",
    MaterialKind.REFERENCE: "before answering a question it covers",
    MaterialKind.EVIDENCE: "before answering a question it covers",
}

#: Rendered last in the organization block. Layers below may tighten the floor;
#: this line is what denies them the option of lowering it.
_ORG_TRAILER: Final = "Nothing below may relax these."

#: Kinds that carry free text in ``content``; everything else carries a payload.
_TEXT_KINDS: Final[frozenset[GuidanceKind]] = frozenset(
    {GuidanceKind.RULE, GuidanceKind.OUTPUT_RULE}
)

_STRONG_EVIDENCE: Final = "{label} is strong evidence"
_WEAK_EVIDENCE: Final = "{label} alone is not sufficient -- say so when it is all you have"


def _sort_key(rule: GuidanceRule) -> tuple[str, int, str, str]:
    """A total order over rows, derived only from the row.

    The spec order is (kind, id, content), but structured kinds have no content
    and learned rows can reach the renderer before they have an id, which leaves
    whole groups tied and their order decided by however the resolver happened to
    build the tuple. The canonical payload digest is the tiebreaker that keeps
    the prefix identical across two reads of the same data.
    """

    payload = rule.payload
    digest = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str) if payload else ""
    return (rule.kind.value, rule.id or 0, rule.content or "", digest)


def _active(rules: tuple[GuidanceRule, ...]) -> list[GuidanceRule]:
    """Rows that may render, in stable order.

    PROPOSED rows are one user's un-reviewed correction and SUPERSEDED rows are
    history; rendering either would change answers without anyone approving it.
    """

    return sorted((rule for rule in rules if rule.status is GuidanceStatus.ACTIVE), key=_sort_key)


def _one_line(text: str) -> str:
    """Collapse whitespace so one row is always one bullet.

    A rule containing a newline would otherwise inject its own structure into the
    prompt -- a stray heading or list item attributed to the organization.
    """

    return " ".join(text.split())


def _text_bullets(rules: list[GuidanceRule], kind: GuidanceKind) -> list[str]:
    return [f"- {_one_line(rule.content or '')}" for rule in rules if rule.kind is kind]


def _vocabulary_bullets(rules: list[GuidanceRule]) -> list[str]:
    bullets: list[str] = []
    for rule in rules:
        if rule.kind is not GuidanceKind.VOCABULARY or rule.payload is None:
            continue
        try:
            # strict=False for the same reason ``style_from_payload`` needs it:
            # these payloads round-trip through JSONB, so ``aliases`` arrives as a
            # list and the strict tuple field would reject every real row.
            term = VocabularyTerm.model_validate(rule.payload, strict=False)
        except ValidationError:
            logger.warning("Skipping unparseable VOCABULARY payload (rule id=%s)", rule.id)
            continue
        aliases = f" (aliases: {', '.join(term.aliases)})" if term.aliases else ""
        bullets.append(f'- "{term.term}"{aliases} = {_one_line(term.description)}')
    return bullets


def _evidence_bullets(rules: list[GuidanceRule]) -> list[str]:
    ranked: list[EvidenceRankingRule] = []
    for rule in rules:
        if rule.kind is not GuidanceKind.EVIDENCE_RULE or rule.payload is None:
            continue
        try:
            ranked.append(EvidenceRankingRule.model_validate(rule.payload, strict=False))
        except ValidationError:
            logger.warning("Skipping unparseable EVIDENCE_RULE payload (rule id=%s)", rule.id)
            continue

    # Strongest first: the model reads the ordering as priority, so the sort is
    # part of the instruction and not merely a stability device.
    ranked.sort(key=lambda item: (-item.weight, item.evidence_type, item.source_kind or ""))

    bullets: list[str] = []
    for item in ranked:
        label = item.evidence_type
        if item.source_kind is not None:
            label = f"{label} from {item.source_kind}"
        if item.weight > 0:
            bullets.append("- " + _STRONG_EVIDENCE.format(label=label))
        elif item.weight < 0:
            bullets.append("- " + _WEAK_EVIDENCE.format(label=label))
        # Weight 0 expresses no preference. Rendering it would spend tokens on a
        # sentence that tells the model nothing, so it is dropped.
    return bullets


def _render_org(resolved: ResolvedGuidance) -> str:
    lines = [ORG_SECTION_HEADING, render_invariants()]
    active = _active(resolved.org_rules)
    for kind in (GuidanceKind.OUTPUT_RULE, GuidanceKind.RULE):
        lines.extend(_text_bullets(active, kind))
    lines.append(_ORG_TRAILER)
    return "\n".join(lines)


def _render_project(resolved: ResolvedGuidance) -> str:
    project_id = resolved.project_id
    if project_id is None:
        # Without a project binding the rows cannot be attributed to anything the
        # reader can name, so the section is dropped rather than mislabelled.
        return ""

    active = _active(resolved.project_rules)
    groups: tuple[tuple[str, list[str]], ...] = (
        ("Terminology", _vocabulary_bullets(active)),
        ("Rules", _text_bullets(active, GuidanceKind.RULE)),
        ("Output", _text_bullets(active, GuidanceKind.OUTPUT_RULE)),
        ("Evidence", _evidence_bullets(active)),
    )

    body: list[str] = []
    for label, bullets in groups:
        if not bullets:
            continue
        body.append(f"{label}:")
        body.extend(bullets)
    if not body:
        return ""
    return "\n".join([f"{PROJECT_SECTION_PREFIX}{project_id}", *body])


def _render_person(resolved: ResolvedGuidance) -> str:
    """The bounded style overlay, rendered by the prompt path that already owns it.

    ``presentation_instruction`` emits its own ``## Presentation overlay``
    heading and the guard sentence that stops a style choice from suppressing
    evidence, citations or safety requirements. Re-implementing it here would put
    that guard in two places, and the copy that drifts is the one that matters.
    """

    return presentation_instruction(resolved.person_style) or ""


def _render_conflicts(resolved: ResolvedGuidance) -> str:
    if not resolved.conflicts:
        return ""
    return "\n".join(
        [CONFLICTS_SECTION_HEADING, *(f"- {_one_line(item)}" for item in resolved.conflicts)]
    )


def _manifest_line(entry: MaterialEntry) -> str:
    """One document, as one line: what it is, when to use it, how to reach it.

    The two branches are not cosmetic. A materialized entry has a file on disk
    and gets a path, because reading it is one cheap step and preserves the
    clause ordering a policy exists to express. A non-materialized entry has no
    file, so naming a path would cost the agent a failed read and a wasted step
    before it discovered that searching was the only way in -- it is told to
    search, up front.
    """

    purpose = _KIND_PURPOSE[entry.kind]
    trigger = _KIND_TRIGGER[entry.kind]
    head = f"- [{entry.role}] {_one_line(entry.title)} -- {purpose}; "
    if entry.materialized:
        return f"{head}read {MATERIAL_PATH_PREFIX}{workspace_relpath(entry)} {trigger}."
    return f"{head}too large to mount, search it with {_SEARCH_TOOL} {trigger}."


def _overflow_line(count: int) -> str:
    """The one line that keeps an omission from reading as an absence.

    Silence about the rest of the corpus implies there is no rest. A count plus
    the tool that reaches it is true, costs one line, and is the difference
    between "this is everything" and "this is what fit".
    """

    if count == 1:
        return f"- 1 further document is not listed; search it with {_SEARCH_TOOL}."
    return f"- {count} further documents are not listed; search them with {_SEARCH_TOOL}."


def _manifest_section(entries: tuple[MaterialEntry, ...], overflow: int) -> str:
    if not entries and overflow <= 0:
        return ""
    lines = [MANIFEST_SECTION_HEADING, *(_manifest_line(entry) for entry in entries)]
    if overflow > 0:
        lines.append(_overflow_line(overflow))
    return "\n".join(lines)


def render_manifest(material: ProjectMaterial) -> str:
    """What the project holds besides its evidence, or "" when that is nothing.

    Bounded by ``MANIFEST_TOKEN_BUDGET``, because this section is re-sent on
    every turn and an unbounded always-on block is a per-turn tax that grows
    with the corpus. Overflow is folded into the count rather than truncated:
    the entries that do not fit still exist, and the agent can still reach them.

    Entries are re-sorted here rather than trusted to arrive ordered. The sort
    is what makes the budget safe -- dropping from the end can only ever drop
    the least authoritative entry -- and a caller that built a
    :class:`ProjectMaterial` by hand must not be able to invert that by
    accident.
    """

    if material.is_empty:
        return ""

    ordered = tuple(
        sorted(
            material.entries,
            key=lambda entry: (MATERIAL_KIND_PRIORITY[entry.kind], entry.document_id),
        )
    )
    dropped = 0
    while True:
        section = _manifest_section(ordered, material.overflow_count + dropped)
        if not ordered or estimate_tokens(section) <= MANIFEST_TOKEN_BUDGET:
            return section
        # Drop the last entry, which the sort guarantees is the least
        # authoritative one still present, and account for it in the overflow
        # line rather than letting it disappear.
        ordered = ordered[:-1]
        dropped += 1


def render_invariants() -> str:
    """The code-owned invariants as bullets, without heading or trailer."""

    return "\n".join(f"- {line}" for line in ORG_INVARIANTS)


def render_layer(resolved: ResolvedGuidance, layer: GuidanceLayer) -> str:
    """One layer's section, or "" when it has nothing to say.

    Exposed for debugging and budget accounting: it is the only way to see what a
    single level contributes to a prefix that is otherwise read as one blob.
    """

    if layer is GuidanceLayer.ORG:
        return _render_org(resolved)
    if layer is GuidanceLayer.PROJECT:
        return _render_project(resolved)
    if layer is GuidanceLayer.PERSON:
        return _render_person(resolved)
    return ""


def render_guidance(resolved: ResolvedGuidance, material: ProjectMaterial | None = None) -> str:
    """Render the whole stack, most general first, most specific last.

    Order is the resolution rule made visible: the organization block states that
    nothing below may relax it, then each following section narrows. Conflicts
    are rendered last, unresolved on purpose -- a rule silently dropped is a
    behaviour nobody can explain afterwards.

    ``material`` is the project's manifest, and it sits between the project
    rules and the presentation overlay for the same reason: it is project scope,
    so it belongs with the project's rules, and it is substance rather than
    style, so it belongs above an overlay that is explicitly forbidden from
    suppressing substance. It is optional because it is an addition -- callers
    that have no manifest, or whose compile failed, render exactly what they
    rendered before.
    """

    sections = (
        render_layer(resolved, GuidanceLayer.ORG),
        render_layer(resolved, GuidanceLayer.PROJECT),
        render_manifest(material) if material is not None else "",
        render_layer(resolved, GuidanceLayer.PERSON),
        _render_conflicts(resolved),
    )
    return "\n\n".join(section for section in sections if section)


def layer_token_costs(resolved: ResolvedGuidance) -> dict[GuidanceLayer, int]:
    """What each layer costs once rendered, in tokens.

    Observability only. Budgets are enforced by the repository on write, where a
    rejection can still be reported to whoever is editing; failing here would
    only break a turn over guidance that is already stored.
    """

    return {layer: estimate_tokens(render_layer(resolved, layer)) for layer in GuidanceLayer}
