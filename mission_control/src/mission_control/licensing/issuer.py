"""Issue Ed25519-signed licence tokens (the claims contract of ``core.py``)."""

from __future__ import annotations

import calendar
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ISSUER = "intodata-nexus"
ALGORITHM = "EdDSA"
PLANS_FILE = Path(__file__).with_name("plans.json")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


@dataclass(frozen=True)
class Plan:
    name: str
    features: tuple[str, ...]
    limits: Mapping[str, int]


def load_plans(path: Path = PLANS_FILE) -> dict[str, Plan]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        name: Plan(name, tuple(spec.get("features", [])),
                   {k: int(v) for k, v in spec.get("limits", {}).items()})
        for name, spec in raw.items()
    }


def add_months(moment: datetime, months: int) -> datetime:
    """Calendar-month arithmetic; clamps to the last day (Jan 31 + 1 -> Feb 28/29)."""
    if months < 1:
        raise ValueError("months must be >= 1")
    index = moment.month - 1 + months
    year, month = moment.year + index // 12, index % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def load_private_key(path: Path) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError(f"{path} is not an Ed25519 private key")
    return key


def public_key_pem(key: Ed25519PrivateKey) -> str:
    return key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()


def issue(
    *,
    client_slug: str,
    plan: Plan,
    period_start: datetime,
    period_end: datetime,
    private_key: Ed25519PrivateKey,
    kid: str,
    licence_id: str | None = None,
) -> str:
    if not _SLUG.match(client_slug):
        raise ValueError(f"invalid client slug: {client_slug!r}")
    if period_end <= period_start:
        raise ValueError("period_end must be after period_start")
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "iss": ISSUER,
        "sub": client_slug,
        "lid": licence_id or str(uuid.uuid4()),
        "plan": plan.name,
        "features": list(plan.features),
        "limits": dict(plan.limits),
        "nbf": int(period_start.timestamp()),
        "exp": int(period_end.timestamp()),
        "iat": int(now.timestamp()),
    }
    return jwt.encode(payload, private_key, algorithm=ALGORITHM, headers={"kid": kid})


def inspect(token: str) -> dict[str, Any]:
    """Decode WITHOUT verifying -- for operator display only."""
    return {"header": jwt.get_unverified_header(token),
            "claims": jwt.decode(token, options={"verify_signature": False})}
