"""Resolve server-provisioned connector credentials without persisting secret values.

References are deliberately restricted to environment variables set aside for
connectors. An owner may select a provisioned credential, but cannot point a
connector at unrelated application secrets by naming their environment keys.
"""

from __future__ import annotations

import json
import os
import re

_REFERENCE = re.compile(r"^env:(NEXUS_CONNECTOR_[A-Z][A-Z0-9_]{0,95})$")


class ConnectorCredentialError(ValueError):
    """A connector credential reference is unavailable or unsupported."""


def resolve_env_secret(
    reference: str | None, *, expected_host: str, expected_port: int | None = None
) -> str:
    """Read a dedicated connector secret bound to the configured remote host.

    The provisioned environment value is a JSON object with ``host`` and
    ``secret``. A caller with a configurable port also requires ``port``.
    Binding prevents a connector owner from sending an existing credential to
    a different remote endpoint by editing connector config.
    """

    match = _REFERENCE.fullmatch(reference or "")
    if match is None:
        raise ConnectorCredentialError("Connector credential reference is invalid")
    raw = os.environ.get(match.group(1))
    if not raw:
        raise ConnectorCredentialError("Connector credential is not provisioned")
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ConnectorCredentialError("Connector credential format is invalid") from exc
    required_fields = {"host", "secret", "port"} if expected_port is not None else {"host", "secret"}
    if not isinstance(value, dict) or set(value) != required_fields:
        raise ConnectorCredentialError("Connector credential format is invalid")
    if not isinstance(value["host"], str) or value["host"].lower() != expected_host.lower():
        raise ConnectorCredentialError("Connector credential host does not match")
    if not isinstance(value["secret"], str) or not value["secret"]:
        raise ConnectorCredentialError("Connector credential format is invalid")
    if expected_port is not None and (
        not isinstance(value["port"], int)
        or isinstance(value["port"], bool)
        or value["port"] != expected_port
    ):
        raise ConnectorCredentialError("Connector credential port does not match")
    return value["secret"]
