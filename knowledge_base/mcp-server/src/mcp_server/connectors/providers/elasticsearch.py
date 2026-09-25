"""Real Elasticsearch data-plane adapter using only semantic, bounded calls."""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import uuid
from typing import Any, Mapping
from urllib.parse import quote

from ..catalog import connector_catalog
from ..domain import (
    ConnectorBudget,
    ConnectorBuildState,
    ConnectorCapability,
    ConnectorInstanceConfig,
    ConnectorKind,
    ConnectorProviderManifest,
    ConnectorSourceProfile,
    ConnectorStatus,
    EvidenceEnvelope,
    EvidenceItem,
    EvidenceQuestion,
)
from ..evidence import finalize_envelope, make_opaque_reference, normalize_event_fields
from ..errors import ConnectorError
from ..transport import ConnectorTransport, OutboundPolicy, normalize_endpoint


_CAPABILITIES = (
    ConnectorCapability(
        id="evidence.sources.describe",
        label="Describe evidence source",
        description="Return the approved source profile and field choices.",
        default_budget=ConnectorBudget(max_calls=2, max_items=1),
    ),
    ConnectorCapability(
        id="runtime.events.aggregate",
        label="Aggregate runtime events",
        description="Aggregate a bounded event window before sampling events.",
        accepted_entity_ref_types=("unit", "flow", "job", "service", "endpoint", "correlation"),
    ),
    ConnectorCapability(
        id="runtime.events.search",
        label="Search runtime events",
        description="Retrieve a bounded set of redacted representative events.",
        accepted_entity_ref_types=("unit", "flow", "job", "service", "endpoint", "correlation"),
    ),
    ConnectorCapability(
        id="runtime.events.get_item",
        label="Get evidence item",
        description="Retrieve one event via an opaque reference issued in this run.",
        default_budget=ConnectorBudget(max_calls=12, max_items=1),
    ),
)

# Keep discovery bounded to event fields. Field capabilities accepts read-only
# index access; GET /{index} would additionally require metadata privileges.
_EVENT_FIELD_GROUPS = (
    ("@timestamp", "timestamp", "*time*", "*Time*", "*TIME*", "*date*", "*Date*"),
    ("*message*", "*Message*", "*MESSAGE*", "*error*", "*exception*", "*stack*", "*reason*"),
    (
        "*service*",
        "*job*",
        "*flow*",
        "*component*",
        "*application*",
        "*severity*",
        "*level*",
        "*environment*",
        "*env*",
        "*host*",
        "*node*",
    ),
    (
        "*failure*",
        "*log*",
        "*detail*",
        "*description*",
        "*original*",
        "*body*",
        "*payload*",
        "*content*",
        "*event*",
        "*trace*",
        "*correlation*",
        "*execution*",
        "*transaction*",
    ),
)


ELASTICSEARCH_MANIFEST = ConnectorProviderManifest(
    provider_id="elastic_stack",
    label="Elastic Stack",
    version="1",
    authentication_types=("api_key", "basic"),
    configuration_schema={
        "endpoint": "https URL",
        "auth_type": "api_key|basic",
        "verify_tls": "boolean",
    },
    secret_fields=("credential", "kibana_credential"),
    capabilities=_CAPABILITIES,
    kind=ConnectorKind.LIVE_EVIDENCE,
    build_state=ConnectorBuildState.AVAILABLE,
    description="Query runtime events and logs held in an Elastic cluster.",
    # One connection per cluster: the endpoint identifies the account.
    account_key_field="endpoint",
)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _source_locator(source: ConnectorSourceProfile) -> str:
    # Locators are system-owned values from a source profile; never accept a
    # caller-provided target expression.  Reject path separators defensively.
    locator = source.locator.strip()
    if not locator or "/" in locator or "\\" in locator:
        raise ConnectorError("invalid_source", "Approved source profile is invalid")
    return locator


