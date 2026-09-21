"""Who may change which level of the guidance stack, and what happens when they try.

Exercised against the in-memory SQLite ``db_session`` rather than a mocked
session, following ``test_project_ownership_api.py``: every interesting property
here -- project scoping, superseding instead of deleting, the token ceiling, the
compiled preview -- is a query result, and a mock would only assert that the
route called the mock.

The last section tests the production ``/chat`` wiring in
``agent_chat_runtime``. It lives here because it is the same feature: the text
this API writes is the text that turn assembles, and the two are only correct
together.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_user, get_db
from src.api.main import app
from src.db.models import Permission, ProjectAssignment, Role, User
from src.harness.agent_chat_runtime import (
    _authoritative_turn_context,
    _compiled_turn_guidance,
    _turn_prompt_context,
)
from src.harness.guidance.models import (
    LAYER_TOKEN_BUDGETS,
    GuidanceKind,
    GuidanceLayer,
    GuidanceRule,
    GuidanceStatus,
    estimate_tokens,
    style_to_payload,
)
from src.harness.guidance.renderer import ORG_INVARIANTS
from src.harness.guidance.repository import GuidanceRepository
from src.harness.packs.models import StyleAudience, StylePresentation, StyleTone

CSRF = {"cookie": "csrf_token=t", "X-CSRF-Token": "t"}

PROJECT = "reverse_engineer_demo"
OTHER_PROJECT = "someone_elses_landscape"


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed_user(
    db: AsyncSession,
    username: str,
    *,
    superadmin: bool = False,
    perms: tuple[str, ...] = (),
    projects: tuple[str, ...] = (),
) -> User:
    """A user with exactly the authority the test needs and nothing more.

    A user with no ``view_all_projects`` and no assignment has an *empty* project
    scope rather than an unrestricted one, which is the shape the 403 tests need.

    ``roles`` and ``permissions`` are always assigned, even when empty. The
    permission checks walk both collections synchronously, and an untouched lazy
    relationship on an async session raises ``MissingGreenlet`` rather than
    returning "no permissions" -- which would turn every authorization test into
    a test of SQLAlchemy loader state.
    """

    role = Role(name=f"role_{username}", description="")
    role.permissions = [Permission(codename=codename, description="") for codename in perms]
    user = User(
        username=username,
        email=f"{username}@example.com",
        is_superadmin=superadmin,
        mfa_enabled=True,
    )
    user.roles = [role]
    db.add(user)
    await db.flush()
    for project_id in projects:
        db.add(ProjectAssignment(user_id=user.id, project_id=project_id))
    await db.flush()
    return user


@pytest.fixture
def as_user(db_session: AsyncSession):
    """Authenticate the app as a seeded user, and clear the overrides afterwards."""

    def _use(user: User) -> None:
        app.dependency_overrides[get_db] = lambda: db_session
        app.dependency_overrides[get_current_user] = lambda: user

    yield _use
    app.dependency_overrides.clear()


# -- organization level ------------------------------------------------------


async def test_only_the_owner_writes_org_rules_but_everyone_may_read_them(
    db_session: AsyncSession, as_user
) -> None:
    owner = await _seed_user(db_session, "owner_a", superadmin=True)
    member = await _seed_user(db_session, "member_a")

    as_user(member)
    async with _client() as client:
        refused = await client.post(
            "/api/v1/guidance/org",
            json={"kind": "rule", "content": "Name the source system for every claim."},
            headers=CSRF,
        )
    assert refused.status_code == 403, refused.text

    as_user(owner)
    async with _client() as client:
        created = await client.post(
            "/api/v1/guidance/org",
            json={"kind": "rule", "content": "Name the source system for every claim."},
            headers=CSRF,
        )
    assert created.status_code == 201, created.text
    assert created.json()["layer"] == "org"
    assert created.json()["status"] == "active"

    # A user is subject to the house rules, so a user may read them.
    as_user(member)
    async with _client() as client:
        listed = await client.get("/api/v1/guidance/org")
    assert listed.status_code == 200, listed.text
    assert [rule["content"] for rule in listed.json()["rules"]] == [
        "Name the source system for every claim."
    ]


async def test_deleting_an_org_rule_supersedes_it_instead_of_removing_it(
    db_session: AsyncSession, as_user
) -> None:
    owner = await _seed_user(db_session, "owner_b", superadmin=True)
    stored = await GuidanceRepository(db_session).add_rule(
        GuidanceRule(
            layer=GuidanceLayer.ORG,
            kind=GuidanceKind.RULE,
            content="Report an empty result as empty.",
        )
    )

    as_user(owner)
    async with _client() as client:
        deleted = await client.delete(f"/api/v1/guidance/org/{stored.id}", headers=CSRF)
        active = await client.get("/api/v1/guidance/org")
        history = await client.get("/api/v1/guidance/org", params={"status": "superseded"})

    assert deleted.status_code == 204, deleted.text
    assert active.json()["rules"] == []
    # The text survives: a past answer is only explainable while its rule is readable.
    assert [rule["content"] for rule in history.json()["rules"]] == [
        "Report an empty result as empty."
    ]


async def test_the_authority_matrix_surfaces_as_a_422(
    db_session: AsyncSession, as_user
) -> None:
    """ORG may not carry VOCABULARY, and the refusal comes from the contract.

    Pins the route's job, which is to reshape the model's ``ValidationError``
    into a 422 rather than to restate the matrix -- an unmapped pydantic error
    raised inside a handler would surface as a 500.
    """

    owner = await _seed_user(db_session, "owner_c", superadmin=True)
    as_user(owner)
    async with _client() as client:
        refused = await client.post(
            "/api/v1/guidance/org",
            json={"kind": "vocabulary", "payload": {"term": "koppelvlak", "description": "x"}},
            headers=CSRF,
        )
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["code"] == "invalid_guidance_rule"
    assert "may not contain kind 'vocabulary'" in refused.json()["detail"]["message"]


async def test_a_full_org_level_returns_409_naming_the_layer_and_the_ceiling(
    db_session: AsyncSession, as_user
) -> None:
    owner = await _seed_user(db_session, "owner_d", superadmin=True)
    repository = GuidanceRepository(db_session)

    # Fill ORG until one more rule of this size would breach the ceiling. Driven
    # by the repository's own accounting so the test does not depend on how many
    # tokens a given sentence happens to cost.
    filler = ("Cite the evidence identifier behind every claim. " * 10)[:500]
    cost = estimate_tokens(filler)
    ceiling = LAYER_TOKEN_BUDGETS[GuidanceLayer.ORG]
    while await repository.layer_token_usage(layer=GuidanceLayer.ORG) + cost <= ceiling:
        await repository.add_rule(
            GuidanceRule(layer=GuidanceLayer.ORG, kind=GuidanceKind.RULE, content=filler)
        )

    as_user(owner)
    async with _client() as client:
        refused = await client.post(
            "/api/v1/guidance/org",
            json={"kind": "rule", "content": filler},
            headers=CSRF,
        )

    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert detail["code"] == "guidance_budget_exceeded"
    assert detail["layer"] == "org"
    assert detail["ceiling"] == ceiling
    assert f"{ceiling}-token ceiling" in detail["message"]


# -- project level -----------------------------------------------------------


async def test_a_user_without_access_to_a_project_is_refused_reads_and_writes(
    db_session: AsyncSession, as_user
) -> None:
    """403, not 404 and not an empty list.

    An empty list would read as "this project has no guidance", which is a
    materially wrong answer to a question about how the system behaves.
    """

    outsider = await _seed_user(
        db_session, "outsider", perms=("manage_projects",), projects=(OTHER_PROJECT,)
    )
    as_user(outsider)
    async with _client() as client:
        read = await client.get(f"/api/v1/guidance/projects/{PROJECT}")
        write = await client.post(
            f"/api/v1/guidance/projects/{PROJECT}",
            json={"kind": "rule", "content": "Werkorder means a 4PS work order."},
            headers=CSRF,
        )
        preview = await client.get(f"/api/v1/guidance/projects/{PROJECT}/preview")
        preferences = await client.get(f"/api/v1/guidance/projects/{PROJECT}/preferences")

    assert read.status_code == 403, read.text
    assert write.status_code == 403, write.text
    assert preview.status_code == 403, preview.text
    assert preferences.status_code == 403, preferences.text


async def test_project_writes_need_manage_projects_as_well_as_access(
    db_session: AsyncSession, as_user
) -> None:
    """Access alone reads; changing how the project answers needs the permission."""

    reader = await _seed_user(db_session, "reader", projects=(PROJECT,))
    as_user(reader)
    async with _client() as client:
        read = await client.get(f"/api/v1/guidance/projects/{PROJECT}")
        write = await client.post(
            f"/api/v1/guidance/projects/{PROJECT}",
            json={"kind": "rule", "content": "Werkorder means a 4PS work order."},
            headers=CSRF,
        )
    assert read.status_code == 200, read.text
    assert write.status_code == 403, write.text
    assert "manage_projects" in write.json()["detail"]["message"]


async def test_a_manager_creates_and_retires_a_project_rule(
    db_session: AsyncSession, as_user
) -> None:
    manager = await _seed_user(
        db_session, "manager_a", perms=("manage_projects",), projects=(PROJECT,)
    )
    as_user(manager)
    async with _client() as client:
        created = await client.post(
            f"/api/v1/guidance/projects/{PROJECT}",
            json={"kind": "rule", "content": "Werkorder means a 4PS work order."},
            headers=CSRF,
        )
        rule_id = created.json()["id"]
        retired = await client.delete(
            f"/api/v1/guidance/projects/{PROJECT}/rules/{rule_id}", headers=CSRF
        )
        remaining = await client.get(f"/api/v1/guidance/projects/{PROJECT}")

    assert created.status_code == 201, created.text
    assert created.json()["project_id"] == PROJECT
    assert retired.status_code == 204, retired.text
    assert remaining.json()["rules"] == []


async def test_approve_promotes_a_proposed_rule_to_active(
    db_session: AsyncSession, as_user
) -> None:
    manager = await _seed_user(
        db_session, "manager_b", perms=("manage_projects",), projects=(PROJECT,)
    )
    # A learned PROJECT rule lands as PROPOSED: one user's correction must not
    # silently rewrite how the project answers for everyone.
    proposed = await GuidanceRepository(db_session).add_rule(
        GuidanceRule(
            layer=GuidanceLayer.PROJECT,
            kind=GuidanceKind.RULE,
            project_id=PROJECT,
            content="TMC run ids are not job ids.",
            source="learned:conversation:7",
        )
    )
    assert proposed.status is GuidanceStatus.PROPOSED

    as_user(manager)
    async with _client() as client:
        before = await client.get(f"/api/v1/guidance/projects/{PROJECT}")
        approved = await client.post(
            f"/api/v1/guidance/projects/{PROJECT}/rules/{proposed.id}/approve", headers=CSRF
        )
        after = await client.get(f"/api/v1/guidance/projects/{PROJECT}")

    # A PROPOSED rule renders nothing until someone signs off on it.
    assert before.json()["rules"] == []
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "active"
    assert [rule["id"] for rule in after.json()["rules"]] == [proposed.id]


async def test_a_manager_cannot_reach_another_projects_rule_by_id(
    db_session: AsyncSession, as_user
) -> None:
    """The path authorizes one project; the rule id must not escape it.

    Without the scoped lookup in the route the repository would happily approve
    or supersede any rule whose id the caller guessed, because it addresses rules
    by id alone.
    """

    manager = await _seed_user(
        db_session, "manager_c", perms=("manage_projects",), projects=(PROJECT,)
    )
    foreign = await GuidanceRepository(db_session).add_rule(
        GuidanceRule(
            layer=GuidanceLayer.PROJECT,
            kind=GuidanceKind.RULE,
            project_id=OTHER_PROJECT,
            content="Another landscape's rule.",
            source="learned:conversation:9",
        )
    )

    as_user(manager)
    async with _client() as client:
        approved = await client.post(
            f"/api/v1/guidance/projects/{PROJECT}/rules/{foreign.id}/approve", headers=CSRF
        )
        deleted = await client.delete(
            f"/api/v1/guidance/projects/{PROJECT}/rules/{foreign.id}", headers=CSRF
        )

    assert approved.status_code == 404, approved.text
    assert deleted.status_code == 404, deleted.text
    survivor = await GuidanceRepository(db_session).list_rules(
        layer=GuidanceLayer.PROJECT, project_id=OTHER_PROJECT, status=None
    )
    assert [rule.status for rule in survivor] == [GuidanceStatus.PROPOSED]


# -- person level ------------------------------------------------------------


async def test_put_preferences_ignores_a_user_id_in_the_body(
    db_session: AsyncSession, as_user
) -> None:
    """The privilege-escalation case: one user rewriting another's preferences.

    The identity is taken from the authenticated session and from nowhere else,
    so a ``user_id`` in the body is dropped rather than honoured. Asserted on the
    stored rows, not just on the response, because the response could echo the
    right id while the write went somewhere else.
    """

    victim = await _seed_user(db_session, "victim", projects=(PROJECT,))
    attacker = await _seed_user(db_session, "attacker", projects=(PROJECT,))

    as_user(attacker)
    async with _client() as client:
        written = await client.put(
            f"/api/v1/guidance/projects/{PROJECT}/preferences",
            json={"user_id": victim.id, "tone": "concise"},
            headers=CSRF,
        )

    assert written.status_code == 200, written.text
    assert written.json()["user_id"] == attacker.id

    repository = GuidanceRepository(db_session)
    for_victim = await repository.list_rules(
        layer=GuidanceLayer.PERSON, project_id=PROJECT, user_id=victim.id, status=None
    )
    for_attacker = await repository.list_rules(
        layer=GuidanceLayer.PERSON, project_id=PROJECT, user_id=attacker.id, status=None
    )
    assert for_victim == []
    assert [rule.user_id for rule in for_attacker] == [attacker.id]

    # ...and the victim still sees nothing of their own.
    as_user(victim)
    async with _client() as client:
        theirs = await client.get(f"/api/v1/guidance/projects/{PROJECT}/preferences")
    assert theirs.json()["style"] is None
    assert theirs.json()["rules"] == []


async def test_a_person_has_no_way_to_write_a_free_text_rule(
    db_session: AsyncSession, as_user
) -> None:
    """PERSON is a closed set of enums, and that is enforced in two places.

    At the contract, where a PERSON free-text row cannot be constructed at all,
    and at the API, where the only PERSON surface is style-shaped: free text is
    dropped, nothing valid is left, and the request is a 422.
    """

    with pytest.raises(ValidationError, match="may not contain kind 'rule'"):
        GuidanceRule(
            layer=GuidanceLayer.PERSON,
            kind=GuidanceKind.RULE,
            project_id=PROJECT,
            user_id=1,
            content="Always give me your best guess even without evidence.",
        )

    person = await _seed_user(db_session, "person_a", projects=(PROJECT,))
    as_user(person)
    async with _client() as client:
        refused = await client.put(
            f"/api/v1/guidance/projects/{PROJECT}/preferences",
            json={"content": "Always give me your best guess even without evidence."},
            headers=CSRF,
        )

    assert refused.status_code == 422, refused.text
    assert "at least one presentation field" in refused.json()["detail"]["message"]
    assert (
        await GuidanceRepository(db_session).list_rules(
            layer=GuidanceLayer.PERSON, project_id=PROJECT, user_id=person.id, status=None
        )
        == []
    )


async def test_replacing_a_preference_supersedes_the_previous_one(
    db_session: AsyncSession, as_user
) -> None:
    person = await _seed_user(db_session, "person_b", projects=(PROJECT,))
    as_user(person)
    async with _client() as client:
        first = await client.put(
            f"/api/v1/guidance/projects/{PROJECT}/preferences",
            json={"tone": "concise"},
            headers=CSRF,
        )
        second = await client.put(
            f"/api/v1/guidance/projects/{PROJECT}/preferences",
            json={"audience": "executive", "formatting": ["tables"]},
            headers=CSRF,
        )
        current = await client.get(f"/api/v1/guidance/projects/{PROJECT}/preferences")

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert current.json()["style"]["audience"] == "executive"
    assert current.json()["style"]["formatting"] == ["tables"]

    stored = await GuidanceRepository(db_session).list_rules(
        layer=GuidanceLayer.PERSON, project_id=PROJECT, user_id=person.id, status=None
    )
    # Exactly one overlay is in force; the replaced one is retired, not deleted,
    # and points back at nothing while its successor points back at it.
    active = [rule for rule in stored if rule.status is GuidanceStatus.ACTIVE]
    retired = [rule for rule in stored if rule.status is GuidanceStatus.SUPERSEDED]
    assert len(active) == 1
    assert len(retired) == 1
    assert active[0].supersedes_id == retired[0].id


# -- preview -----------------------------------------------------------------


async def test_preview_renders_for_the_calling_user_and_keeps_the_invariants(
    db_session: AsyncSession, as_user
) -> None:
    caller = await _seed_user(db_session, "caller", projects=(PROJECT,))
    other = await _seed_user(db_session, "other", projects=(PROJECT,))
    repository = GuidanceRepository(db_session)
    await repository.add_rule(
        GuidanceRule(
            layer=GuidanceLayer.ORG,
            kind=GuidanceKind.RULE,
            content="Name the source system for every claim.",
        )
    )
    await repository.add_rule(
        GuidanceRule(
            layer=GuidanceLayer.PROJECT,
            kind=GuidanceKind.RULE,
            project_id=PROJECT,
            content="TMC run ids are not job ids.",
        )
    )
    await repository.add_rule(
        GuidanceRule(
            layer=GuidanceLayer.PERSON,
            kind=GuidanceKind.STYLE,
            project_id=PROJECT,
            user_id=caller.id,
            payload=style_to_payload(StylePresentation(tone=StyleTone.CONCISE)),
        )
    )
    await repository.add_rule(
        GuidanceRule(
            layer=GuidanceLayer.PERSON,
            kind=GuidanceKind.STYLE,
            project_id=PROJECT,
            user_id=other.id,
            payload=style_to_payload(StylePresentation(audience=StyleAudience.EXECUTIVE)),
        )
    )

    as_user(caller)
    async with _client() as client:
        preview = await client.get(f"/api/v1/guidance/projects/{PROJECT}/preview")

    assert preview.status_code == 200, preview.text
    body = preview.json()
    rendered = body["rendered"]

    # The safety floor is code-owned and renders whatever the database holds.
    for invariant in ORG_INVARIANTS:
        assert invariant in rendered
    assert "Name the source system for every claim." in rendered
    assert "TMC run ids are not job ids." in rendered
    # Rendered for the caller: their preference, never the other user's.
    assert "tone=concise" in rendered
    assert "audience=executive" not in rendered
    assert body["user_id"] == caller.id
    assert body["cache_key"]
    assert body["layer_budgets"]["person"] == LAYER_TOKEN_BUDGETS[GuidanceLayer.PERSON]


# -- production /chat wiring -------------------------------------------------
#
# `_turn_prompt_context` is tested directly rather than through a mounted
# `/chat` turn: reaching the assembled `system_prompt_suffix` that way would
# require a provider, a capability registry and a conversation claim, none of
# which take part in the property under test. `_compiled_turn_guidance` is
# covered separately against a real session, so the compile -> render -> compose
# chain is exercised end to end between the two.


def test_guidance_leads_the_prompt_suffix_ahead_of_the_volatile_scope() -> None:
    """Ordering is a caching decision: stable text first, volatile text after.

    The guidance stack is identical for every turn of a conversation while the
    scope projection and the page scope are not, so guidance first is what keeps
    the cacheable prefix contiguous.
    """

    guidance = "## Rules (organization)\n- Never invent a file path."
    context = _turn_prompt_context(["project-a"], None, guidance)

    assert context.startswith(guidance)
    projection = _authoritative_turn_context(["project-a"])
    assert projection in context
    assert context.index(guidance) < context.index(projection)


def test_an_unavailable_guidance_stack_leaves_the_suffix_unchanged() -> None:
    """Fail-soft has to be invisible, not merely non-fatal.

    An empty stack must not leave a blank block at the head of the prompt, or
    every turn would pay for a section that says nothing.
    """

    assert _turn_prompt_context(["project-a"], None, "") == _turn_prompt_context(
        ["project-a"], None
    )
    assert _turn_prompt_context(["project-a"], None).startswith(
        _authoritative_turn_context(["project-a"])
    )


async def test_compiled_turn_guidance_carries_the_stack_into_the_turn(
    db_session: AsyncSession,
) -> None:
    person = await _seed_user(db_session, "turn_user", projects=(PROJECT,))
    repository = GuidanceRepository(db_session)
    await repository.add_rule(
        GuidanceRule(
            layer=GuidanceLayer.PROJECT,
            kind=GuidanceKind.RULE,
            project_id=PROJECT,
            content="TMC run ids are not job ids.",
        )
    )
    await repository.add_rule(
        GuidanceRule(
            layer=GuidanceLayer.PERSON,
            kind=GuidanceKind.STYLE,
            project_id=PROJECT,
            user_id=person.id,
            payload=style_to_payload(StylePresentation(tone=StyleTone.CONCISE)),
        )
    )

    rendered = await _compiled_turn_guidance(
        db_session, project_scope=[PROJECT], user_id=person.id
    )
    assert "TMC run ids are not job ids." in rendered
    assert "tone=concise" in rendered
    assert ORG_INVARIANTS[0] in rendered

    suffix = _turn_prompt_context([PROJECT], None, rendered)
    assert suffix.startswith(rendered)


async def test_an_ambiguous_project_scope_still_renders_the_house_rules(
    db_session: AsyncSession,
) -> None:
    """Two projects have no single landscape, so no project or person level applies.

    Picking one arbitrarily would govern the answer with another project's
    vocabulary. The organization level binds everywhere and still renders.
    """

    person = await _seed_user(db_session, "wide_scope_user", projects=(PROJECT, OTHER_PROJECT))
    await GuidanceRepository(db_session).add_rule(
        GuidanceRule(
            layer=GuidanceLayer.PROJECT,
            kind=GuidanceKind.RULE,
            project_id=PROJECT,
            content="TMC run ids are not job ids.",
        )
    )

    rendered = await _compiled_turn_guidance(
        db_session, project_scope=[PROJECT, OTHER_PROJECT], user_id=person.id
    )
    assert ORG_INVARIANTS[0] in rendered
    assert "TMC run ids are not job ids." not in rendered
