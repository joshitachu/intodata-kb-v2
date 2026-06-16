"""Typed request scope coupling Qdrant and runtime-store access (EP-005 slice 1).

A conversation turn carries one authenticated project allow-list, but two
subsystems read it under different names: ``project_ids`` for Qdrant/document
retrieval and ``project_scope`` for the runtime Postgres store. Today both names
are passed separately and rely on convention to stay equal. ``RequestScope``
wraps the single allow-list and exposes both names, so the RBAC coupling is
explicit and type-checked — a future change cannot send a different scope to the
two data sources without going through this object.

This is a coupling guard, not a new authorization model: the value and its
semantics are identical to ``get_user_project_ids()``.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.runtime.store import ProjectScope


@dataclass(frozen=True)
class RequestScope:
    """Single authenticated project allow-list, exposed under both subsystem names.

    Semantics follow ``get_user_project_ids()`` exactly:

    - ``None``  -> unrestricted (superadmin / view-all-projects)
    - ``()``    -> no project access
    - ``(ids)`` -> restricted to those project ids
    """

    allowed_project_ids: tuple[str, ...] | None

    @classmethod
    def from_project_ids(cls, project_ids: list[str] | None) -> "RequestScope":
        """Build a scope from the authenticated principal's project id list."""
        if project_ids is None:
            return cls(allowed_project_ids=None)
        return cls(allowed_project_ids=tuple(project_ids))

    @property
    def project_ids(self) -> list[str] | None:
        """Scope for Qdrant/document retrieval (same value as ``project_scope``)."""
        if self.allowed_project_ids is None:
            return None
        return list(self.allowed_project_ids)

    @property
    def project_scope(self) -> ProjectScope:
        """Scope for runtime store/tool execution (same value as ``project_ids``)."""
        return self.project_ids
