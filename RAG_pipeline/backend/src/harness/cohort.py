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

from src.agent_runtime_canary import CanaryStage, CanaryStagePolicy


_BUCKET_COUNT = 100
_BUCKET_SALT = "nexus-agent-runtime-v2"


class RuntimeCohort(StrEnum):
    """The runtime a turn is routed to. Later slices add write/plan cohorts."""

    LEGACY = "legacy"
    V2_DIRECT_PRIMARY = "v2_direct_primary"
    V2_READONLY_PRIMARY = "v2_readonly_primary"
    V2_PLAN_PRIMARY = "v2_plan_primary"
    V2_EFFECT_PRIMARY = "v2_effect_primary"


class CohortDecision(BaseModel):
    """The immutable cohort routing decision recorded for one turn."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cohort: RuntimeCohort
    cohort_source: str
    cohort_bucket: int
    reason_code: str

    @property
    def is_v2_readonly(self) -> bool:
        return self.cohort in {RuntimeCohort.V2_DIRECT_PRIMARY, RuntimeCohort.V2_READONLY_PRIMARY, RuntimeCohort.V2_PLAN_PRIMARY, RuntimeCohort.V2_EFFECT_PRIMARY}

    @property
    def is_v2_direct_primary(self) -> bool:
        return self.cohort is RuntimeCohort.V2_DIRECT_PRIMARY

    @property
    def is_v2_plan_primary(self) -> bool:
        return self.cohort is RuntimeCohort.V2_PLAN_PRIMARY

    @property
    def is_v2_effect_primary(self) -> bool:
        return self.cohort is RuntimeCohort.V2_EFFECT_PRIMARY


def stable_user_bucket(user_id: int, *, bucket_count: int = _BUCKET_COUNT) -> int:
    """Return a stable ``sha256(user_id) mod bucket_count`` bucket.

    The bucket depends only on the user identity, never on message content, so
    the same user always lands in the same bucket regardless of what they type.
    """

    digest = hashlib.sha256(f"{_BUCKET_SALT}:{user_id}".encode("utf-8")).hexdigest()
    return int(digest, 16) % bucket_count


def live_streaming_selected(settings, *, user_id: int) -> bool:  # noqa: ANN001
    """Content-blind Slice 6 admission shared by stream and non-stream turns."""

    stage = CanaryStagePolicy(
        stage=getattr(settings, "agent_runtime_v2_canary_stage", CanaryStage.WORKFLOW_APPROVALS)
    )
    if not stage.allows_direct or not settings.agent_runtime_v2_enabled or not settings.agent_runtime_v2_live_streaming_enabled:
        return False
    bucket = stable_user_bucket(user_id)
    return (
        user_id in set(settings.agent_runtime_v2_live_streaming_users)
        or bucket < settings.agent_runtime_v2_live_streaming_percent
    )


class CohortSelector:
    """Select a runtime cohort from server-owned signals only."""

    def __init__(
        self,
        *,
        enabled: bool,
        readonly_users: list[int] | set[int],
        readonly_percent: int,
        transports: list[str] | set[str],
        planning_enabled: bool = False,
        planning_users: list[int] | set[int] = (),
        planning_percent: int = 0,
        effects_enabled: bool = False,
        effect_users: list[int] | set[int] = (),
        effect_percent: int = 0,
        canary_stage: CanaryStage | str = CanaryStage.WORKFLOW_APPROVALS,
    ) -> None:
        self._enabled = bool(enabled)
        self._readonly_users = frozenset(int(user) for user in readonly_users)
        self._readonly_percent = max(0, min(100, int(readonly_percent)))
        self._transports = frozenset(str(transport) for transport in transports)
        self._planning_enabled = bool(planning_enabled)
        self._planning_users = frozenset(int(user) for user in planning_users)
        self._planning_percent = max(0, min(100, int(planning_percent)))
        self._effects_enabled = bool(effects_enabled)
        self._effect_users = frozenset(int(user) for user in effect_users)
        self._effect_percent = max(0, min(100, int(effect_percent)))
        self._stage = CanaryStagePolicy(stage=canary_stage)

    @classmethod
    def from_settings(cls, settings) -> "CohortSelector":  # noqa: ANN001
        return cls(
            enabled=settings.agent_runtime_v2_enabled,
            readonly_users=settings.agent_runtime_v2_readonly_users,
            readonly_percent=settings.agent_runtime_v2_readonly_percent,
            transports=settings.agent_runtime_v2_transports,
            planning_enabled=settings.agent_runtime_v2_planning_enabled,
            planning_users=settings.agent_runtime_v2_planning_users,
            planning_percent=settings.agent_runtime_v2_planning_percent,
            effects_enabled=settings.agent_runtime_v2_effects_enabled,
            effect_users=settings.agent_runtime_v2_effect_users,
            effect_percent=settings.agent_runtime_v2_effect_percent,
            canary_stage=settings.agent_runtime_v2_canary_stage,
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
        if self._stage.stage is CanaryStage.OFF:
            return legacy("canary_stage_off")
        if transport not in self._transports:
            return legacy("transport_not_enabled")
        effect_candidate = self._stage.allows_effects and self._effects_enabled and (
            user_id in self._effect_users or bucket < self._effect_percent
        )
        if effect_candidate:
            return CohortDecision(
                cohort=RuntimeCohort.V2_EFFECT_PRIMARY,
                cohort_source="flag_effect_canary",
                cohort_bucket=bucket,
                reason_code="effect_allowlist" if user_id in self._effect_users else "effect_percentage",
            )
        # Planning uses its own exact safe manifest, so the effective request
        # registry may still contain consequential capabilities.
        planning_candidate = self._stage.allows_planning and self._planning_enabled and (
            user_id in self._planning_users or bucket < self._planning_percent
        )
        if planning_candidate:
            return CohortDecision(
                cohort=RuntimeCohort.V2_PLAN_PRIMARY,
                cohort_source="flag_planning_canary",
                cohort_bucket=bucket,
                reason_code=("planning_allowlist" if user_id in self._planning_users else "planning_percentage"),
            )
        if not self._stage.allows_readonly:
            in_allowlist = user_id in self._readonly_users
            in_percentage = bucket < self._readonly_percent
            if self._stage.allows_direct and (in_allowlist or in_percentage):
                return CohortDecision(
                    cohort=RuntimeCohort.V2_DIRECT_PRIMARY,
                    cohort_source="flag_internal_no_tool_canary",
                    cohort_bucket=bucket,
                    reason_code="direct_allowlist" if in_allowlist else "direct_percentage",
                )
            return legacy("canary_stage_not_eligible")
        if manifest_has_consequential and self._stage.stage is CanaryStage.WORKFLOW_APPROVALS:
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
    "live_streaming_selected",
]
