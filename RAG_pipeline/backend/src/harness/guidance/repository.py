"""Writes to the guidance stack, and the two controls that make them safe.

Two things have to be true of a level that any user can add to, or "the agent
learns from you" becomes "the prompt grows forever and nobody can say why it
answers this way":

* **The budget is enforced on write, not observed after the fact.** Guidance is
  re-sent on every model call, so an over-budget level is a tax on every turn
  until someone notices. ``add_rule`` refuses the write instead
  (:class:`GuidanceBudgetExceeded`), and ``approve_rule`` re-checks -- approval
  is the moment a proposed rule starts costing tokens, so it is the second
  place the ceiling can be breached.
* **Nothing is deleted.** A replaced rule becomes ``superseded`` and its
  successor records ``supersedes_id``. An answer given last month is only
  explainable if the rule that shaped it is still readable.

The authority matrix (which kinds a level may contain) and the scope shape are
NOT re-checked here. :class:`~src.harness.guidance.models.GuidanceRule` already
enforces both in its validator, so an illegal rule cannot be constructed to be
passed in, and the database repeats the scope half as
``ck_guidance_scope_matches_layer``. A third copy in this module would be a
third thing to keep in step with the other two.
"""

from __future__ import annotations

import datetime
import json
import logging
from collections.abc import Sequence

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.guidance import GuidanceRuleRow
from src.harness.guidance.models import (
    LAYER_TOKEN_BUDGETS,
    GuidanceKind,
    GuidanceLayer,
    GuidanceRule,
    GuidanceStatus,
    estimate_tokens,
    style_to_payload,
)

logger = logging.getLogger(__name__)

__all__ = [
    "GuidanceBudgetExceeded",
    "GuidanceRepository",
    "GuidanceRuleNotFound",
    "renderable_text",
]

#: Provenance prefix written by learned write-back, as opposed to ``admin_ui``.
_LEARNED_SOURCE_PREFIX = "learned:"


class GuidanceBudgetExceeded(ValueError):
    """A write would push a level past its per-level token ceiling."""


class GuidanceRuleNotFound(LookupError):
    """No guidance row with the requested id."""


def _storage_payload(rule: GuidanceRule) -> dict[str, object] | None:
    """The payload as it goes into the JSON column.

    A STYLE payload is normalized through ``style_to_payload`` first. A caller
    holding real enum members would otherwise store something JSONB cannot take,
    and the value that comes back from storage is a string either way -- so
    normalizing on write is what makes the round trip an identity rather than a
    coincidence of how the row was created.
    """

    if rule.kind is not GuidanceKind.STYLE:
        return rule.payload
    style = rule.style
    return style_to_payload(style) if style is not None else rule.payload


def renderable_text(rule: GuidanceRule) -> str:
    """The text a rule contributes to the prompt.

    Public because the compiler's cache key digests the same string: two
    definitions of "what this rule says" would let a budget and a cache key
    disagree about whether a rule changed.

    Free-text kinds render their ``content``; structured kinds render from
    ``payload``, and the canonical ``sort_keys`` dump is what makes the estimate
    stable across two dicts that differ only in key order. The model's validator
    guarantees exactly one of the two is populated for a given kind, so testing
    ``content`` first needs no second copy of the kind-to-shape mapping.

    The stored payload, not the supplied one: the estimate has to describe the
    row that will be read back, not the shape it happened to arrive in.
    """

    if rule.content and rule.content.strip():
        return rule.content
    payload = _storage_payload(rule)
    if payload is None:
        return ""
    return json.dumps(payload, sort_keys=True)


def _status_on_write(rule: GuidanceRule) -> GuidanceStatus:
    """The status a newly written rule actually gets.

    A learned rule that affects only its author needs no gate -- a PERSON rule
    changes nothing for anyone else, and making one wait for review would mean
    a correction never takes effect for the person who made it. A learned rule
    at any wider level does need one: one user's correction must not silently
    rewrite how the project (or the organization) answers for everyone, so it
    lands as PROPOSED and someone approves it.
    """

    if not rule.source.startswith(_LEARNED_SOURCE_PREFIX):
        return rule.status
    if rule.layer is GuidanceLayer.PERSON:
        return GuidanceStatus.ACTIVE
    return GuidanceStatus.PROPOSED


