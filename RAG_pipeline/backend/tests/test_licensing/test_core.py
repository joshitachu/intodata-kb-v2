"""Licence verification and entitlement policy.

MIRRORED TEST: ``knowledge_base/mcp-server/tests/test_licensing/test_core.py``
runs the same cases against the mcp-server copy of ``core.py``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.licensing.core import (
    Capability,
    EntitlementPolicy,
    FileLicenceSource,
    LicenceRefused,
    LicenceState,
    RefusalCode,
    next_licence_path,
    verify_token,
)

VECTORS = json.loads((Path(__file__).parent / "golden_vectors.json").read_text())
KEYS = VECTORS["public_keys"]
SUBJECT = VECTORS["expected_subject"]
VALID_TOKEN = next(v["token"] for v in VECTORS["vectors"] if v["name"] == "valid")
IN_PERIOD = datetime(2026, 10, 15, tzinfo=timezone.utc)
AFTER_PERIOD = datetime(2026, 11, 2, tzinfo=timezone.utc)


class _Static:
    def __init__(self, token: str | None) -> None:
        self.token = token

    def read_token(self) -> str | None:
        return self.token


def _policy(token: str | None, *, enforce: bool = True) -> EntitlementPolicy:
    return EntitlementPolicy(_Static(token), public_keys=KEYS, expected_subject=SUBJECT,
                             enforce=enforce)


@pytest.mark.parametrize("vector", VECTORS["vectors"], ids=lambda v: v["name"])
def test_golden_vectors(vector: dict) -> None:
    status = verify_token(vector["token"], public_keys=KEYS, expected_subject=SUBJECT,
                          now=datetime.fromisoformat(vector["now"]))
    assert status.state.value == vector["expected_state"]


def test_valid_token_exposes_claims_but_not_token() -> None:
    status = verify_token(VALID_TOKEN, public_keys=KEYS, expected_subject=SUBJECT, now=IN_PERIOD)
    assert status.claims is not None
    assert status.claims.plan == "basic"
    public = status.to_public_dict(now=IN_PERIOD)
    assert public["state"] == "valid"
    assert public["limits"] == {"generations_per_period": 3}
    assert public["days_remaining"] == 17
    assert VALID_TOKEN not in json.dumps(public)


def test_empty_subject_is_invalid_not_valid() -> None:
    status = verify_token(VALID_TOKEN, public_keys=KEYS, expected_subject="", now=IN_PERIOD)
    assert status.state is LicenceState.INVALID


def test_leeway_is_bounded() -> None:
    end = datetime(2026, 11, 1, tzinfo=timezone.utc).timestamp()
    at = lambda s: datetime.fromtimestamp(end + s, tz=timezone.utc)  # noqa: E731
    assert verify_token(VALID_TOKEN, public_keys=KEYS, expected_subject=SUBJECT,
                        now=at(30)).state is LicenceState.VALID
    assert verify_token(VALID_TOKEN, public_keys=KEYS, expected_subject=SUBJECT,
                        now=at(61)).state is LicenceState.EXPIRED


def test_enforcement_off_allows_everything() -> None:
    assert _policy(None, enforce=False).check(Capability.CHAT) is None


@pytest.mark.parametrize("capability", list(Capability))
def test_expired_refuses_every_capability(capability: Capability) -> None:
    refusal = _policy(VALID_TOKEN).check(capability, generations_used=0, now=AFTER_PERIOD)
    assert refusal is not None and refusal.code is RefusalCode.LICENCE_EXPIRED


def test_missing_refuses() -> None:
    refusal = _policy(None).check(Capability.CHAT, now=IN_PERIOD)
    assert refusal is not None and refusal.code is RefusalCode.LICENCE_MISSING


def test_generation_quota() -> None:
    policy = _policy(VALID_TOKEN)
    assert policy.check(Capability.GENERATION, generations_used=2, now=IN_PERIOD) is None
    refusal = policy.check(Capability.GENERATION, generations_used=3, now=IN_PERIOD)
    assert refusal is not None and refusal.code is RefusalCode.QUOTA_EXHAUSTED
    detail = refusal.to_detail()
    assert detail["used"] == 3 and detail["limit"] == 3 and detail["code"] == "quota_exhausted"


def test_generation_check_requires_usage() -> None:
    with pytest.raises(ValueError):
        _policy(VALID_TOKEN).check(Capability.GENERATION, now=IN_PERIOD)


def test_require_raises() -> None:
    with pytest.raises(LicenceRefused) as exc:
        _policy(None).require(Capability.SCAN)
    assert exc.value.refusal.code is RefusalCode.LICENCE_MISSING


def test_file_source_reloads_on_change(tmp_path: Path) -> None:
    import os

    path = tmp_path / "licence.jwt"
    source = FileLicenceSource(path)
    assert source.read_token() is None
    path.write_text("first\n")
    assert source.read_token() == "first"
    path.write_text("second\n")
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 5))
    assert source.read_token() == "second"


def test_corrupt_inputs_are_invalid_not_exceptions(tmp_path: Path) -> None:
    """A broken licence must refuse (402), never crash a gated route (500)."""
    import jwt as pyjwt

    key = VECTORS["test_private_key_pem"]
    huge = pyjwt.encode(
        {"iss": "intodata-nexus", "sub": SUBJECT, "lid": "x", "plan": "basic",
         "features": ["chat"], "limits": {}, "nbf": 0, "exp": 10**20},
        key, algorithm="EdDSA", headers={"kid": "test-1"},
    )
    assert verify_token(huge, public_keys=KEYS, expected_subject=SUBJECT,
                        now=IN_PERIOD).state is LicenceState.INVALID

    path = tmp_path / "licence.jwt"
    path.write_bytes(b"\xff\xfe\x00garbage")
    policy = EntitlementPolicy(FileLicenceSource(path), public_keys=KEYS,
                               expected_subject=SUBJECT, enforce=True)
    assert policy.status(now=IN_PERIOD).state is LicenceState.INVALID
    refusal = policy.check(Capability.CHAT, now=IN_PERIOD)
    assert refusal is not None and refusal.code is RefusalCode.LICENCE_INVALID


# -- next slot (queued gapless renewal) ---------------------------------------

NEXT_START = datetime(2026, 11, 1, tzinfo=timezone.utc)
IN_NEXT_PERIOD = datetime(2026, 11, 15, tzinfo=timezone.utc)
AFTER_NEXT_PERIOD = datetime(2026, 12, 15, tzinfo=timezone.utc)


def _renewal_token() -> str:
    """A 'pro' licence that starts exactly when ``VALID_TOKEN`` ends."""
    import jwt as pyjwt

    return pyjwt.encode(
        {"iss": "intodata-nexus", "sub": SUBJECT, "lid": "renewal-1", "plan": "pro",
         "features": ["chat", "generation"], "limits": {"generations_per_period": 9},
         "nbf": int(NEXT_START.timestamp()),
         "exp": int(datetime(2026, 12, 1, tzinfo=timezone.utc).timestamp())},
        VECTORS["test_private_key_pem"], algorithm="EdDSA", headers={"kid": "test-1"},
    )


def _two_slot_policy(current: str | None, queued: str | None) -> EntitlementPolicy:
    return EntitlementPolicy(_Static(current), next_source=_Static(queued), public_keys=KEYS,
                             expected_subject=SUBJECT, enforce=True)


def test_next_slot_waits_while_current_is_valid() -> None:
    policy = _two_slot_policy(VALID_TOKEN, _renewal_token())
    status = policy.status(now=IN_PERIOD)
    assert status.state is LicenceState.VALID and status.claims.plan == "basic"
    queued = policy.next_status(now=IN_PERIOD)
    assert queued is not None and queued.state is LicenceState.NOT_YET_VALID
    assert queued.claims.plan == "pro"
    assert policy.check(Capability.CHAT, now=IN_PERIOD) is None


def test_next_slot_takes_over_once_it_starts() -> None:
    policy = _two_slot_policy(VALID_TOKEN, _renewal_token())
    status = policy.status(now=IN_NEXT_PERIOD)
    assert status.state is LicenceState.VALID and status.claims.plan == "pro"
    # Being served, it is no longer "queued".
    assert policy.next_status(now=IN_NEXT_PERIOD) is None
    assert policy.check(Capability.GENERATION, generations_used=8, now=IN_NEXT_PERIOD) is None


def test_expired_next_slot_reports_the_current_licence() -> None:
    policy = _two_slot_policy(VALID_TOKEN, _renewal_token())
    status = policy.status(now=AFTER_NEXT_PERIOD)
    assert status.state is LicenceState.EXPIRED and status.claims.plan == "basic"


def test_invalid_next_slot_never_grants() -> None:
    policy = _two_slot_policy(None, "not.a.jwt")
    assert policy.status(now=IN_PERIOD).state is LicenceState.MISSING
    queued = policy.next_status(now=IN_PERIOD)
    assert queued is not None and queued.state is LicenceState.INVALID


def test_no_next_source_keeps_single_slot_behaviour() -> None:
    policy = _policy(VALID_TOKEN)
    assert policy.next_status(now=IN_PERIOD) is None
    assert policy.status(now=IN_NEXT_PERIOD).state is LicenceState.EXPIRED


def test_empty_next_slot_is_none() -> None:
    assert _two_slot_policy(VALID_TOKEN, None).next_status(now=IN_PERIOD) is None


def test_next_licence_path_is_a_sibling() -> None:
    assert next_licence_path("/etc/x/licence.jwt") == Path("/etc/x/licence.next.jwt")


def test_file_source_sees_a_replace_with_an_equal_mtime(tmp_path: Path) -> None:
    """An atomic replace changes the inode; that alone must invalidate the cache."""
    import os

    path = tmp_path / "licence.jwt"
    path.write_text("first\n")
    source = FileLicenceSource(path)
    assert source.read_token() == "first"
    mtime_ns = path.stat().st_mtime_ns
    tmp = tmp_path / "new.tmp"
    tmp.write_text("secon\n")  # same size as "first"
    os.utime(tmp, ns=(mtime_ns, mtime_ns))
    os.replace(tmp, path)
    assert source.read_token() == "secon"
