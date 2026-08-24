"""GitHub App authentication: App JWT + short-lived installation tokens.

This is the credential source for App-connected repos. Instead of storing a
long-lived PAT at rest, the backend stores only a non-secret installation id and
mints an installation token (~1h lifetime) on demand here, signing a short-lived
App JWT with the App private key. Tokens are cached per installation until shortly
before they expire.

github.com only (no GitHub Enterprise Server) for v1.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from jose import jwt

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"

# App JWT lifetime. GitHub rejects a JWT whose exp is more than 10 minutes out and
# is sensitive to clock skew, so we back-date iat and stay under the ceiling.
_JWT_LIFETIME = timedelta(minutes=9)
_JWT_CLOCK_SKEW = timedelta(seconds=60)

# Re-mint an installation token this long before its real expiry so an in-flight
# git operation never races the expiry boundary.
_TOKEN_EXPIRY_BUFFER = timedelta(minutes=5)


class GitHubAppConfigError(RuntimeError):
    """Raised when GitHub App settings are missing or malformed."""


@dataclass(frozen=True)
class _CachedToken:
    token: str
    expires_at: datetime  # timezone-aware UTC

    def is_fresh(self, now: datetime) -> bool:
        return self.expires_at - now > _TOKEN_EXPIRY_BUFFER


# installation_id -> _CachedToken. Guarded by _cache_lock.
_token_cache: dict[int, _CachedToken] = {}
_cache_lock = asyncio.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load_private_key() -> str:
    """Return the App private key PEM, preferring the file over the inline value."""
    from mcp_server.config import config

    if config.github_app_private_key_file:
        try:
            with open(config.github_app_private_key_file, encoding="utf-8") as fh:
                return fh.read()
        except OSError as exc:
            raise GitHubAppConfigError(
                f"MCP_GITHUB_APP_PRIVATE_KEY_FILE could not be read: {exc}"
            ) from exc
    if config.github_app_private_key:
        # Env vars commonly carry the PEM with literal "\n" escapes; restore them.
        return config.github_app_private_key.replace("\\n", "\n")
    raise GitHubAppConfigError(
        "GitHub App private key is not set. Set MCP_GITHUB_APP_PRIVATE_KEY or "
        "MCP_GITHUB_APP_PRIVATE_KEY_FILE."
    )


def github_app_oauth_configured() -> bool:
    """True when the App OAuth client (for user-authorization) is configured."""
    from mcp_server.config import config

    return bool(config.github_app_client_id and config.github_app_client_secret)


def github_app_configured() -> bool:
    """True when the App id, slug, and a private key source are all present."""
    from mcp_server.config import config

    return bool(
        config.github_app_id
        and config.github_app_slug
        and (config.github_app_private_key or config.github_app_private_key_file)
    )


def build_app_jwt(*, now: datetime | None = None) -> str:
    """Sign a short-lived RS256 App JWT (issuer = App id) for app-level calls."""
    from mcp_server.config import config

    if not config.github_app_id:
        raise GitHubAppConfigError("MCP_GITHUB_APP_ID is not set.")
    issued = (now or _now()) - _JWT_CLOCK_SKEW
    claims = {
        "iat": int(issued.timestamp()),
        "exp": int((issued + _JWT_LIFETIME + _JWT_CLOCK_SKEW).timestamp()),
        "iss": str(config.github_app_id),
    }
    return jwt.encode(claims, _load_private_key(), algorithm="RS256")


def _app_headers(app_jwt: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {app_jwt}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _parse_expires_at(raw: str | None) -> datetime:
    """Parse GitHub's ISO-8601 expiry; fall back to a conservative 55-minute window."""
    if raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            logger.warning("Unparseable installation token expires_at: %r", raw)
    return _now() + timedelta(minutes=55)


