"""``POST /ops/control/licence`` and the control-key gate (plan slices 1 and 2).

The instance verifies a pushed token BEFORE storing it (Decision 3), so every
rejection must leave the licence file byte-for-byte unchanged, and a successful
install must be visible to the process policy at once, without a restart.
"""

from __future__ import annotations

import hmac
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import mcp_server.api.routes.ops_control as ops_control_mod
import mcp_server.licensing.install as install_mod
import mcp_server.licensing.runtime as runtime_mod
from mcp_server.config import config
from mcp_server.licensing.core import Capability, FileLicenceSource
from mcp_server.licensing.runtime import build_policy, get_policy, set_policy
from tests.test_licensing.licence_fixtures import (
    PUBLIC_KEYS,
    SUBJECT,
    expired_token,
    issue_token,
)

CONTROL_KEY = "c" * 64
URL = "/ops/control/licence"
AUTH = {"Authorization": f"Bearer {CONTROL_KEY}"}


@pytest.fixture
def licence_file(tmp_path, monkeypatch) -> Path:
    """A licence dir under tmp_path, wired exactly like production minus the key ring.

    The golden test key replaces the pinned ring (in the install service and
    the runtime), and the process policy is the production ``build_policy``
    reading both real slot files, so "the policy sees the new file" is really
    tested.
    """
    licence_dir = tmp_path / "licence"
    licence_dir.mkdir()
    path = licence_dir / "licence.jwt"
    monkeypatch.setattr(config, "ops_control_key", CONTROL_KEY)
    monkeypatch.setattr(config, "licence_file", str(path))
    monkeypatch.setattr(config, "licence_client_slug", SUBJECT)
    monkeypatch.setattr(install_mod, "PINNED_PUBLIC_KEYS", PUBLIC_KEYS)
    monkeypatch.setattr(runtime_mod, "PINNED_PUBLIC_KEYS", PUBLIC_KEYS)
    monkeypatch.setattr(config, "licence_enforcement", "enforce")
    set_policy(build_policy())
    yield path
    set_policy(None)


def _next(path: Path) -> Path:
    return path.with_name("licence.next.jwt")


def _write(path: Path, token: str) -> None:
    path.write_text(token + "\n", encoding="utf-8")


def _snapshot(path: Path) -> tuple[bytes, int] | None:
    if not path.exists():
        return None
    return path.read_bytes(), path.stat().st_mtime_ns


async def test_valid_token_is_installed_and_policy_sees_it(client, licence_file):
    token = issue_token(generations=5)

    resp = await client.post(URL, json={"token": token}, headers=AUTH)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["installed"] is True
    assert body["slot"] == "current"
    assert body["next_licence"] is None
    assert body["licence"]["state"] == "valid"
    assert body["licence"]["limits"] == {"generations_per_period": 5}
    assert token not in resp.text
    assert licence_file.read_text(encoding="utf-8").strip() == token
    assert (licence_file.stat().st_mode & 0o777) == 0o640
    # No temp file left behind in the shared directory.
    assert sorted(p.name for p in licence_file.parent.iterdir()) == ["licence.jwt"]
    assert get_policy().status().state.value == "valid"


async def test_not_yet_valid_token_goes_to_current_when_nothing_is_valid(client, licence_file):
    """No licence in force: a future licence takes the current slot (nothing to protect)."""
    now = datetime.now(timezone.utc)
    token = issue_token(start=now + timedelta(days=10), end=now + timedelta(days=40))

    resp = await client.post(URL, json={"token": token}, headers=AUTH)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["slot"] == "current"
    assert body["licence"]["state"] == "not_yet_valid"
    assert body["next_licence"] is None
    assert licence_file.read_text(encoding="utf-8").strip() == token
    assert not _next(licence_file).exists()


async def test_not_yet_valid_token_replaces_an_expired_current(client, licence_file):
    _write(licence_file, expired_token())
    now = datetime.now(timezone.utc)
    token = issue_token(start=now + timedelta(days=1), end=now + timedelta(days=31))

    resp = await client.post(URL, json={"token": token}, headers=AUTH)

    assert resp.status_code == 200, resp.text
    assert resp.json()["slot"] == "current"
    assert licence_file.read_text(encoding="utf-8").strip() == token
    assert not _next(licence_file).exists()


