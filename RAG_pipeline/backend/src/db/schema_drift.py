#!/usr/bin/env python
"""Report columns the models declare but the database does not have.

Why this exists
---------------
A migration that hand-writes ``create_table`` and omits a column the declarative
``Base`` contributes produces a table the ORM cannot SELECT at all: the mapper
emits the declared column list and PostgreSQL answers ``UndefinedColumnError``.
It fails at read time, in production, long after the migration "succeeded".

It has happened three times (alembic 093, 094, 096). The last one broke every
read of a project's runtime read grants with::

    column runtime_source_read_grants.updated_at does not exist

The unit-test suite cannot catch this: migration 005 calls PostgreSQL's
``setval()``, so the chain does not replay on SQLite, and comparing the models
against a ``create_all`` database is tautological. The only honest check is
against a database the migrations actually built -- which is what this script
does.

Usage
-----
Run it after ``alembic upgrade head``, against the deployed database::

    docker compose exec backend python -m src.db.schema_drift

It lives under ``src/`` rather than ``scripts/`` on purpose: the backend image
copies only ``src/``, ``alembic/`` and ``alembic.ini``, so a checker in
``scripts/`` cannot run in the container where it is actually needed.

Reads ``DATABASE_URL`` from the environment (via ``settings``). Exits 0 when the
schema matches, 1 when it does not, printing every offending table and column.
The table set comes from ``Base.metadata.tables`` rather than a hardcoded list,
so new models are covered automatically.
"""

from __future__ import annotations

import asyncio
import sys

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from src.config import settings
from src.db.base import Base
import src.db.models  # noqa: F401  -- import registers every mapper


def _collect_drift(sync_conn: sa.Connection) -> list[str]:
    inspector = sa.inspect(sync_conn)
    present = set(inspector.get_table_names())

    drift: list[str] = []
    for name, table in sorted(Base.metadata.tables.items()):
        if name not in present:
            drift.append(f"{name}: table missing entirely")
            continue
        actual = {column["name"] for column in inspector.get_columns(name)}
        missing = sorted({column.name for column in table.columns} - actual)
        if missing:
            drift.append(f"{name}: missing {', '.join(missing)}")
    return drift


async def main() -> int:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            drift = await conn.run_sync(_collect_drift)
    finally:
        await engine.dispose()

    if not drift:
        print(f"OK: {len(Base.metadata.tables)} mapped tables match the database")
        return 0

    print("SCHEMA DRIFT -- the ORM will raise UndefinedColumnError on these:")
    for line in drift:
        print(f"  {line}")
    print(
        "\nAdd the missing columns in a new alembic revision. Do not paper over "
        "it by removing the column from the model: the Base timestamps are read "
        "by the ORM on every SELECT."
    )
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
