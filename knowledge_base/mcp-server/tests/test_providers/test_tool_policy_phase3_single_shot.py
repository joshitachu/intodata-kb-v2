"""Phase 3 first-pass single-shot tool policy (token-reduction fix).

First-pass `phase3` runs single-shot (no tool loop) by default — the targeted
doc pack is primary and sufficient, so token cost scales with pack size instead
of an accumulating tool conversation. Repair phases keep tools regardless, and
an opt-in config switch restores the agentic loop for first-pass docs.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from mcp_server.config import config
from mcp_server.providers.custom.harness import CustomHarnessProvider


def _tool_capable_provider() -> MagicMock:
    provider = MagicMock()
    provider.supports_tools = True
    return provider


def test_phase3_first_pass_single_shot_by_default(monkeypatch) -> None:
    monkeypatch.setattr(config, "agent_phase3_first_pass_tools", False)
    harness = CustomHarnessProvider(MagicMock())
    policy = harness._tool_policy(_tool_capable_provider(), "phase3")
    assert policy.enabled is False
    assert policy.max_input_tokens == 0
    assert policy.max_calls == 0


def test_phase3_repair_keeps_tools(monkeypatch) -> None:
    monkeypatch.setattr(config, "agent_phase3_first_pass_tools", False)
    harness = CustomHarnessProvider(MagicMock())
    policy = harness._tool_policy(_tool_capable_provider(), "phase3.repair")
    assert policy.enabled is True
    assert policy.max_calls >= 1


def test_phase3_first_pass_tools_opt_in(monkeypatch) -> None:
    monkeypatch.setattr(config, "agent_phase3_first_pass_tools", True)
    harness = CustomHarnessProvider(MagicMock())
    policy = harness._tool_policy(_tool_capable_provider(), "phase3")
    assert policy.enabled is True
    assert policy.max_input_tokens == config.agent_phase3_tool_input_token_cap
    assert policy.max_calls == config.agent_max_tool_calls


def test_phase3_consistency_critic_keep_tools(monkeypatch) -> None:
    monkeypatch.setattr(config, "agent_phase3_first_pass_tools", False)
    harness = CustomHarnessProvider(MagicMock())
    for phase in ("phase3.consistency", "consistency", "critic"):
        policy = harness._tool_policy(_tool_capable_provider(), phase)
        assert policy.enabled is True, f"{phase} must keep tools"


def test_phase2_and_scorecard_unchanged(monkeypatch) -> None:
    monkeypatch.setattr(config, "agent_phase3_first_pass_tools", False)
    harness = CustomHarnessProvider(MagicMock())
    for phase in ("phase2", "scorecard", "phase2.6"):
        policy = harness._tool_policy(_tool_capable_provider(), phase)
        assert policy.enabled is False, f"{phase} must stay disabled"


def test_business_topology_is_push_evidence_not_agentic() -> None:
    """Topology must not pull evidence via budget-limited tools (starvation fix)."""
    harness = CustomHarnessProvider(MagicMock())
    policy = harness._tool_policy(_tool_capable_provider(), "business_topology")
    assert policy.enabled is False
    assert policy.max_input_tokens == 0
    assert policy.max_calls == 0


def test_phase3_opt_in_does_not_change_phase2_or_scorecard(monkeypatch) -> None:
    monkeypatch.setattr(config, "agent_phase3_first_pass_tools", True)
    harness = CustomHarnessProvider(MagicMock())
    for phase in ("phase2", "scorecard", "phase2.6"):
        policy = harness._tool_policy(_tool_capable_provider(), phase)
        assert policy.enabled is False, f"{phase} must stay disabled regardless of phase3 switch"


def test_non_tool_provider_always_disabled(monkeypatch) -> None:
    monkeypatch.setattr(config, "agent_phase3_first_pass_tools", True)
    harness = CustomHarnessProvider(MagicMock())
    provider = MagicMock()
    provider.supports_tools = False
    policy = harness._tool_policy(provider, "phase3")
    assert policy.enabled is False
