from __future__ import annotations

import json

import pytest

from mcp_server.services.generation_profile import (
    RETIRED_CONTRACTS,
    load_published_profile,
    profile_digest,
    validate_profile_route,
)


def test_no_profile_selects_the_shared_generation_lane():
    validate_profile_route(None, platform="talend", engine="opencode-cli")
    validate_profile_route(None, platform="generic", engine="custom")


@pytest.mark.parametrize("contract", sorted(RETIRED_CONTRACTS))
def test_retired_contracts_are_rejected_for_new_runs(contract: str):
    with pytest.raises(ValueError, match="retired and unsupported"):
        validate_profile_route({"contract": contract}, platform="talend", engine="opencode-cli")


def test_unknown_profiles_are_rejected_without_becoming_new_semantics():
    with pytest.raises(ValueError, match="generation_profile is unsupported"):
        validate_profile_route({}, platform="talend", engine="opencode-cli")


def test_historical_profile_is_read_as_opaque_metadata(tmp_path):
    audit = tmp_path / "_audit"
    audit.mkdir()
    historical = {"contract": "agent-workspace-v1", "agent_workspace": {"goal": "old"}}
    (audit / "generation_profile.json").write_text(json.dumps({"generation_profile": historical}))
    assert load_published_profile(tmp_path) == historical
    assert profile_digest(historical)
