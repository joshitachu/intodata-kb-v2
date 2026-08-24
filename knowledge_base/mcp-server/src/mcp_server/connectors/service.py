"""Connector lifecycle, scope checks, and immutable snapshot orchestration."""

from __future__ import annotations

import datetime as dt
import hashlib
from typing import Any, Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from mcp_server.db.models import GenerationConnectorSnapshot

from .catalog import connector_catalog
from .domain import ConnectorInstanceConfig, ConnectorSourceProfile, ConnectorStatus, RunConnectorSnapshot
from .errors import ConnectorError
from .providers.elasticsearch import register_elasticsearch_provider
from .providers.kibana import KibanaDiscoveryProvider
from .providers.talend_tmc import register_talend_tmc_provider
from .repository import ConnectorRepository, instance_config, source_profile
from .secrets import connector_secrets


# Built-in providers are opt-in code imports rather than filesystem discovery.
register_elasticsearch_provider()
register_talend_tmc_provider()


_SECRETISH_CONFIG_KEYS = {"credential", "password", "secret", "token", "authorization", "api_key"}


def _safe_config(config: Mapping[str, Any]) -> dict[str, Any]:
    safe = dict(config)
    for key, value in safe.items():
        # This is a routing choice, not credential material.  Keep the explicit
        # exception narrow (including its value type) so similarly named secret
        # fields remain blocked.
        if key.casefold() == "kibana_reuse_elasticsearch_credentials" and isinstance(value, bool):
            continue
        if any(fragment in key.casefold() for fragment in _SECRETISH_CONFIG_KEYS):
            raise ValueError("Credentials must be supplied through write-only credential fields")
    custom_ca = safe.get("custom_ca_pem")
    if custom_ca is not None and (not isinstance(custom_ca, str) or len(custom_ca) > 65_536):
        raise ValueError("Custom CA certificate is invalid or exceeds the configured limit")
    if safe.get("verify_tls") is False:
        raise ValueError("TLS verification cannot be disabled for connector endpoints")
    return safe


