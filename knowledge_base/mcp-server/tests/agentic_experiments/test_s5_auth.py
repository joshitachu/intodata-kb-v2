from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from mcp_server.experiments.mcp_tools.auth import TaskTokenError, TaskTokenIssuer, TaskTokenVerifier


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _issuer() -> TaskTokenIssuer:
    return TaskTokenIssuer(key_version="v1", signing_key=b"unit-test-signing-key")


def _token(**overrides: object) -> str:
    values: dict[str, object] = {
        "project_id": "project-a",
        "experiment_id": "experiment-a",
        "task_id": "task-a",
        "source_roles": frozenset({"implementation"}),
        "subject_ids": frozenset({"subject-a"}),
        "allowed_tools": frozenset({"nexus_experiment_get_contract"}),
        "budgets": {"max_calls": 2, "max_read_bytes": 100, "max_result_bytes": 100, "max_write_bytes": 100},
        "now": NOW,
        "ttl": timedelta(minutes=1),
    }
    values.update(overrides)
    return _issuer().issue(**values)  # type: ignore[arg-type]


def test_hmac_task_token_binds_audience_expiry_key_version_and_task_scope():
    verifier = TaskTokenVerifier(keys={"v1": b"unit-test-signing-key"})
    claims = verifier.verify(
        _token(),
        now=NOW,
        project_id="project-a",
        experiment_id="experiment-a",
        task_id="task-a",
    )

    assert claims.key_version == "v1"
    assert claims.source_roles == {"implementation"}
    with pytest.raises(TaskTokenError, match="experiment scope"):
        verifier.verify(_token(), now=NOW, experiment_id="other-experiment")
    with pytest.raises(TaskTokenError, match="expired"):
        verifier.verify(_token(), now=NOW + timedelta(minutes=1))


def test_token_rejects_tampering_and_wildcard_scope():
    verifier = TaskTokenVerifier(keys={"v1": b"unit-test-signing-key"})
    token = _token()
    tampered = f"{token[:-1]}x"

    with pytest.raises(TaskTokenError):
        verifier.verify(tampered, now=NOW)
    with pytest.raises(ValueError, match="non-wildcard"):
        _token(subject_ids=frozenset({"*"}))
    with pytest.raises(ValueError, match="non-wildcard"):
        _token(source_roles=frozenset({"role with spaces"}))
    with pytest.raises(ValueError, match="exact non-negative"):
        _token(budgets={"max_calls": 1})
    with pytest.raises(ValueError, match="exceeds"):
        _token(ttl=timedelta(minutes=16))
    with pytest.raises(TaskTokenError, match="future"):
        verifier.verify(_token(now=NOW + timedelta(seconds=1)), now=NOW)
    with pytest.raises(TaskTokenError, match="malformed"):
        verifier.verify("x" * 4097, now=NOW)


def test_verifier_rejects_non_positive_maximum_ttl():
    with pytest.raises(ValueError, match="positive"):
        TaskTokenVerifier(keys={"v1": b"unit-test-signing-key"}, max_ttl=timedelta())
