"""Payment providers (Strategy). Not live: no provider talks to a network.

- ``ManualProvider``: the operator invoices out of band and activates by hand.
  Activation issues a real licence through ``fulfil``.
- ``StubExternalProvider``: placeholder for the hosted payment provider. The
  choice (e.g. Mollie vs Stripe) is an open question in the plan, so it is
  deliberately provider-neutral and refuses every call.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Mapping, Protocol, runtime_checkable

from ..licensing.issuer import Plan, add_months, load_plans
from .fulfilment import LicenceSigner, fulfil
from .models import BillingEvent, BillingEventKind, CheckoutSession, IssuedLicence


@runtime_checkable
class PaymentProvider(Protocol):
    name: str

    def create_checkout(self, client_slug: str, plan: str) -> CheckoutSession:
        """Start a purchase of ``plan`` for ``client_slug``."""
        ...

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> BillingEvent | None:
        """Verify and normalise a provider webhook. ``None`` = not a billing event.

        Implementations MUST authenticate the payload (provider signature)
        before returning an event: an unauthenticated event would mint a licence.
        """
        ...


class ManualProvider:
    """Operator-driven billing: no checkout page, no webhooks."""

    name = "manual"

    def __init__(
        self,
        signer: LicenceSigner | None = None,
        *,
        plans: Mapping[str, Plan] | None = None,
    ) -> None:
        self._signer = signer
        self._plans = plans

    def _plans_catalogue(self) -> Mapping[str, Plan]:
        return load_plans() if self._plans is None else self._plans

    def create_checkout(self, client_slug: str, plan: str) -> CheckoutSession:
        if plan not in self._plans_catalogue():
            raise ValueError(f"unknown plan {plan!r}")
        return CheckoutSession(
            provider=self.name,
            client_slug=client_slug,
            plan=plan,
            session_id=f"manual-{uuid.uuid4()}",
            checkout_url=None,
            instructions=(
                "Invoice the client out of band. On payment, activate with "
                "ManualProvider.activate(...) and install the licence on the VPS "
                "(deploy/RUNBOOK.md section 17)."
            ),
        )

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> BillingEvent | None:
        # Manual billing has no webhooks; nothing inbound can create an event.
        return None

    def activate(
        self,
        client_slug: str,
        plan: str,
        *,
        months: int = 1,
        period_start: datetime | None = None,
        renewal: bool = False,
    ) -> IssuedLicence:
        """Record a manual payment and issue the licence for its period."""
        start = period_start or datetime.now(timezone.utc)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        event = BillingEvent(
            kind=(BillingEventKind.SUBSCRIPTION_RENEWED if renewal
                  else BillingEventKind.SUBSCRIPTION_ACTIVATED),
            provider=self.name,
            client_slug=client_slug,
            plan=plan,
            period_start=start,
            period_end=add_months(start, months),
            external_id=f"manual-{uuid.uuid4()}",
        )
        signer = self._signer or LicenceSigner.from_env()
        issued = fulfil(event, signer, plans=self._plans_catalogue())
        assert issued is not None  # activation/renewal always grant a licence
        return issued


class StubExternalProvider:
    """Hosted payment provider placeholder. Every call is refused."""

    name = "external"
    _REASON = (
        "No hosted payment provider is integrated yet; the choice of provider is "
        "an open question (see .plans/plan.md). Use MC_BILLING_PROVIDER=manual."
    )

    def create_checkout(self, client_slug: str, plan: str) -> CheckoutSession:
        raise NotImplementedError(self._REASON)

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> BillingEvent | None:
        raise NotImplementedError(self._REASON)
