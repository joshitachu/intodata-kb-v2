from types import SimpleNamespace

import pytest

from src.agent_runtime_canary import CanaryStage, CanaryStagePolicy
from src.harness.models import HarnessRiskTier
from src.harness.registry import base_capability_registry


def _settings(**overrides):
    values = {
        "agent_runtime_v2_enabled": True,
        "agent_runtime_v2_readonly_users": [],
        "agent_runtime_v2_readonly_percent": 10,
        "agent_runtime_v2_planning_enabled": False,
        "agent_runtime_v2_planning_users": [],
        "agent_runtime_v2_planning_percent": 0,
        "agent_runtime_v2_effects_enabled": False,
        "agent_runtime_v2_effect_users": [],
        "agent_runtime_v2_effect_percent": 0,
        "agent_runtime_v2_live_streaming_enabled": False,
        "agent_runtime_v2_live_streaming_percent": 0,
        "agent_runtime_v2_transports": ["stream", "nonstream"],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_off_is_the_default_and_allows_no_standard_cohort() -> None:
    policy = CanaryStagePolicy()
    assert policy.stage is CanaryStage.OFF
    assert not policy.allows_direct
    assert policy.allowed_effect_capability_ids(base_capability_registry()) == set()


def test_stage_order_is_monotonic_and_content_free() -> None:
    assert CanaryStagePolicy(stage="internal_no_tool").allows_direct
    assert not CanaryStagePolicy(stage="internal_no_tool").allows_readonly
    assert CanaryStagePolicy(stage="planning").allows_planning
    assert not CanaryStagePolicy(stage="planning").allows_effects
    assert CanaryStagePolicy(stage="workflow_approvals").allows_workflows


def test_reversible_stage_filters_workflow_and_content_risks_by_definition() -> None:
    registry = base_capability_registry()
    allowed = CanaryStagePolicy(
        stage=CanaryStage.REVERSIBLE_EFFECTS
    ).allowed_effect_capability_ids(registry)
    assert allowed is not None
    assert "nexus.packs.create" in allowed
    assert "nexus.workflow.onboard_project" not in allowed
    assert "nexus.packs.drafts.save" not in allowed
    assert all(
        registry.get(capability_id).risk_tier
        in {HarnessRiskTier.READ_ONLY, HarnessRiskTier.REVERSIBLE_WRITE}
        for capability_id in allowed
    )


def test_non_monotonic_or_wider_than_stage_configuration_fails_closed() -> None:
    with pytest.raises(ValueError, match="planning rollout exceeds"):
        CanaryStagePolicy(stage="read_only").validate_rollout_settings(
            _settings(agent_runtime_v2_planning_enabled=True)
        )
    with pytest.raises(ValueError, match="higher-risk rollout"):
        CanaryStagePolicy(stage="reversible_effects").validate_rollout_settings(
            _settings(
                agent_runtime_v2_planning_enabled=True,
                agent_runtime_v2_planning_percent=5,
                agent_runtime_v2_effects_enabled=True,
                agent_runtime_v2_effect_percent=10,
            )
        )
    with pytest.raises(ValueError, match="100%"):
        CanaryStagePolicy(stage="full_standard").validate_rollout_settings(
            _settings(
                agent_runtime_v2_planning_enabled=True,
                agent_runtime_v2_planning_percent=10,
                agent_runtime_v2_effects_enabled=True,
                agent_runtime_v2_effect_percent=10,
            )
        )


def test_full_standard_accepts_only_monotonic_complete_rollout() -> None:
    CanaryStagePolicy(stage="full_standard").validate_rollout_settings(
        _settings(
            agent_runtime_v2_readonly_percent=100,
            agent_runtime_v2_planning_enabled=True,
            agent_runtime_v2_planning_percent=100,
            agent_runtime_v2_effects_enabled=True,
            agent_runtime_v2_effect_percent=100,
            agent_runtime_v2_live_streaming_enabled=True,
            agent_runtime_v2_live_streaming_percent=100,
        )
    )


def test_full_standard_explicitly_allows_workflow_capabilities() -> None:
    registry = base_capability_registry()
    allowed = CanaryStagePolicy(
        stage=CanaryStage.FULL_STANDARD
    ).allowed_effect_capability_ids(registry)

    assert allowed is not None
    assert "nexus.workflow.onboard_project" in allowed
