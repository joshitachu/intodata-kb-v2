"""Process-wide licence policy for this service (not mirrored).

Built lazily so tests can swap it with ``set_policy``.
"""

from __future__ import annotations

import logging

from src.config import settings

from .core import EntitlementPolicy, FileLicenceSource
from .keys import PINNED_PUBLIC_KEYS

logger = logging.getLogger(__name__)

_policy: EntitlementPolicy | None = None


def build_policy() -> EntitlementPolicy:
    return EntitlementPolicy(
        FileLicenceSource(settings.licence_file),
        public_keys=PINNED_PUBLIC_KEYS,
        expected_subject=settings.licence_client_slug,
        enforce=settings.licence_enforcement == "enforce",
    )


def get_policy() -> EntitlementPolicy:
    global _policy
    if _policy is None:
        _policy = build_policy()
    return _policy


def set_policy(policy: EntitlementPolicy | None) -> None:
    """Replace the process policy (tests); ``None`` rebuilds from settings."""
    global _policy
    _policy = policy


def log_startup_state() -> None:
    """One log line at startup so a lockout is diagnosable without a request."""
    policy = get_policy()
    status = policy.status()
    claims = status.claims
    logger.info(
        "licence: enforcement=%s state=%s plan=%s period_end=%s reason=%s",
        "enforce" if policy.enforce else "off",
        status.state.value,
        claims.plan if claims else None,
        claims.period_end.isoformat() if claims else None,
        status.reason or None,
    )
