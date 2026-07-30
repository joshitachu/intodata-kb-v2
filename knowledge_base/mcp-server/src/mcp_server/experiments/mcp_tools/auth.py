"""Short-lived, task-scoped HMAC credentials for the experiment MCP boundary."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import re
import secrets
from typing import Any

from mcp_server.experiments.domain.runtime import redact_payload


_BUDGET_KEYS = frozenset({"max_calls", "max_read_bytes", "max_result_bytes", "max_write_bytes"})
_SAFE_SCOPE = re.compile(r"^[A-Za-z0-9_.:@/-]+$")
_MAX_TOKEN_BYTES = 4096


class TaskTokenError(PermissionError):
    """Token validation failure safe to record in an audit ledger."""


@dataclass(frozen=True, slots=True)
class TaskTokenClaims:
    audience: str
    project_id: str
    experiment_id: str
    task_id: str
    source_roles: frozenset[str]
    subject_ids: frozenset[str]
    allowed_tools: frozenset[str]
    budgets: Mapping[str, int]
    issued_at: datetime
    expires_at: datetime
    key_version: str
    token_id: str


class TaskTokenIssuer:
    """Signs compact HMAC tokens without persisting or exposing secret material."""

    def __init__(
        self,
        *,
        key_version: str,
        signing_key: bytes,
        audience: str = "nexus-experiment-mcp",
        max_ttl: timedelta = timedelta(minutes=15),
    ) -> None:
        if not key_version or not signing_key or not audience:
            raise ValueError("token key version, signing key, and audience are required")
        if max_ttl.total_seconds() <= 0:
            raise ValueError("maximum task token TTL must be positive")
        self._key_version = key_version
        self._signing_key = signing_key
        self._audience = audience
        self._max_ttl = max_ttl

    def issue(
        self,
        *,
        project_id: str,
        experiment_id: str,
        task_id: str,
        source_roles: frozenset[str],
        subject_ids: frozenset[str],
        allowed_tools: frozenset[str],
        budgets: Mapping[str, int],
        now: datetime | None = None,
        ttl: timedelta = timedelta(minutes=10),
    ) -> str:
        if not all((project_id, experiment_id, task_id)) or ttl.total_seconds() <= 0:
            raise ValueError("task identity and positive token TTL are required")
        if ttl > self._max_ttl:
            raise ValueError("task token TTL exceeds configured maximum")
        _assert_scope(source_roles, "source_roles")
        _assert_scope(subject_ids, "subject_ids")
        _assert_scope(allowed_tools, "allowed_tools")
        _assert_budgets(budgets, error_type=ValueError)
        issued_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        payload = {
            "aud": self._audience,
            "bud": dict(sorted(budgets.items())),
            "exp": int((issued_at + ttl).timestamp()),
            "exp_id": experiment_id,
            "iat": int(issued_at.timestamp()),
            "jti": secrets.token_urlsafe(18),
            "kid": self._key_version,
            "project_id": project_id,
            "roles": sorted(source_roles),
            "subjects": sorted(subject_ids),
            "task_id": task_id,
            "tools": sorted(allowed_tools),
        }
        header = {"alg": "HS256", "typ": "NEXUS-TASK"}
        signing_input = f"{_b64_json(header)}.{_b64_json(payload)}".encode("ascii")
        signature = hmac.new(self._signing_key, signing_input, hashlib.sha256).digest()
        return f"{signing_input.decode('ascii')}.{_b64(signature)}"


class TaskTokenVerifier:
    def __init__(
        self,
        *,
        keys: Mapping[str, bytes],
        audience: str = "nexus-experiment-mcp",
        max_ttl: timedelta = timedelta(minutes=15),
    ) -> None:
        if not keys or not audience:
            raise ValueError("verification keys and audience are required")
        if max_ttl.total_seconds() <= 0:
            raise ValueError("maximum task token TTL must be positive")
        self._keys = dict(keys)
        self._audience = audience
        self._max_ttl = max_ttl

    def verify(
        self,
        token: str,
        *,
        now: datetime | None = None,
        project_id: str | None = None,
        experiment_id: str | None = None,
        task_id: str | None = None,
    ) -> TaskTokenClaims:
        if not isinstance(token, str) or len(token.encode("utf-8")) > _MAX_TOKEN_BYTES or token.count(".") != 2:
            raise TaskTokenError("malformed task token")
        try:
            header_part, payload_part, signature_part = token.split(".")
            if not all(part and len(part) <= _MAX_TOKEN_BYTES for part in (header_part, payload_part, signature_part)):
                raise ValueError("invalid token segment length")
            header = _decode_json(header_part)
            payload = _decode_json(payload_part)
        except (ValueError, json.JSONDecodeError) as exc:
            raise TaskTokenError("malformed task token") from exc
        if header.get("alg") != "HS256" or header.get("typ") != "NEXUS-TASK":
            raise TaskTokenError("unsupported task token header")
        key_version = _string(payload, "kid")
        key = self._keys.get(key_version)
        if key is None:
            raise TaskTokenError("unknown task token key version")
        signing_input = f"{header_part}.{payload_part}".encode("ascii")
        expected = hmac.new(key, signing_input, hashlib.sha256).digest()
        try:
            actual = _unb64(signature_part)
        except ValueError as exc:
            raise TaskTokenError("malformed task token signature") from exc
        if not hmac.compare_digest(expected, actual):
            raise TaskTokenError("invalid task token signature")
        now_epoch = int((now or datetime.now(timezone.utc)).timestamp())
        if _string(payload, "aud") != self._audience:
            raise TaskTokenError("task token audience mismatch")
        issued_epoch = _integer(payload, "iat")
        expires_epoch = _integer(payload, "exp")
        if issued_epoch > now_epoch:
            raise TaskTokenError("task token issued in the future")
        if expires_epoch <= issued_epoch:
            raise TaskTokenError("task token expiry must follow issue time")
        if expires_epoch - issued_epoch > int(self._max_ttl.total_seconds()):
            raise TaskTokenError("task token TTL exceeds configured maximum")
        if expires_epoch <= now_epoch:
            raise TaskTokenError("task token expired")
        claims = TaskTokenClaims(
            audience=self._audience,
            project_id=_string(payload, "project_id"),
            experiment_id=_string(payload, "exp_id"),
            task_id=_string(payload, "task_id"),
            source_roles=frozenset(_strings(payload, "roles")),
            subject_ids=frozenset(_strings(payload, "subjects")),
            allowed_tools=frozenset(_strings(payload, "tools")),
            budgets={key: value for key, value in _mapping_ints(payload, "bud").items()},
            issued_at=datetime.fromtimestamp(issued_epoch, tz=timezone.utc),
            expires_at=datetime.fromtimestamp(expires_epoch, tz=timezone.utc),
            key_version=key_version,
            token_id=_string(payload, "jti"),
        )
        _assert_scope(claims.source_roles, "roles", error_type=TaskTokenError)
        _assert_scope(claims.subject_ids, "subjects", error_type=TaskTokenError)
        _assert_scope(claims.allowed_tools, "tools", error_type=TaskTokenError)
        for expected_id, actual_id, label in (
            (project_id, claims.project_id, "project"),
            (experiment_id, claims.experiment_id, "experiment"),
            (task_id, claims.task_id, "task"),
        ):
            if expected_id is not None and expected_id != actual_id:
                raise TaskTokenError(f"task token {label} scope mismatch")
        return claims


def token_audit_fields(claims: TaskTokenClaims) -> dict[str, Any]:
    """Safe token metadata for audit logs; never include the bearer value."""

    return redact_payload(
        {
            "audience": claims.audience,
            "experiment_id": claims.experiment_id,
            "key_version": claims.key_version,
            "project_id": claims.project_id,
            "task_id": claims.task_id,
            "token_id": claims.token_id,
        }
    )


def _assert_scope(
    values: frozenset[str],
    label: str,
    *,
    error_type: type[Exception] = ValueError,
) -> None:
    if not values or any(not value or value == "*" or not _SAFE_SCOPE.fullmatch(value) for value in values):
        raise error_type(f"{label} must contain explicit non-wildcard values")


def _assert_budgets(values: Mapping[str, int], *, error_type: type[Exception]) -> None:
    if set(values) != _BUDGET_KEYS or any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in values.values()
    ):
        raise error_type("task token budgets must have exact non-negative keys")


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    if (
        not value
        or not re.fullmatch(r"[A-Za-z0-9_-]+", value)
        or len(value) % 4 == 1
    ):
        raise ValueError("invalid base64url")
    try:
        decoded = base64.b64decode(
            value + "=" * (-len(value) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, binascii.Error) as exc:
        raise ValueError("invalid base64url") from exc
    if not hmac.compare_digest(_b64(decoded), value):
        raise ValueError("non-canonical base64url")
    return decoded


def _b64_json(value: Mapping[str, Any]) -> str:
    return _b64(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _decode_json(value: str) -> Mapping[str, Any]:
    decoded = json.loads(_unb64(value))
    if not isinstance(decoded, Mapping):
        raise ValueError("token section must be an object")
    return decoded


def _string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise TaskTokenError(f"task token missing {key}")
    return value


def _integer(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TaskTokenError(f"task token has invalid {key}")
    return value


def _strings(payload: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise TaskTokenError(f"task token has invalid {key}")
    return tuple(value)


def _mapping_ints(payload: Mapping[str, Any], key: str) -> Mapping[str, int]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise TaskTokenError(f"task token has invalid {key}")
    _assert_budgets(value, error_type=TaskTokenError)  # type: ignore[arg-type]
    return value