async def mint_installation_token(
    installation_id: int,
    *,
    now: datetime | None = None,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Return a valid installation token, minting (and caching) one if needed."""
    if not installation_id:
        raise GitHubAppConfigError("installation_id is required to mint a token.")
    current = now or _now()

    async with _cache_lock:
        cached = _token_cache.get(installation_id)
        if cached and cached.is_fresh(current):
            return cached.token

        app_jwt = build_app_jwt(now=current)
        owns_client = client is None
        client = client or httpx.AsyncClient(timeout=15.0)
        try:
            resp = await client.post(
                f"{_GITHUB_API}/app/installations/{installation_id}/access_tokens",
                headers=_app_headers(app_jwt),
            )
        finally:
            if owns_client:
                await client.aclose()
        resp.raise_for_status()
        body = resp.json()
        token = body.get("token")
        if not token:
            raise GitHubAppConfigError(
                f"GitHub returned no token for installation {installation_id}."
            )
        _token_cache[installation_id] = _CachedToken(
            token=token,
            expires_at=_parse_expires_at(body.get("expires_at")),
        )
        return token


async def list_installation_repositories(
    installation_id: int,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, object]]:
    """List repos an installation can access (for the connect repo-picker)."""
    token = await mint_installation_token(installation_id, client=client)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    repos: list[dict[str, object]] = []
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=15.0)
    try:
        page = 1
        while True:
            resp = await client.get(
                f"{_GITHUB_API}/installation/repositories",
                headers=headers,
                params={"per_page": 100, "page": page},
            )
            resp.raise_for_status()
            body = resp.json()
            batch = body.get("repositories", [])
            for repo in batch:
                repos.append(
                    {
                        "full_name": repo.get("full_name"),
                        "clone_url": repo.get("clone_url"),
                        "private": repo.get("private"),
                        "default_branch": repo.get("default_branch"),
                    }
                )
            # Stop once we have all of total_count or a short page.
            if len(batch) < 100 or len(repos) >= int(body.get("total_count", len(repos))):
                break
            page += 1
    finally:
        if owns_client:
            await client.aclose()
    return repos


async def installation_exists(
    installation_id: int,
    *,
    client: httpx.AsyncClient | None = None,
) -> bool:
    """True if the App installation still exists on GitHub, False if it is gone.

    An owner can uninstall the App at any time; GitHub never tells us unless we
    ask (or an ``installation`` webhook is delivered). ``GET /app/installations/{id}``
    answers 404 for an uninstalled/deleted installation. Transport and other HTTP
    errors are raised so callers can distinguish "definitely gone" from
    "could not reach GitHub" and never prune state on a transient failure.
    """
    app_jwt = build_app_jwt()
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=15.0)
    try:
        resp = await client.get(
            f"{_GITHUB_API}/app/installations/{installation_id}",
            headers=_app_headers(app_jwt),
        )
        if resp.status_code == 404:
            return False
        resp.raise_for_status()
        return True
    finally:
        if owns_client:
            await client.aclose()


async def forget_installation_token(installation_id: int) -> None:
    """Drop the cached installation token (call when an installation is gone)."""
    async with _cache_lock:
        _token_cache.pop(installation_id, None)


async def get_installation_account_login(
    installation_id: int,
    *,
    client: httpx.AsyncClient | None = None,
) -> str | None:
    """Best-effort: return the org/user login an installation belongs to."""
    app_jwt = build_app_jwt()
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=15.0)
    try:
        resp = await client.get(
            f"{_GITHUB_API}/app/installations/{installation_id}",
            headers=_app_headers(app_jwt),
        )
        resp.raise_for_status()
        account = resp.json().get("account") or {}
        return account.get("login")
    finally:
        if owns_client:
            await client.aclose()


async def list_app_installations(
    *,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, int | str | None]]:
    """List every live installation belonging to this configured GitHub App.

    This is an App-level operation and must only be exposed to the deployment
    Owner. It lets Nexus recover installations that already exist on GitHub but
    whose local ownership callback was missed (for example after restoring the
    database or changing the App setup URL).
    """
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=15.0)
    try:
        app_jwt = build_app_jwt()
        installations: list[dict[str, int | str | None]] = []
        page = 1
        while True:
            resp = await client.get(
                f"{_GITHUB_API}/app/installations",
                headers=_app_headers(app_jwt),
                params={"per_page": 100, "page": page},
            )
            resp.raise_for_status()
            items = resp.json()
            for item in items:
                account = item.get("account") or {}
                installations.append(
                    {
                        "installation_id": int(item["id"]),
                        "account_login": account.get("login"),
                    }
                )
            if len(items) < 100:
                break
            page += 1
        return installations
    finally:
        if owns_client:
            await client.aclose()


async def exchange_user_access_token(
    code: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Exchange an install/authorization code for a GitHub *user* access token."""
    from mcp_server.config import config

    if not (config.github_app_client_id and config.github_app_client_secret):
        raise GitHubAppConfigError(
            "MCP_GITHUB_APP_CLIENT_ID / MCP_GITHUB_APP_CLIENT_SECRET are not set; "
            "cannot verify installation ownership."
        )
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=15.0)
    try:
        resp = await client.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": config.github_app_client_id,
                "client_secret": config.github_app_client_secret,
                "code": code,
            },
        )
        resp.raise_for_status()
        token = resp.json().get("access_token")
        if not token:
            raise GitHubAppConfigError("GitHub did not return a user access token.")
        return token
    finally:
        if owns_client:
            await client.aclose()


async def user_can_access_installation(
    user_token: str,
    installation_id: int,
    *,
    client: httpx.AsyncClient | None = None,
) -> bool:
    """True if the installation appears in the user's own GET /user/installations."""
    headers = {
        "Authorization": f"Bearer {user_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=15.0)
    try:
        page = 1
        while True:
            resp = await client.get(
                f"{_GITHUB_API}/user/installations",
                headers=headers,
                params={"per_page": 100, "page": page},
            )
            resp.raise_for_status()
            body = resp.json()
            installations = body.get("installations", [])
            if any(int(i.get("id", 0)) == int(installation_id) for i in installations):
                return True
            total = int(body.get("total_count", len(installations)))
            if len(installations) < 100 or page * 100 >= total:
                return False
            page += 1
    finally:
        if owns_client:
            await client.aclose()


def install_url(state: str) -> str:
    """Build the GitHub App install redirect URL, carrying a signed state token."""
    from mcp_server.config import config
    from urllib.parse import urlencode

    if not config.github_app_slug:
        raise GitHubAppConfigError("MCP_GITHUB_APP_SLUG is not set.")
    query = urlencode({"state": state})
    return f"https://github.com/apps/{config.github_app_slug}/installations/new?{query}"


def clear_token_cache() -> None:
    """Test/ops hook: drop all cached installation tokens."""
    _token_cache.clear()
