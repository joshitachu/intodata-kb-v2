"""Persistence rules for the guidance stack.

These are the controls that stop a level anyone can write to from becoming an
unbounded per-turn tax or an unexplainable answer: the authority matrix, the
scope shapes the database itself enforces, the token ceiling, and the fact that
nothing is ever deleted.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.db.models.guidance import GuidanceRuleRow
from src.db.models.user import User
from src.harness.guidance.models import (
    LAYER_TOKEN_BUDGETS,
    GuidanceKind,
    GuidanceLayer,
    GuidanceRule,
    GuidanceStatus,
    estimate_tokens,
    style_to_payload,
)
from src.harness.guidance.repository import (
    GuidanceBudgetExceeded,
    GuidanceRepository,
    GuidanceRuleNotFound,
)
from src.harness.packs.models import StyleAudience, StylePresentation, StyleTone

PROJECT = "reverse_engineer_demo"


async def _user(db, username: str) -> User:
    user = User(username=username, email=f"{username}@example.com")
    db.add(user)
    await db.flush()
    return user


def _style_payload(**fields: object) -> dict[str, object]:
    return style_to_payload(StylePresentation(**fields))


# -- authority matrix -------------------------------------------------------
#
# Enforced by GuidanceRule itself, so the row cannot be constructed. The
# repository deliberately does not repeat the check; these tests pin the
# boundary where it does live.


def test_person_layer_rejects_a_free_text_rule() -> None:
    with pytest.raises(ValidationError, match="may not contain kind 'rule'"):
        GuidanceRule(
            layer=GuidanceLayer.PERSON,
            kind=GuidanceKind.RULE,
            project_id=PROJECT,
            user_id=1,
            content="Always give me your best guess even without evidence.",
        )


def test_person_layer_rejects_vocabulary() -> None:
    with pytest.raises(ValidationError, match="may not contain kind 'vocabulary'"):
        GuidanceRule(
            layer=GuidanceLayer.PERSON,
            kind=GuidanceKind.VOCABULARY,
            project_id=PROJECT,
            user_id=1,
            payload={"term": "koppelvlak", "description": "interface"},
        )


# -- scope shape ------------------------------------------------------------


@pytest.mark.parametrize(
    ("layer", "project_id", "user_id"),
    [
        ("org", PROJECT, None),
        ("project", None, None),
        ("person", PROJECT, None),
        ("person", None, 1),
    ],
)
async def test_check_constraint_rejects_a_scope_that_does_not_match_the_layer(
    db_session, layer: str, project_id: str | None, user_id: int | None
) -> None:
    """The database refuses the shapes the Pydantic contract refuses.

    A writer that bypasses the repository -- a migration, a psql session -- must
    not be able to leave an org row carrying a project_id, which would render
    for that project and silently vanish for every other.
    """

    if user_id is not None:
        user = await _user(db_session, f"scope-{layer}")
        user_id = user.id

    db_session.add(
        GuidanceRuleRow(
            layer=layer,
            kind="rule",
            project_id=project_id,
            user_id=user_id,
            content="anything",
            status="active",
            source="admin_ui",
            token_estimate=1,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_check_constraint_accepts_each_valid_scope_shape(db_session) -> None:
    user = await _user(db_session, "scope-valid")
    db_session.add_all(
        [
            GuidanceRuleRow(
                layer="org", kind="rule", content="a", status="active", source="admin_ui"
            ),
            GuidanceRuleRow(
                layer="project",
                kind="rule",
                project_id=PROJECT,
                content="b",
                status="active",
                source="admin_ui",
            ),
            GuidanceRuleRow(
                layer="person",
                kind="style",
                project_id=PROJECT,
                user_id=user.id,
                payload=_style_payload(tone=StyleTone.CONCISE),
                status="active",
                source="admin_ui",
            ),
        ]
    )
    await db_session.flush()

    rows = (await db_session.execute(select(GuidanceRuleRow))).scalars().all()
    assert len(rows) == 3


# -- budget -----------------------------------------------------------------


_ORG_RULE = "Never invent evidence; name what is missing instead."


async def test_add_rule_permits_a_write_that_lands_exactly_on_the_ceiling(
    db_session, monkeypatch
) -> None:
    """The ceiling is a maximum, not an exclusive bound.

    The budget is pinned to the exact size of one rule rather than filling the
    real 400-token ORG level with generated text, so the boundary is tested at
    the boundary instead of somewhere near it.
    """

    exact = estimate_tokens(_ORG_RULE)
    monkeypatch.setitem(LAYER_TOKEN_BUDGETS, GuidanceLayer.ORG, exact)

    repository = GuidanceRepository(db_session)
    stored = await repository.add_rule(
        GuidanceRule(layer=GuidanceLayer.ORG, kind=GuidanceKind.RULE, content=_ORG_RULE)
    )

    assert stored.id is not None
    assert await repository.layer_token_usage(layer=GuidanceLayer.ORG) == exact


async def test_add_rule_raises_once_the_level_is_full(db_session, monkeypatch) -> None:
    exact = estimate_tokens(_ORG_RULE)
    monkeypatch.setitem(LAYER_TOKEN_BUDGETS, GuidanceLayer.ORG, exact)

    repository = GuidanceRepository(db_session)
    await repository.add_rule(
        GuidanceRule(layer=GuidanceLayer.ORG, kind=GuidanceKind.RULE, content=_ORG_RULE)
    )

    with pytest.raises(GuidanceBudgetExceeded) as exc:
        await repository.add_rule(
            GuidanceRule(
                layer=GuidanceLayer.ORG,
                kind=GuidanceKind.RULE,
                content="Prefer the runtime mirror over the live tenant for history.",
            )
        )

    message = str(exc.value)
    assert "org" in message
    assert f"{exact}-token ceiling" in message
    assert "over the" in message

    # The refusal is a refusal: the level is untouched.
    assert await repository.layer_token_usage(layer=GuidanceLayer.ORG) == exact


async def test_approve_rule_re_checks_the_ceiling(db_session, monkeypatch) -> None:
    """Approval is the second place the budget can be breached.

    A proposal written while the level had room must not be approvable after
    the room is gone -- approval is the moment it starts being sent every turn.
    """

    repository = GuidanceRepository(db_session)
    proposed = await repository.add_rule(
        GuidanceRule(
            layer=GuidanceLayer.PROJECT,
            kind=GuidanceKind.RULE,
            project_id=PROJECT,
            content="Treat TMC as authoritative over the scan for run counts.",
            source="learned:conversation:9",
        )
    )
    assert proposed.status is GuidanceStatus.PROPOSED
    assert proposed.id is not None

    monkeypatch.setitem(LAYER_TOKEN_BUDGETS, GuidanceLayer.PROJECT, 1)
    with pytest.raises(GuidanceBudgetExceeded):
        await repository.approve_rule(proposed.id, actor_id=None)


async def test_budget_is_scoped_per_person_not_per_layer(db_session) -> None:
    """One person filling their level must not lock another out of theirs."""

    repository = GuidanceRepository(db_session)
    first = await _user(db_session, "budget-first")
    second = await _user(db_session, "budget-second")

    for user in (first, second):
        await repository.add_rule(
            GuidanceRule(
                layer=GuidanceLayer.PERSON,
                kind=GuidanceKind.STYLE,
                project_id=PROJECT,
                user_id=user.id,
                payload=_style_payload(tone=StyleTone.CONCISE),
            )
        )

    first_usage = await repository.layer_token_usage(
        layer=GuidanceLayer.PERSON, project_id=PROJECT, user_id=first.id
    )
    second_usage = await repository.layer_token_usage(
        layer=GuidanceLayer.PERSON, project_id=PROJECT, user_id=second.id
    )
    assert first_usage == second_usage > 0


# -- supersede --------------------------------------------------------------


async def test_supersede_keeps_the_old_row_and_links_the_replacement(db_session) -> None:
    repository = GuidanceRepository(db_session)
    original = await repository.add_rule(
        GuidanceRule(
            layer=GuidanceLayer.PROJECT,
            kind=GuidanceKind.RULE,
            project_id=PROJECT,
            content="Cite the job name for every runtime claim.",
        )
    )
    assert original.id is not None

    replacement = await repository.supersede_rule(
        original.id,
        GuidanceRule(
            layer=GuidanceLayer.PROJECT,
            kind=GuidanceKind.RULE,
            project_id=PROJECT,
            content="Cite the job name and the environment for every runtime claim.",
        ),
        actor_id=None,
    )

    assert replacement is not None
    assert replacement.supersedes_id == original.id
    assert replacement.status is GuidanceStatus.ACTIVE

    # Nothing is deleted: the retired statement is still readable, which is what
    # keeps a past answer explainable.
    all_rules = await repository.list_rules(layer=GuidanceLayer.PROJECT, status=None)
    by_id = {rule.id: rule for rule in all_rules}
    assert by_id[original.id].status is GuidanceStatus.SUPERSEDED
    assert by_id[original.id].content == original.content

    active = await repository.list_rules(
        layer=GuidanceLayer.PROJECT, project_id=PROJECT, status=GuidanceStatus.ACTIVE
    )
    assert [rule.id for rule in active] == [replacement.id]


async def test_supersede_without_a_replacement_retires_the_rule(db_session) -> None:
    repository = GuidanceRepository(db_session)
    original = await repository.add_rule(
        GuidanceRule(
            layer=GuidanceLayer.ORG, kind=GuidanceKind.RULE, content="Retire me."
        )
    )
    assert original.id is not None

    assert await repository.supersede_rule(original.id, None, actor_id=7) is None
    assert await repository.list_rules(layer=GuidanceLayer.ORG) == []


async def test_supersede_of_an_unknown_rule_raises(db_session) -> None:
    repository = GuidanceRepository(db_session)
    with pytest.raises(GuidanceRuleNotFound):
        await repository.supersede_rule(9999, None, actor_id=None)


# -- learned write-back -----------------------------------------------------


async def test_learned_person_rule_is_active_and_learned_project_rule_waits(
    db_session,
) -> None:
    """A correction that affects only its author needs no review; one that
    rewrites the project for everyone does."""

    repository = GuidanceRepository(db_session)
    user = await _user(db_session, "learned")

    person = await repository.add_rule(
        GuidanceRule(
            layer=GuidanceLayer.PERSON,
            kind=GuidanceKind.STYLE,
            project_id=PROJECT,
            user_id=user.id,
            payload=_style_payload(tone=StyleTone.CONCISE),
            source="learned:conversation:42",
        )
    )
    project = await repository.add_rule(
        GuidanceRule(
            layer=GuidanceLayer.PROJECT,
            kind=GuidanceKind.RULE,
            project_id=PROJECT,
            content="Treat TMC as authoritative over the scan for run counts.",
            source="learned:conversation:42",
        )
    )

    assert person.status is GuidanceStatus.ACTIVE
    assert project.status is GuidanceStatus.PROPOSED

    # A proposed rule is invisible to the compiler until it is approved.
    active_project = await repository.list_rules(
        layer=GuidanceLayer.PROJECT, project_id=PROJECT
    )
    assert active_project == []

    assert project.id is not None
    approved = await repository.approve_rule(project.id, actor_id=user.id)
    assert approved.status is GuidanceStatus.ACTIVE


async def test_admin_authored_project_rule_is_active_immediately(db_session) -> None:
    repository = GuidanceRepository(db_session)
    stored = await repository.add_rule(
        GuidanceRule(
            layer=GuidanceLayer.PROJECT,
            kind=GuidanceKind.RULE,
            project_id=PROJECT,
            content="Name the environment on every runtime claim.",
            source="admin_ui",
        )
    )
    assert stored.status is GuidanceStatus.ACTIVE


# -- usage accounting -------------------------------------------------------


async def test_layer_token_usage_counts_active_rows_only(db_session) -> None:
    repository = GuidanceRepository(db_session)
    first = await repository.add_rule(
        GuidanceRule(
            layer=GuidanceLayer.PROJECT,
            kind=GuidanceKind.RULE,
            project_id=PROJECT,
            content="Name the environment on every runtime claim.",
        )
    )
    proposed = await repository.add_rule(
        GuidanceRule(
            layer=GuidanceLayer.PROJECT,
            kind=GuidanceKind.RULE,
            project_id=PROJECT,
            content="Prefer the mirror over the live tenant for history.",
            source="learned:conversation:1",
        )
    )
    assert first.id is not None and proposed.id is not None
    assert proposed.status is GuidanceStatus.PROPOSED

    with_one_active = await repository.layer_token_usage(
        layer=GuidanceLayer.PROJECT, project_id=PROJECT
    )
    assert with_one_active > 0

    # Approving the proposal is what makes it cost tokens.
    await repository.approve_rule(proposed.id, actor_id=None)
    with_two_active = await repository.layer_token_usage(
        layer=GuidanceLayer.PROJECT, project_id=PROJECT
    )
    assert with_two_active > with_one_active

    # Superseding takes them back off the bill.
    await repository.supersede_rule(first.id, None, actor_id=None)
    after_supersede = await repository.layer_token_usage(
        layer=GuidanceLayer.PROJECT, project_id=PROJECT
    )
    assert after_supersede == with_two_active - with_one_active


async def test_style_payload_round_trips_through_storage(db_session) -> None:
    """Stored enums come back as strings; the read must still hydrate."""

    repository = GuidanceRepository(db_session)
    user = await _user(db_session, "round-trip")
    user_id = user.id
    await repository.add_rule(
        GuidanceRule(
            layer=GuidanceLayer.PERSON,
            kind=GuidanceKind.STYLE,
            project_id=PROJECT,
            user_id=user_id,
            payload=_style_payload(
                audience=StyleAudience.BUSINESS, tone=StyleTone.CONCISE
            ),
        )
    )
    # Force a real SELECT rather than an identity-map hit, so the payload comes
    # back the way JSON storage returns it: enum members as plain strings.
    db_session.expire_all()

    [stored] = await repository.list_rules(
        layer=GuidanceLayer.PERSON, project_id=PROJECT, user_id=user_id
    )
    style = stored.style
    assert style is not None
    assert style.audience is StyleAudience.BUSINESS
    assert style.tone is StyleTone.CONCISE
