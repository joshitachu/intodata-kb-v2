"""Generation quota and run admission for this service (not mirrored).

``core.py`` decides *whether* a capability is allowed given a usage figure; this
module owns the figure. It is the single definition of "a generation that
counts" (plan Decision 6), used by both the run-creation chokepoint
(``ProjectRegistry.create_generate_run``) and the status endpoint, so the number
a user sees is the number that refuses them.

A counted generation is a TOP-LEVEL ``generate_runs`` row -- no
``partition_name``, not an automatic gate retry -- whose ``started_at`` lies in
``[period_start, period_end)`` and whose status is running / completed /
completed_warnings. Failed runs do not count; reruns do (they spend tokens).
Composite children and gate retries are CONTINUATIONS of a top-level run that
was already admitted: they still need a valid licence, but they are neither
quota-checked nor counted, so one user action costs one generation.
"""

from __future__ import annotations

import enum
import logging

from sqlalchemy import and_, func, not_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from mcp_server.db.models import GenerateRun

from .core import (
    GENERATIONS_LIMIT_KEY,
    Capability,
    LicenceClaims,
    LicenceRefused,
    LicenceState,
    Refusal,
)
from .runtime import get_policy

logger = logging.getLogger(__name__)

#: Statuses that consume an allowance. ``failed`` (a system fault) is
#: deliberately absent. ``cancelled`` counts: only a user can cancel a running
#: generation (``POST /mcp/reset-status``), it has already spent tokens, and the
#: next run resumes from its staging -- leaving it out let cancel-and-resume
#: spend without limit.
COUNTED_STATUSES: tuple[str, ...] = ("running", "completed", "completed_warnings", "cancelled")

#: ``triggered_by`` suffix of the automatic correctness-gate retry in
#: ``services/git_reingestion.py``. The retry is the same user-visible update
#: as the run it retries, so it is a continuation, not a second generation.
GATE_RETRY_TRIGGER_SUFFIX = ":gate_retry"

#: ``pg_advisory_xact_lock`` key serialising quota count + insert across every
#: process sharing the database. Arbitrary but fixed ("NXLQ"); one key for the
#: whole instance because the allowance is per instance, not per project.
QUOTA_ADVISORY_LOCK_KEY = 0x4E584C51

#: ``core.EntitlementPolicy.check`` needs a usage figure for GENERATION. Paths
#: that do not create a counted run (continuations, non-run LLM routes) are not
#: quota-limited (Decision 6), so they pass zero: the licence must be valid and
#: include the feature, and nothing is consumed.
_NOT_QUOTA_COUNTED = 0


class RunAdmission(str, enum.Enum):
    """How a new ``generate_runs`` row is admitted by the chokepoint."""

    #: A user-meaningful generation: valid licence + quota, and it counts.
    NEW = "new"
    #: Part of an already-admitted top-level run (composite child, gate retry):
    #: valid licence only, never counted.
    CONTINUATION = "continuation"


def is_quota_counted(*, partition_name: str | None, triggered_by: str) -> bool:
    """Python twin of ``counted_run_clause``; the chokepoint asserts they agree."""
    return partition_name is None and not triggered_by.endswith(GATE_RETRY_TRIGGER_SUFFIX)


def counted_run_clause(claims: LicenceClaims) -> ColumnElement[bool]:
    """SQL predicate selecting the runs that consume this period's allowance."""
    return and_(
        GenerateRun.partition_name.is_(None),
        # autoescape: ``_`` in the suffix is a LIKE wildcard otherwise.
        not_(GenerateRun.triggered_by.endswith(GATE_RETRY_TRIGGER_SUFFIX, autoescape=True)),
        GenerateRun.status.in_(COUNTED_STATUSES),
        GenerateRun.started_at >= claims.period_start,
        GenerateRun.started_at < claims.period_end,
    )


async def count_generations(db: AsyncSession, claims: LicenceClaims) -> int:
    used = await db.scalar(select(func.count(GenerateRun.id)).where(counted_run_clause(claims)))
    return int(used or 0)


