"""Provider-neutral billing records.

These are the only shapes the rest of Mission Control sees. A real payment
provider translates its own webhook payloads into ``BillingEvent`` and never
leaks provider objects past its adapter.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping


class BillingEventKind(str, enum.Enum):
    SUBSCRIPTION_ACTIVATED = "subscription_activated"
    SUBSCRIPTION_RENEWED = "subscription_renewed"
    SUBSCRIPTION_CANCELLED = "subscription_cancelled"
    PAYMENT_FAILED = "payment_failed"


#: Events that grant a (new) licence period. Cancellation and a failed payment
#: issue nothing: the installed licence simply runs to its ``exp`` (no
#: revocation, no grace period -- the licence contract is "valid until expiry").
LICENCE_GRANTING_KINDS = frozenset(
    {BillingEventKind.SUBSCRIPTION_ACTIVATED, BillingEventKind.SUBSCRIPTION_RENEWED}
)


@dataclass(frozen=True)
class CheckoutSession:
    """Where to send a customer to pay for ``plan``.

    ``checkout_url`` is ``None`` for providers without a hosted payment page
    (the manual provider: the operator invoices out of band).
    """

    provider: str
    client_slug: str
    plan: str
    session_id: str
    checkout_url: str | None = None
    instructions: str = ""


@dataclass(frozen=True)
class BillingEvent:
    """One subscription lifecycle event, normalised across providers."""

    kind: BillingEventKind
    provider: str
    client_slug: str
    plan: str
    period_start: datetime
    period_end: datetime
    #: The provider's own id for the event/subscription, for idempotency.
    external_id: str
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.period_start.tzinfo is None or self.period_end.tzinfo is None:
            raise ValueError("billing periods must be timezone-aware")
        if self.period_end <= self.period_start:
            raise ValueError("period_end must be after period_start")

    @property
    def grants_licence(self) -> bool:
        return self.kind in LICENCE_GRANTING_KINDS


@dataclass(frozen=True)
class IssuedLicence:
    """A licence produced for a billing event, ready to install on the VPS."""

    client_slug: str
    plan: str
    period_start: datetime
    period_end: datetime
    kid: str
    token: str
