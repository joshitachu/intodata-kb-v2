"""Credential handling for the Claude Code CLI lane.

Covers the three properties named in ``services/token_refresh.py``: the path is
resolved at call time, the write is atomic, and concurrent refreshes are
serialised so Anthropic's refresh-token rotation cannot strand a run with a
dead token.
"""

from __future__ import annotations

import json
import os
import stat
import time

import pytest

from mcp_server.services import token_refresh
from mcp_server.services.token_refresh import (
    MODE_API_KEY,
    MODE_NONE,
    MODE_OAUTH,
    apply_auth_env,
    credentials_path,
    describe_auth,
    ensure_token_fresh,
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated HOME, so no test reads the developer's real credentials."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv(token_refresh.CONFIG_DIR_ENV_VAR, raising=False)
    for name in token_refresh.API_KEY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def _write_creds(home, *, expires_in_ms: int, refresh_token: str = "refresh-1") -> None:
    path = home / ".claude" / ".credentials.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "access-1",
                    "refreshToken": refresh_token,
                    "expiresAt": int(time.time() * 1000) + expires_in_ms,
                }
            }
        )
    )
    os.chmod(path, 0o600)


def test_credentials_path_follows_home_at_call_time(home, monkeypatch, tmp_path):
    """A module-level Path.home() would pin the path at import.

    The container sets HOME from entrypoint.sh before dropping privileges, so an
    import-time constant addressed the right file only by luck of ordering.
    """
    assert credentials_path() == home / ".claude" / ".credentials.json"

    other = tmp_path / "elsewhere"
    monkeypatch.setenv("HOME", str(other))
    assert credentials_path() == other / ".claude" / ".credentials.json"


def test_config_dir_env_var_wins_over_home(home, monkeypatch, tmp_path):
    """CLAUDE_CONFIG_DIR is what the CLI obeys, so the preflight must too.

    The container sets it so `.claude.json` lands in the persistent volume
    beside the credentials. A preflight still hardcoding $HOME/.claude would
    check a different directory from the one the login wrote to and report
    "not configured" for a host that is authenticated.
    """
    relocated = tmp_path / "cfg"
    monkeypatch.setenv(token_refresh.CONFIG_DIR_ENV_VAR, str(relocated))
    assert credentials_path() == relocated / ".credentials.json"

    relocated.mkdir(parents=True)
    (relocated / ".credentials.json").write_text("{}")
    assert describe_auth().mode == MODE_OAUTH


def test_blank_config_dir_falls_back_to_home(home, monkeypatch):
    monkeypatch.setenv(token_refresh.CONFIG_DIR_ENV_VAR, "   ")
    assert credentials_path() == home / ".claude" / ".credentials.json"


