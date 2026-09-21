"""Reading and editing the layered guidance stack over HTTP.

The stack itself is contract plus storage (:mod:`src.harness.guidance`). What
this module owns is *who may change which level*, and that is the only reason it
is a router of its own rather than three handlers bolted onto an existing admin
surface -- the three levels have three different owners:

* **ORG** is the house floor. Only the deployment Owner writes it, and every
  authenticated user may read it: people are subject to these rules, and a rule
  you cannot read is a behaviour you cannot explain.
* **PROJECT** is landscape knowledge. Writing it needs ``manage_projects`` *and*
  access to that project. Either alone is insufficient -- the permission is
  global, and the access list is the only thing that binds it to one landscape.
* **PERSON** is the caller's own presentation preference. Its scope comes from
  the authenticated session and never from the request. That is the whole
  security story of this level: there is no code path here that writes a row for
  a user id a client supplied.

Two rules cut across all of it:

* **A rule id from a URL is never trusted on its own.** The repository addresses
  rules by id alone, which is correct for it and wrong here; every write that
  takes a ``rule_id`` resolves it through a scoped read first, so the scope the
  route authorized is the scope the write lands in.
* **Nothing is deleted.** DELETE supersedes, so "why did it answer that way last
  month" stays answerable after the rule that caused it has been replaced.
"""

from __future__ import annotations

import datetime
import logging
from typing import Any, Final

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, DBSession, OwnerUser
from src.api.errors import api_error
from src.db.models import User
from src.document_roles import DOCUMENT_ROLES, DocumentRole
from src.harness.guidance.compiler import compile_guidance
from src.harness.guidance.material import (
    MATERIALIZE_MAX_CHARS,
    MaterialEntry,
    MaterialKind,
    material_kind,
    material_slug,
    should_materialize,
    workspace_relpath,
)
from src.harness.guidance.material_repository import (
    compile_project_material,
    load_material_texts,
)
from src.harness.guidance.models import (
    LAYER_TOKEN_BUDGETS,
    GuidanceKind,
    GuidanceLayer,
    GuidanceRule,
    GuidanceStatus,
    style_from_payload,
    style_to_payload,
)
from src.harness.guidance.renderer import (
    MATERIAL_PATH_PREFIX,
    layer_token_costs,
    render_guidance,
)
from src.harness.guidance.repository import (
    GuidanceBudgetExceeded,
    GuidanceRepository,
    GuidanceRuleNotFound,
)
from src.harness.packs.models import (
    CommunicationOption,
    FormattingOption,
    OutputOrganization,
    StyleAudience,
    StylePresentation,
    StyleTone,
    TechnicalDepth,
)
from src.labs.workspace import (
    always_loaded_tokens,
    labs_docs_mounted,
    project_workspace_files,
)
from src.rbac.permissions import (
    MANAGE_PROJECTS,
    get_user_groups,
    get_user_project_ids,
    has_permission,
)
from src.services.audit_service import AuditService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/guidance", tags=["guidance"])

#: Provenance for everything written through this API, as opposed to the
#: ``learned:conversation:<id>`` prefix the write-back path uses. The repository
#: reads it to decide whether a write needs review, so it is not decoration.
_ADMIN_SOURCE = "admin_ui"


# -- wire models -------------------------------------------------------------


class GuidanceRuleResponse(BaseModel):
    """One stored rule, projected for the wire.

    Declared separately from :class:`GuidanceRule` even though that model is
    already a Pydantic contract and not an ORM row: the contract is free to gain
    fields the API has no business publishing, and a response model that is
    literally the domain model is how that happens without anyone noticing.
    """

    id: int | None = None
    layer: GuidanceLayer
    kind: GuidanceKind
    project_id: str | None = None
    user_id: int | None = None
    content: str | None = None
    payload: dict[str, Any] | None = None
    status: GuidanceStatus
    source: str
    supersedes_id: int | None = None
    created_by: int | None = None
    created_at: datetime.datetime | None = None

    @classmethod
    def of(cls, rule: GuidanceRule) -> GuidanceRuleResponse:
        return cls(
            id=rule.id,
            layer=rule.layer,
            kind=rule.kind,
            project_id=rule.project_id,
            user_id=rule.user_id,
            content=rule.content,
            payload=rule.payload,
            status=rule.status,
            source=rule.source,
            supersedes_id=rule.supersedes_id,
            created_by=rule.created_by,
            created_at=rule.created_at,
        )


class GuidanceRuleListResponse(BaseModel):
    rules: tuple[GuidanceRuleResponse, ...] = ()


