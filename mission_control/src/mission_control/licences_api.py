"""Operator licence issuing API (central Mission Control only).

Issuing needs the signing key (``MC_LICENCE_SIGNING_KEY_FILE`` + ``_KID``).
Client-side Mission Controls never have it, so there every issue call is 503.

Issuing is also refused while ``MC_API_KEY`` is the public repo default.

Flow for ``POST /ops/clients/{slug}/licences``:
auth -> slug -> request shape -> signing key -> plan -> (push) resolve instance
(pinned registry / ``clients.json`` URL only) -> period (``start_after_current`` reads the
ledger, never a snapshot) -> ledger writable -> sign (shared billing path) ->
(push) install -> ledger append. Everything that can refuse the request runs
before a licence is signed.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from .auth import require_api_key
from .billing.fulfilment import LicenceSigner, SigningKeyUnavailable
from .billing.models import IssuedLicence
from .billing.providers import ManualProvider
from .config import REPO_DEFAULT_API_KEY, settings
from .fleet import SLUG_PATTERN
from .instance_client import (
    InstanceControlClient,
    InstanceReply,
    InstanceResolutionError,
    InstanceTarget,
    InstanceUnreachable,
    ResolutionFailure,
    get_instance_client,
)
from .licensing.issuer import Plan, add_months, load_plans
from .licensing.ledger import IssuedLicenceLedger, JsonlLedger, LedgerEntry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ops", dependencies=[Depends(require_api_key)])

# Same rule the issuer enforces on ``sub``; checked first so a bad slug never
# reaches the clients.json or ledger paths.
_SLUG = SLUG_PATTERN
_MAX_DETAIL_CHARS = 2000
#: Longest period an explicit ``end`` may give (``months`` is capped at 36).
_MAX_PERIOD_MONTHS = 37
#: How far in the past an explicit ``start`` may lie (clock skew, same-day backdating).
_MAX_START_BACKDATE = timedelta(hours=24)
_INSTALL_SLOTS = ("current", "next")


def _error(status_code: int, code: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=status_code,
                         detail={"code": code, "message": message, **extra})


# -- dependencies ---------------------------------------------------------------

class SignerProvider:
    """Loads the signing key lazily and caches it by (path, kid, mtime)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stamp: tuple[str, str, int] | None = None
        self._signer: LicenceSigner | None = None

    def get(self) -> LicenceSigner:
        key_file, kid = settings.licence_signing_key_file, settings.licence_signing_kid
        if not key_file or not kid:
            raise SigningKeyUnavailable("no signing key configured")
        if settings.api_key == REPO_DEFAULT_API_KEY:
            # Anyone who has read the repo could call the issuer with this key.
            raise SigningKeyUnavailable(
                "MC_API_KEY is the repository default; set a strong key to enable issuing")
        try:
            stamp = (str(key_file), kid, key_file.stat().st_mtime_ns)
        except OSError as exc:
            raise SigningKeyUnavailable(
                f"signing key {key_file} is not readable ({type(exc).__name__})"
            ) from None
        with self._lock:
            if stamp != self._stamp or self._signer is None:
                self._signer = LicenceSigner.load(key_file, kid)
                self._stamp = stamp
            return self._signer

    def available(self) -> bool:
        try:
            self.get()
        except SigningKeyUnavailable as exc:
            if settings.licence_signing_key_file:  # configured but unusable: say so
                logger.warning("licence issuing disabled: %s", exc)
            return False
        return True


_signer_provider = SignerProvider()


def get_signer_provider() -> SignerProvider:
    return _signer_provider


def get_ledger() -> IssuedLicenceLedger:
    return JsonlLedger(settings.licence_ledger_path)


def get_plans() -> dict[str, Plan]:
    return load_plans()


# -- schemas --------------------------------------------------------------------

class IssueLicenceRequest(BaseModel):
    plan: str = Field(min_length=1, max_length=64)
    months: int | None = Field(default=1, ge=1, le=36)
    start: datetime | None = None
    end: datetime | None = None
    start_after_current: bool = False
    push: bool = True
    return_token: bool = False
    issued_by: str | None = Field(default=None, max_length=200)


# -- helpers --------------------------------------------------------------------

def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _aware(datetime.fromisoformat(value))
    except ValueError:
        return None


