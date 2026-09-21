"""Compatibility boundary for retired OpenCode generation contracts.

New documentation runs have no generation profile: provider selection changes
only CLI transport.  Historical rows remain readable as opaque JSON.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

RETIRED_CONTRACTS = frozenset({
    "talend-landscape-v2",
    "talend-enterprise-v1",
    "integration-intelligence-v1",
    "agent-workspace-v1",
})


def normalize_profile(profile: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a JSON-safe profile without assigning it new semantics."""
    if profile is None:
        return None
    if not isinstance(profile, dict):
        raise ValueError("generation_profile must be an object")
    return dict(profile)


def profile_digest(profile: dict[str, Any] | None) -> str:
    return hashlib.sha256(json.dumps(profile, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def assert_profile_matches(expected: dict[str, Any] | None, actual: dict[str, Any] | None) -> None:
    if profile_digest(expected) != profile_digest(actual):
        raise ValueError("Generation profile mismatch; start a fresh attempt instead of mixing contracts")


def validate_profile_route(profile: dict[str, Any] | None, *, platform: str, engine: str) -> None:
    """Reject all former OpenCode orchestration contracts for starts and resumes."""
    if profile is None:
        return
    contract = profile.get("contract")
    if contract in RETIRED_CONTRACTS:
        raise ValueError(
            f"{contract} is retired and unsupported. Start a new Claude-parity generation without generation_profile."
        )
    raise ValueError("generation_profile is unsupported; OpenCode uses the Claude-compatible default workflow")


def load_published_profile(docs_dir: Path) -> dict[str, Any] | None:
    """Read historical audit metadata only; never use it to select a runner."""
    path = docs_dir / "_audit" / "generation_profile.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    profile = data.get("generation_profile") if isinstance(data, dict) else None
    return dict(profile) if isinstance(profile, dict) else None
