"""Auto-refresh Claude Code OAuth tokens before they expire.

The Claude Code CLI stores OAuth credentials in ~/.claude/.credentials.json.
When running as a non-interactive service, the CLI cannot refresh expired
tokens on its own. This module handles that automatically.

THREE PROPERTIES THIS MODULE MUST HOLD, each of which it previously did not:

* **The path is resolved when it is used, not when it is imported.** The
  container sets ``HOME=/app/.home`` from ``entrypoint.sh`` before dropping to
  ``appuser``, so a module-level ``Path.home()`` happened to be right -- but a
  test that monkeypatches ``HOME``, or any caller that runs before the drop,
  silently addressed the wrong file and reported "no credentials" for a account
  that was logged in.

* **The write is atomic.** ``_CREDS_PATH.write_text(...)`` truncates the real
  file and then writes. A crash, a full disk, or a container stop in that window
  leaves a truncated credentials file, and the only recovery is an interactive
  ``claude login`` -- on a headless VPS, at the moment doc generation needs it.

* **Concurrent refreshes are serialised.** Anthropic ROTATES the refresh token:
  line ``oauth["refreshToken"] = data["refresh_token"]`` replaces the old one,
  and the old one stops working. Two documentation runs for two different
  projects can start at once (the generation lock is per project, not global);
  both see an expiring token, both POST, and the second response invalidates the
  first. One run then holds a dead refresh token and the next expiry needs a
  manual re-login. An exclusive lock around read-decide-write makes the second
  caller re-read the freshly written token instead of racing for a new one.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import time
from collections.abc import MutableMapping
from dataclasses import dataclass
from pathlib import Path

import httpx

try:  # POSIX only; absent on Windows, where we degrade to no locking.
    import fcntl
except ImportError:  # pragma: no cover - not exercised on Linux/macOS
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

ANTHROPIC_TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
CLAUDE_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
REFRESH_MARGIN_MS = 5 * 60 * 1000  # refresh 5 minutes before expiry
CREDENTIALS_MODE = 0o600
LOCK_TIMEOUT_SECONDS = 30.0

#: Environment variables that authenticate Claude Code without OAuth. Either one
#: makes an absent credentials file a valid configuration rather than a fault.
API_KEY_ENV_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

#: Relocates the CLI's whole config directory -- credentials AND `.claude.json`.
CONFIG_DIR_ENV_VAR = "CLAUDE_CONFIG_DIR"

MODE_OAUTH = "oauth"
MODE_API_KEY = "api_key"
MODE_NONE = "none"

#: The subcommand is `claude auth login`, NOT `claude login`. A bare `claude
#: login` is parsed as a PROMPT -- the CLI answers "It looks like you typed
#: 'login' as a regular message", exits 0, and authenticates nothing.
_LOGIN_REMEDY = (
    "No Claude Code credentials are configured. Run "
    "`docker compose exec -it -u appuser mcp-server claude auth login --claudeai` "
    "to sign in with the Claude subscription; the token is written to the "
    "persistent claude_home volume. Verify with `claude auth status`. "
    "Alternatively set ANTHROPIC_API_KEY to bill the API account instead."
)


@dataclass(frozen=True)
class AuthState:
    """How the Claude Code CLI will authenticate on the next spawn."""

    mode: str
    credentials_path: Path

    @property
    def configured(self) -> bool:
        return self.mode != MODE_NONE

    @property
    def remedy(self) -> str:
        """Operator-facing instruction; empty when auth is already configured."""
        return "" if self.configured else _LOGIN_REMEDY


def credentials_path() -> Path:
    """Where the Claude Code CLI keeps its OAuth credentials, resolved now.

    ``CLAUDE_CONFIG_DIR`` wins when set, because it is what the CLI itself
    obeys. The container sets it so that `.claude.json` lands in the persistent
    volume alongside the credentials; a preflight still hardcoding
    ``$HOME/.claude`` would silently check a different directory from the one
    the login wrote to, and report "not configured" for an authenticated host.
    """
    configured = os.environ.get(CONFIG_DIR_ENV_VAR, "").strip()
    base = Path(configured) if configured else Path.home() / ".claude"
    return base / ".credentials.json"


def _api_key_present() -> bool:
    return any(os.environ.get(name, "").strip() for name in API_KEY_ENV_VARS)


def describe_auth() -> AuthState:
    """Report the credential mode without contacting the network.

    Callers use this for a preflight that can name the fix. ``ensure_token_fresh``
    deliberately returns True when no credentials file exists -- API-key auth is
    legitimate -- so on its own it cannot tell "authenticated by API key" from
    "not configured at all", and the run failed later inside the subprocess with
    a worse message than this one.
    """
    path = credentials_path()
    if path.exists():
        return AuthState(mode=MODE_OAUTH, credentials_path=path)
    if _api_key_present():
        return AuthState(mode=MODE_API_KEY, credentials_path=path)
    return AuthState(mode=MODE_NONE, credentials_path=path)


def apply_auth_env(env: MutableMapping[str, str]) -> AuthState:
    """Scrub conflicting credentials from ``env`` and report the surviving mode.

    ``ANTHROPIC_API_KEY`` is set on the mcp-server service for the harness's
    ``anthropic/<model>`` adapter, which is a different lane with different
    billing. The Claude Code CLI PREFERS that variable over an OAuth login, so
    inheriting the service environment unchanged makes a deployment that was
    deliberately put on a subscription silently bill the API account instead --
    and nothing in the run output says which one paid.

    Resolution is by mode, not by preference order at the call site: when OAuth
    credentials exist they win and the key is removed for this subprocess only.
    With no credentials file the key is left in place, because then it is the
    only thing authenticating the CLI.
    """
    state = describe_auth()
    if state.mode == MODE_OAUTH:
        for name in API_KEY_ENV_VARS:
            env.pop(name, None)
    return state


class _CredentialsLock:
    """Exclusive advisory lock over the credentials file.

    Held across read-decide-write so a second caller observes the refreshed
    token rather than issuing a competing refresh (see the module docstring).
    Lock failure is logged and the refresh proceeds unlocked: a serialisation
    that cannot be obtained is a worse reason to block a documentation run than
    the race it protects against, and the atomic write still holds.
    """

    def __init__(self, path: Path) -> None:
        self._lock_path = path.with_suffix(path.suffix + ".lock")
        self._handle = None

    def __enter__(self) -> "_CredentialsLock":
        if fcntl is None:
            return self
        try:
            self._lock_path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = open(self._lock_path, "w")  # noqa: SIM115 - closed in __exit__
        except OSError as exc:
            logger.warning("Could not open credentials lock %s: %s", self._lock_path, exc)
            self._handle = None
            return self

        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    logger.warning("Credentials lock failed: %s", exc)
                    return self
                if time.monotonic() >= deadline:
                    logger.warning(
                        "Timed out after %.0fs waiting for the credentials lock; "
                        "refreshing unlocked.",
                        LOCK_TIMEOUT_SECONDS,
                    )
                    return self
                time.sleep(0.2)

    def __exit__(self, *exc_info: object) -> None:
        if self._handle is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None


def _write_credentials_atomically(path: Path, creds: dict) -> None:
    """Replace ``path`` with ``creds`` via a same-directory temp file.

    ``os.replace`` is atomic within a filesystem, so a reader either sees the
    whole old file or the whole new one -- never the truncation window that a
    direct ``write_text`` opens over the only copy of the token.
    """
    tmp_path = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(creds, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_path, CREDENTIALS_MODE)
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def ensure_token_fresh() -> bool:
    """Check if the Claude OAuth token is fresh; refresh if expired.

    Returns True if the token is valid (or was successfully refreshed), or if no
    OAuth credentials file exists at all (API-key auth needs no refresh). Returns
    False only when OAuth credentials are present but unusable. Use
    :func:`describe_auth` to tell "API key" from "nothing configured".
    """
    creds_file = credentials_path()
    if not creds_file.exists():
        logger.debug("No OAuth credentials file at %s -- assuming API key auth", creds_file)
        return True

    with _CredentialsLock(creds_file):
        return _refresh_if_needed(creds_file)


def _refresh_if_needed(creds_file: Path) -> bool:
    try:
        creds = json.loads(creds_file.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to read credentials: %s", e)
        return False

    oauth = creds.get("claudeAiOauth")
    if not oauth:
        # A credentials file in a shape this module does not know. It is NOT a
        # failure: `claude setup-token` and newer CLI releases manage their own
        # renewal, and this helper exists only because an older headless build
        # could not. Blocking here would refuse a run whose auth is fine, and
        # the CLI reports a genuine auth problem far better than we can guess
        # one. Let it through and let the subprocess be the judge.
        logger.warning(
            "Credentials at %s have no claudeAiOauth section; leaving renewal "
            "to the CLI. Check `claude auth status` if the run fails to "
            "authenticate.",
            creds_file,
        )
        return True

    now_ms = int(time.time() * 1000)
    expires_at = oauth.get("expiresAt", 0)

    if expires_at > now_ms + REFRESH_MARGIN_MS:
        # Token still valid. Under the lock this also covers the case where a
        # concurrent caller refreshed while we waited.
        return True

    refresh_token = oauth.get("refreshToken")
    if not refresh_token:
        logger.error("No refresh token available -- manual re-login required")
        return False

    logger.info("OAuth token expired or expiring soon, refreshing...")

    try:
        resp = httpx.post(
            ANTHROPIC_TOKEN_URL,
            json={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CLAUDE_CLIENT_ID,
            },
            timeout=30.0,
        )
    except httpx.HTTPError as e:
        logger.error("Token refresh request failed: %s", e)
        return False

    if resp.status_code != 200:
        logger.error(
            "Token refresh failed (HTTP %d): %s", resp.status_code, resp.text[:300]
        )
        return False

    data = resp.json()
    oauth["accessToken"] = data["access_token"]
    oauth["refreshToken"] = data["refresh_token"]
    oauth["expiresAt"] = now_ms + data.get("expires_in", 28800) * 1000

    try:
        _write_credentials_atomically(creds_file, creds)
        logger.info(
            "OAuth token refreshed successfully (new expiry in %ss)",
            data.get("expires_in", "?"),
        )
    except OSError as e:
        logger.error("Failed to write refreshed credentials: %s", e)
        return False

    return True