def _current_licence_end(ledger: IssuedLicenceLedger, client_slug: str,
                         now: datetime) -> datetime | None:
    """Latest future ``period_end`` among this client's ledger entries.

    The ledger is what this Mission Control actually signed; a heartbeat
    snapshot is not used because anyone holding the shared ``MC_API_KEY`` can
    write one. Entries whose push succeeded are preferred. When none succeeded
    but future entries exist (push failed, or push=false and the token was
    delivered by hand), the latest of those is still used: the renewal is
    queued after the newest licence already issued rather than overlapping it,
    and the operator sees the resulting period before delivering it.
    """
    try:
        entries = ledger.list_for_client(client_slug)
    except OSError as exc:
        logger.error("licence ledger not readable: %s", type(exc).__name__)
        raise _error(503, "ledger_unavailable",
                     "The issuance ledger is not readable; cannot place the renewal.") from exc
    future: list[tuple[datetime, bool]] = []
    for entry in entries:
        end = _parse_iso(entry.period_end)
        if end is not None and end > now:
            future.append((end, entry.push_ok is True))
    if not future:
        return None
    pushed = [end for end, ok in future if ok]
    return max(pushed) if pushed else max(end for end, _ in future)


def _period(body: IssueLicenceRequest, client_slug: str,
            ledger: IssuedLicenceLedger) -> tuple[datetime, datetime]:
    now = datetime.now(UTC).replace(microsecond=0)
    if body.start is not None and body.start_after_current:
        raise _error(422, "invalid_period", "give either start or start_after_current, not both")
    if body.start is not None:
        start = _aware(body.start)
        if start < now - _MAX_START_BACKDATE:
            raise _error(422, "start_in_past",
                         "the period may not start more than 24 hours in the past")
    elif body.start_after_current:
        start = _current_licence_end(ledger, client_slug, now) or now
    else:
        start = now
    if body.end is not None:
        end = _aware(body.end)
    elif body.months is not None:
        end = add_months(start, body.months)
    else:
        raise _error(422, "invalid_period", "give months or end")
    if end <= start:
        raise _error(422, "invalid_period", "the period must end after it starts")
    if end > add_months(start, _MAX_PERIOD_MONTHS):
        raise _error(422, "period_too_long",
                     f"the period may not exceed {_MAX_PERIOD_MONTHS} months")
    if end <= now:
        raise _error(422, "invalid_period", "the period has already ended")
    return start, end


def _resolve_for_push(instances: InstanceControlClient, client_slug: str) -> InstanceTarget:
    try:
        return instances.resolve(client_slug)
    except InstanceResolutionError as exc:
        if exc.failure is ResolutionFailure.NO_CLIENT_ENTRY:
            raise _error(404, "unknown_client", exc.message) from exc
        raise _error(422, "instance_not_addressable", exc.message,
                     reason=exc.failure.value) from exc


def _truncate(value: Any) -> Any:
    if isinstance(value, str) and len(value) > _MAX_DETAIL_CHARS:
        return value[:_MAX_DETAIL_CHARS] + "..."
    return value


