"""Test-only licence issuance: sign tokens with the golden test key and install them.

Imported by the licensing, API and service tests. Always pair ``install_policy``
with ``set_policy(None)`` in teardown (``licence_policy`` fixture does this).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt

from mcp_server.licensing.core import EntitlementPolicy
from mcp_server.licensing.runtime import set_policy

VECTORS = json.loads((Path(__file__).parent / "golden_vectors.json").read_text())
PUBLIC_KEYS: dict[str, str] = VECTORS["public_keys"]
SUBJECT: str = VECTORS["expected_subject"]
PRIVATE_KEY_PEM: str = VECTORS["test_private_key_pem"]
KID = "test-1"


def issue_token(
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    features: list[str] | None = None,
    generations: int | None = 3,
    subject: str = SUBJECT,
) -> str:
    """A licence valid from ``start`` (default: yesterday) to ``end`` (default: +29d)."""
    now = datetime.now(timezone.utc)
    start = start or now - timedelta(days=1)
    end = end or now + timedelta(days=29)
    claims: dict[str, object] = {
        "iss": "intodata-nexus",
        "sub": subject,
        "lid": str(uuid.uuid4()),
        "plan": "basic",
        "features": features if features is not None else ["chat", "generation"],
        "limits": {} if generations is None else {"generations_per_period": generations},
        "nbf": int(start.timestamp()),
        "exp": int(end.timestamp()),
        "iat": int(now.timestamp()),
    }
    return jwt.encode(claims, PRIVATE_KEY_PEM, algorithm="EdDSA", headers={"kid": KID})


def expired_token() -> str:
    now = datetime.now(timezone.utc)
    return issue_token(start=now - timedelta(days=31), end=now - timedelta(days=1))


class StaticSource:
    def __init__(self, token: str | None) -> None:
        self.token = token

    def read_token(self) -> str | None:
        return self.token


def install_policy(token: str | None, *, enforce: bool = True) -> EntitlementPolicy:
    policy = EntitlementPolicy(
        StaticSource(token), public_keys=PUBLIC_KEYS, expected_subject=SUBJECT, enforce=enforce
    )
    set_policy(policy)
    return policy
