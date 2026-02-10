from __future__ import annotations

from src.db.models import User


def get_user_groups(user: User) -> list[str]:
    """Return the list of group names a user belongs to (from their roles).

    These map directly to the ``allowed_groups`` payload field stored in Qdrant
    vectors, enabling row-level access control on search results.
    """
    return [role.name for role in user.roles]