class ElasticsearchProvider:
    manifest = ELASTICSEARCH_MANIFEST

    def __init__(self, transport: ConnectorTransport | None = None) -> None:
        self._transport = transport
        self._opaque_items: dict[str, tuple[int, str, str, str]] = {}

    def _transport_for(self, instance: ConnectorInstanceConfig) -> ConnectorTransport:
        if self._transport is not None:
            return self._transport
        safe = instance.safe_config
        allowed_private = safe.get("approved_private_hosts", [])
        from mcp_server.config import config

        allow_http = config.app_env == "development" and bool(
            safe.get("development_allow_http", False)
        )
        policy = OutboundPolicy(
            require_https=not allow_http,
            allow_private_hosts=frozenset(
                str(item) for item in allowed_private if isinstance(item, str)
            ),
            ca_bundle=str(safe["ca_bundle_path"]) if safe.get("ca_bundle_path") else None,
            custom_ca_pem=str(safe["custom_ca_pem"]) if safe.get("custom_ca_pem") else None,
        )
        return ConnectorTransport(policy)

    def discard_evidence_references(self, envelope: EvidenceEnvelope) -> None:
        """Release opaque item lookups when a caller does not expose get-item."""
        for item in envelope.items:
            self._opaque_items.pop(item.reference, None)

    @staticmethod
    def _endpoint(instance: ConnectorInstanceConfig) -> str:
        from mcp_server.config import config

        endpoint = instance.safe_config.get("endpoint")
        if not isinstance(endpoint, str):
            raise ConnectorError("invalid_endpoint", "Elasticsearch endpoint is required")
        allow_http = config.app_env == "development" and bool(
            instance.safe_config.get("development_allow_http", False)
        )
        return normalize_endpoint(endpoint, require_https=not allow_http)

    @staticmethod
    def _headers(instance: ConnectorInstanceConfig, credential: str) -> dict[str, str]:
        auth_type = instance.safe_config.get("auth_type", "api_key")
        if auth_type == "api_key":
            return {"Authorization": f"ApiKey {credential}", "Accept": "application/json"}
        if auth_type == "basic":
            # credential is stored as an already encoded user:password pair only
            # while in memory; transport never logs this header.
            import base64

            encoded = base64.b64encode(credential.encode()).decode()
            return {"Authorization": f"Basic {encoded}", "Accept": "application/json"}
        raise ConnectorError("invalid_credentials", "Unsupported Elasticsearch authentication type")

    async def test(self, instance: ConnectorInstanceConfig, credential: str) -> dict[str, object]:
        payload, _ = await self._transport_for(instance).request_json(
            "GET", f"{self._endpoint(instance)}/", headers=self._headers(instance, credential)
        )
        cluster_name = payload.get("cluster_name") if isinstance(payload, dict) else None
        version = _as_mapping(payload.get("version") if isinstance(payload, dict) else None).get(
            "number"
        )
        return {
            "status": ConnectorStatus.CONNECTED.value,
            "cluster_name": cluster_name,
            "version": version,
        }

    async def discover(
        self, instance: ConnectorInstanceConfig, credential: str, **options: object
    ) -> list[ConnectorSourceProfile]:
        payload, _ = await self._transport_for(instance).request_json(
            "GET",
            f"{self._endpoint(instance)}/_resolve/index/*",
            headers=self._headers(instance, credential),
            params={"expand_wildcards": "open", "ignore_unavailable": "true"},
        )
        if not isinstance(payload, dict):
            raise ConnectorError(
                "malformed_response", "Elasticsearch discovery response was invalid"
            )
        found: list[ConnectorSourceProfile] = []
        kinds = (("indices", "index"), ("aliases", "alias"), ("data_streams", "data_stream"))
        for key, source_type in kinds:
            for item in payload.get(key, []):
                if not isinstance(item, Mapping):
                    continue
                name = item.get("name")
                if not isinstance(name, str) or not name or name.startswith("."):
                    continue
                found.append(
                    ConnectorSourceProfile(
                        id=f"src_{hashlib.sha256((instance.id + ':' + source_type + ':' + name).encode()).hexdigest()[:24]}",
                        connector_instance_id=instance.id,
                        source_type=source_type,
                        display_label=name,
                        locator=name,
                        fields={},
                    )
                )
        return found

    async def describe(
        self, instance: ConnectorInstanceConfig, source: ConnectorSourceProfile, credential: str
    ) -> ConnectorSourceProfile:
        locator = _source_locator(source)
        capability_fields: dict[str, str] = {}
        transport = self._transport_for(instance)
        endpoint = f"{self._endpoint(instance)}/{quote(locator, safe='*,-_')}/_field_caps"
        headers = self._headers(instance, credential)
        try:
            for patterns in _EVENT_FIELD_GROUPS:
                caps_response, _ = await transport.request_json(
                    "POST",
                    endpoint,
                    headers=headers,
                    json={"fields": list(patterns)},
                )
                capability_fields.update(_field_capability_fields(caps_response))
        except ConnectorError:
            # Search-only API keys and some Elastic-compatible gateways reject
            # ``_field_caps`` even though their bounded search API is usable.
            # Inspect only field names/types from a tiny filtered sample; raw
            # values never leave this method and are never persisted.
            capability_fields = await self._sample_field_capabilities(instance, locator, credential)
            for field_name, field_type in _legacy_profile_capabilities(source.fields).items():
                capability_fields.setdefault(field_name, field_type)
        return ConnectorSourceProfile(
            id=source.id,
            connector_instance_id=source.connector_instance_id,
            source_type=source.source_type,
            display_label=source.display_label,
            locator=locator,
            fields=_detect_field_profile(capability_fields),
            enabled=source.enabled,
            discovered_at=dt.datetime.now(dt.timezone.utc),
            discovery_digest=hashlib.sha256(repr(sorted(capability_fields)).encode()).hexdigest(),
        )

    async def _sample_field_capabilities(
        self, instance: ConnectorInstanceConfig, locator: str, credential: str
    ) -> dict[str, str]:
        patterns = list(
            dict.fromkeys(pattern for group in _EVENT_FIELD_GROUPS for pattern in group)
        )
        payload, _ = await self._transport_for(instance).request_json(
            "POST",
            f"{self._endpoint(instance)}/{quote(locator, safe='*,-_')}/_search",
            headers=self._headers(instance, credential),
            json={
                "size": 5,
                "track_total_hits": False,
                "query": {"match_all": {}},
                "_source": {"includes": patterns},
            },
        )
        hits = _as_mapping(_as_mapping(payload).get("hits")).get("hits", [])
        discovered: dict[str, str] = {}
        if isinstance(hits, list):
            for hit in hits[:5]:
                if not isinstance(hit, Mapping):
                    continue
                _collect_sample_field_types(_as_mapping(hit.get("_source")), discovered)
        return discovered

    async def execute(
        self,
        *,
        instance: ConnectorInstanceConfig,
        source: ConnectorSourceProfile,
        credential: str,
        capability_id: str,
        question: EvidenceQuestion,
        run_id: int,
        project_id: str,
    ) -> EvidenceEnvelope:
        if capability_id not in {capability.id for capability in self.manifest.capabilities}:
            raise ConnectorError("capability_denied", "Unsupported Elasticsearch capability")
        if capability_id == "evidence.sources.describe":
            return self._describe_envelope(instance, source, question, run_id, project_id)
        if capability_id == "runtime.events.get_item":
            return await self._get_item(instance, source, credential, question, run_id, project_id)
        if question.time_from is None or question.time_to is None:
            raise ConnectorError("invalid_query", "Runtime evidence requires a bounded time range")
        max_days = int(source.fields.get("max_time_window_days", 30))
        if (
            question.time_to <= question.time_from
            or question.time_to - question.time_from > dt.timedelta(days=max_days)
        ):
            raise ConnectorError(
                "invalid_query", "Requested time range exceeds the approved source profile"
            )
        if capability_id == "runtime.events.aggregate":
            return await self._aggregate(instance, source, credential, question, run_id, project_id)
        return await self._search(instance, source, credential, question, run_id, project_id)

    def _describe_envelope(
        self,
        instance: ConnectorInstanceConfig,
        source: ConnectorSourceProfile,
        question: EvidenceQuestion,
        run_id: int,
        project_id: str,
    ) -> EvidenceEnvelope:
        now = dt.datetime.now(dt.timezone.utc)
        return finalize_envelope(
            EvidenceEnvelope(
                evidence_id=f"ev_{uuid.uuid4().hex}",
                run_id=run_id,
                project_id=project_id,
                connector_instance_id=instance.id,
                source_id=source.id,
                capability_id="evidence.sources.describe",
                question=question.question,
                entity_refs=question.entity_refs,
                observed_at=now,
                source_window={},
                summary={
                    "display_label": source.display_label,
                    "source_type": source.source_type,
                    "fields": dict(source.fields),
                    "samples_allowed": bool(source.fields.get("samples_allowed", True)),
                },
            )
        )

    async def _aggregate(
        self,
        instance: ConnectorInstanceConfig,
        source: ConnectorSourceProfile,
        credential: str,
        question: EvidenceQuestion,
        run_id: int,
        project_id: str,
    ) -> EvidenceEnvelope:
        group_by = question.group_by[:3] or ("severity",)
        aggregations: dict[str, Any] = {}
        for logical_name in group_by:
            field = _logical_field(source, logical_name)
            if field is None:
                raise ConnectorError(
                    "invalid_query", "Requested grouping is not approved for this source"
                )
            aggregations[logical_name] = {
                "terms": {"field": field, "size": min(question.limit or 25, 25)}
            }
        body = {
            "size": 0,
            "track_total_hits": False,
            "query": _query(source, question),
            "aggs": aggregations,
        }
        payload, _ = await self._transport_for(instance).request_json(
            "POST",
            f"{self._endpoint(instance)}/{quote(_source_locator(source), safe='*,-_')}/_search",
            headers=self._headers(instance, credential),
            json=body,
        )
        aggs = _as_mapping(payload).get("aggregations", {})
        summary: dict[str, Any] = {"groups": {}}
        if isinstance(aggs, Mapping):
            for logical_name, result in aggs.items():
                buckets = _as_mapping(result).get("buckets", [])
                if isinstance(buckets, list):
                    summary["groups"][logical_name] = [
                        {
                            "key": str(bucket.get("key", ""))[:256],
                            "count": int(bucket.get("doc_count", 0)),
                        }
                        for bucket in buckets[:25]
                        if isinstance(bucket, Mapping)
                    ]
        return self._envelope(
            instance, source, "runtime.events.aggregate", question, run_id, project_id, (), summary
        )

    async def check_freshness(
        self,
        instance: ConnectorInstanceConfig,
        source: ConnectorSourceProfile,
        credential: str,
        *,
        checked_at: dt.datetime,
        window_days: int,
    ) -> dict[str, object]:
        """Return source-level coverage without retrieving any event documents."""
        timestamp_field = _timestamp_field(source)
        max_days = int(source.fields.get("max_time_window_days", 30))
        if window_days < 1 or window_days > max_days:
            raise ConnectorError(
                "invalid_query", "Requested freshness window exceeds the approved source profile"
            )
        window_start = checked_at - dt.timedelta(days=window_days)
        body = {
            "size": 0,
            "track_total_hits": True,
            "query": {"match_all": {}},
            "aggs": {
                "latest_event": {"max": {"field": timestamp_field}},
                "timestamped_documents": {"value_count": {"field": timestamp_field}},
                "events_in_window": {
                    "filter": {
                        "range": {
                            timestamp_field: {
                                "gte": window_start.isoformat(),
                                "lte": checked_at.isoformat(),
                            }
                        }
                    }
                },
            },
        }
        payload, _ = await self._transport_for(instance).request_json(
            "POST",
            f"{self._endpoint(instance)}/{quote(_source_locator(source), safe='*,-_')}/_search",
            headers=self._headers(instance, credential),
            json=body,
        )
        response = _as_mapping(payload)
        hits = _as_mapping(response.get("hits"))
        total = hits.get("total", 0)
        if isinstance(total, Mapping):
            total = total.get("value", 0)
        aggregations = _as_mapping(response.get("aggregations"))
        latest = _as_mapping(aggregations.get("latest_event"))
        recent = _as_mapping(aggregations.get("events_in_window"))
        timestamped = _as_mapping(aggregations.get("timestamped_documents"))
        latest_at = latest.get("value_as_string")
        if not isinstance(latest_at, str):
            raw_latest = latest.get("value")
            if isinstance(raw_latest, (int, float)):
                try:
                    latest_at = dt.datetime.fromtimestamp(
                        raw_latest / 1000, tz=dt.timezone.utc
                    ).isoformat()
                except (OverflowError, OSError, ValueError):
                    latest_at = None
            else:
                latest_at = None
        return {
            "latest_event_at": latest_at,
            "events_in_window": _nonnegative_int(recent.get("doc_count", 0)),
            "total_documents": _nonnegative_int(total),
            "timestamped_documents": _nonnegative_int(timestamped.get("value", 0)),
            "freshness_status": _freshness_status(
                events_in_window=_nonnegative_int(recent.get("doc_count", 0)),
                total_documents=_nonnegative_int(total),
                timestamped_documents=_nonnegative_int(timestamped.get("value", 0)),
            ),
        }

    async def _search(
        self,
        instance: ConnectorInstanceConfig,
        source: ConnectorSourceProfile,
        credential: str,
        question: EvidenceQuestion,
        run_id: int,
        project_id: str,
    ) -> EvidenceEnvelope:
        if not bool(source.fields.get("samples_allowed", True)):
            raise ConnectorError("capability_denied", "This source permits aggregates only")
        limit = min(max(1, question.limit or 10), 10)
        includes = list(source.approved_fields)[:30]
        body = {
            "size": limit,
            "track_total_hits": False,
            "query": _query(source, question),
            "_source": {"includes": includes},
            "sort": [{_timestamp_field(source): {"order": "desc"}}],
        }
        payload, _ = await self._transport_for(instance).request_json(
            "POST",
            f"{self._endpoint(instance)}/{quote(_source_locator(source), safe='*,-_')}/_search",
            headers=self._headers(instance, credential),
            json=body,
        )
        hits = _as_mapping(_as_mapping(payload).get("hits")).get("hits", [])
        items: list[EvidenceItem] = []
        if isinstance(hits, list):
            for hit in hits[:limit]:
                if not isinstance(hit, Mapping):
                    continue
                raw_id = hit.get("_id")
                raw_index = hit.get("_index")
                fields = _as_mapping(hit.get("_source"))
                if not isinstance(raw_id, str) or not isinstance(raw_index, str):
                    continue
                ref = make_opaque_reference(run_id, source.id, f"{raw_index}:{raw_id}")
                self._opaque_items[ref] = (run_id, source.id, raw_index, raw_id)
                projected = _project_event_fields(fields, source.approved_fields)
                normalized, _, _ = normalize_event_fields(projected, set(source.approved_fields))
                items.append(
                    EvidenceItem(ref, _event_time(fields, _timestamp_field(source)), normalized)
                )
        return self._envelope(
            instance,
            source,
            "runtime.events.search",
            question,
            run_id,
            project_id,
            tuple(items),
            {"item_count": len(items)},
        )

    async def _get_item(
        self,
        instance: ConnectorInstanceConfig,
        source: ConnectorSourceProfile,
        credential: str,
        question: EvidenceQuestion,
        run_id: int,
        project_id: str,
    ) -> EvidenceEnvelope:
        reference = question.question.strip()
        stored = self._opaque_items.get(reference)
        if stored is None or stored[0] != run_id or stored[1] != source.id:
            raise ConnectorError(
                "evidence_not_found", "Evidence item is not available for this run"
            )
        _, _, index, doc_id = stored
        payload, _ = await self._transport_for(instance).request_json(
            "GET",
            f"{self._endpoint(instance)}/{quote(index, safe='*,-_')}/_doc/{quote(doc_id, safe='')}",
            headers=self._headers(instance, credential),
            params={"_source_includes": ",".join(source.approved_fields[:30])},
        )
        fields = _as_mapping(_as_mapping(payload).get("_source"))
        projected = _project_event_fields(fields, source.approved_fields)
        normalized, _, _ = normalize_event_fields(projected, set(source.approved_fields))
        item = EvidenceItem(reference, _event_time(fields, _timestamp_field(source)), normalized)
        return self._envelope(
            instance,
            source,
            "runtime.events.get_item",
            question,
            run_id,
            project_id,
            (item,),
            {"item_count": 1},
        )

    def _envelope(
        self,
        instance: ConnectorInstanceConfig,
        source: ConnectorSourceProfile,
        capability: str,
        question: EvidenceQuestion,
        run_id: int,
        project_id: str,
        items: tuple[EvidenceItem, ...],
        summary: Mapping[str, Any],
    ) -> EvidenceEnvelope:
        envelope = EvidenceEnvelope(
            evidence_id=f"ev_{uuid.uuid4().hex}",
            run_id=run_id,
            project_id=project_id,
            connector_instance_id=instance.id,
            source_id=source.id,
            capability_id=capability,
            question=question.question,
            entity_refs=question.entity_refs,
            observed_at=dt.datetime.now(dt.timezone.utc),
            source_window={
                "from": question.time_from.isoformat() if question.time_from else "",
                "to": question.time_to.isoformat() if question.time_to else "",
            },
            items=items,
            summary=summary,
        )
        return finalize_envelope(envelope)


