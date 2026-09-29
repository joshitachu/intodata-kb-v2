"""Subscription licence: verification and entitlement policy.

MIRRORED FILE. ``RAG_pipeline/backend/src/licensing/core.py`` and
``knowledge_base/mcp-server/src/mcp_server/licensing/core.py`` must be
byte-identical; ``test_licence_mirror_parity`` fails otherwise. Keep this module
free of service imports (stdlib + PyJWT + cryptography only) so the copies can
stay identical.

A licence is an Ed25519-signed JWT (EdDSA) issued by the operator
(``mission_control.licensing``). It is verified offline against a pinned key
ring, so an instance works until ``exp`` without reaching the operator, and
becomes view-only after it.

Layers:
- ``verify_token``      token -> ``LicenceStatus`` (never raises on bad input)
- ``LicenceSource``     where the token comes from (Strategy; a file today, a
                        per-tenant subscription record for multi-tenant SaaS)
- ``EntitlementPolicy`` ``check(capability)`` -> ``None`` or a ``Refusal``

Two slots. The *current* licence lives in the configured licence file; a
gapless renewal (a token whose period has not started yet) is queued in the
*next* slot, a sibling file named by ``next_licence_path`` (``licence.jwt`` ->
``licence.next.jwt``). ``EntitlementPolicy.status`` serves the current licence
while it is VALID and falls back to the next one only once the next one is
itself VALID, so a pushed renewal never displaces a running licence and takes
over the moment its period starts, with no process having to move a file.
Moving next -> current afterwards is housekeeping done by the writer.
"""

from __future__ import annotations

import enum
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Protocol

import jwt

ISSUER = "intodata-nexus"
ALGORITHM = "EdDSA"
LEEWAY_SECONDS = 60
NEAR_EXPIRY_DAYS = 7


class Capability(str, enum.Enum):
    """Something a user can do that spends tokens or produces new artefacts."""

    CHAT = "chat"
    GENERATION = "generation"
    SCAN = "scan"


#: Capability -> plan feature that grants it. ``None`` = any valid licence.
_REQUIRED_FEATURE: dict[Capability, str | None] = {
    Capability.CHAT: "chat",
    Capability.GENERATION: "generation",
    Capability.SCAN: None,
}


class LicenceState(str, enum.Enum):
    VALID = "valid"
    EXPIRED = "expired"
    NOT_YET_VALID = "not_yet_valid"
    MISSING = "missing"
    INVALID = "invalid"


class RefusalCode(str, enum.Enum):
    LICENCE_EXPIRED = "licence_expired"
    LICENCE_NOT_YET_VALID = "licence_not_yet_valid"
    LICENCE_MISSING = "licence_missing"
    LICENCE_INVALID = "licence_invalid"
    FEATURE_NOT_IN_PLAN = "feature_not_in_plan"
    QUOTA_EXHAUSTED = "quota_exhausted"


_STATE_REFUSAL: dict[LicenceState, RefusalCode] = {
    LicenceState.EXPIRED: RefusalCode.LICENCE_EXPIRED,
    LicenceState.NOT_YET_VALID: RefusalCode.LICENCE_NOT_YET_VALID,
    LicenceState.MISSING: RefusalCode.LICENCE_MISSING,
    LicenceState.INVALID: RefusalCode.LICENCE_INVALID,
}


@dataclass(frozen=True)
class LicenceClaims:
    licence_id: str
    subject: str
    plan: str
    features: frozenset[str]
    limits: Mapping[str, int]
    period_start: datetime
    period_end: datetime

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "LicenceClaims":
        limits = payload.get("limits") or {}
        features = payload.get("features") or []
        if not isinstance(limits, dict) or not isinstance(features, list):
            raise ValueError("limits must be an object and features a list")
        return cls(
            licence_id=str(payload["lid"]),
            subject=str(payload["sub"]),
            plan=str(payload["plan"]),
            features=frozenset(str(f) for f in features),
            limits={str(k): int(v) for k, v in limits.items()},
            period_start=datetime.fromtimestamp(int(payload["nbf"]), tz=timezone.utc),
            period_end=datetime.fromtimestamp(int(payload["exp"]), tz=timezone.utc),
        )