class GuidanceRuleCreateRequest(BaseModel):
    """A new rule at a level the *route* fixes.

    ``layer``, ``project_id`` and ``user_id`` are deliberately absent. All three
    are derived from the path and the authenticated session, so no body can move
    a rule to a level or a scope its author was not authorized for. ``extra`` is
    forbidden rather than ignored for exactly that reason: a client sending
    ``project_id`` is making a scope claim, and silently dropping it would leave
    them believing it took effect.

    ``content`` carries no length bound here on purpose -- ``GuidanceRule``
    already owns that limit, and a second copy of it is a second thing to keep
    in step.
    """

    model_config = ConfigDict(extra="forbid")

    kind: GuidanceKind
    content: str | None = None
    payload: dict[str, Any] | None = None


class StylePreference(BaseModel):
    """The PERSON overlay in the shape JSON actually carries it.

    Used for both the request and the response so the two can never describe the
    same preference differently.

    ``extra`` is *ignored*, not forbidden, and that is a deliberate trade. The
    property worth pinning is that a ``user_id`` in the body has no effect on
    where the row is written; forbidding extras would make that test pass at the
    schema, before the write path it is meant to be testing ever runs. Anything
    the client sends that is not declared below -- a ``user_id``, a free-text
    ``content`` -- is dropped here and cannot reach storage.

    The fields are the real enums rather than ``StylePresentation`` itself:
    that model is ``strict=True`` and would reject ``"business"`` for
    ``StyleAudience.BUSINESS``, which is every value a JSON body can supply.
    ``style_from_payload`` does the coercion at the storage boundary and is the
    only place allowed to.
    """

    model_config = ConfigDict(extra="ignore")

    audience: StyleAudience | None = None
    tone: StyleTone | None = None
    technical_depth: TechnicalDepth | None = None
    output_organization: OutputOrganization | None = None
    formatting: list[FormattingOption] = Field(default_factory=list)
    communication: list[CommunicationOption] = Field(default_factory=list)

    def to_presentation(self) -> StylePresentation:
        """Hydrate the governed contract type, or raise ``ValidationError``.

        A body that set nothing at all fails here ("style overlay must set at
        least one presentation field"), which is the response a request carrying
        only fields this level may not express deserves.
        """

        return style_from_payload(self.model_dump(mode="json"))

    @classmethod
    def of(cls, style: StylePresentation) -> StylePreference:
        return cls.model_validate(style.model_dump(mode="json"))


class PersonPreferencesResponse(BaseModel):
    """The caller's own PERSON rows for one project.

    ``user_id`` is echoed so a client can see which identity the server actually
    wrote for -- the answer is always the authenticated session, never anything
    the request asked for.
    """

    project_id: str
    user_id: int
    style: StylePreference | None = None
    rules: tuple[GuidanceRuleResponse, ...] = ()


class WorkspaceFileResponse(BaseModel):
    """One file of the agent's worktree, content included.

    ``content`` is inline rather than behind a second request per file, and that
    is a deliberate trade rather than an oversight. These files are small by
    construction -- ``MATERIALIZE_MAX_CHARS`` caps the largest of them and the
    layer budgets cap the rest -- so the whole worktree is a handful of
    kilobytes, while a lazy-load endpoint would turn opening the view into one
    round trip per file for a user who is looking at the stack precisely because
    they want to read it.

    ``bytes`` is the encoded length, not ``len(content)``: it is the number that
    matches what is on disk, and the two differ for any non-ASCII text.
    """

    path: str
    origin: str
    always_loaded: bool
    bytes: int
    content: str


class WorkspaceResponse(BaseModel):
    """The worktree an agent turn for this project and caller would be given.

    Projected, never written: see :func:`read_project_workspace`.
    """

    project_id: str
    files: tuple[WorkspaceFileResponse, ...] = ()
    docs_mounted: bool = False
    always_loaded_tokens: int = 0


class DocumentRoutingResponse(BaseModel):
    """What one upload role becomes once it is stored.

    The upload form already asks for a role; until this endpoint existed, the
    answer to "and then what?" lived only in the agent's workspace, after the
    fact. Publishing the mapping lets the form say where a file will land before
    it is uploaded, which is the difference between a labelled dropdown and a
    routing decision the user can make on purpose.

    ``materialize_max_chars`` is repeated on every entry rather than lifted into
    an envelope so the response stays the flat array a caller iterates over. It
    is the ceiling above which ``materializes`` stops holding for a real
    document, and it is what lets a UI explain a search-only file rather than
    just showing one.
    """

    role: str
    kind: MaterialKind
    #: Workspace-relative path template, with ``<id>`` and ``<slug>`` standing in
    #: for the values a real document supplies. ``None`` when the role is never
    #: mounted at all.
    destination: str | None = None
    always_loaded: bool = False
    materializes: bool = False
    materialize_max_chars: int = MATERIALIZE_MAX_CHARS
    description: str


