from __future__ import annotations

from typing import Literal, TypeAlias, cast

DocumentRole: TypeAlias = Literal[
    "source",
    "reference",
    "guidance",
    "standard",
    "template",
    "policy",
]

DEFAULT_DOCUMENT_ROLE: DocumentRole = "source"
DOCUMENT_ROLES: tuple[DocumentRole, ...] = (
    "source",
    "reference",
    "guidance",
    "standard",
    "template",
    "policy",
)
AUXILIARY_DOCUMENT_ROLES: tuple[DocumentRole, ...] = (
    "reference",
    "guidance",
    "standard",
    "policy",
)
TEMPLATE_DOCUMENT_ROLES: tuple[DocumentRole, ...] = ("template",)

_TEMPLATE_INTENT_TERMS = (
    "template",
    "format",
    "structure",
    "outline",
    "report",
    "deliverable",
    "write-up",
    "writeup",
)


def normalize_document_role(value: str | None) -> DocumentRole:
    """Return a valid document role, defaulting absent/blank values to source."""
    role = (value or DEFAULT_DOCUMENT_ROLE).strip().lower()
    if role not in DOCUMENT_ROLES:
        allowed = ", ".join(DOCUMENT_ROLES)
        raise ValueError(f"Invalid document_role: {value!r}. Allowed values: {allowed}")
    return cast(DocumentRole, role)


def has_template_intent(query: str) -> bool:
    """Return True when template documents may be used as structure context."""
    lowered = query.lower()
    return any(term in lowered for term in _TEMPLATE_INTENT_TERMS)