def _field_capability_fields(response: Any) -> dict[str, str]:
    output: dict[str, str] = {}
    for name, types in _as_mapping(_as_mapping(response).get("fields")).items():
        if not isinstance(name, str) or not isinstance(types, Mapping) or not types:
            continue
        # Field caps may report more than one concrete type when an alias spans
        # heterogeneous indices. Keep a field only when at least one concrete
        # type is searchable, and prefer the useful type over object metadata.
        candidates: list[str] = []
        for type_name, details in types.items():
            if not isinstance(type_name, str):
                continue
            detail = _as_mapping(details)
            if detail.get("searchable") is False:
                continue
            candidates.append(str(detail.get("type") or type_name))
        if candidates:
            priority = {"text": 0, "match_only_text": 1, "wildcard": 2, "keyword": 3, "date": 4}
            output[name] = min(candidates, key=lambda value: priority.get(value.casefold(), 10))
    return output


def _legacy_profile_capabilities(profile: Mapping[str, Any]) -> dict[str, str]:
    """Upgrade useful names from a pre-v2 profile without trusting log values."""
    output: dict[str, str] = {}
    timestamp = profile.get("timestamp_field")
    if isinstance(timestamp, str) and timestamp:
        output[timestamp] = "date"
    for key in ("message_fields", "search_fields", "context_fields"):
        values = profile.get(key, [])
        if not isinstance(values, (list, tuple)):
            continue
        for value in values:
            if not isinstance(value, str) or not value:
                continue
            canonical = re.sub(r"\.(?:keyword|raw)$", "", value, flags=re.IGNORECASE)
            output.setdefault(canonical, "text")
    for key in ("service_field", "severity_field", "environment_field"):
        value = profile.get(key)
        if isinstance(value, str) and value:
            output.setdefault(value, "keyword")
    return output


