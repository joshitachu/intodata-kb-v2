"""Billing scaffold (not live): payment-provider interface for subscriptions.

Nothing here reads ``mission_control.config`` (which requires ``MC_API_KEY`` at
import) or the signing key at import time. Provider selection and the signing
key are resolved lazily, when they are actually used.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping

from .fulfilment import LicenceSigner, SigningKeyUnavailable, fulfil, issue_licence
from .models import BillingEvent, BillingEventKind, CheckoutSession, IssuedLicence
from .providers import ManualProvider, PaymentProvider, StubExternalProvider

PROVIDER_ENV = "MC_BILLING_PROVIDER"
DEFAULT_PROVIDER = "manual"

_FACTORIES: dict[str, Callable[[], PaymentProvider]] = {
    ManualProvider.name: ManualProvider,
    StubExternalProvider.name: StubExternalProvider,
}


def get_provider(name: str | None = None, *, env: Mapping[str, str] | None = None) -> PaymentProvider:
    """Build the configured provider (``MC_BILLING_PROVIDER``, default ``manual``)."""
    env = os.environ if env is None else env
    selected = (name or env.get(PROVIDER_ENV) or DEFAULT_PROVIDER).strip().lower()
    factory = _FACTORIES.get(selected)
    if factory is None:
        raise ValueError(
            f"unknown {PROVIDER_ENV}={selected!r}; known: {', '.join(sorted(_FACTORIES))}"
        )
    return factory()


__all__ = [
    "BillingEvent",
    "BillingEventKind",
    "CheckoutSession",
    "IssuedLicence",
    "LicenceSigner",
    "ManualProvider",
    "PaymentProvider",
    "SigningKeyUnavailable",
    "StubExternalProvider",
    "fulfil",
    "get_provider",
    "issue_licence",
]