class GuidancePreviewResponse(BaseModel):
    """The fully rendered stack for one (project, caller).

    ``layer_tokens`` is what each level costs once rendered, against the ceiling
    in ``LAYER_TOKEN_BUDGETS``. It is the only way an operator can see *why* a
    write was refused with a 409 before making the next one.
    """

    project_id: str
    user_id: int
    rendered: str
    cache_key: str
    conflicts: tuple[str, ...] = ()
    layer_tokens: dict[str, int] = Field(default_factory=dict)
    layer_budgets: dict[str, int] = Field(default_factory=dict)


# -- authorization -----------------------------------------------------------


async def _require_project_access(user: User, db: AsyncSession, project_id: str) -> None:
    """403 unless the caller may see ``project_id``.

    ``get_user_project_ids`` returns ``None`` for an unrestricted caller and a
    list otherwise; a project id outside a non-``None`` list is a refusal, not an
    empty result. Answering "no rules" would read to the caller as "this project
    has no guidance", which is a materially wrong answer to a question about how
    the system behaves.

    Deliberately 403 rather than 404. The caller named the project id, so a 404
    conceals nothing they did not already supply, while a 403 tells an operator
    which of the two gates they failed.
    """

    scope = await get_user_project_ids(user, db)
    if scope is not None and project_id not in scope:
        raise api_error(403, "forbidden", "No access to this project")


async def _require_project_management(user: User, db: AsyncSession, project_id: str) -> None:
    """403 unless the caller may *change* this project's guidance.

    Both halves are required. ``manage_projects`` is a global permission and says
    nothing about which landscape; the access list says which landscape and
    nothing about authority. Kept inline rather than expressed as a
    ``require_permission`` dependency so the two halves of one gate read as one
    rule in one place, and so both refusals share this router's error shape.
    """

    if not has_permission(user, MANAGE_PROJECTS):
        raise api_error(403, "forbidden", f"Permission required: {MANAGE_PROJECTS}")
    await _require_project_access(user, db, project_id)


async def _scoped_rule(
    repository: GuidanceRepository,
    *,
    rule_id: int,
    layer: GuidanceLayer,
    project_id: str | None = None,
    user_id: int | None = None,
) -> GuidanceRule:
    """The rule with this id, but only if it really sits at this scope.

    The repository addresses a rule by id alone. Handing it an id straight from
    the URL would mean a manager authorized for one project could supersede or
    approve another project's rule by guessing a number, because nothing below
    this point re-checks the scope the route just authorized.

    Reads every lifecycle state: approving requires finding a PROPOSED row, and
    a caller re-issuing a DELETE against an already-superseded one deserves the
    repository's answer rather than a misleading 404.
    """

    candidates = await repository.list_rules(
        layer=layer, project_id=project_id, user_id=user_id, status=None
    )
    for rule in candidates:
        if rule.id == rule_id:
            return rule
    raise api_error(
        404,
        "guidance_rule_not_found",
        f"No {layer.value} guidance rule with id {rule_id} in this scope",
    )


def _budget_conflict(layer: GuidanceLayer, exc: GuidanceBudgetExceeded) -> HTTPException:
    """409, not 500: a full level is an expected outcome an admin can act on.

    The repository's message already names the level, the arithmetic and the
    ceiling; ``layer`` and ``ceiling`` are repeated as fields so a UI does not
    have to parse prose to render the right control.
    """

    return api_error(
        409,
        "guidance_budget_exceeded",
        str(exc),
        layer=layer.value,
        ceiling=LAYER_TOKEN_BUDGETS[layer],
    )


def _invalid_rule(exc: ValidationError) -> HTTPException:
    """422 for a rule the contract refuses to construct.

    The authority matrix, the scope shape and the payload shape all live in
    ``GuidanceRule``'s validator and are not restated here. A raw pydantic
    ``ValidationError`` raised inside a handler would otherwise surface as a 500
    -- FastAPI only maps its own ``RequestValidationError`` -- so it is caught
    and re-shaped, not re-implemented.
    """

    return api_error(422, "invalid_guidance_rule", str(exc))


