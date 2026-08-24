import datetime as dt

from mcp_server.connectors.domain import EvidenceEnvelope, EvidenceItem
from mcp_server.connectors.evidence import finalize_envelope, make_opaque_reference


def test_evidence_redacts_secrets_and_does_not_expose_provider_id():
    envelope = EvidenceEnvelope(
        evidence_id="ev_1", run_id=9, project_id="project", connector_instance_id="connector",
        source_id="source", capability_id="runtime.events.search", question="question", entity_refs=(),
        observed_at=dt.datetime.now(dt.timezone.utc), source_window={},
        items=(EvidenceItem("item", None, {"message": "Bearer abcdefghijklmnopqrstuvwxyz0123456789", "password": "nope"}),),
    )
    result = finalize_envelope(envelope)
    assert result.redaction["count"] >= 2
    assert result.items[0].fields["password"] == "[REDACTED]"
    assert "abcdefghijklmnopqrstuvwxyz" not in str(result.items)
    assert result.digest.startswith("sha256:")


def test_opaque_item_reference_changes_between_runs():
    assert make_opaque_reference(1, "src", "index:raw-id") != make_opaque_reference(2, "src", "index:raw-id")