def _sample_field_type(field_name: str, value: Any) -> str | None:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "long"
    if isinstance(value, float):
        return "double"
    if not isinstance(value, str):
        return None
    normalized_name = field_name.casefold()
    if normalized_name == "@timestamp" or normalized_name.rsplit(".", 1)[-1] in {
        "timestamp",
        "created",
        "ingested",
    }:
        try:
            dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            return "date"
        except ValueError:
            pass
    return "text"


def _collect_sample_field_types(
    value: Mapping[str, Any], output: dict[str, str], *, prefix: str = "", depth: int = 0
) -> None:
    """Collect only bounded field names and inferred scalar types from a sample."""
    if depth >= 8 or len(output) >= 512:
        return
    for key, child in value.items():
        if not isinstance(key, str) or not key:
            continue
        field_name = f"{prefix}.{key}" if prefix else key
        if isinstance(child, Mapping):
            _collect_sample_field_types(child, output, prefix=field_name, depth=depth + 1)
            continue
        if isinstance(child, (list, tuple)):
            for item in child[:10]:
                if isinstance(item, Mapping):
                    _collect_sample_field_types(item, output, prefix=field_name, depth=depth + 1)
                else:
                    field_type = _sample_field_type(field_name, item)
                    if field_type:
                        output.setdefault(field_name, field_type)
                if len(output) >= 512:
                    return
            continue
        field_type = _sample_field_type(field_name, child)
        if field_type:
            output.setdefault(field_name, field_type)
        if len(output) >= 512:
            return