async def _audit(
    db: AsyncSession,
    request: Request,
    *,
    actor: User,
    action: str,
    rule_id: int | None,
    details: dict[str, Any],
) -> None:
    """Record one guidance write. Every write on this router goes through here.

    Guidance is the durable reason an answer looked the way it did, so the trail
    of who changed it is part of the same explanation.
    """

    audit = AuditService(db)
    await audit.log(
        user_id=actor.id,
        username=actor.username,
        action=action,
        resource_type="guidance_rule",
        resource_id=str(rule_id) if rule_id is not None else None,
        details=details,
        ip_address=request.client.host if request.client else None,
    )


# -- organization level ------------------------------------------------------


@router.get("/org", response_model=GuidanceRuleListResponse)
async def list_org_guidance(
    user: CurrentUser,
    db: DBSession,
    status: GuidanceStatus = GuidanceStatus.ACTIVE,
) -> GuidanceRuleListResponse:
    """The house rules, readable by anyone they apply to.

    Not Owner-gated on purpose: every authenticated user answers under these
    rules, and guidance you are subject to but cannot read is a behaviour nobody
    can account for. Writing them is another matter entirely (see below).

    Defaults to ACTIVE because the common question is "what is in force now".
    ``?status=proposed`` surfaces learned rules awaiting review and
    ``?status=superseded`` reads the history behind a past answer.
    """

    _ = user
    rules = await GuidanceRepository(db).list_rules(layer=GuidanceLayer.ORG, status=status)
    return GuidanceRuleListResponse(rules=tuple(GuidanceRuleResponse.of(rule) for rule in rules))


@router.post("/org", response_model=GuidanceRuleResponse, status_code=201)
async def create_org_guidance(
    request: Request,
    body: GuidanceRuleCreateRequest,
    owner: OwnerUser,
    db: DBSession,
) -> GuidanceRuleResponse:
    """Add a house rule. Owner only.

    This level binds every project and every user in the deployment, so the gate
    is the deployment Owner rather than any project-scoped authority.
    """

    try:
        rule = GuidanceRule(
            layer=GuidanceLayer.ORG,
            kind=body.kind,
            content=body.content,
            payload=body.payload,
            source=_ADMIN_SOURCE,
            created_by=owner.id,
        )
    except ValidationError as exc:
        raise _invalid_rule(exc)

    repository = GuidanceRepository(db)
    try:
        stored = await repository.add_rule(rule)
    except GuidanceBudgetExceeded as exc:
        # Rolled back on every refused write, uniformly. Whether the repository
        # had already flushed anything by the time it refused is its business,
        # not a detail this route should be reading from its source.
        await db.rollback()
        raise _budget_conflict(GuidanceLayer.ORG, exc)

    await _audit(
        db,
        request,
        actor=owner,
        action="create_org_guidance_rule",
        rule_id=stored.id,
        details={"layer": stored.layer.value, "kind": stored.kind.value, "content": stored.content},
    )
    await db.commit()
    logger.info("Org guidance rule %s created by %s", stored.id, owner.username)
    return GuidanceRuleResponse.of(stored)


@router.delete("/org/{rule_id}", status_code=204)
async def retire_org_guidance(
    request: Request,
    rule_id: int,
    owner: OwnerUser,
    db: DBSession,
) -> None:
    """Retire a house rule. Owner only, and a supersede rather than a delete.

    The row keeps its text and gains status ``superseded``. An answer given last
    month is only explainable while the rule that shaped it is still readable.
    """

    repository = GuidanceRepository(db)
    # Scope-checked before the write: the repository would happily retire a
    # PERSON preference through this path if handed its id.
    await _scoped_rule(repository, rule_id=rule_id, layer=GuidanceLayer.ORG)
    try:
        await repository.supersede_rule(rule_id, None, actor_id=owner.id)
    except GuidanceRuleNotFound as exc:
        await db.rollback()
        raise api_error(404, "guidance_rule_not_found", str(exc))

    await _audit(
        db,
        request,
        actor=owner,
        action="supersede_org_guidance_rule",
        rule_id=rule_id,
        details={"layer": GuidanceLayer.ORG.value},
    )
    await db.commit()
    logger.info("Org guidance rule %s superseded by %s", rule_id, owner.username)


# -- project level -----------------------------------------------------------


