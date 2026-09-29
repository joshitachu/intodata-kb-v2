"""Turn a licence-granting billing event into a signed licence.

Shared by every provider, so a real payment provider only has to produce a
``BillingEvent``; how a paid period becomes a licence is decided once, here.
The operator "Issue" API goes through the same path (``ManualProvider.activate``
-> ``fulfil`` -> ``issue_licence``); ``issue_licence`` is the only function in
Mission Control that signs a licence.

The signing key is read lazily (``LicenceSigner.from_env``) and never at import
time: this module is importable in a client stack, which must never hold the key.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

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

    def __repr__(self) -> str:  # never print key material
        return f"LicenceSigner(kid={self.kid!r})"

    @classmethod
    def load(cls, key_file: str | Path | None, kid: str | None) -> LicenceSigner:
        """Load the signing key; ``SigningKeyUnavailable`` if unset or unusable.

        The error names the path and the failure type only, never key content.
        """
        if not key_file or not kid:
            raise SigningKeyUnavailable(
                f"{KEY_ENV} and {KID_ENV} must be set; licences are issued on the "
                "operator machine only"
            )
        try:
            key = load_private_key(Path(key_file))
        except (OSError, ValueError, TypeError) as exc:
            raise SigningKeyUnavailable(
                f"signing key {key_file} could not be loaded ({type(exc).__name__})"
            ) from None
        return cls(key, kid)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> LicenceSigner:
        env = os.environ if env is None else env
        return cls.load(env.get(KEY_ENV), env.get(KID_ENV))


def issue_licence(
    *,
    client_slug: str,
    plan: Plan,
    period_start: datetime,
    period_end: datetime,
    signer: LicenceSigner,
) -> IssuedLicence:
    """Sign one licence. The single issuing function behind billing and the UI."""
    licence_id = str(uuid.uuid4())
    issued_at = datetime.now(UTC).replace(microsecond=0)
    token = issue(
        client_slug=client_slug,
        plan=plan,
        period_start=period_start,
        period_end=period_end,
        private_key=signer.private_key,
        kid=signer.kid,
        licence_id=licence_id,
        issued_at=issued_at,
    )
    return IssuedLicence(
        client_slug=client_slug,
        plan=plan.name,
        period_start=period_start,
        period_end=period_end,
        kid=signer.kid,
        licence_id=licence_id,
        issued_at=issued_at,
        token=token,
    )


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
    return issue_licence(
        client_slug=event.client_slug,
        plan=plan,
        period_start=event.period_start,
        period_end=event.period_end,
        signer=signer,
    )
