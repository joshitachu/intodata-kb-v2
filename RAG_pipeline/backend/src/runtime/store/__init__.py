"""Held runtime-data store (Model 2 skeleton).

The repository is the read/write boundary for persisted runtime data. Reads are
project-scoped using the existing get_user_project_ids contract; the in-process
write-port is the seam a future sync worker uses to populate the store.
"""

from __future__ import annotations

# Allowed-project scope, matching get_user_project_ids semantics:
#   None  -> unrestricted (superadmin / view-all)
#   []    -> no access (no rows; point lookups treated as not found)
#   [ids] -> restrict to those project_ids
ProjectScope = list[str] | None


class RuntimeRecordNotFoundError(Exception):
    """Raised by repository point lookups when no in-scope record exists. A later
    slice maps this to RuntimeProviderError at the provider boundary."""


class RuntimeDuplicateSourceError(Exception):
    """Raised when a physical runtime source has already been registered."""


from src.runtime.store.repository import RuntimeStoreRepository  # noqa: E402

__all__ = ["ProjectScope", "RuntimeDuplicateSourceError", "RuntimeRecordNotFoundError", "RuntimeStoreRepository"]
