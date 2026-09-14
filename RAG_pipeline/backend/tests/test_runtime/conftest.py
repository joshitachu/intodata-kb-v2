"""Shared fixtures for the runtime repository tests."""

import pytest
from sqlalchemy import event


class StatementRecorder:
    """Records the SQL a block of repository work issues.

    Runtime reads answer with aggregates over tables holding millions of rows, so
    "how many statements, and did any of them stream whole rows" is part of their
    contract rather than a detail. Asserting on it needs the real emitted SQL.
    """

    def __init__(self, engine):
        self._engine = engine
        self.statements: list[str] = []

    def _record(self, conn, cursor, statement, parameters, context, executemany):
        self.statements.append(statement)

    def __enter__(self) -> "StatementRecorder":
        self.statements.clear()
        event.listen(self._engine.sync_engine, "before_cursor_execute", self._record)
        return self

    def __exit__(self, *exc) -> bool:
        event.remove(self._engine.sync_engine, "before_cursor_execute", self._record)
        return False

    @property
    def count(self) -> int:
        return len(self.statements)

    def mentioning(self, fragment: str) -> list[str]:
        return [statement for statement in self.statements if fragment in statement]


@pytest.fixture
def statements(engine):
    """Factory for a `StatementRecorder` bound to the test engine."""
    return lambda: StatementRecorder(engine)