@router.get("/projects/{project_id}", response_model=GuidanceRuleListResponse)
async def list_project_guidance(
    project_id: str,
    user: CurrentUser,
    db: DBSession,
    status: GuidanceStatus = GuidanceStatus.ACTIVE,
) -> GuidanceRuleListResponse:
    """This landscape's rules, readable by anyone with access to the project.

    Read access is the project access check alone: ``manage_projects`` gates
    changing how the project answers, not seeing why it answers that way.
    """

    await _require_project_access(user, db, project_id)
    rules = await GuidanceRepository(db).list_rules(
        layer=GuidanceLayer.PROJECT, project_id=project_id, status=status
    )
    return GuidanceRuleListResponse(rules=tuple(GuidanceRuleResponse.of(rule) for rule in rules))


@router.post("/projects/{project_id}", response_model=GuidanceRuleResponse, status_code=201)
async def create_project_guidance(
    request: Request,
    project_id: str,
    body: GuidanceRuleCreateRequest,
    user: CurrentUser,
    db: DBSession,
) -> GuidanceRuleResponse:
    """Add a rule for one landscape. Requires ``manage_projects`` and access."""

    await _require_project_management(user, db, project_id)
    try:
        rule = GuidanceRule(
            layer=GuidanceLayer.PROJECT,
            kind=body.kind,
            project_id=project_id,
            content=body.content,
            payload=body.payload,
            source=_ADMIN_SOURCE,
            created_by=user.id,
        )
    except ValidationError as exc:
        raise _invalid_rule(exc)

    repository = GuidanceRepository(db)
    try:
        stored = await repository.add_rule(rule)
    except GuidanceBudgetExceeded as exc:
        await db.rollback()
        raise _budget_conflict(GuidanceLayer.PROJECT, exc)

    await _audit(
        db,
        request,
        actor=user,
        action="create_project_guidance_rule",
        rule_id=stored.id,
        details={
            "layer": stored.layer.value,
            "kind": stored.kind.value,
            "project_id": project_id,
            "content": stored.content,
        },
    )
    await db.commit()
    logger.info("Project guidance rule %s created for %s by %s", stored.id, project_id, user.username)
    return GuidanceRuleResponse.of(stored)


@router.delete("/projects/{project_id}/rules/{rule_id}", status_code=204)
async def retire_project_guidance(
    request: Request,
    project_id: str,
    rule_id: int,
    user: CurrentUser,
    db: DBSession,
) -> None:
    """Retire a rule for one landscape. Supersede, never hard delete."""

    await _require_project_management(user, db, project_id)
    repository = GuidanceRepository(db)
    # The path authorized *this* project; the scoped read is what stops the id
    # from belonging to a different one.
    await _scoped_rule(
        repository, rule_id=rule_id, layer=GuidanceLayer.PROJECT, project_id=project_id
    )
    try:
        await repository.supersede_rule(rule_id, None, actor_id=user.id)
    except GuidanceRuleNotFound as exc:
        await db.rollback()
        raise api_error(404, "guidance_rule_not_found", str(exc))

    await _audit(
        db,
        request,
        actor=user,
        action="supersede_project_guidance_rule",
        rule_id=rule_id,
        details={"layer": GuidanceLayer.PROJECT.value, "project_id": project_id},
    )
    await db.commit()
    logger.info("Project guidance rule %s superseded for %s by %s", rule_id, project_id, user.username)


@router.post(
    "/projects/{project_id}/rules/{rule_id}/approve", response_model=GuidanceRuleResponse
)
async def approve_project_guidance(
    request: Request,
    project_id: str,
    rule_id: int,
    user: CurrentUser,
    db: DBSession,
) -> GuidanceRuleResponse:
    """Promote a PROPOSED learned rule to ACTIVE.

    A learned PROJECT rule waits here because one user's correction must not
    silently rewrite how the project answers for everyone. Approval is also the
    moment the rule starts costing tokens on every turn, which is why the
    repository re-runs the budget check and this route can still answer 409.
    """

    await _require_project_management(user, db, project_id)
    repository = GuidanceRepository(db)
    await _scoped_rule(
        repository, rule_id=rule_id, layer=GuidanceLayer.PROJECT, project_id=project_id
    )
    try:
        approved = await repository.approve_rule(rule_id, actor_id=user.id)
    except GuidanceBudgetExceeded as exc:
        await db.rollback()
        raise _budget_conflict(GuidanceLayer.PROJECT, exc)
    except GuidanceRuleNotFound as exc:
        await db.rollback()
        raise api_error(404, "guidance_rule_not_found", str(exc))

    await _audit(
        db,
        request,
        actor=user,
        action="approve_project_guidance_rule",
        rule_id=rule_id,
        details={
            "layer": GuidanceLayer.PROJECT.value,
            "project_id": project_id,
            "status": approved.status.value,
        },
    )
    await db.commit()
    logger.info("Project guidance rule %s approved for %s by %s", rule_id, project_id, user.username)
    return GuidanceRuleResponse.of(approved)


