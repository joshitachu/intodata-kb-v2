"""PostgreSQL evidence adapter using a fixed, project-scoped query template.

The agent supplies only a search term and a result limit. An owner selects a
database view exposing the prescribed columns, and the project id comes from
the server-validated capability scope. The database account should have SELECT
grants only; the transaction also enforces READ ONLY and a statement timeout.
"""

from __future__ import annotations

import asyncio
import re
import ssl
from collections.abc import Awaitable, Callable
from typing import Any

import asyncpg
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .credentials import resolve_env_secret
from .models import (
    ConnectorHealthResult,
    ConnectorHealthStatus,
    ConnectorQuery,
    ConnectorReadResult,
    ConnectorResultItem,
)


POSTGRES_SEARCH_CAPABILITY_ID = "connector.postgres.search"
_HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_VIEW_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}(?:\.[A-Za-z_][A-Za-z0-9_]{0,62})?$")


class PostgresReadOnlyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=5432, ge=1, le=65535)
    database: str = Field(min_length=1, max_length=63)
    username: str = Field(min_length=1, max_length=63)
    view_name: str = Field(min_length=1, max_length=127)
    timeout_seconds: float = Field(default=5.0, ge=0.1, le=10.0)
    max_results: int = Field(default=10, ge=1, le=50)

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        if not _HOST.fullmatch(value) or ".." in value or value.endswith("."):
            raise ValueError("host must be a DNS name or IPv4 address")
        return value

    @field_validator("database", "username")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("database and username must be simple identifiers")
        return value

    @field_validator("view_name")
    @classmethod
    def validate_view_name(cls, value: str) -> str:
        if not _VIEW_NAME.fullmatch(value):
            raise ValueError("view_name must be a simple view or schema.view name")
        return value


Connect = Callable[..., Awaitable[Any]]
PasswordResolver = Callable[..., str]


class PostgresReadOnlyConnector:
    def __init__(
        self,
        *,
        connect: Connect | None = None,
        password_resolver: PasswordResolver = resolve_env_secret,
    ) -> None:
        self._connect = connect or asyncpg.connect
        self._password_resolver = password_resolver

    def validate_config(self, config: dict) -> dict:
        return PostgresReadOnlyConfig.model_validate(config).model_dump(mode="json")

    async def health(
        self, config: dict, credential_reference: str | None
    ) -> ConnectorHealthResult:
        parsed = PostgresReadOnlyConfig.model_validate(config)
        password = self._password_resolver(
            credential_reference, expected_host=parsed.host, expected_port=parsed.port
        )
        connection = await self._open(parsed, password)
        try:
            async with connection.transaction(readonly=True):
                await self._set_timeout(connection, parsed)
                await connection.fetchval("SELECT 1", timeout=parsed.timeout_seconds)
                # LIMIT 0 still parses the fixed search and checks view columns,
                # types, and SELECT permission without reading customer rows.
                await connection.fetch(
                    _search_sql(parsed.view_name),
                    "nexus-health",
                    0,
                    "__nexus_health__",
                    timeout=parsed.timeout_seconds,
                )
        finally:
            await self._close(connection)
        return ConnectorHealthResult(
            status=ConnectorHealthStatus.HEALTHY,
            summary="PostgreSQL read-only connection is available",
        )

    async def read(
        self,
        capability_id: str,
        query: ConnectorQuery,
        config: dict,
        credential_reference: str | None,
        project_id: str | None,
    ) -> ConnectorReadResult:
        if capability_id != POSTGRES_SEARCH_CAPABILITY_ID:
            raise ValueError("Unsupported connector capability")
        if not project_id:
            raise ValueError("PostgreSQL connector requires a bound project")
        parsed = PostgresReadOnlyConfig.model_validate(config)
        password = self._password_resolver(
            credential_reference, expected_host=parsed.host, expected_port=parsed.port
        )
        limit = min(query.limit, parsed.max_results)
        connection = await self._open(parsed, password)
        rows: list[dict[str, Any]] = []
        try:
            async with connection.transaction(readonly=True):
                await self._set_timeout(connection, parsed)
                cursor = connection.cursor(
                    _search_sql(parsed.view_name),
                    query.query,
                    limit + 1,
                    project_id,
                    prefetch=limit + 1,
                    timeout=parsed.timeout_seconds,
                )
                async for record in cursor:
                    rows.append(dict(record))
                    if len(rows) > limit:
                        break
        except Exception:
            raise RuntimeError("PostgreSQL connector query failed") from None
        finally:
            await self._close(connection)
        items = [_normalize_row(row, parsed.database) for row in rows[:limit]]
        return ConnectorReadResult(items=items, truncated=len(rows) > limit)

    async def _open(self, config: PostgresReadOnlyConfig, password: str) -> Any:
        try:
            return await self._connect(
                host=config.host,
                port=config.port,
                database=config.database,
                user=config.username,
                password=password,
                ssl=ssl.create_default_context(),
                timeout=config.timeout_seconds,
                server_settings={"application_name": "nexus_readonly_connector"},
            )
        except Exception:
            raise RuntimeError("PostgreSQL connector connection failed") from None

    @staticmethod
    async def _set_timeout(connection: Any, config: PostgresReadOnlyConfig) -> None:
        timeout_ms = int(config.timeout_seconds * 1000)
        await connection.execute(f"SET LOCAL statement_timeout = {timeout_ms}")

    @staticmethod
    async def _close(connection: Any) -> None:
        try:
            await asyncio.wait_for(connection.close(), timeout=3)
        except Exception:
            connection.terminate()


def _normalize_row(row: dict[str, Any], database: str) -> ConnectorResultItem:
    if not {"title", "excerpt", "source_ref"}.issubset(row):
        raise ValueError("PostgreSQL query must return title, excerpt, and source_ref")
    ref = str(row["source_ref"] or "")
    if not ref:
        raise ValueError("PostgreSQL query returned an empty source_ref")
    return ConnectorResultItem(
        title=str(row["title"] or "")[:200],
        excerpt=str(row["excerpt"] or "")[:2000],
        source_ref=f"postgres:{database}:{ref}"[:255],
        metadata={"database": database},
    )


def _search_sql(view_name: str) -> str:
    quoted_view = ".".join(f'"{part}"' for part in view_name.split("."))
    return (
        "SELECT title, excerpt, source_ref FROM "
        f"{quoted_view} "
        "WHERE project_id = $3 "
        "AND position(lower($1) in lower(searchable_text)) > 0 "
        "ORDER BY source_ref LIMIT $2"
    )