async def test_renewal_is_queued_behind_a_valid_licence(client, licence_file):
    """The reported bug: a gapless renewal used to overwrite the running licence (-> 402)."""
    now = datetime.now(timezone.utc)
    current_end = now + timedelta(days=5)
    current = issue_token(end=current_end, generations=1)
    assert (await client.post(URL, json={"token": current}, headers=AUTH)).status_code == 200
    before = _snapshot(licence_file)
    renewal = issue_token(start=current_end, end=current_end + timedelta(days=30), generations=7)

    resp = await client.post(URL, json={"token": renewal}, headers=AUTH)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["slot"] == "next"
    assert body["licence"]["state"] == "valid"
    assert body["licence"]["limits"] == {"generations_per_period": 1}
    assert body["next_licence"]["state"] == "not_yet_valid"
    assert body["next_licence"]["limits"] == {"generations_per_period": 7}
    assert renewal not in resp.text
    assert _snapshot(licence_file) == before
    assert _next(licence_file).read_text(encoding="utf-8").strip() == renewal
    assert (_next(licence_file).stat().st_mode & 0o777) == 0o640

    policy = get_policy()
    assert policy.check(Capability.CHAT) is None
    assert policy.status().claims.limits == {"generations_per_period": 1}
    # Past the current licence's end, the queued renewal is in force at once.
    later = current_end + timedelta(days=1)
    served = policy.status(now=later)
    assert served.state.value == "valid"
    assert served.claims.limits == {"generations_per_period": 7}
    assert policy.check(Capability.CHAT, now=later) is None
    assert policy.next_status(now=later) is None


async def test_due_renewal_is_promoted_at_the_next_install(client, licence_file):
    now = datetime.now(timezone.utc)
    started = issue_token(start=now - timedelta(hours=1), generations=4)
    _write(licence_file, expired_token())
    _write(_next(licence_file), started)
    newer = issue_token(start=now + timedelta(days=29), end=now + timedelta(days=59))

    resp = await client.post(URL, json={"token": newer}, headers=AUTH)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Promotion ran first, so a VALID licence was in force and the push was queued.
    assert body["slot"] == "next"
    assert licence_file.read_text(encoding="utf-8").strip() == started
    assert _next(licence_file).read_text(encoding="utf-8").strip() == newer
    assert body["licence"]["limits"] == {"generations_per_period": 4}


def test_promote_if_due_moves_next_into_current(licence_file):
    now = datetime.now(timezone.utc)
    started = issue_token(start=now - timedelta(hours=1))
    _write(licence_file, expired_token())
    _write(_next(licence_file), started)
    reader = FileLicenceSource(licence_file)
    reader.read_token()

    runtime_mod.promote_if_due()

    assert licence_file.read_text(encoding="utf-8").strip() == started
    assert not _next(licence_file).exists()
    assert reader.read_token() == started
    assert (licence_file.stat().st_mode & 0o777) == 0o640
    assert get_policy().status().state.value == "valid"


def test_promote_if_due_leaves_a_waiting_renewal_alone(licence_file):
    now = datetime.now(timezone.utc)
    current = issue_token()
    renewal = issue_token(start=now + timedelta(days=29), end=now + timedelta(days=59))
    _write(licence_file, current)
    _write(_next(licence_file), renewal)

    assert install_mod.LicenceInstallService.from_config().promote_if_due() is False

    assert licence_file.read_text(encoding="utf-8").strip() == current
    assert _next(licence_file).read_text(encoding="utf-8").strip() == renewal


def test_promote_if_due_clears_a_dead_next_slot(licence_file):
    _write(licence_file, issue_token())
    _write(_next(licence_file), expired_token())

    assert install_mod.LicenceInstallService.from_config().promote_if_due() is False

    assert not _next(licence_file).exists()


