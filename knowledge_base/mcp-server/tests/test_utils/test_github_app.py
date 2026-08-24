"""Tests for GitHub App JWT signing and installation-token minting."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt

from mcp_server.utils import github_app


def _rsa_keypair() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return priv, pub


_PRIV_PEM, _PUB_PEM = _rsa_keypair()


@pytest.fixture(autouse=True)
def _app_config(monkeypatch):
    from mcp_server.config import config

    monkeypatch.setattr(config, "github_app_id", "123456")
    monkeypatch.setattr(config, "github_app_slug", "my-test-app")
    monkeypatch.setattr(config, "github_app_private_key", _PRIV_PEM)
    monkeypatch.setattr(config, "github_app_private_key_file", "")
    github_app.clear_token_cache()
    yield
    github_app.clear_token_cache()


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._payload


class FakeClient:
    def __init__(self, post_payload: dict | None = None, get_payloads: list[dict] | None = None) -> None:
        self.post_payload = post_payload or {}
        self.get_payloads = get_payloads or []
        self.post_calls: list[str] = []
        self.get_calls: list[dict] = []

    async def post(self, url, **kwargs):
        self.post_calls.append(url)
        return FakeResponse(self.post_payload)

    async def get(self, url, **kwargs):
        self.get_calls.append(kwargs.get("params", {}))
        payload = self.get_payloads[len(self.get_calls) - 1]
        return FakeResponse(payload)


def test_build_app_jwt_has_expected_claims():
    token = github_app.build_app_jwt()
    claims = jwt.decode(
        token,
        _PUB_PEM,
        algorithms=["RS256"],
        options={"verify_aud": False},
    )
    assert claims["iss"] == "123456"
    assert claims["exp"] > claims["iat"]
    # exp must stay under GitHub's 10-minute ceiling from iat.
    assert claims["exp"] - claims["iat"] <= 10 * 60


def test_build_app_jwt_requires_app_id(monkeypatch):
    from mcp_server.config import config

    monkeypatch.setattr(config, "github_app_id", "")
    with pytest.raises(github_app.GitHubAppConfigError):
        github_app.build_app_jwt()


async def test_mint_installation_token_caches_within_lifetime():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fake = FakeClient(
        post_payload={
            "token": "ghs_minted_abc",
            "expires_at": (now + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        }
    )
    t1 = await github_app.mint_installation_token(42, now=now, client=fake)
    t2 = await github_app.mint_installation_token(42, now=now + timedelta(minutes=1), client=fake)

    assert t1 == t2 == "ghs_minted_abc"
    assert len(fake.post_calls) == 1  # second call served from cache
    assert fake.post_calls[0].endswith("/app/installations/42/access_tokens")


async def test_mint_installation_token_remints_after_expiry_buffer():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fake = FakeClient(
        post_payload={
            "token": "ghs_short_lived",
            # Expires in 1 minute — inside the 5-minute re-mint buffer.
            "expires_at": (now + timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        }
    )
    await github_app.mint_installation_token(7, now=now, client=fake)
    await github_app.mint_installation_token(7, now=now + timedelta(seconds=30), client=fake)

    assert len(fake.post_calls) == 2  # stale token forced a re-mint


async def test_mint_requires_installation_id():
    with pytest.raises(github_app.GitHubAppConfigError):
        await github_app.mint_installation_token(0)


async def test_list_installation_repositories_maps_fields():
    fake = FakeClient(
        post_payload={"token": "ghs_x", "expires_at": None},
        get_payloads=[
            {
                "total_count": 1,
                "repositories": [
                    {
                        "full_name": "acme/repo",
                        "clone_url": "https://github.com/acme/repo.git",
                        "private": True,
                        "default_branch": "main",
                    }
                ],
            }
        ],
    )
    repos = await github_app.list_installation_repositories(99, client=fake)
    assert repos == [
        {
            "full_name": "acme/repo",
            "clone_url": "https://github.com/acme/repo.git",
            "private": True,
            "default_branch": "main",
        }
    ]


def test_install_url_carries_state():
    url = github_app.install_url("signed-state-123")
    assert url.startswith("https://github.com/apps/my-test-app/installations/new?")
    assert "state=signed-state-123" in url


def test_github_app_configured_true_when_all_present():
    assert github_app.github_app_configured() is True


def test_github_app_configured_false_when_key_missing(monkeypatch):
    from mcp_server.config import config

    monkeypatch.setattr(config, "github_app_private_key", "")
    monkeypatch.setattr(config, "github_app_private_key_file", "")
    assert github_app.github_app_configured() is False


@pytest.fixture
def oauth_config(monkeypatch):
    from mcp_server.config import config

    monkeypatch.setattr(config, "github_app_client_id", "Iv1.abc")
    monkeypatch.setattr(config, "github_app_client_secret", "secret")


async def test_exchange_user_access_token_returns_token(oauth_config):
    fake = FakeClient(post_payload={"access_token": "gho_user_token"})
    token = await github_app.exchange_user_access_token("the-code", client=fake)
    assert token == "gho_user_token"
    assert fake.post_calls[0] == "https://github.com/login/oauth/access_token"


async def test_exchange_user_access_token_requires_client_config(monkeypatch):
    from mcp_server.config import config

    monkeypatch.setattr(config, "github_app_client_id", "")
    monkeypatch.setattr(config, "github_app_client_secret", "")
    with pytest.raises(github_app.GitHubAppConfigError):
        await github_app.exchange_user_access_token("code")


async def test_user_can_access_installation_true_when_present():
    fake = FakeClient(get_payloads=[{"total_count": 1, "installations": [{"id": 42}]}])
    assert await github_app.user_can_access_installation("tok", 42, client=fake) is True


async def test_user_can_access_installation_false_when_absent():
    fake = FakeClient(get_payloads=[{"total_count": 1, "installations": [{"id": 7}]}])
    assert await github_app.user_can_access_installation("tok", 42, client=fake) is False


def test_github_app_oauth_configured(monkeypatch):
    from mcp_server.config import config

    monkeypatch.setattr(config, "github_app_client_id", "Iv1.abc")
    monkeypatch.setattr(config, "github_app_client_secret", "secret")
    assert github_app.github_app_oauth_configured() is True
    monkeypatch.setattr(config, "github_app_client_secret", "")
    assert github_app.github_app_oauth_configured() is False


class StatusClient:
    """Fake client returning a fixed status code for GET."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.get_calls: list[str] = []

    async def get(self, url, **kwargs):
        self.get_calls.append(url)
        return FakeResponse({}, status_code=self.status_code)


async def test_installation_exists_true_on_200():
    fake = StatusClient(200)
    assert await github_app.installation_exists(4242, client=fake) is True
    assert fake.get_calls[0].endswith("/app/installations/4242")


async def test_installation_exists_false_on_404():
    """An uninstalled App answers 404 — the only signal that it is really gone."""
    assert await github_app.installation_exists(4242, client=StatusClient(404)) is False


async def test_installation_exists_raises_on_server_error():
    """A 5xx must not be read as 'gone' or callers would prune live state."""
    with pytest.raises(RuntimeError):
        await github_app.installation_exists(4242, client=StatusClient(500))


async def test_forget_installation_token_drops_cache():
    fake = FakeClient(post_payload={"token": "ghs_x", "expires_at": None})
    await github_app.mint_installation_token(99, client=fake)
    await github_app.forget_installation_token(99)
    # A second mint must hit GitHub again rather than serve the dropped token.
    await github_app.mint_installation_token(99, client=fake)
    assert len(fake.post_calls) == 2