class TestDescribeAuth:
    def test_oauth_when_credentials_file_exists(self, home):
        _write_creds(home, expires_in_ms=3_600_000)
        assert describe_auth().mode == MODE_OAUTH

    def test_api_key_when_env_var_set(self, home, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        state = describe_auth()
        assert state.mode == MODE_API_KEY
        assert state.configured is True
        assert state.remedy == ""

    def test_blank_api_key_is_not_configuration(self, home, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
        assert describe_auth().mode == MODE_NONE

    def test_nothing_configured_names_the_fix(self, home):
        state = describe_auth()
        assert state.mode == MODE_NONE
        assert state.configured is False
        # The remedy has to be actionable on a headless VPS, which means naming
        # the exact command rather than "authenticate first".
        assert "claude auth login" in state.remedy
        assert "-u appuser" in state.remedy
        # `claude login` (no `auth`) is parsed by the CLI as a PROMPT: it replies
        # "It looks like you typed 'login' as a regular message", exits 0, and
        # authenticates nothing. An operator following that literally sees a
        # success exit code and no login. Verified against Claude Code 2.1.280.
        assert "claude login" not in state.remedy

    def test_absent_credentials_still_pass_ensure_token_fresh(self, home):
        """API-key auth needs no refresh, so absence is not a refresh failure.

        This is exactly why describe_auth() exists as a separate preflight: on
        its own ensure_token_fresh() cannot distinguish "API key" from "nothing".
        """
        assert ensure_token_fresh() is True
        assert describe_auth().configured is False


class TestEnsureTokenFresh:
    def test_valid_token_makes_no_network_call(self, home, monkeypatch):
        _write_creds(home, expires_in_ms=3_600_000)

        def _explode(*args, **kwargs):
            raise AssertionError("refresh attempted for a still-valid token")

        monkeypatch.setattr(token_refresh.httpx, "post", _explode)
        assert ensure_token_fresh() is True

    def test_expiring_token_is_refreshed_and_persisted(self, home, monkeypatch):
        # Inside REFRESH_MARGIN_MS: still valid, but due for renewal.
        _write_creds(home, expires_in_ms=60_000)

        class _Resp:
            status_code = 200

            @staticmethod
            def json():
                return {
                    "access_token": "access-2",
                    "refresh_token": "refresh-2",
                    "expires_in": 28800,
                }

        monkeypatch.setattr(token_refresh.httpx, "post", lambda *a, **k: _Resp())
        assert ensure_token_fresh() is True

        saved = json.loads((home / ".claude" / ".credentials.json").read_text())
        assert saved["claudeAiOauth"]["accessToken"] == "access-2"
        assert saved["claudeAiOauth"]["refreshToken"] == "refresh-2"

    def test_refreshed_file_keeps_owner_only_permissions(self, home, monkeypatch):
        _write_creds(home, expires_in_ms=60_000)

        class _Resp:
            status_code = 200

            @staticmethod
            def json():
                return {"access_token": "a", "refresh_token": "r", "expires_in": 100}

        monkeypatch.setattr(token_refresh.httpx, "post", lambda *a, **k: _Resp())
        ensure_token_fresh()

        mode = stat.S_IMODE(os.stat(home / ".claude" / ".credentials.json").st_mode)
        assert mode == 0o600, f"credentials world-readable after refresh: {oct(mode)}"

    def test_failed_refresh_leaves_the_original_token_intact(self, home, monkeypatch):
        """The pre-fix write truncated the only copy before writing.

        A failure mid-write left an unusable file whose only recovery was an
        interactive re-login.
        """
        _write_creds(home, expires_in_ms=60_000, refresh_token="refresh-original")
        before = (home / ".claude" / ".credentials.json").read_text()

        class _Resp:
            status_code = 500
            text = "upstream unavailable"

        monkeypatch.setattr(token_refresh.httpx, "post", lambda *a, **k: _Resp())
        assert ensure_token_fresh() is False
        assert (home / ".claude" / ".credentials.json").read_text() == before

    def test_write_failure_leaves_no_temp_file_behind(self, home, monkeypatch):
        _write_creds(home, expires_in_ms=60_000)

        class _Resp:
            status_code = 200

            @staticmethod
            def json():
                return {"access_token": "a", "refresh_token": "r", "expires_in": 100}

        monkeypatch.setattr(token_refresh.httpx, "post", lambda *a, **k: _Resp())

        def _fail_replace(src, dst):
            raise OSError("disk full")

        monkeypatch.setattr(token_refresh.os, "replace", _fail_replace)
        assert ensure_token_fresh() is False

        leftovers = list((home / ".claude").glob(".credentials.json.tmp-*"))
        assert leftovers == [], f"temp credential files left on disk: {leftovers}"


class TestUnrecognisedCredentialFormat:
    def test_unknown_credential_shape_does_not_block_the_run(self, home, monkeypatch):
        """`claude setup-token` and newer releases manage their own renewal.

        This module exists only because an older headless build could not. A
        credentials file it cannot parse means "not my job", not "auth broken" --
        refusing the run there would block a deployment whose auth is fine.
        """
        path = home / ".claude" / ".credentials.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"someNewerFormat": {"token": "x"}}))

        def _explode(*args, **kwargs):
            raise AssertionError("attempted to refresh a format we do not own")

        monkeypatch.setattr(token_refresh.httpx, "post", _explode)
        assert ensure_token_fresh() is True
        assert describe_auth().mode == MODE_OAUTH

    def test_unreadable_credentials_still_fail(self, home):
        """Distinct from the above: this file IS ours and IS broken."""
        path = home / ".claude" / ".credentials.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ not json")
        assert ensure_token_fresh() is False


class TestApplyAuthEnv:
    """Which credential the spawned CLI actually uses, and therefore who pays.

    ANTHROPIC_API_KEY is set on the mcp-server service for the harness's
    anthropic/<model> adapter. The Claude Code CLI prefers it over an OAuth
    login, so a deployment deliberately put on a subscription would bill the API
    account instead, with nothing in the run output naming the difference.
    """

    def test_oauth_login_wins_over_the_harness_api_key(self, home, monkeypatch):
        _write_creds(home, expires_in_ms=3_600_000)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-harness")

        env = {"ANTHROPIC_API_KEY": "sk-harness", "PATH": "/usr/bin"}
        state = apply_auth_env(env)

        assert state.mode == MODE_OAUTH
        assert "ANTHROPIC_API_KEY" not in env
        assert env["PATH"] == "/usr/bin", "unrelated environment was disturbed"

    def test_api_key_survives_when_there_is_no_oauth_login(self, home, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-harness")

        env = {"ANTHROPIC_API_KEY": "sk-harness"}
        state = apply_auth_env(env)

        assert state.mode == MODE_API_KEY
        assert env["ANTHROPIC_API_KEY"] == "sk-harness", (
            "the key is the only thing authenticating the CLI here; removing it "
            "turns a working API-key deployment into an unauthenticated one"
        )

    def test_scrubbing_does_not_mutate_the_parent_process(self, home, monkeypatch):
        _write_creds(home, expires_in_ms=3_600_000)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-harness")

        apply_auth_env({**os.environ})

        assert os.environ["ANTHROPIC_API_KEY"] == "sk-harness", (
            "the harness adapter shares this process and still needs the key"
        )


class TestConcurrentRefresh:
    def test_second_caller_sees_the_first_refresh_instead_of_racing(
        self, home, monkeypatch
    ):
        """Anthropic rotates refresh tokens, so a double refresh strands a run.

        The lock is held across read-decide-write. The second caller therefore
        re-reads a file whose expiry is already renewed and issues no request of
        its own.
        """
        _write_creds(home, expires_in_ms=60_000)
        calls: list[str] = []

        class _Resp:
            status_code = 200

            @staticmethod
            def json():
                return {
                    "access_token": "access-2",
                    "refresh_token": "refresh-2",
                    "expires_in": 28800,
                }

        def _post(*args, **kwargs):
            calls.append(kwargs.get("json", {}).get("refresh_token", ""))
            return _Resp()

        monkeypatch.setattr(token_refresh.httpx, "post", _post)

        assert ensure_token_fresh() is True
        assert ensure_token_fresh() is True

        assert calls == ["refresh-1"], (
            "the second call re-refreshed an already-renewed token; under "
            f"rotation that invalidates the first result. calls={calls}"
        )