def test_promote_if_due_never_blocks_startup(licence_file, monkeypatch):
    monkeypatch.setattr(config, "licence_file", str(licence_file.parent / "absent" / "l.jwt"))
    runtime_mod.promote_if_due()  # must not raise
    runtime_mod.log_startup_state()


async def test_revoke_by_replace_drops_a_queued_renewal_with_a_gap(client, licence_file):
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=20)
    assert (await client.post(URL, json={"token": issue_token(end=end)}, headers=AUTH)).status_code == 200
    renewal = issue_token(start=end, end=end + timedelta(days=30))
    assert (await client.post(URL, json={"token": renewal}, headers=AUTH)).json()["slot"] == "next"
    shorter = issue_token(end=now + timedelta(days=2), generations=0)

    resp = await client.post(URL, json={"token": shorter}, headers=AUTH)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["slot"] == "current"
    assert body["next_licence"] is None
    assert licence_file.read_text(encoding="utf-8").strip() == shorter
    assert not _next(licence_file).exists()


async def test_mid_period_replace_keeps_a_continuing_renewal(client, licence_file):
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=20)
    assert (await client.post(URL, json={"token": issue_token(end=end)}, headers=AUTH)).status_code == 200
    renewal = issue_token(start=end, end=end + timedelta(days=30))
    assert (await client.post(URL, json={"token": renewal}, headers=AUTH)).json()["slot"] == "next"
    upgrade = issue_token(end=end, generations=50)

    resp = await client.post(URL, json={"token": upgrade}, headers=AUTH)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["slot"] == "current"
    assert body["licence"]["limits"] == {"generations_per_period": 50}
    assert body["next_licence"]["state"] == "not_yet_valid"
    assert _next(licence_file).read_text(encoding="utf-8").strip() == renewal


async def test_stale_temp_files_are_swept(client, licence_file):
    stale = licence_file.parent / ".licence.jwt.crashed.tmp"
    stale_next = licence_file.parent / ".licence.next.jwt.crashed.tmp"
    fresh = licence_file.parent / ".licence.jwt.inflight.tmp"
    unrelated = licence_file.parent / "notes.tmp"
    for p in (stale, stale_next, fresh, unrelated):
        p.write_text("x")
    old = time.time() - install_mod.STALE_TEMP_SECONDS - 60
    for p in (stale, stale_next, unrelated):
        os.utime(p, (old, old))

    resp = await client.post(URL, json={"token": issue_token()}, headers=AUTH)

    assert resp.status_code == 200, resp.text
    assert not stale.exists() and not stale_next.exists()
    assert fresh.exists() and unrelated.exists()


async def test_same_tick_renewal_is_visible_to_a_caching_reader(client, licence_file):
    """The readers cache by mtime; a replacement with an equal mtime would be invisible.

    Simulate the worst case (coarse filesystem, or an old file stamped in the
    future) by pushing the current file's mtime ahead of the clock. The new
    file must still be seen, both by this process and by a separate reader
    that cached the old file (the RAG backend's ``FileLicenceSource``).
    """
    first = issue_token(generations=1)
    assert (await client.post(URL, json={"token": first}, headers=AUTH)).status_code == 200
    future_ns = licence_file.stat().st_mtime_ns + 5_000_000_000
    os.utime(licence_file, ns=(future_ns, future_ns))
    other_reader = FileLicenceSource(licence_file)
    assert other_reader.read_token() == first
    assert get_policy().status().claims.limits == {"generations_per_period": 1}

    second = issue_token(generations=2)
    resp = await client.post(URL, json={"token": second}, headers=AUTH)

    assert resp.status_code == 200, resp.text
    assert resp.json()["licence"]["limits"] == {"generations_per_period": 2}
    assert licence_file.stat().st_mtime_ns > future_ns
    assert other_reader.read_token() == second


