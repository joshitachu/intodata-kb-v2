"""Flag-driven, content-blind runtime cohort selection for the agent runtime.

Slice 3 introduces the first cohort that routes real traffic to the standard
``create_agent`` runtime. Selection reads only server-owned signals (feature
flags, an allowlist, a stable per-user bucket, the enabled transports, and
whether the effective manifest exposes any consequential capability). It never
inspects message text, task labels, or conversation history, and it cannot
change the cohort within a turn.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


_BUCKET_COUNT = 100
_BUCKET_SALT = "nexus-agent-runtime-v2"


class RuntimeCohort(StrEnum):
    """The runtime a turn is routed to. Later slices add write/plan cohorts."""

    LEGACY = "legacy"
    V2_READONLY_PRIMARY = "v2_readonly_primary"


class CohortDecision(BaseModel):
    """The immutable cohort routing decision recorded for one turn."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cohort: RuntimeCohort
    cohort_source: str
    cohort_bucket: int
    reason_code: str

    @property
    def is_v2_readonly(self) -> bool:
        return self.cohort is RuntimeCohort.V2_READONLY_PRIMARY


def stable_user_bucket(user_id: int, *, bucket_count: int = _BUCKET_COUNT) -> int:
    """Return a stable ``sha256(user_id) mod bucket_count`` bucket.

    The bucket depends only on the user identity, never on message content, so
    the same user always lands in the same bucket regardless of what they type.
    """

    digest = hashlib.sha256(f"{_BUCKET_SALT}:{user_id}".encode("utf-8")).hexdigest()
    return int(digest, 16) % bucket_count


class CohortSelector:
    """Select a runtime cohort from server-owned signals only."""

    def __init__(
        self,
        *,
        enabled: bool,
        readonly_users: list[int] | set[int],
        readonly_percent: int,
        transports: list[str] | set[str],
    ) -> None:
        self._enabled = bool(enabled)
        self._readonly_users = frozenset(int(user) for user in readonly_users)
        self._readonly_percent = max(0, min(100, int(readonly_percent)))
        self._transports = frozenset(str(transport) for transport in transports)

    @classmethod
    def from_settings(cls, settings) -> "CohortSelector":  # noqa: ANN001
        return cls(
            enabled=settings.agent_runtime_v2_enabled,
            readonly_users=settings.agent_runtime_v2_readonly_users,
            readonly_percent=settings.agent_runtime_v2_readonly_percent,
            transports=settings.agent_runtime_v2_transports,
        )

    def select(
        self,
        *,
        user_id: int,
        transport: str,
        manifest_has_consequential: bool,
    ) -> CohortDecision:
        """Resolve the cohort for one turn.

        ``manifest_has_consequential`` is the server's own assessment of whether
        the effective manifest for this turn exposes any non-``read_only``
        capability. It is a server signal, not derived from message content.
        """

        bucket = stable_user_bucket(user_id)

        def legacy(reason_code: str) -> CohortDecision:
            return CohortDecision(
                cohort=RuntimeCohort.LEGACY,
                cohort_source="legacy_default",
                cohort_bucket=bucket,
                reason_code=reason_code,
            )

        if not self._enabled:
            return legacy("v2_master_switch_off")
        if transport not in self._transports:
            return legacy("transport_not_enabled")
        if manifest_has_consequential:
            return legacy("manifest_exposes_consequential_capability")
        in_allowlist = user_id in self._readonly_users
        in_percentage = bucket < self._readonly_percent
        if not (in_allowlist or in_percentage):
            return legacy("not_in_readonly_canary")
        return CohortDecision(
            cohort=RuntimeCohort.V2_READONLY_PRIMARY,
            cohort_source="flag_readonly_canary",
            cohort_bucket=bucket,
            reason_code="readonly_allowlist" if in_allowlist else "readonly_percentage",
        )


__all__ = [
    "CohortDecision",
    "CohortSelector",
    "RuntimeCohort",
    "stable_user_bucket",
]
