"""Credential assignments in copied code are redacted, and caught if they are not.

Enrichment copies verbatim tMap expressions and route predicates into the
published topology. A hardcoded secret in code is an assignment, a shape the
URL/bearer/private-key patterns never matched. The scrubber and the fail-closed
check share one pattern so they cannot drift.
"""

from __future__ import annotations

import pytest

from mcp_server.services.business_topology.validation import (
    _sensitive_findings,
    redact_secretlike,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('String password = "s3cr3t!";', "String password = [REDACTED];"),
        ('url + "&pwd=abc123&user=x"', 'url + "&pwd=[REDACTED]&user=x"'),
        ('apiKey: "AKIA123"', "apiKey: [REDACTED]"),
        ("client_secret=xyz", "client_secret=[REDACTED]"),
    ],
)
def test_credential_assignments_are_redacted_and_then_clean(text: str, expected: str) -> None:
    assert _sensitive_findings(text)
    assert redact_secretlike(text) == expected
    assert not _sensitive_findings(redact_secretlike(text))


@pytest.mark.parametrize(
    "text",
    [
        'row.pwd == null ? "x" : row.pwd',
        "token: refreshed hourly",
        "The password is rotated monthly",
        "password = [REDACTED]",
        # Code, not secrets: an expression and a context reference.
        "String password = decoded.substring(0, i)",
        "password=context.db_password",
        # Placeholders the scanner already wrote.
        "cxf://host?password=[CREDENTIAL_PARAM_REDACTED]&user=x",
    ],
)
def test_comparisons_prose_and_redacted_values_are_left_alone(text: str) -> None:
    assert redact_secretlike(text) == text
    assert not _sensitive_findings(text)


def test_model_written_topology_prose_is_redacted_but_ids_are_not() -> None:
    from mcp_server.services.business_topology.models import BusinessTopology
    from mcp_server.services.business_topology.validation import redact_topology_prose

    topology = BusinessTopology.model_validate({
        "scan_fingerprint": "fp",
        "metadata": {"project_id": "p", "scan_fingerprint": "fp"},
        "coverage": {"total_artifacts": 0, "cited_artifacts": 0, "total_evidence": 0,
                     "cited_evidence": 0, "coverage_ratio": 0},
        "unknowns": [{
            "id": "unk-pwd=1", "name": "q", "status": "not_established", "confidence": 0.1,
            "evidence_ids": ["ev:password=x"], "question": 'Is pwd="hunter22" still used?',
        }],
    })

    unknown = redact_topology_prose(topology).unknowns[0]

    assert unknown.question == "Is pwd=[REDACTED] still used?"
    assert unknown.id == "unk-pwd=1"
    assert unknown.evidence_ids == ["ev:password=x"]