# -- person level ------------------------------------------------------------
#
# SECURITY: both handlers below take the identity from ``user.id`` on the
# authenticated session. Neither accepts a user id from the path, the query
# string or the body -- doing so would let one user read or rewrite another's
# preferences with nothing but an integer. ``StylePreference`` drops any
# undeclared field for the same reason.


@router.get("/projects/{project_id}/preferences", response_model=PersonPreferencesResponse)
async def get_person_preferences(
    project_id: str,
    user: CurrentUser,
    db: DBSession,
) -> PersonPreferencesResponse:
    """The calling user's own preferences for this project.

    Project access is still required: a preference is scoped to a landscape, and
    reading one for a project you cannot see would confirm that the project
    exists and that you once had an opinion about it.
    """

    await _require_project_access(user, db, project_id)
    rules = await GuidanceRepository(db).list_rules(
        layer=GuidanceLayer.PERSON,
        project_id=project_id,
        user_id=user.id,  # authenticated session only -- never a supplied id
        status=GuidanceStatus.ACTIVE,
    )
    # Last wins: rules come back in insertion order and the newest overlay is
    # the one in force. Normal operation keeps exactly one row here.
    style: StylePresentation | None = None
    for rule in rules:
        parsed = rule.style
        if parsed is not None:
            style = parsed
    return PersonPreferencesResponse(
        project_id=project_id,
        user_id=user.id,
        style=StylePreference.of(style) if style is not None else None,
        rules=tuple(GuidanceRuleResponse.of(rule) for rule in rules),
    )


@router.put("/projects/{project_id}/preferences", response_model=PersonPreferencesResponse)
async def set_person_preferences(
    request: Request,
    project_id: str,
    body: StylePreference,
    user: CurrentUser,
    db: DBSession,
) -> PersonPreferencesResponse:
    """Replace the calling user's style overlay for this project.

    The replacement supersedes rather than overwrites, so the preference that
    shaped an old answer stays readable. Any extra ACTIVE overlay found at this
    scope is retired in the same write: the level is meant to hold one, and
    duplicates would both double-charge the 150-token PERSON ceiling and merge
    in an order nobody chose.
    """

    await _require_project_access(user, db, project_id)
    try:
        style = body.to_presentation()
    except ValidationError as exc:
        raise _invalid_rule(exc)

    replacement = GuidanceRule(
        layer=GuidanceLayer.PERSON,
        kind=GuidanceKind.STYLE,
        project_id=project_id,
        user_id=user.id,  # authenticated session only -- never a supplied id
        payload=style_to_payload(style),
        source=_ADMIN_SOURCE,
        created_by=user.id,
    )

    repository = GuidanceRepository(db)
    existing_ids: list[int] = [
        rule.id
        for rule in await repository.list_rules(
            layer=GuidanceLayer.PERSON,
            project_id=project_id,
            user_id=user.id,
            status=GuidanceStatus.ACTIVE,
        )
        if rule.kind is GuidanceKind.STYLE and rule.id is not None
    ]

    stored: GuidanceRule | None
    try:
        for stale_id in existing_ids[:-1]:
            await repository.supersede_rule(stale_id, None, actor_id=user.id)
        if existing_ids:
            stored = await repository.supersede_rule(
                existing_ids[-1], replacement, actor_id=user.id
            )
        else:
            stored = await repository.add_rule(replacement)
    except GuidanceBudgetExceeded as exc:
        # Rolled back explicitly: ``supersede_rule`` flushes the retirement
        # before it writes the successor, so committing after a refused write
        # would leave the caller with no preference at all instead of the old
        # one they still expect to be in force.
        await db.rollback()
        raise _budget_conflict(GuidanceLayer.PERSON, exc)
    except GuidanceRuleNotFound as exc:
        await db.rollback()
        raise api_error(404, "guidance_rule_not_found", str(exc))

    await _audit(
        db,
        request,
        actor=user,
        action="update_person_guidance_preferences",
        rule_id=stored.id if stored is not None else None,
        details={
            "layer": GuidanceLayer.PERSON.value,
            "project_id": project_id,
            "superseded_rule_ids": existing_ids,
            "style": style_to_payload(style),
        },
    )
    await db.commit()
    logger.info("Person guidance preferences updated for %s by %s", project_id, user.username)

    return PersonPreferencesResponse(
        project_id=project_id,
        user_id=user.id,
        style=StylePreference.of(style),
        rules=(GuidanceRuleResponse.of(stored),) if stored is not None else (),
    )


