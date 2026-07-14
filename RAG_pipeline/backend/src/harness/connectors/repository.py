"""Database operations for configured connectors and capability bindings."""

from __future__ import annotations

import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.models.harness_connector import (
    HarnessConnectorCapabilityBindingRow,
    HarnessConnectorInstanceRow,
)

from .models import CapabilityBindingCreate, ConnectorInstanceCreate, ConnectorInstanceUpdate


class ConnectorNotFoundError(LookupError):
    pass


class ConnectorRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_instance(self, data: ConnectorInstanceCreate, config: dict) -> HarnessConnectorInstanceRow:
        row = HarnessConnectorInstanceRow(
            name=data.name,
            provider_key=data.provider_key,
            config=config,
            auth_mode=data.auth_mode.value,
            credential_reference=data.credential_reference,
            enabled=data.enabled,
            health_status="unknown",
        )
        self.db.add(row)
        await self.db.flush()
        await self.db.refresh(row)
        return row

    async def get_instance(self, instance_id: int, *, bindings: bool = False) -> HarnessConnectorInstanceRow:
        statement = select(HarnessConnectorInstanceRow).where(HarnessConnectorInstanceRow.id == instance_id)
        if bindings:
            statement = statement.options(selectinload(HarnessConnectorInstanceRow.capability_bindings))
        row = (await self.db.execute(statement)).scalar_one_or_none()
        if row is None:
            raise ConnectorNotFoundError(instance_id)
        return row

    async def list_instances(self) -> list[HarnessConnectorInstanceRow]:
        result = await self.db.execute(select(HarnessConnectorInstanceRow).order_by(HarnessConnectorInstanceRow.id))
        return list(result.scalars())

    async def list_instances_for_scope(
        self, project_ids: list[str] | None
    ) -> list[HarnessConnectorInstanceRow]:
        result = await self.db.execute(
            select(HarnessConnectorInstanceRow)
            .options(selectinload(HarnessConnectorInstanceRow.capability_bindings))
            .order_by(HarnessConnectorInstanceRow.id)
        )
        rows = list(result.scalars())
        if project_ids is None:
            return rows
        allowed = set(project_ids)
        return [
            row
            for row in rows
            if any(
                not binding.project_ids or bool(allowed.intersection(binding.project_ids))
                for binding in row.capability_bindings
            )
        ]

    async def update_instance(
        self, row: HarnessConnectorInstanceRow, data: ConnectorInstanceUpdate, config: dict | None
    ) -> HarnessConnectorInstanceRow:
        changes = data.model_dump(exclude_unset=True)
        if "name" in changes:
            row.name = changes["name"]
        if config is not None:
            row.config = config
        if "enabled" in changes:
            row.enabled = changes["enabled"]
        if data.clear_credential_reference:
            row.credential_reference = None
        elif "credential_reference" in changes:
            row.credential_reference = changes["credential_reference"]
        await self.db.flush()
        await self.db.refresh(row)
        return row

    async def delete_instance(self, row: HarnessConnectorInstanceRow) -> None:
        await self.db.delete(row)
        await self.db.flush()

    async def add_binding(
        self, instance_id: int, data: CapabilityBindingCreate
    ) -> HarnessConnectorCapabilityBindingRow:
        row = HarnessConnectorCapabilityBindingRow(
            connector_instance_id=instance_id,
            **data.model_dump(),
        )
        self.db.add(row)
        await self.db.flush()
        await self.db.refresh(row)
        return row

    async def delete_binding(self, instance_id: int, binding_id: int) -> None:
        statement = select(HarnessConnectorCapabilityBindingRow).where(
            HarnessConnectorCapabilityBindingRow.id == binding_id,
            HarnessConnectorCapabilityBindingRow.connector_instance_id == instance_id,
        )
        row = (await self.db.execute(statement)).scalar_one_or_none()
        if row is None:
            raise ConnectorNotFoundError(binding_id)
        await self.db.delete(row)
        await self.db.flush()

    async def set_health(self, row: HarnessConnectorInstanceRow, status: str, summary: str) -> None:
        row.health_status = status
        row.health_summary = summary[:500]
        row.health_checked_at = datetime.datetime.now(datetime.UTC)
        await self.db.flush()

    async def eligible_bindings(
        self, project_id: str | None, domain: str | None = None
    ) -> list[HarnessConnectorCapabilityBindingRow]:
        result = await self.db.execute(
            select(HarnessConnectorCapabilityBindingRow)
            .join(HarnessConnectorInstanceRow)
            .options(selectinload(HarnessConnectorCapabilityBindingRow.connector_instance))
            .where(
                HarnessConnectorCapabilityBindingRow.enabled.is_(True),
                HarnessConnectorInstanceRow.enabled.is_(True),
                HarnessConnectorInstanceRow.health_status == "healthy",
            )
            .order_by(HarnessConnectorCapabilityBindingRow.id)
        )
        rows = list(result.scalars())
        return [
            row for row in rows
            if (not row.project_ids or project_id in row.project_ids)
            and (not row.domains or domain in row.domains)
        ]

    async def list_eligible_bindings(
        self, project_ids: list[str] | None
    ) -> list[HarnessConnectorCapabilityBindingRow]:
        result = await self.db.execute(
            select(HarnessConnectorCapabilityBindingRow)
            .join(HarnessConnectorInstanceRow)
            .options(selectinload(HarnessConnectorCapabilityBindingRow.connector_instance))
            .where(
                HarnessConnectorCapabilityBindingRow.enabled.is_(True),
                HarnessConnectorInstanceRow.enabled.is_(True),
                HarnessConnectorInstanceRow.health_status == "healthy",
            )
            .order_by(HarnessConnectorCapabilityBindingRow.id)
        )
        rows = list(result.scalars())
        if project_ids is None:
            return rows
        allowed = set(project_ids)
        return [
            row
            for row in rows
            if not row.project_ids or bool(allowed.intersection(row.project_ids))
        ]