def _detect_field_profile(capability_fields: Mapping[str, str]) -> dict[str, Any]:
    names = list(capability_fields)

    def pick(candidates: tuple[str, ...], *, types: set[str] | None = None) -> str | None:
        eligible = [
            name for name in names if types is None or capability_fields[name].casefold() in types
        ]
        lower = {name.casefold(): name for name in eligible}
        for candidate in candidates:
            if candidate.casefold() in lower:
                return lower[candidate.casefold()]
        # Prefer a nested leaf match (``event.timestamp``) over a broad
        # substring (``processing_time_ms``). Candidate order is semantic
        # priority and name order never decides the chosen role.
        for candidate in candidates:
            matches = [
                name
                for name in eligible
                if name.rsplit(".", 1)[-1].casefold() == candidate.casefold()
            ]
            if matches:
                return min(matches, key=lambda name: (name.count("."), len(name), name.casefold()))
        for candidate in candidates:
            matches = [name for name in eligible if candidate.casefold() in name.casefold()]
            if matches:
                return min(matches, key=lambda name: (len(name), name.casefold()))
        return None

    string_types = {"text", "match_only_text", "keyword", "wildcard"}
    searchable = [
        name
        for name, field_type in capability_fields.items()
        if field_type.casefold() in string_types
    ]
    # Avoid returning/querying both ``message`` and its ``message.keyword``
    # multi-field. The original field retains more useful content and prevents
    # duplicate values in bounded observations.
    searchable = [
        name
        for name in searchable
        if not (
            name.rsplit(".", 1)[-1].casefold() in {"keyword", "raw"}
            and name.rsplit(".", 1)[0] in capability_fields
        )
    ]
    searchable.sort(
        key=lambda name: (
            0
            if any(
                token in name.casefold()
                for token in ("message", "error", "exception", "stack", "reason", "failure")
            )
            else 1,
            0 if capability_fields[name].casefold() in {"text", "match_only_text"} else 1,
            name.casefold(),
        )
    )
    message_fields = [
        name
        for name in searchable
        if any(
            token in name.casefold()
            for token in (
                "message",
                "error",
                "exception",
                "stack",
                "reason",
                "failure",
                "description",
                "detail",
            )
        )
        # ``log.original`` / ``event.original`` remain searchable but are not
        # projected to chat. Labs returns bounded, sanitized evidence fields,
        # never the original raw log payload.
        and not name.casefold().endswith(".original")
    ][:5]
    timestamp_field = pick(
        ("@timestamp", "timestamp", "event.created", "event.ingested", "time"),
        types={"date", "date_nanos"},
    )
    service_field = pick(
        ("service.name", "service", "service_name", "application.name", "application")
    )
    severity_field = pick(("log.level", "severity", "level", "event.severity"))
    environment_field = pick(
        ("service.environment", "deployment.environment", "environment", "env")
    )
    context_fields = [
        value
        for value in (
            pick(("job.name", "job", "job_name", "jobname")),
            pick(("flow.name", "flow", "route.name", "route")),
            pick(("component.name", "component", "logger_name", "logger")),
            pick(("host.name", "host", "node.name", "node")),
            pick(("trace.id", "trace_id", "traceid")),
            pick(
                (
                    "transaction.id",
                    "correlation.id",
                    "correlation_id",
                    "execution.id",
                    "execution_id",
                )
            ),
        )
        if value
    ]
    preferred_searchable = list(
        dict.fromkeys(
            [
                *message_fields,
                service_field,
                severity_field,
                environment_field,
                *context_fields,
                *searchable,
            ]
        )
    )
    profile: dict[str, Any] = {
        "profile_version": 2,
        "timestamp_field": timestamp_field,
        "message_fields": message_fields
        or [value for value in (pick(("message", "log.message", "error.message")),) if value],
        "search_fields": [value for value in preferred_searchable if value][:12],
        "service_field": service_field,
        "severity_field": severity_field,
        "environment_field": environment_field,
        "context_fields": context_fields,
        "max_time_window_days": 30,
        "samples_allowed": True,
    }
    return {key: value for key, value in profile.items() if value not in (None, [])}


