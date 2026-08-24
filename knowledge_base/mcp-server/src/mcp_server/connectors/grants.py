"""Opaque, in-process capability grants for connector evidence runs.

The opaque value is authentication material for the Nexus MCP facade, not a
provider credential.  It is deliberately kept out of contracts, prompts,
events, and the staging workspace.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import secrets
from typing import Mapping


class GrantDenied(PermissionError):
    """A capability grant is absent, invalid, expired, or out of scope."""


@dataclass(frozen=True)
class ConnectorBudget:
    """Hard ceilings for one run, enforced by :class:`CapabilityGrantStore`."""

    max_calls: int = 12
    max_wall_seconds: int = 90
    max_items: int = 30
    max_response_bytes: int = 128 * 1024
    max_evidence_bytes: int = 512 * 1024

    def __post_init__(self) -> None:
        if any(value < 0 for value in self.__dict__.values()):
            raise ValueError("connector budgets must be non-negative")


@dataclass(frozen=True)
class CapabilityGrant:
    """Immutable authority snapshot for the external-evidence Gather step."""

    grant_id: str
    run_id: int | str
    project_id: str
    user_id: str | None
    snapshot_id: str | None
    connector_instance_ids: frozenset[str]
    source_ids: frozenset[str]
    capability_ids: frozenset[str]
    source_capabilities: Mapping[str, frozenset[str]]
    issued_at: datetime
    expires_at: datetime
    nonce: str
    budget: ConnectorBudget = field(default_factory=ConnectorBudget)
    phase: str = "gather"

    @classmethod
    def new(
        cls,
        *,
        run_id: int | str,
        project_id: str,
        user_id: str | None,
        connector_instance_ids: set[str] | frozenset[str],
        source_capabilities: Mapping[str, set[str] | frozenset[str]],
        capability_ids: set[str] | frozenset[str],
        snapshot_id: str | None = None,
        budget: ConnectorBudget | None = None,
        ttl_seconds: int = 600,
    ) -> "CapabilityGrant":
        if ttl_seconds <= 0:
            raise ValueError("grant TTL must be positive")
        now = datetime.now(UTC)
        normalized_sources = {
            str(source_id): frozenset(str(capability) for capability in capabilities)
            for source_id, capabilities in source_capabilities.items()
        }
        allowed = frozenset(str(item) for item in capability_ids)
        if not allowed or not normalized_sources:
            raise ValueError("a grant requires at least one capability and source")
        if not all(caps <= allowed for caps in normalized_sources.values()):
            raise ValueError("source capability cannot exceed grant capability scope")
        return cls(
            grant_id=f"gr_{secrets.token_urlsafe(18)}",
            run_id=run_id,
            project_id=project_id,
            user_id=user_id,
            snapshot_id=snapshot_id,
            connector_instance_ids=frozenset(str(item) for item in connector_instance_ids),
            source_ids=frozenset(normalized_sources),
            capability_ids=allowed,
            source_capabilities=normalized_sources,
            issued_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
            nonce=secrets.token_urlsafe(24),
            budget=budget or ConnectorBudget(),
        )


@dataclass
class BudgetUsage:
    calls: int = 0
    wall_seconds: float = 0.0
    items: int = 0
    response_bytes: int = 0
    evidence_bytes: int = 0


@dataclass
class _StoredGrant:
    grant: CapabilityGrant
    facade_nonce: str | None = None
    revoked: bool = False
    usage: BudgetUsage = field(default_factory=BudgetUsage)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class CapabilityGrantStore:
    """One-time facade binding, expiry/revocation, and atomic budget accounting."""

    def __init__(self) -> None:
        self._grants: dict[str, _StoredGrant] = {}
        self._tokens: dict[str, str] = {}

    def issue(self, grant: CapabilityGrant) -> str:
        """Store a grant and return its unguessable facade token exactly once."""

        if grant.grant_id in self._grants:
            raise ValueError("grant ID is already issued")
        token = secrets.token_urlsafe(48)
        self._grants[grant.grant_id] = _StoredGrant(grant=grant)
        self._tokens[token] = grant.grant_id
        return token

    def revoke(self, grant_id: str) -> None:
        stored = self._grants.get(grant_id)
        if stored is not None:
            stored.revoked = True

    def get(self, token: str) -> CapabilityGrant:
        stored = self._stored(token)
        self._assert_active(stored)
        return stored.grant

    async def authorize(
        self,
        token: str,
        *,
        facade_nonce: str,
        run_id: int | str,
        project_id: str,
        phase: str,
        capability_id: str,
        source_id: str,
    ) -> CapabilityGrant:
        """Fail closed before any provider code is reached.

        The first facade nonce binds this opaque token to one launched facade.
        A copied token cannot be replayed from a second facade or another phase.
        """

        stored = self._stored(token)
        async with stored.lock:
            self._assert_active(stored)
            grant = stored.grant
            if stored.facade_nonce is None:
                stored.facade_nonce = facade_nonce
            elif not secrets.compare_digest(stored.facade_nonce, facade_nonce):
                raise GrantDenied("grant nonce replayed by another facade")
            if grant.run_id != run_id or grant.project_id != project_id:
                raise GrantDenied("grant does not belong to this run/project")
            if grant.phase != "gather" or phase != "gather":
                raise GrantDenied("connector capabilities are Gather-only")
            if capability_id not in grant.capability_ids:
                raise GrantDenied("capability is not granted")
            if source_id not in grant.source_ids or capability_id not in grant.source_capabilities[source_id]:
                raise GrantDenied("source/capability pair is not granted")
            if stored.usage.calls >= grant.budget.max_calls:
                raise GrantDenied("connector call budget exhausted")
            stored.usage.calls += 1
            return grant

    async def settle(
        self,
        token: str,
        *,
        elapsed_seconds: float,
        item_count: int,
        response_bytes: int,
        evidence_bytes: int,
    ) -> None:
        """Atomically account for a completed call and revoke on any overrun."""

        stored = self._stored(token)
        async with stored.lock:
            grant = stored.grant
            usage = stored.usage
            usage.wall_seconds += max(0.0, elapsed_seconds)
            usage.items += max(0, item_count)
            usage.response_bytes += max(0, response_bytes)
            usage.evidence_bytes += max(0, evidence_bytes)
            over = (
                usage.wall_seconds > grant.budget.max_wall_seconds
                or usage.items > grant.budget.max_items
                or response_bytes > grant.budget.max_response_bytes
                or usage.evidence_bytes > grant.budget.max_evidence_bytes
            )
            if over:
                stored.revoked = True
                raise GrantDenied("connector budget exhausted")

    def usage(self, token: str) -> BudgetUsage:
        stored = self._stored(token)
        return BudgetUsage(**stored.usage.__dict__)

    def _stored(self, token: str) -> _StoredGrant:
        grant_id = self._tokens.get(token)
        stored = self._grants.get(grant_id or "")
        if stored is None:
            raise GrantDenied("unknown capability grant")
        return stored

    @staticmethod
    def _assert_active(stored: _StoredGrant) -> None:
        if stored.revoked:
            raise GrantDenied("capability grant is revoked")
        if datetime.now(UTC) >= stored.grant.expires_at:
            stored.revoked = True
            raise GrantDenied("capability grant expired")
