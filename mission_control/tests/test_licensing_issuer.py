from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mission_control.licensing.__main__ import main
from mission_control.licensing.issuer import add_months, issue, load_plans, public_key_pem


def test_basic_plan_is_chat_plus_three_generations() -> None:
    basic = load_plans()["basic"]
    assert set(basic.features) == {"chat", "generation"}
    assert basic.limits == {"generations_per_period": 3}


@pytest.mark.parametrize("start,months,end", [
    (datetime(2026, 1, 31, tzinfo=timezone.utc), 1, datetime(2026, 2, 28, tzinfo=timezone.utc)),
    (datetime(2026, 11, 15, tzinfo=timezone.utc), 2, datetime(2027, 1, 15, tzinfo=timezone.utc)),
])
def test_add_months(start: datetime, months: int, end: datetime) -> None:
    assert add_months(start, months) == end


def test_issued_token_verifies_with_the_public_key() -> None:
    key = Ed25519PrivateKey.generate()
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    token = issue(client_slug="acme", plan=load_plans()["basic"], period_start=start,
                  period_end=add_months(start, 1), private_key=key, kid="k1")
    assert jwt.get_unverified_header(token)["kid"] == "k1"
    claims = jwt.decode(token, public_key_pem(key), algorithms=["EdDSA"],
                        issuer="intodata-nexus", options={"verify_exp": False, "verify_nbf": False})
    assert claims["sub"] == "acme" and claims["plan"] == "basic"
    assert claims["limits"] == {"generations_per_period": 3}


@pytest.mark.parametrize("slug", ["", "Acme", "a/b", "-x"])
def test_rejects_bad_slug(slug: str) -> None:
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        issue(client_slug=slug, plan=load_plans()["basic"], period_start=start,
              period_end=add_months(start, 1), private_key=Ed25519PrivateKey.generate(), kid="k")


def test_cli_keygen_then_issue(tmp_path: Path) -> None:
    key = tmp_path / "k.pem"
    assert main(["keygen", "--out", str(key)]) == 0
    assert oct(key.stat().st_mode & 0o777) == "0o600"
    assert main(["keygen", "--out", str(key)]) == 1  # never overwrites
    out = tmp_path / "acme.jwt"
    assert main(["issue", "--client", "acme", "--key", str(key), "--kid", "k", "--out", str(out)]) == 0
    assert jwt.get_unverified_header(out.read_text().strip())["kid"] == "k"
