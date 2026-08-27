"""Guard against model-vs-migration column drift.

A migration that hand-writes `create_table` and omits a column the declarative
`Base` contributes produces a table the ORM cannot SELECT at all: the mapper
emits the declared column list and PostgreSQL answers `UndefinedColumnError`.

This has now happened three times:

* 093 -- `runtime_static_topology_snapshots`, `runtime_job_scan_maps`
* 094 -- `instance_settings`
* 096 -- `runtime_source_read_grants` (broke every read of a project's read
  grants in production), `wizard_events` (same omission, not yet read via the ORM)

What this file can and cannot check
-----------------------------------
Comparing the models against a database built by the MIGRATION CHAIN is the
check that would have caught all three. It cannot run here: migration 005 calls
PostgreSQL's `setval()`, so the chain does not replay on SQLite, and a test that
only ever skips is worse than no test -- it reads as coverage.

So the split is:

* this file -- pure metadata, always runs, catches a model that fails to declare
  a Base column (the precondition for the drift);
* `src/db/schema_drift.py` -- run against the real PostgreSQL, catches a
  migration that omitted a declared column. That is the one to run after
  `alembic upgrade head` on a deploy.
"""

from __future__ import annotations

import sqlalchemy as sa

from src.db.base import Base
import src.db.models  # noqa: F401  -- import registers every mapper


def _mapped_class_tables() -> dict[str, sa.Table]:
    """Tables backed by a mapped class.

    `Base.metadata.tables` also holds association `Table()` objects
    (`user_roles`, `role_permissions` in `src/db/models/user.py`). Those are not
    Base subclasses and correctly have no timestamps, so asserting over raw
    metadata produces false failures.
    """
    tables: dict[str, sa.Table] = {}
    for mapper in Base.registry.mappers:
        table = mapper.local_table
        if isinstance(table, sa.Table):
            tables[table.name] = table
    return tables


def test_base_timestamps_are_declared_on_every_mapped_class() -> None:
    """`Base` promises `created_at` and `updated_at` on every mapped class.

    Catches a model that opts out -- for example by redeclaring only
    `created_at`, which is exactly the shape `runtime_source_read_grants` had
    when its migration then omitted `updated_at`.
    """
    missing = sorted(
        f"{name}.{column}"
        for name, table in _mapped_class_tables().items()
        for column in ("created_at", "updated_at")
        if column not in table.columns
    )
    assert not missing, (
        "Mapped classes missing an inherited Base timestamp. Either declare the "
        "column or stop inheriting Base; a half-declared pair is what lets a "
        "migration and the model disagree:\n  " + "\n  ".join(missing)
    )


def test_association_tables_are_excluded_from_the_timestamp_rule() -> None:
    """Pin the exclusion so the helper above is not "fixed" into uselessness.

    `user_roles` and `role_permissions` are association tables with no mapped
    class. If a future refactor turned them into mapped classes, this test fails
    and the timestamp rule must then cover them.
    """
    mapped = _mapped_class_tables()
    for name in ("user_roles", "role_permissions"):
        assert name in Base.metadata.tables, f"{name} vanished from the metadata"
        assert name not in mapped, (
            f"{name} is now a mapped class; it must satisfy the Base timestamp "
            "rule, so remove it from this exclusion."
        )


def test_drift_script_covers_every_mapped_table() -> None:
    """The live check must iterate mapped tables, not a hand-maintained list.

    A hardcoded table list in the script would silently stop covering new
    models, which is how this class of bug survives.
    """
    from pathlib import Path

    script = (
        Path(__file__).resolve().parents[2] / "src" / "db" / "schema_drift.py"
    ).read_text(encoding="utf-8")
    assert "Base.metadata.tables" in script, (
        "schema_drift.py must derive its table set from the metadata"
    )
