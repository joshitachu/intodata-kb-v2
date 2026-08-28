"""Resolve a user's onboarding persona from their persisted authority.

The walkthrough, the first-run wizard and the product guide all need to answer
"who is this person, and what should they be taught first?". Deriving that
independently in each place is how the old flow ended up branching on
``is_superadmin`` alone, so an Admin who was not the deployment Owner received
the same one-card welcome as a read-only member.

Persona is a *teaching* concept, deliberately coarser than the RBAC model. It
answers "which walkthrough" — never "may this user do X". Authorization stays
with :mod:`src.rbac.permissions`; a persona must never be used as a permission
check, because a persona is stable while permissions can be revoked.
"""

from __future__ import annotations

from typing import Literal

from src.db.models import User
from src.rbac.permissions import (
    ASSIGN_PROJECTS,
    CREATE_PROJECTS,
    MANAGE_PROJECTS,
    MANAGE_USERS,
    has_permission,
)

Persona = Literal["owner", "admin", "project_owner", "developer"]

# Ordered most to least authority. The first match wins, so a user who is both
# an Owner and a project owner is taught as an Owner.
PERSONA_ORDER: tuple[Persona, ...] = ("owner", "admin", "project_owner", "developer")


def resolve_persona(user: User, *, owns_projects: bool = False) -> Persona:
    """Return the walkthrough persona for ``user``.

    ``owns_projects`` is passed in rather than queried here so this function
    stays synchronous and side-effect free; the caller already knows whether it
    needs the extra project lookup for its own purposes.
    """
    if user.is_superadmin:
        return "owner"
    # Member administration is the dividing line between "runs the workspace"
    # and "uses the workspace". Checked as a permission, not a role name, so a
    # deployment that renamed its admin role still resolves correctly.
    if has_permission(user, MANAGE_USERS) or has_permission(user, ASSIGN_PROJECTS):
        return "admin"
    if owns_projects or has_permission(user, MANAGE_PROJECTS) or has_permission(
        user, CREATE_PROJECTS
    ):
        return "project_owner"
    return "developer"
