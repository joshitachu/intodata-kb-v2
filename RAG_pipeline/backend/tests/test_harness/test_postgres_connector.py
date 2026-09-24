from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from pydantic import ValidationError

from src.harness.connectors.catalog import default_connector_provider_registry
from src.harness.connectors.models import (
    CapabilityBindingCreate,
    ConnectorAuthMode,
    ConnectorInstanceCreate,
    ConnectorQuery,
)
from src.harness.connectors.postgres_readonly import (
    POSTGRES_SEARCH_CAPABILITY_ID,
    PostgresReadOnlyConnector,
)
from src.harness.connectors.repository import ConnectorRepository
from src.harness.connectors.service import ConnectorPolicyError, ConnectorService


SQL = (
    'SELECT title, excerpt, source_ref FROM "approved_project_view" '
    "WHERE project_id = $3 "
    "AND position(lower($1) in lower(searchable_text)) > 0 "
    "ORDER BY source_ref LIMIT $2"
)


def _config(**overrides):
    return {
        "host": "db.example.com",
        "database": "reporting",
        "username": "nexus_reader",
        "view_name": "approved_project_view",
        "max_results": 2,
        **overrides,
    }


class FakeConnection:
    def __init__(self, rows=()):
        self.rows = rows
        self.transaction_readonly = None
        self.commands = []
        self.cursor_args = None
        self.closed = False

    @asynccontextmanager
    async def transaction(self, *, readonly):
        self.transaction_readonly = readonly
        yield

    async def execute(self, sql):
        self.commands.append(sql)

    async def fetchval(self, sql, *, timeout):
        self.commands.append(sql)
        return 1

    async def fetch(self, sql, *args, timeout):
        self.commands.append((sql, args, timeout))
        return []

    async def cursor(self, *args, **kwargs):
        self.cursor_args = (args, kwargs)
        for row in self.rows:
            yield row

    async def close(self):
        self.closed = True

    def terminate(self):
        self.closed = True


@pytest.mark.asyncio
async def test_postgres_query_uses_bound_project_readonly_transaction_and_hard_row_cap():
    connection = FakeConnection(
        [
            {"title": "A", "excerpt": "First", "source_ref": "one"},
            {"title": "B", "excerpt": "Second", "source_ref": "two"},
            {"title": "C", "excerpt": "Third", "source_ref": "three"},
            {"title": "D", "excerpt": "Fourth", "source_ref": "four"},
        ]
    )
    connect_kwargs = {}

    async def connect(**kwargs):
        connect_kwargs.update(kwargs)
        return connection

    adapter = PostgresReadOnlyConnector(
        connect=connect,
        password_resolver=lambda reference, *, expected_host, expected_port: "provisioned-password",
    )
    result = await adapter.read(
        POSTGRES_SEARCH_CAPABILITY_ID,
        ConnectorQuery(query="queue", limit=50),
        _config(),
        "env:NEXUS_CONNECTOR_REPORTING",
        "project-alpha",
    )

    assert [item.source_ref for item in result.items] == [
        "postgres:reporting:one",
        "postgres:reporting:two",
    ]
    assert result.truncated is True
    assert connection.transaction_readonly is True
    assert connection.cursor_args[0] == (SQL, "queue", 3, "project-alpha")
    assert connection.cursor_args[1]["prefetch"] == 3
    assert connection.commands == ["SET LOCAL statement_timeout = 5000"]
    assert connection.closed is True
    assert connect_kwargs["password"] == "provisioned-password"
    assert connect_kwargs["ssl"].verify_mode != 0


@pytest.mark.asyncio
async def test_postgres_refuses_missing_project_before_connecting():
    async def unexpected_connect(**kwargs):
        pytest.fail("database should not be contacted")

    adapter = PostgresReadOnlyConnector(
        connect=unexpected_connect,
        password_resolver=lambda reference, *, expected_host, expected_port: "password",
    )
    with pytest.raises(ValueError, match="bound project"):
        await adapter.read(
            POSTGRES_SEARCH_CAPABILITY_ID,
            ConnectorQuery(query="queue"),
            _config(),
            "env:NEXUS_CONNECTOR_REPORTING",
            None,
        )


