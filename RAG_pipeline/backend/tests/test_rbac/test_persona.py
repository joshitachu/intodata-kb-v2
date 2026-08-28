"""Walkthrough persona resolution (src/rbac/persona.py).

The old first-run flow branched on ``is_superadmin`` alone, so an Admin who was
not the deployment Owner was taught as if they were a read-only member. These
tests pin the distinction, and pin that persona is derived from permissions
rather than role names so a renamed admin role still resolves.
"""

from unittest.mock import MagicMock

from src.db.models import Permission, Role, User
from src.rbac.persona import resolve_persona


def _user(*, superadmin: bool = False, role_name: str = "member", permissions=()):
    perms = []
    for codename in permissions:
        perm = MagicMock(spec=Permission)
        perm.codename = codename
        perms.append(perm)

    role = MagicMock(spec=Role)
    role.name = role_name
    role.permissions = perms

    user = MagicMock(spec=User)
    user.is_superadmin = superadmin
    user.roles = [role]
    return user


def test_owner_is_resolved_from_the_authority_flag():
    assert resolve_persona(_user(superadmin=True)) == "owner"


def test_member_admin_is_not_treated_as_a_plain_developer():
    """The regression that made an Admin see the one-card member welcome."""
    admin = _user(role_name="admin", permissions=["manage_users"])
    assert resolve_persona(admin) == "admin"


def test_admin_persona_survives_a_renamed_role():
    """Persona keys off permissions, so custom role naming still works."""
    admin = _user(role_name="workspace-operators", permissions=["assign_projects"])
    assert resolve_persona(admin) == "admin"


def test_project_owner_is_resolved_from_ownership():
    member = _user(role_name="developer", permissions=["query_rag"])
    assert resolve_persona(member, owns_projects=True) == "project_owner"


def test_project_creator_is_a_project_owner_before_owning_anything():
    """Someone who can create projects needs the project-owner walkthrough."""
    creator = _user(role_name="developer", permissions=["create_projects"])
    assert resolve_persona(creator) == "project_owner"


def test_plain_member_is_a_developer():
    member = _user(role_name="developer", permissions=["query_rag"])
    assert resolve_persona(member) == "developer"


def test_user_with_no_roles_still_resolves():
    """A member mid-provisioning must not break the first screen."""
    user = MagicMock(spec=User)
    user.is_superadmin = False
    user.roles = []
    assert resolve_persona(user) == "developer"


def test_higher_authority_wins_over_project_ownership():
    """An Owner who also owns projects is taught as an Owner."""
    owner = _user(superadmin=True)
    assert resolve_persona(owner, owns_projects=True) == "owner"
