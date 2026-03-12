from unittest.mock import MagicMock

from src.rbac.permissions import (
    ALL_PERMISSIONS,
    can_view_all_projects,
    get_user_groups,
    get_user_permissions,
    has_permission,
)
from src.rbac.auth import resolve_user_groups


def _make_user_with_roles(role_names: list[str], permissions: list[str] | None = None,
                           is_superadmin: bool = False):
    """Create a mock User with roles and optional permissions."""
    user = MagicMock()
    user.is_superadmin = is_superadmin
    roles = []
    for name in role_names:
        role = MagicMock()
        role.name = name
        if permissions is not None:
            role_perms = []
            for p in permissions:
                perm = MagicMock()
                perm.codename = p
                role_perms.append(perm)
            role.permissions = role_perms
        else:
            role.permissions = []
        roles.append(role)
    user.roles = roles
    return user


def test_get_user_groups_empty():
    user = _make_user_with_roles([])
    assert get_user_groups(user) == []


def test_get_user_groups_single():
    user = _make_user_with_roles(["engineering"])
    assert get_user_groups(user) == ["engineering"]


def test_get_user_groups_multiple():
    user = _make_user_with_roles(["engineering", "management", "hr"])
    groups = get_user_groups(user)
    assert groups == ["engineering", "management", "hr"]


def test_resolve_user_groups_delegates():
    user = _make_user_with_roles(["sales"])
    assert resolve_user_groups(user) == ["sales"]


# --- has_permission tests ---

def test_has_permission_superadmin():
    """Superadmin always has any permission."""
    user = _make_user_with_roles(["admin"], is_superadmin=True)
    assert has_permission(user, "manage_users") is True
    assert has_permission(user, "nonexistent_permission") is True


def test_has_permission_via_role():
    """User with permission in their role returns True."""
    user = _make_user_with_roles(
        ["developer"], permissions=["query_rag", "view_assigned_projects"]
    )
    assert has_permission(user, "query_rag") is True


def test_has_permission_missing():
    """User without the permission returns False."""
    user = _make_user_with_roles(
        ["developer"], permissions=["query_rag"]
    )
    assert has_permission(user, "manage_users") is False


# --- get_user_permissions tests ---

def test_get_user_permissions_superadmin():
    """Superadmin gets all 9 permissions."""
    user = _make_user_with_roles(["admin"], is_superadmin=True)
    perms = get_user_permissions(user)
    assert perms == ALL_PERMISSIONS


def test_get_user_permissions_developer():
    """Non-superadmin only gets assigned permissions."""
    user = _make_user_with_roles(
        ["developer"], permissions=["query_rag", "view_assigned_projects"]
    )
    perms = get_user_permissions(user)
    assert perms == {"query_rag", "view_assigned_projects"}


# --- can_view_all_projects tests ---

def test_can_view_all_projects_admin():
    """Superadmin can view all projects."""
    user = _make_user_with_roles(["admin"], is_superadmin=True)
    assert can_view_all_projects(user) is True