class ConnectorService:
    def __init__(self, session: AsyncSession, repository: ConnectorRepository | None = None) -> None:
        self.session = session
        self.repository = repository or ConnectorRepository(session)

    async def create_instance(self, *, provider_id: str, display_name: str, safe_config: Mapping[str, Any],
                              credential_type: str, credential: str, kibana_credential: str | None,
                              user_id: int, username: str | None) -> object:
        try:
            manifest = connector_catalog.manifest(provider_id)
        except KeyError as exc:
            # Provider IDs are caller input on the management API.  Preserve a
            # structured validation failure instead of leaking a catalogue
            # lookup exception as a 500.
            raise ValueError("Unknown connector provider") from exc
        if credential_type not in manifest.authentication_types:
            raise ValueError("Unsupported connector credential type")
        account_key = account_key_for(manifest, safe_config)
        if account_key:
            existing = await self.repository.find_by_account_key(provider_id, account_key)
            if existing is not None:
                raise ConnectorError(
                    "duplicate_account",
                    f"{manifest.label} is already connected for this account "
                    f"as '{existing.display_name}'. Rotate that connection's "
                    "credential instead of adding a second one.",
                )
        instance = await self.repository.create_instance(
            provider_id=provider_id, display_name=display_name, safe_config=_safe_config(safe_config),
            credential_type=credential_type, credential=credential, kibana_credential=kibana_credential, user_id=user_id,
            account_key=account_key,
        )
        await self.repository.audit(user_id=user_id, username=username, action="connector.create",
                                    resource_type="connector", resource_id=instance.id,
                                    details={"provider_id": provider_id})
        return instance

    async def update_instance(self, connector_id: str, *, display_name: str | None, safe_config: Mapping[str, Any] | None,
                              enabled: bool | None, user_id: int, username: str | None) -> object:
        instance = await self._required_instance(connector_id)
        result = await self.repository.update_instance(
            instance, display_name=display_name, safe_config=_safe_config(safe_config) if safe_config is not None else None,
            enabled=enabled, user_id=user_id,
        )
        await self.repository.audit(user_id=user_id, username=username, action="connector.update",
                                    resource_type="connector", resource_id=connector_id,
                                    details={"display_name_changed": display_name is not None, "enabled": enabled})
        return result

    async def rotate_credential(self, connector_id: str, *, credential: str, credential_type: str | None,
                                kibana_credential: str | None, reuse_elasticsearch_credential: bool,
                                user_id: int, username: str | None) -> None:
        instance = await self._required_instance(connector_id)
        manifest = connector_catalog.manifest(instance.provider_id)
        if credential_type and credential_type not in manifest.authentication_types:
            raise ValueError("Unsupported connector credential type")
        await self.repository.rotate_credential(instance, credential=credential, credential_type=credential_type,
                                                kibana_credential=kibana_credential,
                                                reuse_elasticsearch_credential=reuse_elasticsearch_credential, user_id=user_id)
        await self.repository.audit(user_id=user_id, username=username, action="connector.rotate_credential",
                                    resource_type="connector", resource_id=connector_id, details={"rotated": True})

    async def test_instance(self, connector_id: str, *, user_id: int, username: str | None) -> dict[str, object]:
        instance = await self._required_instance(connector_id)
        if not instance.enabled:
            raise ConnectorError("connector_disabled", "Connector is disabled")
        provider = connector_catalog.provider(instance.provider_id)
        now = dt.datetime.now(dt.timezone.utc)
        try:
            credential = self.repository.secrets.decrypt_for_provider(instance.credential_ciphertext)
            provider_result = await provider.test(instance_config(instance), credential)
            instance.status = "connected"
            instance.health_code = None
            result = {
                "status": "connected",
                "message": "Connection succeeded",
                "checked_at": now,
                "details": {key: value for key, value in provider_result.items() if key != "status"},
            }
        except ConnectorError as exc:
            instance.status = "degraded"
            instance.health_code = exc.code
            result = {
                "status": "degraded", "message": exc.message, "checked_at": now,
                "details": {"code": exc.code},
            }
        finally:
            instance.last_checked_at = now
        await self.repository.audit(user_id=user_id, username=username, action="connector.test",
                                    resource_type="connector", resource_id=connector_id,
                                    details={"status": instance.status, "health_code": instance.health_code})
        return result

    async def discover(self, connector_id: str, *, selected_source_ids: list[str] | None = None,
                       include_kibana: bool = False, kibana_space: str = "default", data_view_id: str | None = None,
                       user_id: int, username: str | None) -> list[object]:
        instance = await self._required_instance(connector_id)
        if not instance.enabled:
            raise ConnectorError("connector_disabled", "Connector is disabled")
        provider = connector_catalog.provider(instance.provider_id)
        credential = self.repository.secrets.decrypt_for_provider(instance.credential_ciphertext)
        config = instance_config(instance)
        profiles: list[ConnectorSourceProfile]
        if selected_source_ids:
            selected = await self.repository.get_sources(connector_id, selected_source_ids)
            if len(selected) != len(set(selected_source_ids)):
                raise ConnectorError("invalid_source", "One or more selected sources are not approved for this connector")
            profiles = [await provider.describe(config, source_profile(row), credential) for row in selected]
        else:
            profiles = await provider.discover(config, credential)
        if include_kibana and instance.provider_id == "elastic_stack":
            kibana_credential = (
                self.repository.secrets.decrypt_for_provider(instance.kibana_credential_ciphertext)
                if instance.kibana_credential_ciphertext else credential
            )
            kibana = KibanaDiscoveryProvider()
            if data_view_id:
                profiles.append(await kibana.get_data_view(config, kibana_credential, data_view_id, kibana_space))
            else:
                # A successful Kibana probe is intentionally non-fatal to direct
                # Elasticsearch discovery; UI can call the focused data-view API.
                await kibana.list_spaces(config, kibana_credential)
        rows = await self.repository.upsert_sources(profiles)
        await self.repository.audit(user_id=user_id, username=username, action="connector.discover",
                                    resource_type="connector", resource_id=connector_id,
                                    details={"source_count": len(rows), "kibana": include_kibana})
        return rows

    async def kibana_spaces(self, connector_id: str) -> list[dict[str, str]]:
        instance = await self._required_instance(connector_id)
        if instance.provider_id != "elastic_stack":
            raise ConnectorError("unsupported_provider", "Kibana discovery is available only for Elastic Stack")
        credential = self.repository.secrets.decrypt_for_provider(
            instance.kibana_credential_ciphertext or instance.credential_ciphertext
        )
        return await KibanaDiscoveryProvider().list_spaces(instance_config(instance), credential)

    async def kibana_data_views(self, connector_id: str, space: str) -> list[dict[str, str]]:
        instance = await self._required_instance(connector_id)
        if instance.provider_id != "elastic_stack":
            raise ConnectorError("unsupported_provider", "Kibana discovery is available only for Elastic Stack")
        credential = self.repository.secrets.decrypt_for_provider(
            instance.kibana_credential_ciphertext or instance.credential_ciphertext
        )
        return await KibanaDiscoveryProvider().list_data_views(instance_config(instance), credential, space)

    async def upsert_binding(self, *, project_id: str, connector_id: str, source_ids: list[str], capability_ids: list[str],
                             budgets: Mapping[str, int], max_time_window_days: int | None, enabled: bool,
                             user_id: int, username: str | None) -> object:
        instance = await self._required_instance(connector_id)
        if not await self.repository.project_exists(project_id):
            raise ConnectorError("project_not_found", "Project does not exist")
        connector_catalog.validate_grant(instance.provider_id, capability_ids)
        sources = await self.repository.get_sources(connector_id, source_ids)
        if len(sources) != len(set(source_ids)):
            raise ConnectorError("invalid_source", "Binding contains a source from another connector")
        if not source_ids or not capability_ids:
            raise ValueError("Connector binding requires at least one source and capability")
        for value in budgets.values():
            if not isinstance(value, int) or value < 0:
                raise ValueError("Connector budgets must be non-negative integers")
        binding = await self.repository.upsert_binding(
            project_id=project_id, connector_instance_id=connector_id, source_ids=source_ids,
            capability_ids=capability_ids, budgets=budgets, max_time_window_days=max_time_window_days,
            enabled=enabled, user_id=user_id,
        )
        await self.repository.audit(user_id=user_id, username=username, action="connector.binding.upsert",
                                    resource_type="project_connector_binding", resource_id=binding.id,
                                    details={"project_id": project_id, "connector_id": connector_id,
                                             "version": binding.version, "source_count": len(source_ids),
                                             "capability_count": len(capability_ids)})
        return binding

    async def delete_instance(self, connector_id: str, *, user_id: int, username: str | None) -> None:
        instance = await self._required_instance(connector_id)
        if await self.repository.active_binding_count(connector_id):
            raise ConnectorError("active_bindings", "Remove active project bindings before deleting this connector")
        await self.repository.delete_instance(instance)
        await self.repository.audit(user_id=user_id, username=username, action="connector.delete",
                                    resource_type="connector", resource_id=connector_id, details={})

    async def create_run_snapshots(self, *, generate_run_id: int, project_id: str, user_id: int,
                                   expires_in_seconds: int = 900) -> list[RunConnectorSnapshot]:
        """Freeze enabled, valid bindings. Called only by OpenCode Gather preflight."""
        now = dt.datetime.now(dt.timezone.utc)
        expires_at = now + dt.timedelta(seconds=expires_in_seconds)
        results: list[RunConnectorSnapshot] = []
        for binding in await self.repository.list_bindings(project_id):
            if not binding.enabled:
                continue
            instance = await self.repository.get_instance(binding.connector_instance_id)
            if instance is None or not instance.enabled:
                continue
            connector_catalog.validate_grant(instance.provider_id, binding.capability_ids)
            sources = await self.repository.get_sources(instance.id, binding.source_ids)
            if len(sources) != len(set(binding.source_ids)) or any(not source.enabled for source in sources):
                continue
            row = await self.repository.create_snapshot(
                generate_run_id=generate_run_id, project_id=project_id, user_id=user_id,
                binding=binding, instance=instance, sources=sources, issued_at=now, expires_at=expires_at,
            )
            results.append(RunConnectorSnapshot(
                id=row.id, generate_run_id=generate_run_id, project_id=project_id, initiating_user_id=user_id,
                connector_instance_id=instance.id, binding_id=binding.id, binding_version=binding.version,
                source_profiles=tuple(source_profile(source) for source in sources),
                capability_ids=tuple(binding.capability_ids), budgets=dict(binding.budgets or {}),
                issued_at=now, expires_at=expires_at,
            ))
        return results

    @classmethod
    async def create_opencode_runtime(
        cls, *, generate_run_id: int, project_id: str, user_id: int | None, resume: bool = False,
    ) -> object | None:
        """Build live Gather authority from persisted bindings, or return ``None``.

        The returned object contains an opaque MCP grant and backend-only frozen
        provider state.  It is intentionally created only by the OpenCode
        coordinator; legacy providers never call this method.
        """
        from mcp_server.config import config
        from mcp_server.db.session import async_session_factory
        from .broker import CapabilityBroker, ConnectorRunRuntime
        from .grants import CapabilityGrant, CapabilityGrantStore, ConnectorBudget as GrantBudget

        if not config.opencode_connectors_enabled or user_id is None:
            return None
        async with async_session_factory() as session:
            service = cls(session)
            persisted_snapshots = await service.repository.list_generation_snapshots(generate_run_id)
            if resume or persisted_snapshots:
                # A resume (or a retry after preflight already created rows) is
                # never allowed to create a fresh snapshot: that would silently
                # adopt binding/source/credential rotations or duplicate rows.
                if not persisted_snapshots:
                    return None
                now = dt.datetime.now(dt.timezone.utc)
                if any(
                    row.project_id != project_id
                    or row.grant_revoked_at is not None
                    or row.grant_expires_at is None
                    or row.grant_expires_at <= now
                    for row in persisted_snapshots
                ):
                    return None
                snapshots: list[RunConnectorSnapshot] = []
                for row in persisted_snapshots:
                    data = row.snapshot if isinstance(row.snapshot, dict) else {}
                    raw_profiles = data.get("source_profiles")
                    if not isinstance(raw_profiles, list):
                        return None
                    profiles: list[ConnectorSourceProfile] = []
                    for raw in raw_profiles:
                        if not isinstance(raw, dict) or not isinstance(raw.get("locator_ciphertext"), str):
                            return None
                        try:
                            locator = connector_secrets.decrypt_for_provider(raw["locator_ciphertext"])
                        except ConnectorError:
                            return None
                        profiles.append(ConnectorSourceProfile(
                            id=str(raw.get("id", "")), connector_instance_id=row.connector_instance_id,
                            source_type=str(raw.get("source_type", "")), display_label=str(raw.get("display_label", "")),
                            locator=locator, fields=dict(raw.get("field_profile") or {}), enabled=bool(raw.get("enabled", True)),
                        ))
                    snapshots.append(RunConnectorSnapshot(
                        id=row.id, generate_run_id=generate_run_id, project_id=project_id,
                        initiating_user_id=row.initiating_user_id, connector_instance_id=row.connector_instance_id,
                        binding_id=row.binding_id, binding_version=row.binding_version, source_profiles=tuple(profiles),
                        capability_ids=tuple(data.get("capability_ids") or ()), budgets=dict(data.get("budgets") or {}),
                        issued_at=row.grant_issued_at or now, expires_at=row.grant_expires_at,
                        revoked_at=row.grant_revoked_at,
                    ))
            else:
                snapshots = await service.create_run_snapshots(
                    generate_run_id=generate_run_id, project_id=project_id, user_id=user_id,
                )
                if not snapshots:
                    return None
            # Freeze the instance config and encrypted credential references for
            # Gather.  Credential rotation after preflight therefore cannot
            # silently change a running generation; plaintext appears only in
            # the short provider call below.
            executions: dict[str, tuple[object, object, object, str]] = {}
            source_capabilities: dict[str, set[str]] = {}
            snapshot_ids: dict[str, str] = {}
            connector_ids: set[str] = set()
            capability_ids: set[str] = set()
            budget_values: list[Mapping[str, int]] = []
            for snapshot in snapshots:
                persisted = await session.get(GenerationConnectorSnapshot, snapshot.id)
                data = persisted.snapshot if persisted is not None and isinstance(persisted.snapshot, dict) else {}
                if resume:
                    provider_id = data.get("provider_id")
                    safe_config = data.get("instance_safe_config")
                    credential_ciphertext = data.get("credential_ciphertext")
                    if not isinstance(provider_id, str) or not isinstance(safe_config, dict) or not isinstance(credential_ciphertext, str):
                        return None
                    instance = ConnectorInstanceConfig(
                        id=snapshot.connector_instance_id, provider_id=provider_id,
                        display_name="Frozen connector", safe_config=safe_config,
                        enabled=True, status=ConnectorStatus.CONNECTED,
                    )
                else:
                    instance_row = await service.repository.get_instance(snapshot.connector_instance_id)
                    if instance_row is None:
                        continue
                    instance = instance_config(instance_row)
                    credential_ciphertext = instance_row.credential_ciphertext
                provider = connector_catalog.provider(instance.provider_id)
                connector_ids.add(instance.id)
                capability_ids.update(snapshot.capability_ids)
                budget_values.append(snapshot.budgets)
                for profile in snapshot.source_profiles:
                    executions[profile.id] = (provider, instance, profile, credential_ciphertext)
                    source_capabilities[profile.id] = set(snapshot.capability_ids)
                    snapshot_ids[profile.id] = snapshot.id
            if not executions or not capability_ids:
                return None
            provider_ids = {instance.provider_id for _, instance, _, _ in executions.values()}
            for provider_id in provider_ids:
                provider_capabilities = {
                    capability
                    for source_id, (_, instance, _, _) in executions.items()
                    if instance.provider_id == provider_id
                    for capability in source_capabilities[source_id]
                }
                connector_catalog.validate_grant(provider_id, provider_capabilities)
            # Multiple bindings compose at the most restrictive aggregate run
            # budget. Individual source/capability scope stays in the grant.
            def minimum(key: str, default: int) -> int:
                values = [int(item[key]) for item in budget_values if key in item]
                return min([default, *values])
            grant = CapabilityGrant.new(
                run_id=generate_run_id, project_id=project_id, user_id=str(user_id),
                connector_instance_ids=connector_ids, source_capabilities=source_capabilities,
                capability_ids=capability_ids, snapshot_id=next(iter(snapshot_ids.values())),
                budget=GrantBudget(
                    max_calls=minimum("max_calls", 12), max_wall_seconds=minimum("max_wall_seconds", 90),
                    max_items=minimum("max_items", 30), max_response_bytes=minimum("max_bytes", 128 * 1024),
                    max_evidence_bytes=minimum("max_total_bytes", 512 * 1024),
                ),
                ttl_seconds=max(1, min(
                    900,
                    int((min(snapshot.expires_at for snapshot in snapshots) - dt.datetime.now(dt.timezone.utc)).total_seconds()),
                )),
            )
            nonce_hash = hashlib.sha256(grant.nonce.encode()).hexdigest()
            for snapshot_id in set(snapshot_ids.values()):
                row = await session.get(GenerationConnectorSnapshot, snapshot_id)
                if row is not None:
                    row.grant_nonce_hash = nonce_hash
            await session.commit()

        async def executor(grant_obj: object, capability_id: str, source_id: str, request: Mapping[str, Any]) -> object:
            try:
                provider, instance, source, credential_ciphertext = executions[source_id]
            except KeyError as exc:
                raise ConnectorError("invalid_source", "Source is not available to this run") from exc
            credential = connector_secrets.decrypt_for_provider(credential_ciphertext)
            question = _evidence_question_from_request(capability_id, request)
            return await provider.execute(
                instance=instance, source=source, credential=credential, capability_id=capability_id,
                question=question, run_id=generate_run_id, project_id=project_id,
            )

        async def receipt_sink(receipt: Mapping[str, Any]) -> None:
            """Persist receipt in a fresh session; never persist an envelope/body."""
            source_id = str(receipt.get("source_id") or "")
            snapshot_id = snapshot_ids.get(source_id)
            if snapshot_id is None:
                return
            now = dt.datetime.now(dt.timezone.utc)
            async with async_session_factory() as receipt_session:
                repository = ConnectorRepository(receipt_session)
                await repository.add_invocation_receipt(
                    evidence_id=receipt.get("evidence_id"), generation_snapshot_id=snapshot_id,
                    connector_instance_id=executions[source_id][1].id, source_id=source_id,
                    capability_id=str(receipt.get("capability_id") or ""),
                    query_summary=dict(receipt.get("query_summary") or {}), status=str(receipt.get("status") or "failed"),
                    started_at=_parse_receipt_time(receipt.get("started_at"), now),
                    completed_at=_parse_receipt_time(receipt.get("completed_at"), now),
                    latency_ms=max(0, int(receipt.get("latency_ms") or 0)),
                    item_count=max(0, int(receipt.get("item_count") or 0)),
                    byte_count=max(0, int(receipt.get("response_bytes") or 0)),
                    redaction_count=0, truncation={}, limitations=list(receipt.get("limitations") or ()),
                    result_digest=receipt.get("evidence_digest"), denial_reason=receipt.get("denial_reason"),
                )
                await receipt_session.commit()

        grants = CapabilityGrantStore()
        token = grants.issue(grant)
        metadata: dict[str, object] = {}
        for _, instance, source, _ in executions.values():
            for capability in source_capabilities[source.id]:
                metadata.setdefault(capability, connector_catalog.capability(instance.provider_id, capability))
        broker = CapabilityBroker(grants=grants, capability_metadata=metadata, executor=executor, receipt_sink=receipt_sink)
        return ConnectorRunRuntime(
            broker=broker, token=token, run_id=generate_run_id, project_id=project_id, grant_id=grant.grant_id,
        )

    async def _required_instance(self, connector_id: str) -> object:
        instance = await self.repository.get_instance(connector_id)
        if instance is None:
            raise ConnectorError("connector_not_found", "Connector was not found")
        return instance


