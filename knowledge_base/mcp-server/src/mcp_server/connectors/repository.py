"""Persistence operations for connectors, bindings, snapshots, and receipts."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from typing import Any, Iterable, Mapping

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mcp_server.db.models import (
    ConnectorInstance,
    ConnectorInvocation,
    ConnectorSource,
    GenerationConnectorSnapshot,
    Project,
    ProjectConnectorBindingModel,
)

from .domain import (
    ConnectorInstanceConfig,
    ConnectorSourceProfile,
    ProjectConnectorBinding,
)
from .secrets import ConnectorSecretService, connector_secrets


class ConnectorRepository:
    def __init__(self, session: AsyncSession, secrets: ConnectorSecretService = connector_secrets) -> None:
        self.session = session
        self.secrets = secrets

    async def list_instances(self) -> list[ConnectorInstance]:
        return list((await self.session.scalars(select(ConnectorInstance).order_by(ConnectorInstance.display_name))).all())

    async def get_instance(self, connector_id: str) -> ConnectorInstance | None:
        return await self.session.get(ConnectorInstance, connector_id)

    async def create_instance(
        self, *, provider_id: str, display_name: str, safe_config: Mapping[str, Any], credential_type: str,
        credential: str | None, kibana_credential: str | None, user_id: int, account_key: str = "",
    ) -> ConnectorInstance:
        instance = ConnectorInstance(
            id=str(uuid.uuid4()), provider_id=provider_id, display_name=display_name.strip(),
            # Empty stays NULL: a unique index must not treat "no declared
            # account field" as one shared key across every such instance.
            account_key=account_key or None,
            safe_config=dict(safe_config), credential_type=credential_type,
            credential_ciphertext=self.secrets.encrypt(credential) if credential else None,
            kibana_credential_ciphertext=self.secrets.encrypt(kibana_credential) if kibana_credential else None,
            created_by_user_id=user_id, updated_by_user_id=user_id,
        )
        self.session.add(instance)
        await self.session.flush()
        return instance

    async def find_by_account_key(
        self, provider_id: str, account_key: str
    ) -> ConnectorInstance | None:
        """Existing connection to the same provider account, if any."""
        result = await self.session.execute(
            select(ConnectorInstance)
            .where(ConnectorInstance.provider_id == provider_id)
            .where(ConnectorInstance.account_key == account_key)
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def update_instance(self, instance: ConnectorInstance, *, display_name: str | None,
                              safe_config: Mapping[str, Any] | None, enabled: bool | None, user_id: int) -> ConnectorInstance:
        if display_name is not None:
            instance.display_name = display_name.strip()
        if safe_config is not None:
            instance.safe_config = dict(safe_config)
        if enabled is not None:
            instance.enabled = enabled
            if not enabled:
                instance.status = "disabled"
        instance.updated_by_user_id = user_id
        await self.session.flush()
        return instance

    async def rotate_credential(self, instance: ConnectorInstance, *, credential: str, credential_type: str | None,
                                kibana_credential: str | None, reuse_elasticsearch_credential: bool, user_id: int) -> None:
        instance.credential_ciphertext = self.secrets.encrypt(credential)
        if credential_type:
            instance.credential_type = credential_type
        if reuse_elasticsearch_credential:
            instance.kibana_credential_ciphertext = None
        elif kibana_credential is not None:
            instance.kibana_credential_ciphertext = self.secrets.encrypt(kibana_credential)
        instance.updated_by_user_id = user_id
        await self.session.flush()

    async def list_sources(self, connector_id: str) -> list[ConnectorSource]:
        return list((await self.session.scalars(
            select(ConnectorSource).where(ConnectorSource.connector_instance_id == connector_id).order_by(ConnectorSource.display_label)
        )).all())

    async def get_sources(self, connector_id: str, source_ids: Iterable[str]) -> list[ConnectorSource]:
        wanted = list(dict.fromkeys(source_ids))
        if not wanted:
            return []
        return list((await self.session.scalars(
            select(ConnectorSource).where(
                ConnectorSource.connector_instance_id == connector_id,
                ConnectorSource.id.in_(wanted),
            )
        )).all())

    async def upsert_sources(self, profiles: Iterable[ConnectorSourceProfile]) -> list[ConnectorSource]:
        persisted: list[ConnectorSource] = []
        for profile in profiles:
            row = await self.session.get(ConnectorSource, profile.id)
            if row is None:
                row = ConnectorSource(
                    id=profile.id, connector_instance_id=profile.connector_instance_id,
                    source_type=profile.source_type, display_label=profile.display_label,
                    locator_ciphertext=self.secrets.encrypt(profile.locator), field_profile=dict(profile.fields),
                    discovered_at=profile.discovered_at, discovery_digest=profile.discovery_digest, enabled=profile.enabled,
                )
                self.session.add(row)
            else:
                if row.connector_instance_id != profile.connector_instance_id:
                    raise ValueError("Source ID belongs to a different connector")
                row.source_type = profile.source_type
                row.display_label = profile.display_label
                row.locator_ciphertext = self.secrets.encrypt(profile.locator)
                # Broad index discovery carries no field profile. Preserve an
                # earlier detailed profile until this source is explicitly
                # described again, so refreshing the source picker cannot make
                # active Labs searches forget their searchable fields.
                if profile.fields:
                    incoming_fields = dict(profile.fields)
                    if profile.source_type == "mcp_tool":
                        unchanged = bool(
                            row.discovery_digest
                            and row.discovery_digest == profile.discovery_digest
                        )
                        if unchanged:
                            # A routine rediscovery must not revoke an approved tool.
                            # Review metadata is owner-authored and is not supplied by
                            # the remote server, so preserve it separately from the
                            # refreshed remote description/schema.
                            for key in (
                                "review_status",
                                "approved_purpose",
                                "approved_risk",
                                "reviewed_by_user_id",
                                "reviewed_at",
                            ):
                                if key in row.field_profile:
                                    incoming_fields[key] = row.field_profile[key]
                        else:
                            # Tool/schema drift always fails closed. It cannot retain
                            # an old approval or remain eligible for a project grant.
                            incoming_fields["review_status"] = (
                                "changed" if row.discovery_digest else "pending"
                            )
                            row.enabled = False
                    row.field_profile = incoming_fields
                    row.discovered_at = profile.discovered_at
                    row.discovery_digest = profile.discovery_digest
                if profile.source_type != "mcp_tool":
                    row.enabled = profile.enabled
            persisted.append(row)
        await self.session.flush()
        return persisted

    async def get_source(self, connector_id: str, source_id: str) -> ConnectorSource | None:
        return await self.session.scalar(
            select(ConnectorSource).where(
                ConnectorSource.id == source_id,
                ConnectorSource.connector_instance_id == connector_id,
            )
        )

    async def review_remote_tool(
        self,
        source: ConnectorSource,
        *,
        enabled: bool,
        approved_purpose: str | None,
        user_id: int,
    ) -> ConnectorSource:
        if source.source_type != "mcp_tool":
            raise ValueError("Only discovered MCP tools can be reviewed")
        profile = dict(source.field_profile or {})
        if enabled:
            if not source.discovery_digest or profile.get("schema_digest") != source.discovery_digest:
                raise ValueError("Remote MCP tool discovery is stale")
            if not approved_purpose:
                raise ValueError("An approved tool purpose is required")
            profile.update(
                {
                    "review_status": "approved",
                    "approved_purpose": approved_purpose.strip(),
                    # The first remote-MCP release is intentionally read-only.
                    # MCP annotations are only hints and never lower this value.
                    "approved_risk": "read_only",
                    "reviewed_by_user_id": user_id,
                    "reviewed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                }
            )
        else:
            profile["review_status"] = "disabled"
            profile.pop("approved_purpose", None)
            profile.pop("approved_risk", None)
        source.field_profile = profile
        source.enabled = enabled
        await self.session.flush()
        return source

    async def get_binding(self, project_id: str, connector_id: str) -> ProjectConnectorBindingModel | None:
        return await self.session.scalar(select(ProjectConnectorBindingModel).where(
            ProjectConnectorBindingModel.project_id == project_id,
            ProjectConnectorBindingModel.connector_instance_id == connector_id,
        ))

    async def list_bindings(self, project_id: str) -> list[ProjectConnectorBindingModel]:
        return list((await self.session.scalars(select(ProjectConnectorBindingModel).where(
            ProjectConnectorBindingModel.project_id == project_id
        ).order_by(ProjectConnectorBindingModel.created_at))).all())

    async def upsert_binding(
        self, *, project_id: str, connector_instance_id: str, source_ids: list[str], capability_ids: list[str],
        budgets: Mapping[str, int], max_time_window_days: int | None, enabled: bool, user_id: int,
    ) -> ProjectConnectorBindingModel:
        binding = await self.get_binding(project_id, connector_instance_id)
        if binding is None:
            binding = ProjectConnectorBindingModel(
                id=str(uuid.uuid4()), project_id=project_id, connector_instance_id=connector_instance_id,
                source_ids=list(source_ids), capability_ids=list(capability_ids), budgets=dict(budgets),
                max_time_window_days=max_time_window_days, enabled=enabled, version=1,
                created_by_user_id=user_id, updated_by_user_id=user_id,
            )
            self.session.add(binding)
        else:
            binding.source_ids = list(source_ids)
            binding.capability_ids = list(capability_ids)
            binding.budgets = dict(budgets)
            binding.max_time_window_days = max_time_window_days
            binding.enabled = enabled
            binding.version += 1
            binding.updated_by_user_id = user_id
        await self.session.flush()
        return binding

    async def delete_binding(self, project_id: str, connector_id: str) -> bool:
        binding = await self.get_binding(project_id, connector_id)
        if binding is None:
            return False
        await self.session.delete(binding)
        await self.session.flush()
        return True

    async def delete_instance(self, instance: ConnectorInstance) -> None:
        await self.session.delete(instance)
        await self.session.flush()

    async def active_binding_count(self, connector_id: str) -> int:
        rows = await self.session.scalars(select(ProjectConnectorBindingModel.id).where(
            ProjectConnectorBindingModel.connector_instance_id == connector_id,
            ProjectConnectorBindingModel.enabled.is_(True),
        ))
        return len(list(rows))

    async def project_exists(self, project_id: str) -> bool:
        return await self.session.get(Project, project_id) is not None

    async def create_snapshot(
        self, *, generate_run_id: int, project_id: str, user_id: int, binding: ProjectConnectorBindingModel,
        instance: ConnectorInstance, sources: list[ConnectorSource], issued_at: dt.datetime, expires_at: dt.datetime,
    ) -> GenerationConnectorSnapshot:
        # Source locator ciphertext is deliberately not copied into an immutable
        # snapshot. The live row is resolved only after connector/source identity
        # checks, while the public profile is frozen below.
        snapshot = {
            "provider_id": instance.provider_id,
            # Encrypted credential and locator material is safe for the same
            # access boundary as the snapshot row. It freezes a resumed run
            # without ever materialising plaintext in the database.
            "instance_safe_config": dict(instance.safe_config or {}),
            "credential_ciphertext": instance.credential_ciphertext,
            "binding_id": binding.id,
            "binding_version": binding.version,
            "source_profiles": [
                {"id": source.id, "source_type": source.source_type, "display_label": source.display_label,
                 "field_profile": source.field_profile, "locator_ciphertext": source.locator_ciphertext,
                 "enabled": source.enabled}
                for source in sources
            ],
            "capability_ids": list(binding.capability_ids),
            "budgets": dict(binding.budgets),
            "max_time_window_days": binding.max_time_window_days,
        }
        digest = "sha256:" + hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        row = GenerationConnectorSnapshot(
            id=str(uuid.uuid4()), generate_run_id=generate_run_id, project_id=project_id,
            initiating_user_id=user_id, connector_instance_id=instance.id, binding_id=binding.id,
            binding_version=binding.version, snapshot=snapshot, grant_issued_at=issued_at,
            grant_expires_at=expires_at, digest=digest,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_generation_snapshots(self, generate_run_id: int) -> list[GenerationConnectorSnapshot]:
        return list((await self.session.scalars(
            select(GenerationConnectorSnapshot).where(
                GenerationConnectorSnapshot.generate_run_id == generate_run_id
            ).order_by(GenerationConnectorSnapshot.created_at)
        )).all())

    async def add_invocation_receipt(self, **values: Any) -> ConnectorInvocation:
        row = ConnectorInvocation(id=str(uuid.uuid4()), **values)
        self.session.add(row)
        await self.session.flush()
        return row

    async def audit(self, *, user_id: int, username: str | None, action: str, resource_type: str,
                    resource_id: str, details: Mapping[str, Any]) -> None:
        # This table is shared with RAG and intentionally gets only safe IDs and
        # diff metadata. Details are JSON text for compatibility with existing rows.
        await self.session.execute(text("""
            INSERT INTO audit_logs (user_id, username, action, resource_type, resource_id, details, created_at)
            VALUES (:user_id, :username, :action, :resource_type, :resource_id, :details, :created_at)
        """), {
            "user_id": user_id, "username": username, "action": action, "resource_type": resource_type,
            "resource_id": resource_id, "details": json.dumps(details, sort_keys=True),
            "created_at": dt.datetime.now(dt.timezone.utc),
        })


def instance_config(row: ConnectorInstance) -> ConnectorInstanceConfig:
    from .domain import ConnectorStatus
    return ConnectorInstanceConfig(
        id=row.id, provider_id=row.provider_id, display_name=row.display_name, safe_config=dict(row.safe_config or {}),
        ownership_scope=row.ownership_scope, enabled=row.enabled, status=ConnectorStatus(row.status),
        provider_version=row.provider_version, redaction_policy_version=row.redaction_policy_version,
        credential_type=row.credential_type,
    )


def source_profile(row: ConnectorSource, secrets: ConnectorSecretService = connector_secrets) -> ConnectorSourceProfile:
    return ConnectorSourceProfile(
        id=row.id, connector_instance_id=row.connector_instance_id, source_type=row.source_type,
        display_label=row.display_label, locator=secrets.decrypt_for_provider(row.locator_ciphertext),
        fields=dict(row.field_profile or {}), enabled=row.enabled, discovered_at=row.discovered_at,
        discovery_digest=row.discovery_digest,
    )


def binding_domain(row: ProjectConnectorBindingModel) -> ProjectConnectorBinding:
    return ProjectConnectorBinding(
        id=row.id, project_id=row.project_id, connector_instance_id=row.connector_instance_id,
        source_ids=tuple(row.source_ids or []), capability_ids=tuple(row.capability_ids or []),
        budgets=dict(row.budgets or {}), max_time_window_days=row.max_time_window_days,
        enabled=row.enabled, version=row.version,
    )