def _dict_or_none(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _push_result(reply: InstanceReply | None, error: str | None) -> dict[str, Any]:
    """The instance's answer, shaped for the UI.

    ``slot`` is where the instance put the licence (``"current"``, or ``"next"``
    when it was queued behind the running one); ``next_licence`` is the queued
    licence's public view. Both are ``None`` when the instance does not say.
    """
    result: dict[str, Any] = {"attempted": True, "ok": False, "status_code": None,
                              "detail": None, "instance_licence": None,
                              "slot": None, "next_licence": None}
    if reply is None:
        return {**result, "attempted": error is not None, "detail": error}
    body = reply.body
    ok = reply.ok and not (isinstance(body, dict) and body.get("installed") is False)
    result.update(ok=ok, status_code=reply.status_code)
    if isinstance(body, dict):
        slot = body.get("slot")
        result.update(
            detail=None if ok else body.get("detail", body),
            instance_licence=_dict_or_none(body.get("licence")),
            slot=slot if slot in _INSTALL_SLOTS else None,
            next_licence=_dict_or_none(body.get("next_licence")),
        )
    else:
        result["detail"] = None if ok else _truncate(body)
    return result


def _iso(moment: datetime) -> str:
    return moment.isoformat()


# -- routes ---------------------------------------------------------------------

@router.get("/licensing/plans")
async def list_plans(
    plans: Annotated[dict[str, Plan], Depends(get_plans)],
    signers: Annotated[SignerProvider, Depends(get_signer_provider)],
) -> dict[str, Any]:
    return {
        "plans": [
            {"name": p.name, "description": p.description, "features": list(p.features),
             "limits": dict(p.limits)}
            for p in plans.values()
        ],
        "issuing_enabled": signers.available(),
    }


@router.post("/clients/{client_slug}/licences", status_code=status.HTTP_201_CREATED)
async def issue_client_licence(
    client_slug: str,
    body: IssueLicenceRequest,
    plans: Annotated[dict[str, Plan], Depends(get_plans)],
    signers: Annotated[SignerProvider, Depends(get_signer_provider)],
    ledger: Annotated[IssuedLicenceLedger, Depends(get_ledger)],
    instances: Annotated[InstanceControlClient, Depends(get_instance_client)],
) -> dict[str, Any]:
    if not _SLUG.match(client_slug):
        raise _error(422, "invalid_client", f"invalid client slug {client_slug!r}")
    if body.return_token and body.push:
        # A pushed licence is delivered by the push; returning it as well would
        # put a second copy of a live token in the operator's browser.
        raise _error(422, "return_token_with_push",
                     "return_token is only allowed with push=false")
    try:
        signer = signers.get()
    except SigningKeyUnavailable as exc:
        logger.warning("licence issue refused for %s: %s", client_slug, exc)
        raise _error(503, "issuing_disabled",
                     "No licence signing key is configured on this Mission Control.") from exc
    if body.plan not in plans:
        raise _error(422, "unknown_plan", f"unknown plan {body.plan!r}",
                     known=sorted(plans))
    target = _resolve_for_push(instances, client_slug) if body.push else None
    start, end = _period(body, client_slug, ledger)
    try:
        ledger.check_writable()
    except OSError as exc:
        logger.error("licence ledger not writable: %s", type(exc).__name__)
        raise _error(503, "ledger_unavailable",
                     "The issuance ledger is not writable; nothing was issued.") from exc

    issued_by = (body.issued_by or "").strip() or None
    issued: IssuedLicence = ManualProvider(signer, plans=plans).activate(
        client_slug, body.plan, period_start=start, period_end=end,
        renewal=body.start_after_current,
        metadata={"issued_by": issued_by} if issued_by else None,
    )

    reply: InstanceReply | None = None
    push_error: str | None = None
    if target is not None:
        try:
            reply = await instances.install_licence(target, issued.token)
        except InstanceUnreachable as exc:
            push_error = f"Could not reach VPS: {exc}"
    push = _push_result(reply, push_error)

    entry = LedgerEntry(
        licence_id=issued.licence_id,
        client=client_slug,
        plan=issued.plan,
        period_start=_iso(issued.period_start),
        period_end=_iso(issued.period_end),
        issued_at=_iso(issued.issued_at),
        issued_by=issued_by,
        kid=issued.kid,
        push_ok=push["ok"] if push["attempted"] else None,
        push_status_code=push["status_code"],
    )
    try:
        ledger.append(entry)
    except OSError as exc:
        # Signed (and maybe installed) but unrecorded: surface it, with the id to reconcile.
        logger.error("licence %s for %s issued but NOT recorded in the ledger: %s",
                     issued.licence_id, client_slug, type(exc).__name__)
        raise _error(500, "ledger_write_failed",
                     "The licence was issued but could not be recorded in the ledger.",
                     licence_id=issued.licence_id, push=push) from exc

    logger.info(
        "licence issued id=%s client=%s plan=%s period=%s..%s by=%s push=%s status=%s",
        issued.licence_id, client_slug, issued.plan, entry.period_start, entry.period_end,
        issued_by, push["ok"] if push["attempted"] else "skipped", push["status_code"],
    )
    result: dict[str, Any] = {
        "licence_id": issued.licence_id,
        "client": client_slug,
        "plan": issued.plan,
        "period_start": entry.period_start,
        "period_end": entry.period_end,
        "issued_at": entry.issued_at,
        "issued_by": issued_by,
        "push": push,
    }
    if body.return_token:
        result["token"] = issued.token
    return result


@router.get("/clients/{client_slug}/licences")
async def list_client_licences(
    client_slug: str,
    ledger: Annotated[IssuedLicenceLedger, Depends(get_ledger)],
) -> dict[str, Any]:
    if not _SLUG.match(client_slug):
        raise _error(422, "invalid_client", f"invalid client slug {client_slug!r}")
    return {"licences": [e.to_dict() for e in ledger.list_for_client(client_slug)]}