# -- preview -----------------------------------------------------------------


@router.get("/projects/{project_id}/preview", response_model=GuidancePreviewResponse)
async def preview_project_guidance(
    project_id: str,
    user: CurrentUser,
    db: DBSession,
) -> GuidancePreviewResponse:
    """The fully rendered stack as the calling user would receive it.

    Rendered for the caller and nobody else: a preview that accepted a target
    user id would be a read of that user's preferences wearing a different name.
    Someone auditing how the system answers for another person should read the
    org and project levels, which are the levels that are shared.

    ``style_policy`` is left unset because nothing persists an
    ``OrganizationGovernancePolicy`` yet; the compiler treats that as "no
    whitelist configured" and permits every field.
    """

    await _require_project_access(user, db, project_id)
    resolved = await compile_guidance(db, project_id=project_id, user_id=user.id)
    costs = layer_token_costs(resolved)
    return GuidancePreviewResponse(
        project_id=project_id,
        user_id=user.id,
        rendered=render_guidance(resolved),
        cache_key=resolved.cache_key,
        conflicts=resolved.conflicts,
        layer_tokens={layer.value: cost for layer, cost in costs.items()},
        layer_budgets={layer.value: budget for layer, budget in LAYER_TOKEN_BUDGETS.items()},
    )


# -- the agent's worktree ----------------------------------------------------


@router.get("/projects/{project_id}/workspace", response_model=WorkspaceResponse)
async def read_project_workspace(
    project_id: str,
    user: CurrentUser,
    db: DBSession,
) -> WorkspaceResponse:
    """The real worktree a turn for this project and this caller would be given.

    Read-only in the strongest sense: nothing here touches the filesystem except
    the ``is_dir()`` probe behind ``labs_docs_mounted``. The layout comes from
    ``project_workspace_files``, which is pure, so a user can look at the
    workspace of a conversation that has not happened yet -- and, more to the
    point, so what they are shown cannot be a second description of the tree that
    drifts from the one the agent actually gets.

    The inputs are the ones a turn compiles, in the same order and by the same
    functions (``compile_guidance``, ``compile_project_material``,
    ``load_material_texts``; see ``src/labs/runtime.py``). Anything less faithful
    would make this a mock-up.

    ``opencode.json`` is absent, and that is the security property of the
    endpoint rather than an omission: it carries the MCP bearer token. The
    projection is where that exclusion is enforced, so no route can reintroduce
    it by accident.

    Authorization is project access alone, matching ``GET /projects/{id}``:
    reading how the assistant is configured for a landscape you work in is not a
    management action, and a stack you cannot inspect is behaviour nobody can
    account for.
    """

    await _require_project_access(user, db, project_id)

    guidance = await compile_guidance(db, project_id=project_id, user_id=user.id)

    # ``compile_project_material`` documents what this list has to be, and fails
    # closed on anything less: the caller's role names *plus* the project ids
    # they may read -- the same union the Qdrant search filter composes
    # internally. ``src/labs/runtime.py`` builds it as
    # ``[*user_groups, *(project_scope or ())]``.
    #
    # The one difference here is deliberate. ``get_user_project_ids`` answers
    # ``None`` for an unrestricted caller, and ``or ()`` would turn "may read
    # every project" into "may read none of them" -- an empty manifest for every
    # superadmin. ``_require_project_access`` above has already decided this
    # caller may read *this* project, so that is the id added, and no wider
    # claim is made than the one the gate just granted.
    scope = await get_user_project_ids(user, db)
    user_groups = [*get_user_groups(user), *(scope if scope is not None else [project_id])]

    material = await compile_project_material(
        db, project_id=project_id, user_groups=user_groups
    )
    material_texts = await load_material_texts(
        db, [entry.document_id for entry in material.mountable]
    )
    docs_mounted = await labs_docs_mounted(project_id)

    files = project_workspace_files(
        project_id=project_id,
        guidance=guidance,
        material=material,
        material_texts=material_texts,
        docs_mounted=docs_mounted,
    )

    return WorkspaceResponse(
        project_id=project_id,
        files=tuple(
            WorkspaceFileResponse(
                path=file.path,
                origin=file.origin,
                always_loaded=file.always_loaded,
                bytes=len(file.content.encode("utf-8")),
                content=file.content,
            )
            for file in files
        ),
        docs_mounted=docs_mounted,
        always_loaded_tokens=always_loaded_tokens(files),
    )