@pytest.mark.parametrize(
    ("make_token", "state", "reason_fragment"),
    [
        (lambda: issue_token()[:-6] + "AAAAAA", "invalid", "rejected"),
        (lambda: issue_token(subject="another-client"), "invalid", "different client"),
        (expired_token, "expired", "expired"),
        (lambda: "", "missing", "no licence"),
        (lambda: "not.a.jwt", "invalid", "well-formed"),
    ],
    ids=["forged-signature", "wrong-slug", "expired", "empty", "garbage"],
)
async def test_rejected_token_leaves_the_file_unchanged(
    client, licence_file, make_token, state, reason_fragment
):
    current = issue_token()
    assert (await client.post(URL, json={"token": current}, headers=AUTH)).status_code == 200
    before = _snapshot(licence_file)

    resp = await client.post(URL, json={"token": make_token()}, headers=AUTH)

    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "licence_rejected"
    assert detail["state"] == state
    assert reason_fragment in detail["reason"]
    assert _snapshot(licence_file) == before
    assert get_policy().status().state.value == "valid"


async def test_token_signed_by_an_unpinned_key_is_rejected(client, licence_file, monkeypatch):
    """With the real pinned ring, a test-key token is foreign: nothing is written."""
    from mcp_server.licensing.keys import PINNED_PUBLIC_KEYS

    monkeypatch.setattr(install_mod, "PINNED_PUBLIC_KEYS", PINNED_PUBLIC_KEYS)

    resp = await client.post(URL, json={"token": issue_token()}, headers=AUTH)

    assert resp.status_code == 422
    assert resp.json()["detail"]["reason"] == "licence was signed by an unknown key"
    assert not licence_file.exists()


async def test_oversize_token_is_refused_before_verification(client, licence_file, monkeypatch):
    def _must_not_verify(*_args, **_kwargs):
        raise AssertionError("an oversize token must not reach the verifier")

    monkeypatch.setattr(install_mod, "verify_token", _must_not_verify)

    resp = await client.post(
        URL, json={"token": "x" * (install_mod.MAX_TOKEN_BYTES + 1)}, headers=AUTH
    )

    assert resp.status_code == 413
    assert resp.json()["detail"]["code"] == "licence_too_large"
    assert not licence_file.exists()


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0,
                    reason="needs POSIX permissions and a non-root user")
async def test_unwritable_directory_is_503_and_keeps_the_old_file(client, licence_file):
    current = issue_token()
    assert (await client.post(URL, json={"token": current}, headers=AUTH)).status_code == 200
    before = _snapshot(licence_file)
    licence_file.parent.chmod(0o550)
    try:
        resp = await client.post(URL, json={"token": issue_token(generations=9)}, headers=AUTH)
    finally:
        licence_file.parent.chmod(0o750)

    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"]["code"] == "licence_store_unwritable"
    assert _snapshot(licence_file) == before


async def test_missing_directory_is_503(client, licence_file, monkeypatch):
    monkeypatch.setattr(config, "licence_file", str(licence_file.parent / "absent" / "l.jwt"))

    resp = await client.post(URL, json={"token": issue_token()}, headers=AUTH)

    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "licence_store_unwritable"


async def test_wrong_control_key_is_401_and_writes_nothing(client, licence_file):
    resp = await client.post(
        URL, json={"token": issue_token()}, headers={"Authorization": "Bearer wrong"}
    )

    assert resp.status_code == 401
    assert not licence_file.exists()


async def test_unset_control_key_is_503(client, licence_file, monkeypatch):
    monkeypatch.setattr(config, "ops_control_key", "")

    resp = await client.post(URL, json={"token": issue_token()}, headers=AUTH)

    assert resp.status_code == 503
    assert not licence_file.exists()


async def test_control_key_is_compared_in_constant_time(client, licence_file, monkeypatch):
    calls: list[tuple[object, object]] = []
    real = hmac.compare_digest

    def _spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(ops_control_mod.hmac, "compare_digest", _spy)

    resp = await client.post(URL, json={"token": ""}, headers={"Authorization": "Bearer nope"})

    assert resp.status_code == 401
    assert calls == [(b"nope", CONTROL_KEY.encode())]


async def test_scan_trigger_uses_the_same_gate(client, licence_file):
    """The pre-existing trigger routes share the dependency: 401 before any lookup."""
    resp = await client.post(
        "/ops/control/scan/any-project", headers={"Authorization": "Bearer wrong"}
    )

    assert resp.status_code == 401
