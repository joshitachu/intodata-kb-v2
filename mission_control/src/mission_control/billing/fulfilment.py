"""Turn a licence-granting billing event into a signed licence.

Shared by every provider, so a real payment provider only has to produce a
``BillingEvent``; how a paid period becomes a licence is decided once, here.

The signing key is read lazily (``LicenceSigner.from_env``) and never at import
time: this module is importable in a client stack, which must never hold the key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ..licensing.__main__ import KEY_ENV, KID_ENV
from ..licensing.issuer import Plan, issue, load_plans, load_private_key
from .models import BillingEvent, IssuedLicence


class SigningKeyUnavailable(RuntimeError):
    """The operator signing key is not configured on this machine."""


@dataclass(frozen=True)
class LicenceSigner:
    private_key: Ed25519PrivateKey
    kid: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "LicenceSigner":
        env = os.environ if env is None else env
        key_file, kid = env.get(KEY_ENV), env.get(KID_ENV)
        if not key_file or not kid:
            raise SigningKeyUnavailable(
                f"{KEY_ENV} and {KID_ENV} must be set; licences are issued on the "
                "operator machine only"
            )
        return cls(load_private_key(Path(key_file)), kid)


def fulfil(
    event: BillingEvent,
    signer: LicenceSigner,
    *,
    plans: Mapping[str, Plan] | None = None,
) -> IssuedLicence | None:
    """Issue the licence an event pays for; ``None`` for events that grant none."""
    if not event.grants_licence:
        return None
    catalogue = load_plans() if plans is None else plans
    plan = catalogue.get(event.plan)
    if plan is None:
        raise ValueError(f"unknown plan {event.plan!r}; known: {', '.join(sorted(catalogue))}")
    token = issue(
        client_slug=event.client_slug,
        plan=plan,
        period_start=event.period_start,
        period_end=event.period_end,
        private_key=signer.private_key,
        kid=signer.kid,
    )
    return IssuedLicence(
        client_slug=event.client_slug,
        plan=plan.name,
        period_start=event.period_start,
        period_end=event.period_end,
        kid=signer.kid,
        token=token,
    )
