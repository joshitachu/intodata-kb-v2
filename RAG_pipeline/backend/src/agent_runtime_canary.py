"""Content-blind Slice 8 canary-stage authority.

This module deliberately sits above ``src.harness`` so application settings can
validate at import time without loading runtime providers or creating a config
cycle.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from src.harness.registry import CapabilityRegistry


class CanaryStage(StrEnum):
    OFF = "off"
    INTERNAL_NO_TOOL = "internal_no_tool"
    READ_ONLY = "read_only"
    PLANNING = "planning"
    REVERSIBLE_EFFECTS = "reversible_effects"
    WORKFLOW_APPROVALS = "workflow_approvals"
    FULL_STANDARD = "full_standard"


_STAGE_ORDER = {
    CanaryStage.OFF: 0,
    CanaryStage.INTERNAL_NO_TOOL: 1,
    CanaryStage.READ_ONLY: 2,
    CanaryStage.PLANNING: 3,
    CanaryStage.REVERSIBLE_EFFECTS: 4,
    CanaryStage.WORKFLOW_APPROVALS: 5,
    CanaryStage.FULL_STANDARD: 6,
}


class CanaryStagePolicy(BaseModel):
    """Frozen maximum-risk policy for one configured release."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stage: CanaryStage = CanaryStage.OFF

    def at_least(self, stage: CanaryStage) -> bool:
        return _STAGE_ORDER[self.stage] >= _STAGE_ORDER[stage]

    @property
    def allows_direct(self) -> bool:
        return self.at_least(CanaryStage.INTERNAL_NO_TOOL)

    @property
    def allows_readonly(self) -> bool:
        return self.at_least(CanaryStage.READ_ONLY)

    @property
    def allows_planning(self) -> bool:
        return self.at_least(CanaryStage.PLANNING)

    @property
    def allows_effects(self) -> bool:
        return self.at_least(CanaryStage.REVERSIBLE_EFFECTS)

    @property
    def allows_workflows(self) -> bool:
        return self.at_least(CanaryStage.WORKFLOW_APPROVALS)

    @property
    def requires_full_standard(self) -> bool:
        return self.stage is CanaryStage.FULL_STANDARD

    def allowed_effect_capability_ids(
        self, registry: "CapabilityRegistry"
    ) -> set[str] | None:
        """Return the server-owned effect ceiling for the active stage.

        Keep ``full_standard`` explicit rather than returning ``None``.  The
        executor applies this value independently of the model-visible
        manifest; an explicit set keeps those two policy surfaces aligned and
        prevents a workflow capability such as onboarding from being visible
        but rejected at execution time.
        """

        if self.allows_workflows:
            return {
                capability.id
                for capability in registry.list(enabled_only=True)
            }
        if self.stage is not CanaryStage.REVERSIBLE_EFFECTS:
            return set()
        return {
            capability.id
            for capability in registry.list(enabled_only=False)
            if capability.risk_tier.value in {"read_only", "reversible_write"}
        }

    def validate_rollout_settings(self, settings) -> None:  # noqa: ANN001
        """Reject non-monotonic or wider-than-stage rollout configuration."""

        if self.stage is CanaryStage.OFF:
            return
        if not settings.agent_runtime_v2_enabled:
            raise ValueError("a non-off canary stage requires agent_runtime_v2_enabled")
        readonly = int(settings.agent_runtime_v2_readonly_percent)
        planning = int(settings.agent_runtime_v2_planning_percent)
        effects = int(settings.agent_runtime_v2_effect_percent)
        if self.allows_direct and not settings.agent_runtime_v2_readonly_users and readonly == 0:
            raise ValueError("canary stage requires a bounded read allowlist or percentage")
        if not self.allows_planning and (settings.agent_runtime_v2_planning_enabled or planning):
            raise ValueError("planning rollout exceeds the configured canary stage")
        if self.allows_planning and not settings.agent_runtime_v2_planning_enabled:
            raise ValueError("planning stage requires planning to be enabled")
        if self.allows_planning and not settings.agent_runtime_v2_planning_users and planning == 0:
            raise ValueError("planning stage requires a bounded allowlist or percentage")
        if not self.allows_effects and (settings.agent_runtime_v2_effects_enabled or effects):
            raise ValueError("effect rollout exceeds the configured canary stage")
        if self.allows_effects and not settings.agent_runtime_v2_effects_enabled:
            raise ValueError("effect stage requires effects to be enabled")
        if self.allows_effects and not settings.agent_runtime_v2_effect_users and effects == 0:
            raise ValueError("effect stage requires a bounded allowlist or percentage")
        if effects > planning or planning > readonly:
            raise ValueError("higher-risk rollout percentage cannot exceed a lower-risk stage")
        if self.requires_full_standard and (readonly, planning, effects) != (100, 100, 100):
            raise ValueError("full_standard requires 100% read, planning, and effects")
        if self.requires_full_standard and (
            not settings.agent_runtime_v2_live_streaming_enabled
            or int(settings.agent_runtime_v2_live_streaming_percent) != 100
            or set(settings.agent_runtime_v2_transports) != {"stream", "nonstream"}
        ):
            raise ValueError("full_standard requires 100% live streaming on both transports")


__all__ = ["CanaryStage", "CanaryStagePolicy"]