@pytest.mark.asyncio
async def test_postgres_service_binding_blocks_other_projects_before_database_access(db_session):
    connections: list[FakeConnection] = []

    async def connect(**kwargs):
        connection = FakeConnection()
        connections.append(connection)
        return connection

    registry = default_connector_provider_registry()
    provider = registry.get("postgres_readonly")
    provider._connect = connect
    provider._password_resolver = lambda reference, *, expected_host, expected_port: "password"
    service = ConnectorService(ConnectorRepository(db_session), registry)
    row = await service.create(
        ConnectorInstanceCreate(
            name="Project reporting",
            provider_key="postgres_readonly",
            config=_config(),
            auth_mode=ConnectorAuthMode.CREDENTIAL_REFERENCE,
            credential_reference="env:NEXUS_CONNECTOR_REPORTING",
        )
    )
    await service.bind(
        row.id,
        CapabilityBindingCreate(
            capability_id=POSTGRES_SEARCH_CAPABILITY_ID,
            project_ids=["project-alpha"],
        ),
    )
    await service.check_health(row.id)
    assert len(connections) == 1

    for denied_project in ("project-beta", None):
        with pytest.raises(ConnectorPolicyError, match="project scope"):
            await service.read(
                row.id,
                POSTGRES_SEARCH_CAPABILITY_ID,
                denied_project,
                None,
                ConnectorQuery(query="queue"),
            )
    assert len(connections) == 1


@pytest.mark.asyncio
async def test_postgres_health_tests_readonly_connection_without_running_template():
    connection = FakeConnection()

    async def connect(**kwargs):
        return connection

    adapter = PostgresReadOnlyConnector(
        connect=connect,
        password_resolver=lambda reference, *, expected_host, expected_port: "password",
    )
    result = await adapter.health(_config(), "env:NEXUS_CONNECTOR_REPORTING")

    assert result.status.value == "healthy"
    assert connection.transaction_readonly is True
    assert connection.commands == [
        "SET LOCAL statement_timeout = 5000",
        "SELECT 1",
        (SQL, ("nexus-health", 0, "__nexus_health__"), 5.0),
    ]
    assert connection.closed is True


@pytest.mark.asyncio
async def test_postgres_health_rejects_inaccessible_or_incompatible_view():
    class BrokenViewConnection(FakeConnection):
        async def fetch(self, sql, *args, timeout):
            raise RuntimeError("permission denied for view")

    connection = BrokenViewConnection()

    async def connect(**kwargs):
        return connection

    adapter = PostgresReadOnlyConnector(
        connect=connect,
        password_resolver=lambda reference, *, expected_host, expected_port: "password",
    )
    with pytest.raises(RuntimeError, match="permission denied"):
        await adapter.health(_config(), "env:NEXUS_CONNECTOR_REPORTING")
    assert connection.closed is True


@pytest.mark.asyncio
async def test_postgres_connection_error_does_not_return_secret_or_endpoint_detail():
    async def fail_connect(**kwargs):
        raise RuntimeError(f"failed for {kwargs['password']} at {kwargs['host']}")

    adapter = PostgresReadOnlyConnector(
        connect=fail_connect,
        password_resolver=lambda reference, *, expected_host, expected_port: "hidden-password",
    )
    with pytest.raises(RuntimeError) as captured:
        await adapter.read(
            POSTGRES_SEARCH_CAPABILITY_ID,
            ConnectorQuery(query="queue"),
            _config(),
            "env:NEXUS_CONNECTOR_REPORTING",
            "project-alpha",
        )
    assert str(captured.value) == "PostgreSQL connector connection failed"
    assert captured.value.__suppress_context__ is True


@pytest.mark.parametrize(
    "view_name",
    [
        "approved_view; DROP TABLE users",
        "public.approved_view WHERE true",
        "public..approved_view",
        "/tmp/approved_view",
        "public.approved_view--bypass",
    ],
)
def test_postgres_rejects_non_view_configuration(view_name):
    with pytest.raises(ValidationError):
        PostgresReadOnlyConnector().validate_config(_config(view_name=view_name))