def _uses_advisory_lock(db: AsyncSession) -> bool:
    """Only PostgreSQL has advisory locks; SQLite (tests) serialises writers itself."""
    return db.get_bind().dialect.name == "postgresql"


async def acquire_quota_lock(db: AsyncSession) -> None:
    """Take the instance-wide quota lock for the rest of ``db``'s transaction.

    Transaction-scoped, so it is released by the caller's commit or rollback and
    can never leak. Re-entrant within one transaction, so a route that must take
    it before a row lock (see ``generate_docs``) may do so and the chokepoint's
    own acquisition is then a no-op. Lock order rule: take this BEFORE any
    ``SELECT ... FOR UPDATE`` on ``projects`` -- inserting a run takes a
    ``FOR KEY SHARE`` lock on its project row, so the opposite order deadlocks
    against a concurrent admission for the same project.
    """
    if _uses_advisory_lock(db):
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": QUOTA_ADVISORY_LOCK_KEY}
        )


async def serialize_generation_admission(db: AsyncSession) -> None:
    """``acquire_quota_lock`` when a count will actually happen (enforcement on)."""
    if get_policy().enforce:
        await acquire_quota_lock(db)


async def admit_generation(db: AsyncSession, admission: RunAdmission) -> None:
    """Raise ``LicenceRefused`` unless a run with ``admission`` may be created now.

    For ``NEW`` the lock is taken before the count, in ``db``'s transaction, and
    stays held until the caller commits the insert -- so two concurrent starts
    cannot both see the last free slot. Nothing is counted when enforcement is
    off or the licence is not valid (the refusal does not depend on usage then).
    """
    policy = get_policy()
    if not policy.enforce:
        return
    if admission is RunAdmission.CONTINUATION:
        policy.require(Capability.GENERATION, generations_used=_NOT_QUOTA_COUNTED)
        return
    status = policy.status()
    used = _NOT_QUOTA_COUNTED
    if status.state is LicenceState.VALID and status.claims is not None:
        await acquire_quota_lock(db)
        used = await count_generations(db, status.claims)
    policy.require(Capability.GENERATION, generations_used=used)


def admit_scan() -> None:
    """Raise ``LicenceRefused`` unless a scan may start (any valid licence)."""
    get_policy().require(Capability.SCAN)


async def check_capability(capability: Capability, *, quota: bool = False) -> Refusal | None:
    """Advisory check for route gates and background entry points.

    ``quota=True`` also applies the generation allowance, read WITHOUT the lock:
    it exists so a route that starts a generation in the background can answer
    402 up front. The locked check at run creation stays authoritative.
    """
    policy = get_policy()
    if not policy.enforce:
        return None
    if capability is not Capability.GENERATION:
        return policy.check(capability)
    used = _NOT_QUOTA_COUNTED
    if quota:
        status = policy.status()
        if status.state is LicenceState.VALID and status.claims is not None:
            used = await generations_used(status.claims)
    return policy.check(Capability.GENERATION, generations_used=used)


async def generations_used(claims: LicenceClaims) -> int:
    """Count in a fresh session (for callers that hold none)."""
    # Resolved at call time so a test (or a re-bound engine) that replaces the
    # session factory is honoured.
    from mcp_server.db import session as db_session

    async with db_session.async_session_factory() as db:
        return await count_generations(db, claims)


async def usage_summary() -> dict[str, int | None]:
    """``usage`` block of ``GET /mcp/licence/status``. Counts only on a valid licence."""
    status = get_policy().status()
    if status.state is not LicenceState.VALID or status.claims is None:
        return {"generations_used": None, "generations_limit": None}
    return {
        "generations_used": await generations_used(status.claims),
        "generations_limit": status.claims.limits.get(GENERATIONS_LIMIT_KEY),
    }


def log_refusal(where: str, exc: LicenceRefused, **context: object) -> None:
    """The one warning line a background caller emits when it skips on a refusal."""
    extra = " ".join(f"{key}={value}" for key, value in context.items())
    logger.warning(
        "licence refused %s: code=%s capability=%s%s",
        where,
        exc.refusal.code.value,
        exc.refusal.capability.value,
        f" {extra}" if extra else "",
    )
