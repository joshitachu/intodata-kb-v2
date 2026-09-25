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
    ("*service*", "*job*", "*flow*", "*severity*", "*level*", "*environment*", "*env*"),
    ("*failure*", "*log*", "*detail*", "*description*", "*original*"),
)


ELASTICSEARCH_MANIFEST = ConnectorProviderManifest(
    provider_id="elastic_stack",
    label="Elastic Stack",
    version="1",
    authentication_types=("api_key", "basic"),
    configuration_schema={"endpoint": "https URL", "auth_type": "api_key|basic", "verify_tls": "boolean"},
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
        allow_http = config.app_env == "development" and bool(safe.get("development_allow_http", False))
        policy = OutboundPolicy(
            require_https=not allow_http,
            allow_private_hosts=frozenset(str(item) for item in allowed_private if isinstance(item, str)),
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
        allow_http = config.app_env == "development" and bool(instance.safe_config.get("development_allow_http", False))
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
        version = _as_mapping(payload.get("version") if isinstance(payload, dict) else None).get("number")
        return {"status": ConnectorStatus.CONNECTED.value, "cluster_name": cluster_name, "version": version}

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
            raise ConnectorError("malformed_response", "Elasticsearch discovery response was invalid")
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
        for patterns in _EVENT_FIELD_GROUPS:
            caps_response, _ = await transport.request_json(
                "POST", endpoint, headers=headers, json={"fields": list(patterns)},
            )
            capability_fields.update(_field_capability_fields(caps_response))
        return ConnectorSourceProfile(
            id=source.id,
            connector_instance_id=source.connector_instance_id,
            source_type=source.source_type,
            display_label=source.display_label,
            locator=locator,
            fields=_detect_field_profile(capability_fields),
            enabled=source.enabled,
            discovered_at=dt.datetime.now(dt.timezone.utc),
            discovery_digest=hashlib.sha256(
                repr(sorted(capability_fields)).encode()
            ).hexdigest(),
        )

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
        if question.time_to <= question.time_from or question.time_to - question.time_from > dt.timedelta(days=max_days):
            raise ConnectorError("invalid_query", "Requested time range exceeds the approved source profile")
        if capability_id == "runtime.events.aggregate":
            return await self._aggregate(instance, source, credential, question, run_id, project_id)
        return await self._search(instance, source, credential, question, run_id, project_id)

    def _describe_envelope(
        self, instance: ConnectorInstanceConfig, source: ConnectorSourceProfile, question: EvidenceQuestion,
        run_id: int, project_id: str,
    ) -> EvidenceEnvelope:
        now = dt.datetime.now(dt.timezone.utc)
        return finalize_envelope(EvidenceEnvelope(
            evidence_id=f"ev_{uuid.uuid4().hex}", run_id=run_id, project_id=project_id,
            connector_instance_id=instance.id, source_id=source.id, capability_id="evidence.sources.describe",
            question=question.question, entity_refs=question.entity_refs, observed_at=now,
            source_window={}, summary={"display_label": source.display_label, "source_type": source.source_type,
                                        "fields": dict(source.fields), "samples_allowed": bool(source.fields.get("samples_allowed", True))},
        ))

    async def _aggregate(self, instance: ConnectorInstanceConfig, source: ConnectorSourceProfile, credential: str,
                         question: EvidenceQuestion, run_id: int, project_id: str) -> EvidenceEnvelope:
        group_by = question.group_by[:3] or ("severity",)
        aggregations: dict[str, Any] = {}
        for logical_name in group_by:
            field = _logical_field(source, logical_name)
            if field is None:
                raise ConnectorError("invalid_query", "Requested grouping is not approved for this source")
            aggregations[logical_name] = {"terms": {"field": field, "size": min(question.limit or 25, 25)}}
        body = {"size": 0, "track_total_hits": False, "query": _query(source, question), "aggs": aggregations}
        payload, _ = await self._transport_for(instance).request_json(
            "POST", f"{self._endpoint(instance)}/{quote(_source_locator(source), safe='*,-_')}/_search",
            headers=self._headers(instance, credential), json=body,
        )
        aggs = _as_mapping(payload).get("aggregations", {})
        summary: dict[str, Any] = {"groups": {}}
        if isinstance(aggs, Mapping):
            for logical_name, result in aggs.items():
                buckets = _as_mapping(result).get("buckets", [])
                if isinstance(buckets, list):
                    summary["groups"][logical_name] = [
                        {"key": str(bucket.get("key", ""))[:256], "count": int(bucket.get("doc_count", 0))}
                        for bucket in buckets[:25] if isinstance(bucket, Mapping)
                    ]
        return self._envelope(instance, source, "runtime.events.aggregate", question, run_id, project_id, (), summary)

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
            raise ConnectorError("invalid_query", "Requested freshness window exceeds the approved source profile")
        window_start = checked_at - dt.timedelta(days=window_days)
        body = {
            "size": 0,
            "track_total_hits": True,
            "query": {"match_all": {}},
            "aggs": {
                "latest_event": {"max": {"field": timestamp_field}},
                "timestamped_documents": {"value_count": {"field": timestamp_field}},
                "events_in_window": {"filter": {"range": {timestamp_field: {
                    "gte": window_start.isoformat(), "lte": checked_at.isoformat(),
                }}}},
            },
        }
        payload, _ = await self._transport_for(instance).request_json(
            "POST", f"{self._endpoint(instance)}/{quote(_source_locator(source), safe='*,-_')}/_search",
            headers=self._headers(instance, credential), json=body,
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
                    latest_at = dt.datetime.fromtimestamp(raw_latest / 1000, tz=dt.timezone.utc).isoformat()
                except (OverflowError, OSError, ValueError):
                    latest_at = None
            else:
                latest_at = None
        return {
            "latest_event_at": latest_at,
            "events_in_window": _nonnegative_int(recent.get("doc_count", 0)),
            "total_documents": _nonnegative_int(total),
            "timestamped_documents": _nonnegative_int(timestamped.get("value", 0)),
        }

    async def _search(self, instance: ConnectorInstanceConfig, source: ConnectorSourceProfile, credential: str,
                      question: EvidenceQuestion, run_id: int, project_id: str) -> EvidenceEnvelope:
        if not bool(source.fields.get("samples_allowed", True)):
            raise ConnectorError("capability_denied", "This source permits aggregates only")
        limit = min(max(1, question.limit or 10), 10)
        includes = list(source.approved_fields)[:30]
        body = {"size": limit, "track_total_hits": False, "query": _query(source, question),
                "_source": {"includes": includes}, "sort": [{_timestamp_field(source): {"order": "desc"}}]}
        payload, _ = await self._transport_for(instance).request_json(
            "POST", f"{self._endpoint(instance)}/{quote(_source_locator(source), safe='*,-_')}/_search",
            headers=self._headers(instance, credential), json=body,
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
                normalized, _, _ = normalize_event_fields(fields, set(source.approved_fields))
                items.append(EvidenceItem(ref, _event_time(fields, _timestamp_field(source)), normalized))
        return self._envelope(instance, source, "runtime.events.search", question, run_id, project_id, tuple(items), {"item_count": len(items)})

    async def _get_item(self, instance: ConnectorInstanceConfig, source: ConnectorSourceProfile, credential: str,
                        question: EvidenceQuestion, run_id: int, project_id: str) -> EvidenceEnvelope:
        reference = question.question.strip()
        stored = self._opaque_items.get(reference)
        if stored is None or stored[0] != run_id or stored[1] != source.id:
            raise ConnectorError("evidence_not_found", "Evidence item is not available for this run")
        _, _, index, doc_id = stored
        payload, _ = await self._transport_for(instance).request_json(
            "GET", f"{self._endpoint(instance)}/{quote(index, safe='*,-_')}/_doc/{quote(doc_id, safe='')}",
            headers=self._headers(instance, credential), params={"_source_includes": ",".join(source.approved_fields[:30])},
        )
        fields = _as_mapping(_as_mapping(payload).get("_source"))
        normalized, _, _ = normalize_event_fields(fields, set(source.approved_fields))
        item = EvidenceItem(reference, _event_time(fields, _timestamp_field(source)), normalized)
        return self._envelope(instance, source, "runtime.events.get_item", question, run_id, project_id, (item,), {"item_count": 1})

    def _envelope(self, instance: ConnectorInstanceConfig, source: ConnectorSourceProfile, capability: str,
                  question: EvidenceQuestion, run_id: int, project_id: str, items: tuple[EvidenceItem, ...], summary: Mapping[str, Any]) -> EvidenceEnvelope:
        envelope = EvidenceEnvelope(
            evidence_id=f"ev_{uuid.uuid4().hex}", run_id=run_id, project_id=project_id,
            connector_instance_id=instance.id, source_id=source.id, capability_id=capability,
            question=question.question, entity_refs=question.entity_refs, observed_at=dt.datetime.now(dt.timezone.utc),
            source_window={"from": question.time_from.isoformat() if question.time_from else "", "to": question.time_to.isoformat() if question.time_to else ""},
            items=items, summary=summary,
        )
        return finalize_envelope(envelope)


def _field_capability_fields(response: Any) -> dict[str, str]:
    output: dict[str, str] = {}
    for name, types in _as_mapping(_as_mapping(response).get("fields")).items():
        if isinstance(name, str) and isinstance(types, Mapping) and types:
            output[name] = str(next(iter(types)))
    return output


def _detect_field_profile(capability_fields: Mapping[str, str]) -> dict[str, Any]:
    names = list(capability_fields)
    def pick(candidates: tuple[str, ...]) -> str | None:
        lower = {name.casefold(): name for name in names}
        for candidate in candidates:
            if candidate.casefold() in lower:
                return lower[candidate.casefold()]
        for name in names:
            if any(candidate.casefold() in name.casefold() for candidate in candidates):
                return name
        return None
    string_types = {"text", "match_only_text", "keyword", "wildcard"}
    searchable = [
        name for name, field_type in capability_fields.items()
        if field_type.casefold() in string_types
    ]
    searchable.sort(key=lambda name: (
        0 if any(token in name.casefold() for token in ("message", "error", "exception", "stack", "reason", "failure")) else 1,
        0 if capability_fields[name].casefold() in {"text", "match_only_text"} else 1,
        name.casefold(),
    ))
    message_fields = [
        name for name in searchable
        if any(token in name.casefold() for token in (
            "message", "error", "exception", "stack", "reason", "failure", "description", "detail", "original",
        ))
    ][:5]
    profile: dict[str, Any] = {
        "timestamp_field": pick(("@timestamp", "timestamp", "event.created", "time")),
        "message_fields": message_fields or [value for value in (pick(("message", "log.message", "error.message")),) if value],
        "search_fields": searchable[:12],
        "service_field": pick(("service.name", "service", "service_name", "job", "flow")),
        "severity_field": pick(("log.level", "severity", "level", "event.severity")),
        "environment_field": pick(("service.environment", "environment", "env")),
        "max_time_window_days": 30,
        "samples_allowed": True,
    }
    return {key: value for key, value in profile.items() if value not in (None, [])}


def _timestamp_field(source: ConnectorSourceProfile) -> str:
    field = source.fields.get("timestamp_field")
    if not isinstance(field, str) or field not in source.approved_fields:
        raise ConnectorError("invalid_source", "Source has no approved timestamp field")
    return field


def _logical_field(source: ConnectorSourceProfile, logical_name: str) -> str | None:
    aliases = {"service": "service_field", "severity": "severity_field", "environment": "environment_field"}
    field = source.fields.get(aliases.get(logical_name, logical_name))
    return field if isinstance(field, str) and field in source.approved_fields else None


def _query(source: ConnectorSourceProfile, question: EvidenceQuestion) -> dict[str, Any]:
    filters: list[dict[str, Any]] = [
        {"range": {
            _timestamp_field(source): {
                "gte": question.time_from.isoformat(),
                "lte": question.time_to.isoformat(),
            }
        }}
    ]
    severity_field = _logical_field(source, "severity")
    if question.severities and severity_field:
        filters.append({"terms": {severity_field: list(question.severities[:20])}})
    should: list[dict[str, Any]] = []
    searchable = [
        field for field in (source.searchable_fields or source.approved_fields)
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
                should.append({"multi_match": {
                    "query": alternative, "fields": searchable, "operator": "and",
                }})
    bool_query: dict[str, Any] = {"filter": filters}
    if should:
        bool_query.update({"should": should, "minimum_should_match": 1})
    elif any(ref.split(":", 1)[-1].strip() != "*" for ref in question.entity_refs):
        # A source with no searchable text fields must never turn a keyword
        # query into a time-only match that returns unrelated events.
        bool_query["must"] = [{"match_none": {}}]
    return {"bool": bool_query}


def _event_time(fields: Mapping[str, Any], timestamp_field: str) -> dt.datetime | None:
    value = fields.get(timestamp_field)
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
