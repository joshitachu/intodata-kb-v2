from unittest.mock import MagicMock

from src.rbac.permissions import get_user_groups
from src.rbac.auth import resolve_user_groups


def _make_user_with_roles(role_names: list[str]):
    """Create a mock User with roles."""
    user = MagicMock()
    roles = []
    for name in role_names:
        role = MagicMock()
        role.name = name
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
