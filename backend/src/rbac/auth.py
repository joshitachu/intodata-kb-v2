from __future__ import annotations

from src.db.models import User
from src.rbac.permissions import get_user_groups


def resolve_user_groups(user: User) -> list[str]:
    """Extract the user's role-based group names for Qdrant RBAC filtering."""
    return get_user_groups(user)
