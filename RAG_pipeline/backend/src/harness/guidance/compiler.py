"""Resolve the three durable levels into one renderable stack.

The compiler is the only place that decides what a given (project, person)
actually gets. Three decisions live here and nowhere else:

* **Style is merged, not concatenated.** ORG sets the floor, PROJECT overrides
  field by field, PERSON overrides on top. Sending three overlapping style
  blocks and hoping the model prefers the last one is not a hierarchy; it is
  three contradictory instructions competing for attention.
* **The organization bounds what the lower levels may say.** A person's
  preference is honoured only where ``StyleGovernancePolicy`` permits that field
  and that value. A value the policy forbids is dropped and *stated* in
  ``conflicts`` -- a preference that silently does nothing is the failure mode
  where a user keeps re-asking for something they were never going to get.
* **A database failure degrades tuning, never the turn.** The organization's
  invariants are code-owned constants in the renderer, so an empty
  ``ResolvedGuidance`` still renders the safety floor. Failing the turn instead
  would trade a small loss for a total one.

The result carries a ``cache_key`` over everything that fed it. The render is
pure, so an unchanged key means an unchanged prompt prefix -- the property the
prefix ordering exists to protect.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.harness.guidance.models import (
    GuidanceKind,
    GuidanceLayer,
    GuidanceRule,
    GuidanceStatus,
    ResolvedGuidance,
)
from src.harness.guidance.repository import GuidanceRepository, renderable_text
from src.harness.packs.models import (
    CommunicationOption,
    FormattingOption,
    OutputOrganization,
    StyleAudience,
    StyleField,
    StyleGovernancePolicy,
    StylePresentation,
    StyleTone,
    TechnicalDepth,
)

logger = logging.getLogger(__name__)

__all__ = ["compile_guidance"]

#: A value one style field can hold. The scalar fields are single enum members;
#: ``formatting`` and ``communication`` are tuples of them.
_StyleValue = (
    StyleAudience
    | StyleTone
    | TechnicalDepth
    | OutputOrganization
    | tuple[FormattingOption, ...]
    | tuple[CommunicationOption, ...]
)

#: Field -> the policy attribute listing the values permitted for it.
_ALLOWED_VALUES_ATTR: dict[StyleField, str] = {
    StyleField.AUDIENCE: "allowed_audiences",
    StyleField.TONE: "allowed_tones",
    StyleField.TECHNICAL_DEPTH: "allowed_technical_depths",
    StyleField.OUTPUT_ORGANIZATION: "allowed_output_organizations",
    StyleField.FORMATTING: "allowed_formatting",
    StyleField.COMMUNICATION: "allowed_communication",
}

#: The two multi-valued fields; the rest hold one member or nothing.
_SEQUENCE_FIELDS: frozenset[StyleField] = frozenset(
    {StyleField.FORMATTING, StyleField.COMMUNICATION}
)


def _normalized(text: str) -> str:
    """Comparison form for dedupe: case and spacing are not content.

    Trailing periods go too, so "Never invent evidence" and "Never invent
    evidence." are one statement rather than two lines of prompt saying the
    same thing.
    """

    return " ".join(text.split()).casefold().rstrip(".")


def _bound_value(
    field: StyleField,
    value: _StyleValue,
    policy: StyleGovernancePolicy | None,
) -> tuple[_StyleValue | None, tuple[str, ...]]:
    """Trim ``value`` to what ``policy`` permits.

    Returns the permitted remainder (``None`` if nothing survives) and the
    labels of what was removed. A ``None`` policy permits everything: no
    organization has configured a whitelist, so bounding against an empty
    default would silently discard every preference in the system.
    """

    if policy is None:
        return value, ()

    if field not in policy.permitted_fields:
        labels = tuple(str(item) for item in value) if field in _SEQUENCE_FIELDS else (str(value),)
        return None, labels

    allowed = getattr(policy, _ALLOWED_VALUES_ATTR[field])
    if field in _SEQUENCE_FIELDS:
        # Member-wise: permitting "markdown" while forbidding "tables" should
        # keep the markdown, not throw the whole preference away.
        kept = tuple(item for item in value if item in allowed)
        dropped = tuple(str(item) for item in value if item not in allowed)
        return (kept or None), dropped
    if value in allowed:
        return value, ()
    return None, (str(value),)


def _merge_style(
    layered: list[tuple[GuidanceLayer, StylePresentation]],
    policy: StyleGovernancePolicy | None,
) -> tuple[StylePresentation | None, list[str]]:
    """Merge style overlays in order, bounded by the organization's whitelist.

    ``layered`` is least specific first. A field set at a later level replaces
    the earlier value; a field left unset there leaves the earlier value
    standing, which is what makes ORG a floor rather than a competing opinion.

    The whitelist is applied per candidate rather than to the final result. If
    the person asks for a forbidden tone and the project set a permitted one,
    bounding at the end would leave the field unset; bounding per candidate
    leaves the project's value in force, which is the honest outcome -- and the
    conflict line says exactly that.

    Precedence itself is never reported as a conflict: a person overriding a
    project default is the design, not a disagreement. Only a value that was
    asked for and not honoured is worth a line in the prompt.
    """

    merged: dict[StyleField, _StyleValue] = {}
    provenance: dict[StyleField, GuidanceLayer] = {}
    conflicts: list[str] = []

    for layer, style in layered:
        for field in StyleField:
            value: _StyleValue | None = getattr(style, field.value)
            if value is None or value == ():
                continue

            kept, dropped = _bound_value(field, value, policy)
            if dropped:
                conflicts.append(_conflict_line(layer, field, dropped, kept, merged, provenance))
            if kept is None:
                continue
            merged[field] = kept
            provenance[field] = layer

    if not merged:
        return None, conflicts
    # Built from real enum members, so the strict model accepts it directly.
    return StylePresentation(**{field.value: value for field, value in merged.items()}), conflicts


def _conflict_line(
    layer: GuidanceLayer,
    field: StyleField,
    dropped: tuple[str, ...],
    kept: _StyleValue | None,
    merged: dict[StyleField, _StyleValue],
    provenance: dict[StyleField, GuidanceLayer],
) -> str:
    """One sentence naming what was asked for, what happened, and what applies."""

    asked = ", ".join(dropped)
    head = (
        f"{layer.value} style preference {field.value}={asked} is not permitted "
        f"by the organization style policy"
    )
    if kept is not None:
        remainder = ", ".join(str(item) for item in kept) if field in _SEQUENCE_FIELDS else str(kept)
        return f"{head}; {field.value}={remainder} applies."
    previous = merged.get(field)
    if previous is not None:
        shown = (
            ", ".join(str(item) for item in previous)
            if field in _SEQUENCE_FIELDS
            else str(previous)
        )
        return f"{head}; the {provenance[field].value} setting {field.value}={shown} applies instead."
    return f"{head}; {field.value} is unset."


def _dedupe(
    project_rules: list[GuidanceRule], org_rules: list[GuidanceRule]
) -> tuple[list[GuidanceRule], list[GuidanceRule]]:
    """Drop repeated statements, keeping the most specific occurrence.

    The same house rule restated by a project is one instruction, not two, and
    the duplicate costs tokens on every turn. The project copy wins because it
    is the level that can be amended; the org copy still applies through it.

    Structured rules (vocabulary, evidence) carry no ``content`` and are left
    alone: two payloads that look alike are not necessarily the same statement,
    and guessing costs more than the duplicate would.
    """

    seen: set[str] = set()
    kept_project: list[GuidanceRule] = []
    kept_org: list[GuidanceRule] = []
    for target, source in ((kept_project, project_rules), (kept_org, org_rules)):
        for rule in source:
            text = _normalized(rule.content or "")
            if not text:
                target.append(rule)
                continue
            if text in seen:
                continue
            seen.add(text)
            target.append(rule)
    return kept_project, kept_org


def _cache_key(
    contributions: list[tuple[int, datetime.datetime | None, str]],
    *,
    project_id: str | None,
    user_id: int | None,
) -> str:
    """Digest of every row that fed this compile, plus the scope it was for.

    ``(id, updated_at)`` is the spec'd input; the rendered text is digested
    alongside it because ``updated_at`` alone is not reliable enough to carry
    the "unchanged key means unchanged prompt" guarantee. SQLite's
    ``CURRENT_TIMESTAMP`` has one-second resolution and PostgreSQL's ``now()``
    is fixed for a whole transaction, so two edits can share a timestamp. The
    text is a superset of that signal: the key still changes whenever the
    timestamp would, and also when it would not.

    Rows dropped by dedupe are included. They took part in the decision, so a
    change to one can change the outcome even though it did not render.
    """

    payload = {
        "project_id": project_id,
        "user_id": user_id,
        "rows": sorted(
            [rule_id, updated_at.isoformat() if updated_at else "", text]
            for rule_id, updated_at, text in contributions
        ),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


async def compile_guidance(
    db: AsyncSession,
    *,
    project_id: str | None,
    user_id: int | None,
    style_policy: StyleGovernancePolicy | None = None,
) -> ResolvedGuidance:
    """Compile the ACTIVE guidance visible to one (project, person).

    ``style_policy`` is the organization's permitted-style whitelist. It is a
    parameter rather than a database read because nothing persists an
    ``OrganizationGovernancePolicy`` yet; the caller that gains one passes it
    here, and until then ``None`` means "no whitelist configured" and every
    field is permitted.

    Never raises on a failed or unreadable read. The renderer's code-owned
    invariants do not come from here, so an empty result loses the tuning and
    keeps the safety floor -- the opposite trade to failing the turn.
    """

    repository = GuidanceRepository(db)
    try:
        org_rules = await repository.list_rules(
            layer=GuidanceLayer.ORG, status=GuidanceStatus.ACTIVE
        )
        project_rules: list[GuidanceRule] = []
        person_rules: list[GuidanceRule] = []
        if project_id:
            project_rules = await repository.list_rules(
                layer=GuidanceLayer.PROJECT,
                project_id=project_id,
                status=GuidanceStatus.ACTIVE,
            )
            if user_id is not None:
                person_rules = await repository.list_rules(
                    layer=GuidanceLayer.PERSON,
                    project_id=project_id,
                    user_id=user_id,
                    status=GuidanceStatus.ACTIVE,
                )
        loaded = org_rules + project_rules + person_rules
        versions = await repository.rule_versions(
            [rule.id for rule in loaded if rule.id is not None]
        )
    except SQLAlchemyError:
        # Deliberate fail-soft. Guidance is tuning; the invariants that make an
        # answer safe are constants in the renderer and are unaffected.
        logger.error(
            "Guidance compile failed for project=%s user=%s; "
            "continuing with code-owned invariants only",
            project_id,
            user_id,
            exc_info=True,
        )
        return ResolvedGuidance(project_id=project_id, user_id=user_id)
    except ValidationError:
        # Same trade for the same reason: a stored row that no longer parses
        # would otherwise break every turn for this project until someone
        # noticed, and the row is not repairable at turn time.
        logger.error(
            "Guidance rows for project=%s user=%s failed validation; "
            "continuing with code-owned invariants only",
            project_id,
            user_id,
            exc_info=True,
        )
        return ResolvedGuidance(project_id=project_id, user_id=user_id)

    layered: list[tuple[GuidanceLayer, StylePresentation]] = []
    for layer, rules in (
        (GuidanceLayer.ORG, org_rules),
        (GuidanceLayer.PROJECT, project_rules),
        (GuidanceLayer.PERSON, person_rules),
    ):
        for rule in rules:
            style = rule.style
            if style is not None:
                layered.append((layer, style))

    person_style, conflicts = _merge_style(layered, style_policy)

    # STYLE rules are consumed by the merge. Leaving them in the rendered rule
    # lists as well would state the same preference twice, once merged and once
    # raw, and the raw one would not be whitelist-bounded.
    kept_project, kept_org = _dedupe(
        [rule for rule in project_rules if rule.kind is not GuidanceKind.STYLE],
        [rule for rule in org_rules if rule.kind is not GuidanceKind.STYLE],
    )

    return ResolvedGuidance(
        project_id=project_id,
        user_id=user_id,
        org_rules=tuple(kept_org),
        project_rules=tuple(kept_project),
        person_style=person_style,
        conflicts=tuple(conflicts),
        cache_key=_cache_key(
            [
                (rule.id, versions.get(rule.id), renderable_text(rule))
                for rule in loaded
                if rule.id is not None
            ],
            project_id=project_id,
            user_id=user_id,
        ),
    )