class GuidanceRepository:
    """Reads and writes ``guidance_rules``, converting rows at the boundary.

    Writes fail loudly: a rejected budget or a violated constraint must reach
    the caller, because a silently dropped rule is a rule the operator believes
    is in force. The fail-soft behaviour belongs one level up, on the read path
    (see :mod:`src.harness.guidance.compiler`), where losing tuning is better
    than losing the turn.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # -- conversion ------------------------------------------------------
    #
    # Both directions live here and nowhere else. The STYLE payload in
    # particular is the one place a round-trip can go wrong (stored enums come
    # back as strings), and a second conversion site is how that gets
    # rediscovered the hard way.

    @staticmethod
    def _to_rule(row: GuidanceRuleRow) -> GuidanceRule:
        return GuidanceRule(
            id=row.id,
            layer=GuidanceLayer(row.layer),
            kind=GuidanceKind(row.kind),
            project_id=row.project_id,
            user_id=row.user_id,
            content=row.content,
            payload=row.payload,
            status=GuidanceStatus(row.status),
            source=row.source,
            supersedes_id=row.supersedes_id,
            created_by=row.created_by,
            created_at=row.created_at,
        )

    @staticmethod
    def _to_row(
        rule: GuidanceRule,
        *,
        status: GuidanceStatus,
        token_estimate: int,
    ) -> GuidanceRuleRow:
        # ``rule.id`` is deliberately ignored: a write always creates a row.
        # Editing in place would erase the text that produced past answers.
        return GuidanceRuleRow(
            layer=rule.layer.value,
            kind=rule.kind.value,
            project_id=rule.project_id,
            user_id=rule.user_id,
            content=rule.content,
            payload=_storage_payload(rule),
            status=status.value,
            source=rule.source,
            supersedes_id=rule.supersedes_id,
            token_estimate=token_estimate,
            created_by=rule.created_by,
        )

    # -- reads -----------------------------------------------------------

    @staticmethod
    def _scoped(
        statement: Select[tuple[GuidanceRuleRow]],
        *,
        layer: GuidanceLayer | None,
        project_id: str | None,
        user_id: int | None,
        status: GuidanceStatus | None,
    ) -> Select[tuple[GuidanceRuleRow]]:
        """Apply the filters that are set. ``None`` means "do not filter"."""

        if layer is not None:
            statement = statement.where(GuidanceRuleRow.layer == layer.value)
        if project_id is not None:
            statement = statement.where(GuidanceRuleRow.project_id == project_id)
        if user_id is not None:
            statement = statement.where(GuidanceRuleRow.user_id == user_id)
        if status is not None:
            statement = statement.where(GuidanceRuleRow.status == status.value)
        return statement

    async def list_rules(
        self,
        *,
        layer: GuidanceLayer | None = None,
        project_id: str | None = None,
        user_id: int | None = None,
        status: GuidanceStatus | None = GuidanceStatus.ACTIVE,
    ) -> list[GuidanceRule]:
        """Rules matching every filter that is set, oldest first.

        Ordering is by id, which is insertion order: the render is a stack, and
        a rule added later is stated later. Passing ``status=None`` reads every
        lifecycle state, which is what a review or an audit view wants.
        """

        statement = self._scoped(
            select(GuidanceRuleRow),
            layer=layer,
            project_id=project_id,
            user_id=user_id,
            status=status,
        ).order_by(GuidanceRuleRow.id)
        result = await self.db.execute(statement)
        return [self._to_rule(row) for row in result.scalars()]

    async def rule_versions(self, rule_ids: Sequence[int]) -> dict[int, datetime.datetime]:
        """``updated_at`` per id, for the compiler's cache key.

        Exists because :class:`GuidanceRule` carries no ``updated_at`` -- it is
        a statement, not a row -- while the cache key has to notice an edit in
        place. Reading it separately keeps that persistence detail out of the
        contract instead of widening the model to carry it.
        """

        if not rule_ids:
            return {}
        result = await self.db.execute(
            select(GuidanceRuleRow.id, GuidanceRuleRow.updated_at).where(
                GuidanceRuleRow.id.in_(list(rule_ids))
            )
        )
        return {row_id: updated_at for row_id, updated_at in result.all()}

    async def layer_token_usage(
        self,
        *,
        layer: GuidanceLayer,
        project_id: str | None = None,
        user_id: int | None = None,
    ) -> int:
        """Tokens currently spent by ACTIVE rules at one scope.

        Only ACTIVE rows count: a PROPOSED rule is not rendered and so costs
        nothing yet, and a SUPERSEDED one is history. The ceiling is about what
        the prompt carries, not about how much has ever been written.
        """

        statement = self._scoped(
            select(func.coalesce(func.sum(GuidanceRuleRow.token_estimate), 0)),
            layer=layer,
            project_id=project_id,
            user_id=user_id,
            status=GuidanceStatus.ACTIVE,
        )
        return int((await self.db.execute(statement)).scalar_one())

    # -- writes ----------------------------------------------------------

    async def _assert_within_budget(
        self,
        *,
        layer: GuidanceLayer,
        project_id: str | None,
        user_id: int | None,
        incoming_tokens: int,
        exclude_rule_id: int | None = None,
    ) -> None:
        ceiling = LAYER_TOKEN_BUDGETS[layer]
        used = await self.layer_token_usage(layer=layer, project_id=project_id, user_id=user_id)
        if exclude_rule_id is not None:
            # Re-checking an existing ACTIVE row must not count it twice.
            used -= await self._token_estimate_of(exclude_rule_id)
        total = used + incoming_tokens
        if total > ceiling:
            raise GuidanceBudgetExceeded(
                f"{layer.value} guidance budget exceeded: {used} tokens in use plus "
                f"{incoming_tokens} incoming is {total}, over the {ceiling}-token "
                f"ceiling by {total - ceiling}. Supersede or retire an existing "
                f"{layer.value} rule first."
            )

    async def _token_estimate_of(self, rule_id: int) -> int:
        result = await self.db.execute(
            select(GuidanceRuleRow.token_estimate).where(GuidanceRuleRow.id == rule_id)
        )
        return int(result.scalar_one_or_none() or 0)

    async def _get_row(self, rule_id: int) -> GuidanceRuleRow:
        row = (
            await self.db.execute(select(GuidanceRuleRow).where(GuidanceRuleRow.id == rule_id))
        ).scalar_one_or_none()
        if row is None:
            raise GuidanceRuleNotFound(f"no guidance rule with id {rule_id}")
        return row

    async def add_rule(self, rule: GuidanceRule) -> GuidanceRule:
        """Persist a new rule, enforcing the level's token ceiling.

        Raises :class:`GuidanceBudgetExceeded` when the level is full. This is a
        refusal, not a warning: a level that is allowed to overrun its ceiling
        does not have one.
        """

        tokens = estimate_tokens(renderable_text(rule))
        status = _status_on_write(rule)
        if status is GuidanceStatus.ACTIVE:
            # A PROPOSED row renders nothing, so it cannot breach the ceiling
            # yet; ``approve_rule`` runs the same check at the moment it would.
            await self._assert_within_budget(
                layer=rule.layer,
                project_id=rule.project_id,
                user_id=rule.user_id,
                incoming_tokens=tokens,
            )

        row = self._to_row(rule, status=status, token_estimate=tokens)
        self.db.add(row)
        await self.db.flush()
        await self.db.refresh(row)
        return self._to_rule(row)

    async def supersede_rule(
        self,
        rule_id: int,
        replacement: GuidanceRule | None,
        *,
        actor_id: int | None,
    ) -> GuidanceRule | None:
        """Retire a rule, optionally writing its successor.

        Returns the stored replacement, or ``None`` when the rule was retired
        without one. The old row keeps its text and gains status ``superseded``;
        deleting it would make a past answer unexplainable.

        A missing ``rule_id`` raises rather than no-opping: silently succeeding
        at retiring nothing leaves the caller believing a rule is gone.
        """

        row = await self._get_row(rule_id)
        row.status = GuidanceStatus.SUPERSEDED.value
        # Retire first, then write the successor, so the budget check sees the
        # level without the rule being replaced. Doing it the other way round
        # would reject a same-size replacement on a level that is exactly full.
        await self.db.flush()

        if replacement is None:
            logger.info(
                "Guidance rule %s superseded with no replacement by actor %s", rule_id, actor_id
            )
            return None

        successor = replacement.model_copy(
            update={
                "supersedes_id": rule_id,
                "created_by": actor_id if actor_id is not None else replacement.created_by,
            }
        )
        return await self.add_rule(successor)

    async def approve_rule(self, rule_id: int, *, actor_id: int | None) -> GuidanceRule:
        """Move a PROPOSED rule to ACTIVE, re-checking the level's ceiling.

        Approval is the point at which a learned rule starts being sent on every
        turn, so it is the second place the budget can be breached -- a proposal
        written when the level had room can be approved after it no longer does.

        The approver is logged rather than stored: this table holds statements,
        and who signed off on one belongs to the audit trail. Approving an
        already-ACTIVE rule is a no-op so a retried request is harmless.
        """

        row = await self._get_row(rule_id)
        if row.status == GuidanceStatus.ACTIVE.value:
            return self._to_rule(row)

        await self._assert_within_budget(
            layer=GuidanceLayer(row.layer),
            project_id=row.project_id,
            user_id=row.user_id,
            incoming_tokens=row.token_estimate,
        )
        row.status = GuidanceStatus.ACTIVE.value
        await self.db.flush()
        await self.db.refresh(row)
        logger.info("Guidance rule %s approved by actor %s", rule_id, actor_id)
        return self._to_rule(row)