# -- where an upload lands ---------------------------------------------------

#: Placeholders in a published destination. A real path substitutes the
#: document's id and the slug derived from its filename; the template is what
#: the upload form can show before either exists.
_DESTINATION_ID_PLACEHOLDER: Final = "<id>"
_DESTINATION_SLUG_PLACEHOLDER: Final = "<slug>"

#: A document that exists only to be handed to ``workspace_relpath``. Asking that
#: function where a role lands is the point: the directory names live in
#: ``src/harness/guidance/material.py`` and a second copy of them here would be a
#: routing table that agrees with the real one only until someone renames one.
_PROBE_DOCUMENT_ID: Final = 0
_PROBE_FILENAME: Final = "routing-probe.md"

#: The size ``should_materialize`` is asked about. One character: the question
#: this endpoint answers is "does this role mount at all", and the smallest
#: non-empty document is the one that isolates the role gate from the size gate.
_MATERIALIZE_PROBE_CHARS: Final = 1

#: Mounted material is never inlined into ``AGENTS.md``; only its one-line
#: manifest entry is. That asymmetry is the design -- it is how a 30k-character
#: policy stays reachable for the price of one sentence per turn -- and it holds
#: for every role, so it is stated once here rather than derived per role.
_MOUNTED_MATERIAL_IS_ALWAYS_LOADED: Final = False

#: What a document of each kind is for, in the words a person choosing an upload
#: role needs. Keyed on the kind rather than the role so a role added later
#: inherits a description from whatever it is mapped to -- and
#: ``src/harness/guidance/material.py`` asserts at import that every role is
#: mapped to something.
_KIND_DESCRIPTION: Final[dict[MaterialKind, str]] = {
    MaterialKind.EVIDENCE: "Evidence. Searched by similarity, never mounted.",
    MaterialKind.REFERENCE: "Background the assistant reads when a question touches it.",
    MaterialKind.METHOD: "A method the assistant follows when doing that kind of work.",
    MaterialKind.CONSTRAINT: "A requirement every answer must respect, whatever was asked.",
}


def _destination_template(role: DocumentRole, kind: MaterialKind) -> str | None:
    """Where a document of this role would be written, as a path template.

    ``None`` means the role is never mounted at all, which is a different claim
    from "mounted somewhere the UI cannot name" and has to stay distinguishable.

    The probe entry is thrown away immediately; its only job is to make
    ``workspace_relpath`` answer. Its id and slug are then swapped for
    placeholders in one replacement of a single unique substring, rather than by
    matching ``0`` or ``routing-probe`` anywhere they appear.
    """

    if not should_materialize(role, _MATERIALIZE_PROBE_CHARS):
        return None
    probe = MaterialEntry(
        document_id=_PROBE_DOCUMENT_ID, filename=_PROBE_FILENAME, role=role, kind=kind
    )
    probe_stem = f"{_PROBE_DOCUMENT_ID}-{material_slug(_PROBE_FILENAME)}"
    template = workspace_relpath(probe).replace(
        probe_stem, f"{_DESTINATION_ID_PLACEHOLDER}-{_DESTINATION_SLUG_PLACEHOLDER}"
    )
    return f"{MATERIAL_PATH_PREFIX}{template}"


@router.get("/document-routing", response_model=list[DocumentRoutingResponse])
async def list_document_routing(user: CurrentUser) -> list[DocumentRoutingResponse]:
    """What each upload role becomes, so the form can say so before the upload.

    Every field is derived -- the roles from ``DOCUMENT_ROLES``, the kind from
    ``material_kind``, the destination from ``workspace_relpath``,
    ``materializes`` from ``should_materialize``. A role added to
    ``src/document_roles.py`` appears here with no edit to this module, which is
    the only way a routing table stays true: one that has to be maintained
    alongside the routing it describes is a table that will eventually describe
    something else.

    Any authenticated user may read it. It is a statement about how the product
    handles uploads, not about any project's contents, so there is nothing here
    to scope.
    """

    _ = user
    entries: list[DocumentRoutingResponse] = []
    for role in DOCUMENT_ROLES:
        kind = material_kind(role)
        destination = _destination_template(role, kind)
        entries.append(
            DocumentRoutingResponse(
                role=role,
                kind=kind,
                destination=destination,
                always_loaded=_MOUNTED_MATERIAL_IS_ALWAYS_LOADED,
                materializes=destination is not None,
                description=_KIND_DESCRIPTION[kind],
            )
        )
    return entries
