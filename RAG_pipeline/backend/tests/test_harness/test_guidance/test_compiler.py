"""What a given (project, person) actually gets, and why.

The compiler is where the hierarchy stops being three tables and becomes one
prompt prefix: the style merge, the organization's whitelist, dedupe, the cache
key that has to mean something, and the fail-soft that keeps a database outage
from taking the turn with it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from src.db.models.guidance import GuidanceRuleRow
from src.db.models.user import User
from src.harness.guidance import compiler
from src.harness.guidance.compiler import compile_guidance
from src.harness.guidance.models import (
    GuidanceKind,
    GuidanceLayer,
    GuidanceRule,
    style_to_payload,
)
from src.harness.guidance.repository import GuidanceRepository
from src.harness.packs.models import (
    FormattingOption,
    OutputOrganization,
    StyleAudience,
    StyleField,
    StyleGovernancePolicy,
    StylePresentation,
    StyleTone,
    TechnicalDepth,
)

PROJECT = "reverse_engineer_demo"


async def _user(db, username: str = "compiler") -> User:
    user = User(username=username, email=f"{username}@example.com")
    db.add(user)
    await db.flush()
    return user


def _style(**fields: object) -> dict[str, object]:
    return style_to_payload(StylePresentation(**fields))


async def _seed_three_level_style(db, user_id: int) -> GuidanceRepository:
    repository = GuidanceRepository(db)
    await repository.add_rule(
        GuidanceRule(
            layer=GuidanceLayer.ORG,
            kind=GuidanceKind.STYLE,
            payload=_style(
                audience=StyleAudience.ENGINEERING,
                tone=StyleTone.NEUTRAL,
                technical_depth=TechnicalDepth.STANDARD,
            ),
        )
    )
    await repository.add_rule(
        GuidanceRule(
            layer=GuidanceLayer.PROJECT,
            kind=GuidanceKind.STYLE,
            project_id=PROJECT,
            payload=_style(tone=StyleTone.EXPLANATORY, output_organization=OutputOrganization.SECTIONED),
        )
    )
    await repository.add_rule(
        GuidanceRule(
            layer=GuidanceLayer.PERSON,
            kind=GuidanceKind.STYLE,
            project_id=PROJECT,
            user_id=user_id,
            payload=_style(tone=StyleTone.CONCISE, audience=StyleAudience.BUSINESS),
        )
    )
    return repository


# -- merge ------------------------------------------------------------------


async def test_style_merges_across_three_levels_with_the_person_winning(db_session) -> None:
    user = await _user(db_session)
    await _seed_three_level_style(db_session, user.id)

    resolved = await compile_guidance(db_session, project_id=PROJECT, user_id=user.id)
    style = resolved.person_style

    assert style is not None
    # Person overrode both levels below it.
    assert style.tone is StyleTone.CONCISE
    assert style.audience is StyleAudience.BUSINESS
    # Project supplied a field neither of the other levels set.
    assert style.output_organization is OutputOrganization.SECTIONED
    # Org remains the floor for anything nobody overrode.
    assert style.technical_depth is TechnicalDepth.STANDARD
    # Precedence is the design, not a disagreement, so it produces no noise.
    assert resolved.conflicts == ()


async def test_person_style_is_ignored_for_a_different_user(db_session) -> None:
    owner = await _user(db_session, "owner")
    other = await _user(db_session, "other")
    await _seed_three_level_style(db_session, owner.id)

    resolved = await compile_guidance(db_session, project_id=PROJECT, user_id=other.id)

    assert resolved.person_style is not None
    assert resolved.person_style.tone is StyleTone.EXPLANATORY
    assert resolved.person_style.audience is StyleAudience.ENGINEERING


# -- whitelist --------------------------------------------------------------


async def test_a_field_outside_the_whitelist_is_dropped_and_recorded(db_session) -> None:
    """A preference that is not honoured has to be visible, not silent."""

    user = await _user(db_session)
    await _seed_three_level_style(db_session, user.id)

    # TONE is permitted (and both values are allowed); AUDIENCE is not permitted
    # at all, so every level's audience is dropped.
    policy = StyleGovernancePolicy(
        permitted_fields=frozenset(
            {
                StyleField.TONE,
                StyleField.TECHNICAL_DEPTH,
                StyleField.OUTPUT_ORGANIZATION,
            }
        ),
        allowed_tones=frozenset({StyleTone.NEUTRAL, StyleTone.CONCISE, StyleTone.EXPLANATORY}),
        allowed_technical_depths=frozenset({TechnicalDepth.STANDARD}),
        allowed_output_organizations=frozenset({OutputOrganization.SECTIONED}),
    )

    resolved = await compile_guidance(
        db_session, project_id=PROJECT, user_id=user.id, style_policy=policy
    )

    assert resolved.person_style is not None
    assert resolved.person_style.audience is None
    assert resolved.person_style.tone is StyleTone.CONCISE
    assert any(
        "person style preference audience=business" in line for line in resolved.conflicts
    )


async def test_a_forbidden_person_value_falls_back_to_the_permitted_project_value(
    db_session,
) -> None:
    """The honest outcome when two levels pull apart, stated in one line."""

    user = await _user(db_session)
    await _seed_three_level_style(db_session, user.id)

    policy = StyleGovernancePolicy(
        permitted_fields=frozenset({StyleField.TONE}),
        allowed_tones=frozenset({StyleTone.EXPLANATORY, StyleTone.NEUTRAL}),
    )

    resolved = await compile_guidance(
        db_session, project_id=PROJECT, user_id=user.id, style_policy=policy
    )

    assert resolved.person_style is not None
    assert resolved.person_style.tone is StyleTone.EXPLANATORY
    assert any(
        "person style preference tone=concise" in line and "project setting tone=explanatory" in line
        for line in resolved.conflicts
    )


async def test_a_partly_permitted_sequence_keeps_the_allowed_members(db_session) -> None:
    user = await _user(db_session)
    repository = GuidanceRepository(db_session)
    await repository.add_rule(
        GuidanceRule(
            layer=GuidanceLayer.PERSON,
            kind=GuidanceKind.STYLE,
            project_id=PROJECT,
            user_id=user.id,
            payload=_style(formatting=(FormattingOption.MARKDOWN, FormattingOption.TABLES)),
        )
    )

    policy = StyleGovernancePolicy(
        permitted_fields=frozenset({StyleField.FORMATTING}),
        allowed_formatting=frozenset({FormattingOption.MARKDOWN}),
    )
    resolved = await compile_guidance(
        db_session, project_id=PROJECT, user_id=user.id, style_policy=policy
    )

    assert resolved.person_style is not None
    assert resolved.person_style.formatting == (FormattingOption.MARKDOWN,)
    assert any("formatting=tables" in line for line in resolved.conflicts)


async def test_no_policy_permits_everything(db_session) -> None:
    user = await _user(db_session)
    await _seed_three_level_style(db_session, user.id)

    resolved = await compile_guidance(db_session, project_id=PROJECT, user_id=user.id)

    assert resolved.conflicts == ()
    assert resolved.person_style is not None
    assert resolved.person_style.audience is StyleAudience.BUSINESS


# -- dedupe -----------------------------------------------------------------


async def test_the_same_statement_at_two_levels_renders_once(db_session) -> None:
    repository = GuidanceRepository(db_session)
    await repository.add_rule(
        GuidanceRule(
            layer=GuidanceLayer.ORG,
            kind=GuidanceKind.RULE,
            content="Never invent evidence.",
        )
    )
    await repository.add_rule(
        GuidanceRule(
            layer=GuidanceLayer.PROJECT,
            kind=GuidanceKind.RULE,
            project_id=PROJECT,
            # Same statement, different casing and spacing.
            content="never   invent evidence",
        )
    )
    await repository.add_rule(
        GuidanceRule(
            layer=GuidanceLayer.PROJECT,
            kind=GuidanceKind.RULE,
            project_id=PROJECT,
            content="Name the environment on every runtime claim.",
        )
    )

    resolved = await compile_guidance(db_session, project_id=PROJECT, user_id=None)

    assert resolved.org_rules == ()
    assert [rule.content for rule in resolved.project_rules] == [
        "never   invent evidence",
        "Name the environment on every runtime claim.",
    ]


async def test_style_rules_are_not_rendered_twice(db_session) -> None:
    """A STYLE row is consumed by the merge; leaving it in the rule lists would
    state the same preference again, unbounded by the whitelist."""

    user = await _user(db_session)
    await _seed_three_level_style(db_session, user.id)

    resolved = await compile_guidance(db_session, project_id=PROJECT, user_id=user.id)

    assert resolved.org_rules == ()
    assert resolved.project_rules == ()
    assert resolved.person_style is not None


# -- cache key --------------------------------------------------------------


async def test_cache_key_is_stable_across_two_identical_compiles(db_session) -> None:
    user = await _user(db_session)
    await _seed_three_level_style(db_session, user.id)

    first = await compile_guidance(db_session, project_id=PROJECT, user_id=user.id)
    second = await compile_guidance(db_session, project_id=PROJECT, user_id=user.id)

    assert first.cache_key
    assert first.cache_key == second.cache_key


async def test_cache_key_separates_two_people_on_one_project(db_session) -> None:
    owner = await _user(db_session, "key-owner")
    other = await _user(db_session, "key-other")
    await _seed_three_level_style(db_session, owner.id)

    owner_key = (await compile_guidance(db_session, project_id=PROJECT, user_id=owner.id)).cache_key
    other_key = (await compile_guidance(db_session, project_id=PROJECT, user_id=other.id)).cache_key

    assert owner_key != other_key


async def test_cache_key_changes_when_a_rule_changes(db_session) -> None:
    repository = GuidanceRepository(db_session)
    original = await repository.add_rule(
        GuidanceRule(
            layer=GuidanceLayer.PROJECT,
            kind=GuidanceKind.RULE,
            project_id=PROJECT,
            content="Name the environment on every runtime claim.",
        )
    )
    assert original.id is not None
    before = (await compile_guidance(db_session, project_id=PROJECT, user_id=None)).cache_key

    await repository.supersede_rule(
        original.id,
        GuidanceRule(
            layer=GuidanceLayer.PROJECT,
            kind=GuidanceKind.RULE,
            project_id=PROJECT,
            content="Name the environment and the engine on every runtime claim.",
        ),
        actor_id=None,
    )
    after = (await compile_guidance(db_session, project_id=PROJECT, user_id=None)).cache_key

    assert before != after


async def test_cache_key_changes_on_an_in_place_edit_within_the_same_second(
    db_session,
) -> None:
    """`updated_at` alone cannot carry the guarantee.

    SQLite's CURRENT_TIMESTAMP has one-second resolution and PostgreSQL's now()
    is fixed per transaction, so an edit can leave the timestamp unchanged. The
    key digests the rendered text as well, which is why this still moves.
    """

    repository = GuidanceRepository(db_session)
    stored = await repository.add_rule(
        GuidanceRule(
            layer=GuidanceLayer.ORG,
            kind=GuidanceKind.RULE,
            content="Never invent evidence.",
        )
    )
    assert stored.id is not None
    before = (await compile_guidance(db_session, project_id=None, user_id=None)).cache_key

    # An out-of-band edit, as an admin UI that updated a row in place would do.
    row = (
        await db_session.execute(
            select(GuidanceRuleRow).where(GuidanceRuleRow.id == stored.id)
        )
    ).scalar_one()
    row.content = "Never invent evidence; name what is missing."
    await db_session.flush()

    after = (await compile_guidance(db_session, project_id=None, user_id=None)).cache_key
    assert before != after


# -- fail-soft --------------------------------------------------------------


async def test_a_database_failure_yields_empty_guidance_instead_of_raising(
    db_session, monkeypatch
) -> None:
    """Guidance is tuning; the invariants are code-owned constants in the
    renderer, so losing the read must never lose the turn."""

    user = await _user(db_session)
    await _seed_three_level_style(db_session, user.id)

    async def _explode(*args: object, **kwargs: object) -> list[GuidanceRule]:
        raise SQLAlchemyError("connection reset by peer")

    monkeypatch.setattr(compiler.GuidanceRepository, "list_rules", _explode)

    resolved = await compile_guidance(db_session, project_id=PROJECT, user_id=user.id)

    assert resolved.is_empty
    assert resolved.org_rules == ()
    assert resolved.project_rules == ()
    assert resolved.person_style is None
    assert resolved.project_id == PROJECT
    assert resolved.user_id == user.id


async def test_an_unreadable_row_does_not_take_the_turn_with_it(
    db_session, monkeypatch
) -> None:
    """Same trade for a row that no longer parses: it is not repairable at turn
    time, and failing every turn until someone notices helps nobody."""

    from pydantic import ValidationError

    user = await _user(db_session)
    await _seed_three_level_style(db_session, user.id)

    async def _explode(*args: object, **kwargs: object) -> list[GuidanceRule]:
        raise ValidationError.from_exception_data("GuidanceRule", [])

    monkeypatch.setattr(compiler.GuidanceRepository, "list_rules", _explode)

    resolved = await compile_guidance(db_session, project_id=PROJECT, user_id=user.id)

    assert resolved.is_empty


async def test_an_empty_stack_compiles_to_empty_guidance(db_session) -> None:
    resolved = await compile_guidance(db_session, project_id=PROJECT, user_id=None)

    assert resolved.is_empty
    assert resolved.conflicts == ()
    assert resolved.cache_key


@pytest.mark.parametrize("project_id", [None, PROJECT])
async def test_org_rules_render_with_or_without_a_project(db_session, project_id) -> None:
    repository = GuidanceRepository(db_session)
    await repository.add_rule(
        GuidanceRule(
            layer=GuidanceLayer.ORG,
            kind=GuidanceKind.RULE,
            content="Never invent evidence.",
        )
    )

    resolved = await compile_guidance(db_session, project_id=project_id, user_id=None)

    assert [rule.content for rule in resolved.org_rules] == ["Never invent evidence."]