def _freshness_status(
    *, events_in_window: int, total_documents: int, timestamped_documents: int
) -> str:
    """Classify coverage without guessing an environment-specific age limit."""
    if events_in_window > 0:
        return "active"
    if timestamped_documents > 0:
        return "stale"
    if total_documents > 0:
        return "timestamp_unavailable"
    return "empty"


def _nested_field_value(fields: Mapping[str, Any], field_name: str) -> tuple[bool, Any]:
    """Read a field-caps dotted path from Elasticsearch's nested ``_source``."""
    if field_name in fields:
        return True, fields[field_name]

    parts = field_name.split(".")

    def descend(value: Any, remaining: list[str]) -> tuple[bool, Any]:
        if not remaining:
            return True, value
        if isinstance(value, Mapping):
            part = remaining[0]
            if part not in value:
                return False, None
            return descend(value[part], remaining[1:])
        if isinstance(value, (list, tuple)):
            collected: list[Any] = []
            for item in value[:100]:
                found, nested = descend(item, remaining)
                if found:
                    collected.append(nested)
            if collected:
                return True, collected
        return False, None

    return descend(fields, parts)


def _project_event_fields(
    fields: Mapping[str, Any], approved_fields: tuple[str, ...]
) -> dict[str, Any]:
    """Project only discovered fields, preserving dotted names for provenance."""
    projected: dict[str, Any] = {}
    for field_name in approved_fields[:30]:
        if field_name.casefold() == "original" or field_name.casefold().endswith(".original"):
            # Older stored profiles may still approve raw log payloads. Keep
            # them searchable, but never project them to dashboard or chat.
            continue
        found, value = _nested_field_value(fields, field_name)
        if found:
            projected[field_name] = value
    return projected


