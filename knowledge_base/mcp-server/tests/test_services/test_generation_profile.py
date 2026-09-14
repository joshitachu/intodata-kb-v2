from __future__ import annotations

import json

import pytest

from mcp_server.services.generation_profile import (
    assert_profile_matches,
    load_published_profile,
    normalize_profile,
    profile_digest,
    validate_profile_route,
)
from mcp_server.services.generation_run_state import persist_generation_profile


def test_profile_defaults_are_canonical_and_stable():
    profile = normalize_profile({})
    assert profile["contract"] == "talend-landscape-v2"
    assert profile["budgets"]["max_requirements_per_task"] == 12
    assert profile["budgets"]["analysis_total_timeout_seconds"] == 1800
    assert profile["budgets"]["max_scope_evidence_bytes"] == 400_000
    assert profile["budgets"]["max_scope_units"] == 6
    assert profile["budgets"]["max_analysis_total_processed_tokens"] == 5_000_000
    assert profile["budgets"]["analysis_cost_dispatch_ceiling_usd"] == 10.0
    assert profile_digest(profile) == profile_digest(dict(reversed(list(profile.items()))))


def test_retired_contracts_still_load_but_cannot_start_a_new_run():
    """History stays readable; the retired lanes stay unselectable.

    Persisted run rows, published ``_audit/generation_profile.json`` files and
    ``assert_profile_matches`` digests all carry the old contract strings, so
    parsing them must keep working. Starting a NEW run on one must not.
    """
    for contract in ("talend-landscape-v2", "talend-enterprise-v1", "integration-intelligence-v1"):
        stored = normalize_profile({"contract": contract})
        assert stored["contract"] == contract
        assert profile_digest(stored)
        with pytest.raises(ValueError, match="retired"):
            validate_profile_route(stored, platform="talend", engine="opencode-cli")

    # `{}` is not a neutral value: it defaults to landscape v2, which is
    # retired. Remapping it to the agentic lane would start a paid run nobody
    # asked for, so it is rejected rather than reinterpreted.
    with pytest.raises(ValueError, match="retired"):
        validate_profile_route({}, platform="talend", engine="opencode-cli")


def test_agentic_profile_preserves_goal_and_is_platform_and_engine_neutral():
    profile = normalize_profile(
        {
            "contract": "agent-workspace-v1",
            "agent_workspace": {
                "goal": "Where does OrderStatus come from?",
                "agent_provider": "opencode",
            },
        }
    )

    assert profile["agent_workspace"]["goal"] == "Where does OrderStatus come from?"
    # No per-session or per-unit quota may reappear in this contract: those are
    # what turned a 142-design estate into fixed batches.
    serialized = json.dumps(profile)
    for banned in ("units_per_session", "concurrency", "max_run_calls", "max_analysis_tasks"):
        assert banned not in serialized

    # Any platform whose scan produces the workspace qualifies, and which
    # coding agent runs it is `agent_provider` -- an unregistered one fails
    # closed in the registry, not here.
    validate_profile_route(profile, platform="workato", engine="opencode-cli")
    validate_profile_route(profile, platform="talend", engine="custom")

    # Legacy is still selected by the absence of a profile, not by a contract.
    validate_profile_route(None, platform="talend", engine="custom")


def test_resume_cannot_mix_legacy_and_v2(tmp_path):
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    (staging / "_run_manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="profile mismatch"):
        persist_generation_profile(staging, normalize_profile({}), resume=True)


def test_profile_receipt_round_trip(tmp_path):
    staging = tmp_path / "docs"
    staging.mkdir()
    profile = normalize_profile({"budgets": {}})
    persist_generation_profile(staging, profile, resume=False)
    assert load_published_profile(staging) == profile
    receipt = json.loads((staging / "_audit" / "generation_profile.json").read_text())
    assert receipt["digest"] == profile_digest(profile)
    assert_profile_matches(profile, receipt["generation_profile"])