@dataclass(frozen=True)
class LicenceStatus:
    state: LicenceState
    claims: LicenceClaims | None = None
    #: Human-readable reason for a non-valid state. Never contains the token.
    reason: str = ""

    def to_public_dict(self, *, now: datetime | None = None) -> dict[str, object]:
        """Shape served by the ``/licence/status`` endpoints."""
        now = now or datetime.now(timezone.utc)
        out: dict[str, object] = {"state": self.state.value, "reason": self.reason or None}
        if self.claims is None:
            out.update(plan=None, features=[], limits={}, period_start=None, period_end=None,
                       days_remaining=None, near_expiry=False)
            return out
        seconds_left = (self.claims.period_end - now).total_seconds()
        days_left = max(0, int(seconds_left // 86400))
        out.update(
            plan=self.claims.plan,
            features=sorted(self.claims.features),
            limits=dict(self.claims.limits),
            period_start=self.claims.period_start.isoformat(),
            period_end=self.claims.period_end.isoformat(),
            days_remaining=days_left if self.state is LicenceState.VALID else 0,
            near_expiry=self.state is LicenceState.VALID and seconds_left < NEAR_EXPIRY_DAYS * 86400,
        )
        return out


def verify_token(
    token: str | None,
    *,
    public_keys: Mapping[str, str],
    expected_subject: str,
    now: datetime | None = None,
) -> LicenceStatus:
    """Verify a licence token. Returns a status for every input; never raises."""
    if not token or not token.strip():
        return LicenceStatus(LicenceState.MISSING, reason="no licence installed")
    token = token.strip()
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        return LicenceStatus(LicenceState.INVALID, reason="licence is not a well-formed token")
    if header.get("alg") != ALGORITHM:
        return LicenceStatus(LicenceState.INVALID, reason="licence uses an unsupported algorithm")
    key = public_keys.get(str(header.get("kid") or ""))
    if key is None:
        return LicenceStatus(LicenceState.INVALID, reason="licence was signed by an unknown key")

    now = now or datetime.now(timezone.utc)
    try:
        # Time claims are checked below against ``now`` so tests and the
        # expired/not-yet-valid distinction do not depend on the wall clock.
        payload = jwt.decode(
            token,
            key,
            algorithms=[ALGORITHM],
            issuer=ISSUER,
            options={"verify_exp": False, "verify_nbf": False, "verify_aud": False,
                     "require": ["iss", "sub", "exp", "nbf", "lid", "plan"]},
        )
        claims = LicenceClaims.from_payload(payload)
    except (jwt.PyJWTError, KeyError, TypeError, ValueError, OverflowError, OSError) as exc:
        return LicenceStatus(LicenceState.INVALID, reason=f"licence rejected: {type(exc).__name__}")

    if not expected_subject:
        return LicenceStatus(LicenceState.INVALID, claims,
                             reason="this instance has no client slug configured")
    if claims.subject != expected_subject:
        return LicenceStatus(LicenceState.INVALID, claims,
                             reason="licence was issued for a different client")
    ts = now.timestamp()
    if ts < claims.period_start.timestamp() - LEEWAY_SECONDS:
        return LicenceStatus(LicenceState.NOT_YET_VALID, claims, reason="licence period has not started")
    if ts >= claims.period_end.timestamp() + LEEWAY_SECONDS:
        return LicenceStatus(LicenceState.EXPIRED, claims, reason="licence has expired")
    return LicenceStatus(LicenceState.VALID, claims)


class LicenceSource(Protocol):
    """Where the current licence token comes from."""

    def read_token(self) -> str | None: ...


#: Returned for a file that exists but cannot be decoded, so the state is
#: INVALID ("not a well-formed token") rather than MISSING or an exception.
UNREADABLE_TOKEN = "<unreadable licence file>"


def next_licence_path(licence_file: str | Path) -> Path:
    """The next-slot sibling of a licence file: ``licence.jwt`` -> ``licence.next.jwt``.

    Same directory on purpose: the licence directory is the unit that is
    mounted (read-write into mcp-server, read-only into the RAG backend), so
    both services see both slots.
    """
    path = Path(licence_file)
    return path.with_name(f"{path.stem}.next{path.suffix}")


class FileLicenceSource:
    """Reads the token from a mounted file; re-reads when the file changes.

    "Changes" is keyed on inode, size and nanosecond mtime together, so an
    atomic replace (new inode) is seen even when the filesystem's timestamp
    granularity would give the new file the same mtime as the old one.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._stamp: tuple[int, int, int] | None = None
        self._token: str | None = None

    def read_token(self) -> str | None:
        try:
            st = self._path.stat()
        except OSError:
            return None
        stamp = (st.st_ino, st.st_size, st.st_mtime_ns)
        with self._lock:
            if stamp != self._stamp:
                try:
                    self._token = self._path.read_text(encoding="utf-8").strip() or None
                except UnicodeDecodeError:
                    self._token = UNREADABLE_TOKEN
                except OSError:
                    return None
                self._stamp = stamp
            return self._token


@dataclass(frozen=True)
class Refusal:
    code: RefusalCode
    message: str
    capability: Capability
    details: Mapping[str, object] = field(default_factory=dict)

    def to_detail(self) -> dict[str, object]:
        """HTTP 402 ``detail`` body. Stable contract for both frontends."""
        return {"code": self.code.value, "message": self.message,
                "capability": self.capability.value, **dict(self.details)}


class LicenceRefused(Exception):
    """Raised at chokepoints (e.g. run creation); mapped to HTTP 402."""

    def __init__(self, refusal: Refusal) -> None:
        super().__init__(refusal.message)
        self.refusal = refusal


_REFUSAL_MESSAGES: dict[RefusalCode, str] = {
    RefusalCode.LICENCE_EXPIRED: "The subscription has expired. Existing content stays readable; renew to continue.",
    RefusalCode.LICENCE_NOT_YET_VALID: "The subscription period has not started yet.",
    RefusalCode.LICENCE_MISSING: "No subscription licence is installed on this instance.",
    RefusalCode.LICENCE_INVALID: "The installed subscription licence is not valid for this instance.",
    RefusalCode.FEATURE_NOT_IN_PLAN: "This feature is not included in the current plan.",
    RefusalCode.QUOTA_EXHAUSTED: "The plan's generation allowance for this period is used up.",
}

GENERATIONS_LIMIT_KEY = "generations_per_period"


class EntitlementPolicy:
    """Decides whether a capability is allowed under the current licence.

    ``next_source`` is the optional queued-renewal slot (see module docstring).
    """

    def __init__(
        self,
        source: LicenceSource,
        *,
        public_keys: Mapping[str, str],
        expected_subject: str,
        enforce: bool,
        next_source: LicenceSource | None = None,
    ) -> None:
        self._source = source
        self._next_source = next_source
        self._public_keys = dict(public_keys)
        self._expected_subject = expected_subject
        self.enforce = enforce

    def _verify(self, token: str | None, now: datetime | None) -> LicenceStatus:
        return verify_token(token, public_keys=self._public_keys,
                            expected_subject=self._expected_subject, now=now)

    def _read_next(self, now: datetime | None) -> LicenceStatus | None:
        if self._next_source is None:
            return None
        token = self._next_source.read_token()
        return None if token is None else self._verify(token, now)

    def status(self, *, now: datetime | None = None) -> LicenceStatus:
        """The licence in force: current while VALID, else next once it is VALID.

        Otherwise the current slot's status, so a refusal names the current
        licence's problem (expired, missing, ...), not the queued one's.
        """
        now = now or datetime.now(timezone.utc)
        current = self._verify(self._source.read_token(), now)
        if current.state is LicenceState.VALID:
            return current
        queued = self._read_next(now)
        if queued is not None and queued.state is LicenceState.VALID:
            return queued
        return current

    def next_status(self, *, now: datetime | None = None) -> LicenceStatus | None:
        """The queued renewal, for status endpoints and telemetry.

        ``None`` when the next slot is empty, or when ``status`` is already
        serving it (the next licence has started and replaced an expired one).
        """
        now = now or datetime.now(timezone.utc)
        queued = self._read_next(now)
        if queued is None:
            return None
        if queued.state is LicenceState.VALID:
            current = self._verify(self._source.read_token(), now)
            if current.state is not LicenceState.VALID:
                return None
        return queued

    def check(
        self,
        capability: Capability,
        *,
        generations_used: int | None = None,
        now: datetime | None = None,
    ) -> Refusal | None:
        """``None`` when allowed. ``generations_used`` is required for GENERATION."""
        if not self.enforce:
            return None
        status = self.status(now=now)
        if status.state is not LicenceState.VALID or status.claims is None:
            code = _STATE_REFUSAL.get(status.state, RefusalCode.LICENCE_INVALID)
            return Refusal(code, _REFUSAL_MESSAGES[code], capability)
        claims = status.claims
        feature = _REQUIRED_FEATURE[capability]
        if feature is not None and feature not in claims.features:
            code = RefusalCode.FEATURE_NOT_IN_PLAN
            return Refusal(code, _REFUSAL_MESSAGES[code], capability, {"plan": claims.plan})
        if capability is Capability.GENERATION:
            if generations_used is None:
                raise ValueError("generations_used is required to check GENERATION")
            limit = claims.limits.get(GENERATIONS_LIMIT_KEY)
            if limit is not None and generations_used >= limit:
                code = RefusalCode.QUOTA_EXHAUSTED
                return Refusal(code, _REFUSAL_MESSAGES[code], capability,
                               {"used": generations_used, "limit": limit,
                                "period_end": claims.period_end.isoformat()})
        return None

    def require(self, capability: Capability, **kwargs: object) -> None:
        """Raise ``LicenceRefused`` instead of returning a refusal."""
        refusal = self.check(capability, **kwargs)  # type: ignore[arg-type]
        if refusal is not None:
            raise LicenceRefused(refusal)
