from __future__ import annotations

import os
from datetime import datetime, timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mission_control.billing import (
    BillingEvent,
    BillingEventKind,
    LicenceSigner,
    ManualProvider,
    PaymentProvider,
    SigningKeyUnavailable,
    StubExternalProvider,
    fulfil,
    get_provider,
)
from mission_control.licensing.issuer import add_months, public_key_pem

START = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _verify(token: str, key: Ed25519PrivateKey) -> dict:
    return jwt.decode(token, public_key_pem(key), algorithms=["EdDSA"], issuer="intodata-nexus",
                      options={"verify_exp": False, "verify_nbf": False})


@pytest.fixture
def key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def test_manual_activation_issues_a_verifiable_licence_for_the_period(key) -> None:
    provider = ManualProvider(LicenceSigner(key, "test-kid"))
    issued = provider.activate("acme", "basic", months=1, period_start=START)

    assert jwt.get_unverified_header(issued.token)["kid"] == "test-kid"
    claims = _verify(issued.token, key)
    assert claims["sub"] == "acme"
    assert claims["plan"] == "basic"
    assert set(claims["features"]) == {"chat", "generation"}
    assert claims["limits"] == {"generations_per_period": 3}
    assert claims["nbf"] == int(START.timestamp())
    assert claims["exp"] == int(add_months(START, 1).timestamp())
    assert (issued.period_start, issued.period_end) == (START, add_months(START, 1))


def test_a_licence_from_another_key_does_not_verify(key) -> None:
    issued = ManualProvider(LicenceSigner(key, "k")).activate("acme", "basic", period_start=START)
    with pytest.raises(jwt.InvalidSignatureError):
        _verify(issued.token, Ed25519PrivateKey.generate())


def test_manual_activation_reads_the_signing_key_lazily_from_env(tmp_path, monkeypatch, key) -> None:
    from cryptography.hazmat.primitives import serialization

    pem = tmp_path / "signing.pem"
    pem.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
    monkeypatch.setenv("MC_LICENCE_SIGNING_KEY_FILE", str(pem))
    monkeypatch.setenv("MC_LICENCE_SIGNING_KID", "env-kid")
    issued = ManualProvider().activate("acme", "basic", period_start=START)
    assert issued.kid == "env-kid"
    assert _verify(issued.token, key)["sub"] == "acme"


def test_manual_activation_without_a_signing_key_is_refused(monkeypatch) -> None:
    monkeypatch.delenv("MC_LICENCE_SIGNING_KEY_FILE", raising=False)
    monkeypatch.delenv("MC_LICENCE_SIGNING_KID", raising=False)
    with pytest.raises(SigningKeyUnavailable):
        ManualProvider().activate("acme", "basic", period_start=START)


@pytest.mark.parametrize("kind", [BillingEventKind.SUBSCRIPTION_CANCELLED,
                                  BillingEventKind.PAYMENT_FAILED])
def test_non_granting_events_issue_nothing(kind, key) -> None:
    event = BillingEvent(kind=kind, provider="manual", client_slug="acme", plan="basic",
                         period_start=START, period_end=add_months(START, 1), external_id="e1")
    assert fulfil(event, LicenceSigner(key, "k")) is None


def test_unknown_plan_is_refused(key) -> None:
    with pytest.raises(ValueError, match="unknown plan"):
        ManualProvider(LicenceSigner(key, "k")).activate("acme", "platinum", period_start=START)


def test_manual_provider_has_no_webhooks_and_no_checkout_page() -> None:
    provider = ManualProvider()
    assert provider.parse_webhook({}, b'{"kind":"subscription_activated"}') is None
    session = provider.create_checkout("acme", "basic")
    assert session.checkout_url is None and session.provider == "manual"


def test_stub_external_provider_refuses_every_call() -> None:
    stub = StubExternalProvider()
    with pytest.raises(NotImplementedError):
        stub.create_checkout("acme", "basic")
    with pytest.raises(NotImplementedError):
        stub.parse_webhook({}, b"")


def test_factory_defaults_to_manual_and_selects_by_env() -> None:
    assert isinstance(get_provider(env={}), ManualProvider)
    assert isinstance(get_provider(env={"MC_BILLING_PROVIDER": "external"}), StubExternalProvider)
    for provider in (ManualProvider(), StubExternalProvider()):
        assert isinstance(provider, PaymentProvider)
    with pytest.raises(ValueError, match="unknown MC_BILLING_PROVIDER"):
        get_provider(env={"MC_BILLING_PROVIDER": "paypal"})


def test_billing_import_does_not_need_mission_control_settings() -> None:
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items() if k != "MC_API_KEY"}
    result = subprocess.run(
        [sys.executable, "-c", "import mission_control.billing, sys; "
         "sys.exit('mission_control.config' in sys.modules)"],
        env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_billing_webhook_returns_501(monkeypatch) -> None:
    monkeypatch.setenv("MC_API_KEY", "test-key")
    from fastapi.testclient import TestClient

    from mission_control.main import app

    client = TestClient(app)
    resp = client.post("/ops/billing/webhook", content=b'{"kind":"subscription_activated"}',
                       headers={"Content-Type": "application/json"})
    assert resp.status_code == 501
    assert resp.json()["detail"]["code"] == "billing_not_live"
    # Existing endpoints keep their auth.
    assert client.get("/ops/clients").status_code in (401, 403)
