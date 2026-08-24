"""Run-scoped, provider-neutral broker for OpenCode connector evidence.

This is the security boundary between an MCP tool call and a provider adapter.
It authorizes trusted capability metadata before dispatch, enforces aggregate
budgets, writes sanitized receipts, and accepts only normalized envelopes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, datetime
import hashlib
import inspect
import json
import time
from typing import Any, Protocol

from mcp_server.connectors.grants import CapabilityGrant, CapabilityGrantStore, GrantDenied


class BrokerDenied(GrantDenied):
    """A tool request was rejected without reaching an external provider."""


class CapabilityMetadata(Protocol):
    """Trusted, code-owned metadata supplied by the provider catalogue."""

    id: str
    read_only: bool
    phase_eligibility: object


class ConnectorExecutor(Protocol):
    async def __call__(
        self,
        grant: CapabilityGrant,
        capability_id: str,
        source_id: str,
        request: Mapping[str, Any],
    ) -> Any: ...


ReceiptSink = Callable[[dict[str, Any]], Awaitable[None] | None]


@dataclass(frozen=True)
class InvocationReceipt:
    invocation_id: str
    grant_id: str
    run_id: int | str
    project_id: str
    snapshot_id: str | None
    capability_id: str
    source_id: str
    status: str
    started_at: str
    completed_at: str
    latency_ms: int
    item_count: int = 0
    response_bytes: int = 0
    evidence_bytes: int = 0
    evidence_id: str | None = None
    evidence_digest: str | None = None
    limitations: tuple[str, ...] = ()
    denial_reason: str | None = None
    query_summary: Mapping[str, Any] = field(default_factory=dict)

    def to_safe_dict(self) -> dict[str, Any]:
        return asdict(self)


def _to_mapping(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif is_dataclass(value):
        value = asdict(value)
    if not isinstance(value, dict):
        raise BrokerDenied("provider returned an invalid evidence envelope")
    return value


def _capability_is_gather_read_only(metadata: Any) -> bool:
    """Check catalogue metadata, never a name prefix or model-provided label."""

    read_only = getattr(metadata, "read_only", None)
    if read_only is None and isinstance(metadata, Mapping):
        read_only = metadata.get("read_only")
    phase_eligibility = getattr(metadata, "phase_eligibility", None)
    if phase_eligibility is None:
        phase_eligibility = getattr(metadata, "phases", None)
    if phase_eligibility is None and isinstance(metadata, Mapping):
        phase_eligibility = metadata.get("phase_eligibility", metadata.get("phases"))
    if isinstance(phase_eligibility, str):
        phases = {phase_eligibility}
    elif isinstance(phase_eligibility, (list, tuple, set, frozenset)):
        phases = {str(item) for item in phase_eligibility}
    else:
        phases = set()
    return read_only is True and "gather" in phases


def sanitize_query_summary(value: Mapping[str, Any]) -> dict[str, Any]:
    """Retain typed intent for audit while refusing URLs, DSL, credentials, and payloads."""

    allowed = {
        "question", "entity_refs", "time_window", "group_by", "severities", "limit",
        "source_id", "capability_id",
    }
    sensitive = {"url", "endpoint", "authorization", "token", "credential", "dsl", "query", "body"}
    clean: dict[str, Any] = {}
    for key, raw in value.items():
        normalized = str(key).lower()
        if normalized in sensitive or key not in allowed:
            continue
        if isinstance(raw, str):
            clean[key] = raw.replace("\x00", " ")[:500]
        elif isinstance(raw, (int, float, bool)) or raw is None:
            clean[key] = raw
        elif isinstance(raw, (list, tuple)):
            clean[key] = [str(item).replace("\x00", " ")[:200] for item in raw[:25]]
        elif isinstance(raw, Mapping):
            clean[key] = {
                str(child)[:80]: str(item).replace("\x00", " ")[:200]
                for child, item in list(raw.items())[:10]
            }
    return clean


class CapabilityBroker:
    """Authorize semantic calls using immutable grants and trusted metadata."""

    def __init__(
        self,
        *,
        grants: CapabilityGrantStore,
        capability_metadata: Mapping[str, CapabilityMetadata | Mapping[str, Any]],
        executor: ConnectorExecutor,
        receipt_sink: ReceiptSink | None = None,
    ) -> None:
        self.grants = grants
        self.capability_metadata = dict(capability_metadata)
        self.executor = executor
        self.receipt_sink = receipt_sink
        self.receipts: list[InvocationReceipt] = []
        # In-memory only: accepted envelopes are reconciled immediately after
        # Gather and are never written as a raw provider export.
        self.evidence_by_id: dict[str, dict[str, Any]] = {}
        self._opaque_evidence_refs: dict[str, tuple[str, str]] = {}

    def tool_catalogue(self, token: str) -> tuple[str, ...]:
        grant = self.grants.get(token)
        return tuple(
            capability_id
            for capability_id in sorted(grant.capability_ids)
            if _capability_is_gather_read_only(self.capability_metadata.get(capability_id))
        )

    async def execute(
        self,
        token: str,
        *,
        facade_nonce: str,
        run_id: int | str,
        project_id: str,
        phase: str,
        capability_id: str,
        source_id: str,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Execute one semantic evidence call and persist a safe receipt first."""

        started = datetime.now(UTC)
        started_clock = time.monotonic()
        query_summary = sanitize_query_summary(request)
        grant: CapabilityGrant | None = None
        try:
            metadata = self.capability_metadata.get(capability_id)
            if not _capability_is_gather_read_only(metadata):
                raise BrokerDenied("capability is not trusted Gather read-only metadata")
            grant = await self.grants.authorize(
                token,
                facade_nonce=facade_nonce,
                run_id=run_id,
                project_id=project_id,
                phase=phase,
                capability_id=capability_id,
                source_id=source_id,
            )
            envelope = _to_mapping(await self.executor(grant, capability_id, source_id, request))
            self._validate_envelope(envelope, grant, capability_id, source_id)
            response_bytes = len(json.dumps(envelope, separators=(",", ":"), default=str).encode())
            items = envelope.get("items")
            item_count = len(items) if isinstance(items, list) else 0
            evidence_bytes = response_bytes
            await self.grants.settle(
                token,
                elapsed_seconds=time.monotonic() - started_clock,
                item_count=item_count,
                response_bytes=response_bytes,
                evidence_bytes=evidence_bytes,
            )
            receipt = self._receipt(
                grant, capability_id, source_id, "accepted", started, started_clock,
                query_summary=query_summary,
                item_count=item_count,
                response_bytes=response_bytes,
                evidence_bytes=evidence_bytes,
                evidence_id=str(envelope.get("evidence_id")),
                evidence_digest=str(envelope.get("digest")),
                limitations=self._limitation_codes(envelope),
            )
            await self._persist(receipt)
            self.evidence_by_id[str(envelope["evidence_id"])] = envelope
            for item in envelope.get("items", []):
                if isinstance(item, Mapping):
                    # Envelope serialization names this field ``reference``.
                    # Never accept a provider-defined alternate field here.
                    evidence_ref = item.get("reference")
                    if isinstance(evidence_ref, str) and evidence_ref.startswith("evref_"):
                        self._opaque_evidence_refs[evidence_ref] = (grant.grant_id, source_id)
            return envelope
        except BrokerDenied as exc:
            if grant is None:
                try:
                    grant = self.grants.get(token)
                except GrantDenied:
                    grant = None
            receipt = self._receipt(
                grant, capability_id, source_id, "denied", started, started_clock,
                query_summary=query_summary, denial_reason=str(exc),
            )
            await self._persist(receipt)
            raise
        except GrantDenied as exc:
            receipt = self._receipt(
                grant, capability_id, source_id, "denied", started, started_clock,
                query_summary=query_summary, denial_reason=str(exc),
            )
            await self._persist(receipt)
            raise BrokerDenied(str(exc)) from exc
        except Exception:
            receipt = self._receipt(
                grant, capability_id, source_id, "failed", started, started_clock,
                query_summary=query_summary, limitations=("provider_failure",),
            )
            await self._persist(receipt)
            raise

    @staticmethod
    def _validate_envelope(
        envelope: Mapping[str, Any], grant: CapabilityGrant,
        capability_id: str, source_id: str,
    ) -> None:
        required = {"evidence_id", "run_id", "project_id", "source_id", "capability_id", "digest"}
        if not required <= set(envelope):
            raise BrokerDenied("provider returned an incomplete evidence envelope")
        if envelope["run_id"] != grant.run_id or envelope["project_id"] != grant.project_id:
            raise BrokerDenied("provider returned cross-run/project evidence")
        if envelope["source_id"] != source_id or envelope["capability_id"] != capability_id:
            raise BrokerDenied("provider returned mismatched evidence scope")
        if not isinstance(envelope.get("evidence_id"), str) or not envelope["evidence_id"].startswith("ev_"):
            raise BrokerDenied("provider returned an invalid evidence ID")
        if not isinstance(envelope.get("digest"), str) or not envelope["digest"].startswith("sha256:"):
            raise BrokerDenied("provider returned an invalid evidence digest")

    @staticmethod
    def _limitation_codes(envelope: Mapping[str, Any]) -> tuple[str, ...]:
        raw = envelope.get("limitations")
        if not isinstance(raw, list):
            return ()
        values: list[str] = []
        for value in raw[:20]:
            if isinstance(value, str):
                values.append(value[:100])
            elif isinstance(value, Mapping) and isinstance(value.get("code"), str):
                values.append(str(value["code"])[:100])
        return tuple(values)

    @staticmethod
    def _receipt(
        grant: CapabilityGrant | None,
        capability_id: str,
        source_id: str,
        status: str,
        started: datetime,
        started_clock: float,
        *,
        query_summary: Mapping[str, Any],
        item_count: int = 0,
        response_bytes: int = 0,
        evidence_bytes: int = 0,
        evidence_id: str | None = None,
        evidence_digest: str | None = None,
        limitations: tuple[str, ...] = (),
        denial_reason: str | None = None,
    ) -> InvocationReceipt:
        completed = datetime.now(UTC)
        seed = f"{getattr(grant, 'grant_id', 'unknown')}:{capability_id}:{source_id}:{started.isoformat()}"
        return InvocationReceipt(
            invocation_id=f"inv_{hashlib.sha256(seed.encode()).hexdigest()[:24]}",
            grant_id=grant.grant_id if grant else "unknown",
            run_id=grant.run_id if grant else "unknown",
            project_id=grant.project_id if grant else "unknown",
            snapshot_id=grant.snapshot_id if grant else None,
            capability_id=capability_id,
            source_id=source_id,
            status=status,
            started_at=started.isoformat(),
            completed_at=completed.isoformat(),
            latency_ms=int((time.monotonic() - started_clock) * 1000),
            item_count=item_count,
            response_bytes=response_bytes,
            evidence_bytes=evidence_bytes,
            evidence_id=evidence_id,
            evidence_digest=evidence_digest,
            limitations=limitations,
            denial_reason=denial_reason[:200] if denial_reason else None,
            query_summary=query_summary,
        )

    async def _persist(self, receipt: InvocationReceipt) -> None:
        self.receipts.append(receipt)
        if self.receipt_sink is None:
            return
        result = self.receipt_sink(receipt.to_safe_dict())
        if inspect.isawaitable(result):
            await result

    def source_for_evidence_ref(self, token: str, evidence_ref: str) -> str | None:
        """Resolve an opaque event reference within the same run grant only."""

        grant = self.grants.get(token)
        owner = self._opaque_evidence_refs.get(evidence_ref)
        if owner is None or owner[0] != grant.grant_id:
            return None
        return owner[1]


@dataclass
class ConnectorRunRuntime:
    """Runtime object injected by the generation service after binding snapshotting."""

    broker: CapabilityBroker
    token: str
    run_id: int | str
    project_id: str
    grant_id: str
    phase: str = "gather"
    _closed: bool = False

    @property
    def enabled(self) -> bool:
        return not self._closed and bool(self.broker.tool_catalogue(self.token))

    def close(self) -> None:
        self._closed = True
        self.broker.grants.revoke(self.grant_id)
        self.broker.evidence_by_id.clear()
        self.broker._opaque_evidence_refs.clear()

    def audit_manifest(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "project_id": self.project_id,
            "grant_id": self.grant_id,
            "phase": "gather",
            "receipts": [receipt.to_safe_dict() for receipt in self.broker.receipts],
        }