def _parse_receipt_time(value: object, default: dt.datetime) -> dt.datetime:
    if isinstance(value, str):
        try:
            return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return default


def _evidence_question_from_request(capability_id: str, request: Mapping[str, Any]):
    from .domain import EvidenceQuestion
    if capability_id == "runtime.events.get_item":
        reference = request.get("evidence_ref")
        if not isinstance(reference, str):
            raise ConnectorError("invalid_query", "Evidence reference is required")
        return EvidenceQuestion(question=reference)
    time_window = request.get("time_window")
    if capability_id == "evidence.sources.describe":
        return EvidenceQuestion(question="Describe approved source")
    if not isinstance(time_window, Mapping):
        raise ConnectorError("invalid_query", "Runtime evidence requires a bounded time range")
    try:
        start = dt.datetime.fromisoformat(str(time_window["from"]).replace("Z", "+00:00"))
        end = dt.datetime.fromisoformat(str(time_window["to"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError) as exc:
        raise ConnectorError("invalid_query", "Runtime evidence time range is invalid") from exc
    def strings(key: str, maximum: int) -> tuple[str, ...]:
        raw = request.get(key, [])
        return tuple(str(item) for item in raw[:maximum]) if isinstance(raw, list) else ()
    question = request.get("question")
    if not isinstance(question, str):
        raise ConnectorError("invalid_query", "Evidence question is required")
    limit = request.get("limit")
    return EvidenceQuestion(
        question=question, entity_refs=strings("entity_refs", 25), time_from=start, time_to=end,
        severities=strings("severities", 10), group_by=strings("group_by", 5),
        limit=limit if isinstance(limit, int) and not isinstance(limit, bool) else None,
    )