def _timestamp_field(source: ConnectorSourceProfile) -> str:
    field = source.fields.get("timestamp_field")
    if not isinstance(field, str) or field not in source.approved_fields:
        raise ConnectorError("invalid_source", "Source has no approved timestamp field")
    return field


def _logical_field(source: ConnectorSourceProfile, logical_name: str) -> str | None:
    aliases = {
        "service": "service_field",
        "severity": "severity_field",
        "environment": "environment_field",
    }
    field = source.fields.get(aliases.get(logical_name, logical_name))
    return field if isinstance(field, str) and field in source.approved_fields else None


def _query(source: ConnectorSourceProfile, question: EvidenceQuestion) -> dict[str, Any]:
    filters: list[dict[str, Any]] = [
        {
            "range": {
                _timestamp_field(source): {
                    "gte": question.time_from.isoformat(),
                    "lte": question.time_to.isoformat(),
                }
            }
        }
    ]
    severity_field = _logical_field(source, "severity")
    if question.severities and severity_field:
        filters.append({"terms": {severity_field: list(question.severities[:20])}})
    should: list[dict[str, Any]] = []
    searchable = [
        field
        for field in (source.searchable_fields or source.approved_fields)
        if field != _timestamp_field(source)
    ][:12]
    for ref in question.entity_refs[:20]:
        if len(should) >= 20:
            break
        token = ref.split(":", 1)[-1][:256]
        # A lone '*' is the operator's explicit "any event" probe, not a
        # literal token to search for in message fields.
        if token == "*":
            continue
        alternatives = re.split(r"\s+OR\s+", token, flags=re.IGNORECASE)
        for alternative in alternatives:
            if len(should) >= 20:
                break
            alternative = alternative.strip()
            if alternative and searchable:
                should.append(
                    {
                        "multi_match": {
                            "query": alternative,
                            "fields": searchable,
                            "operator": "and",
                        }
                    }
                )
    bool_query: dict[str, Any] = {"filter": filters}
    if should:
        bool_query.update({"should": should, "minimum_should_match": 1})
    elif any(ref.split(":", 1)[-1].strip() != "*" for ref in question.entity_refs):
        # A source with no searchable text fields must never turn a keyword
        # query into a time-only match that returns unrelated events.
        bool_query["must"] = [{"match_none": {}}]
    return {"bool": bool_query}


def _event_time(fields: Mapping[str, Any], timestamp_field: str) -> dt.datetime | None:
    found, value = _nested_field_value(fields, timestamp_field)
    if not found:
        return None
    if not isinstance(value, str):
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def register_elasticsearch_provider() -> None:
    try:
        connector_catalog.register(ELASTICSEARCH_MANIFEST, ElasticsearchProvider())
    except ValueError:
        # Explicit imports may run more than once in test reloads; duplicate IDs
        # remain rejected by the catalogue for all other callers.
        pass
