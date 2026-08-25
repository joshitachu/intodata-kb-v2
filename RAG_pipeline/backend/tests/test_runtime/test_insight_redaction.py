"""Entity-shape redaction across the runtime INSIGHT lane.

``tests/test_redaction.py`` pins the shared filter in ``src.runtime.redaction``.
This file pins its use by the insight pipeline, which is a separate lane with a
separate leak: ``parse_runtime_log_message`` passed extracted values through
``redact_credentials`` alone, so ``runtime_enriched_events.endpoint`` (10,406
stored rows) and ``.host`` (10,366) held customer object-store buckets, internal
FQDNs, single-label internal hostnames and a customer SaaS host verbatim, and
the derived ``causal_message`` / incident-evidence ``excerpt`` carried the same
shapes on into an LLM tool result.

Two contracts are pinned here and they pull in opposite directions:

  * every identifying shape the live corpus actually contained is normalised;
  * every identifier the downstream pipeline treats as ground truth survives --
    component names (the documentation harness's component-echo validator
    rejects a ``tXxx_N`` reference absent from its evidence, so redacting one
    turns a real runtime fact into a fabrication false-positive), Java/.NET
    class names, and URI schemes.

Values marked "from the live mirror" are verbatim, matching the convention in
``test_redaction.py``: the point of the assertion is that THESE strings reached
a prompt. The one exception is the Azure tenant id, which is synthesised -- the
URL flattening makes the real value irrelevant to what is being tested.
"""

import datetime
import hashlib
import subprocess
import sys
from datetime import UTC

import pytest
from sqlalchemy import select

from src.db.models.runtime_data import RuntimeExecutionRow, RuntimeLogEventRow
from src.db.models.runtime_insights import (
    RuntimeEnrichedEventRow,
    RuntimeIncidentEvidenceRow,
    RuntimeIncidentRow,
)
from src.runtime.insights.builder import build_runtime_insights_for_execution
from src.runtime.insights.parser import (
    parse_runtime_log_message,
    redact_endpoint,
    redact_host,
    redact_log_text,
)
from src.runtime.insights.repository import RuntimeInsightRepository

_SYNCED_AT = datetime.datetime(2026, 6, 17, 10, 0, tzinfo=UTC)
_PROJECT = "esbmain"

# Endpoint values taken from `select distinct endpoint from
# runtime_enriched_events` on the live mirror, one per distinct leak shape.
_LEAKED_ENDPOINTS = [
    "https://volkerinfra.relaticsonline.com/dataexchange.asmx",
    "https://SRVA0673:9200/talend-esb-monitoring-alias/_doc:",
    "https://talend-acc.vwinfra.nl/services/4psSubscriber/data_event",
    "gs://vaxxinova-talend/jpn_vjp_rawitemApplicationEntriesDelta.csv",
    "https://mas.manage.infranl.almhosting.com/maximo/api/os/mxapiwo?pageno=1",
    "https://connect-pl.esize.nl/Esize.Messaging.OrderImport_Proxy/x.asmx?WSDL",
    "https://us-central1-vaxxinova-dwh.cloudfunctions.net",
    "jdbc:oracle:thin:@//db-prod-scan:1521/ORCLPDB1",
    # Synthesised tenant id; the real one is an Azure directory identifier.
    "https://login.microsoftonline.com/11111111-2222-3333-4444-555555555555/oauth2/v2.0",
]

# Every distinct `host` value shape the mirror holds. Three of them are single
# label (`SRVA0673`, `srvo0667`, `db-prod-scan`) and are invisible to the shared
# FQDN rule, which is why this field needs whole-value treatment.
_LEAKED_HOSTS = [
    "volkerinfra.relaticsonline.com",
    "SRVA0673",
    "srvo0667",
    "db-prod-scan",
    "talend-acc.vwinfra.nl",
    "mas.manage.infranl.almhosting.com",
    "connect-pl.esize.nl",
    "us-central1-vaxxinova-dwh.cloudfunctions.net",
]

# Fragments that identify a customer, an estate or a tenant. No redacted value
# may contain any of them.
_IDENTIFIER_FRAGMENTS = (
    "vaxxinova",
    "volkerinfra",
    "relaticsonline",
    "vwinfra",
    "almhosting",
    "infranl",
    "esize",
    "SRVA0673",
    "srvo0667",
    "db-prod-scan",
    "cloudfunctions",
    "microsoftonline",
)


def _assert_no_identifier(text: str) -> None:
    lowered = text.lower()
    for fragment in _IDENTIFIER_FRAGMENTS:
        assert fragment.lower() not in lowered, f"{fragment!r} survived in {text!r}"


async def _seed_failed_execution(
    session,
    *,
    execution_id: str,
    job_name: str,
    lines: list[tuple[int, str, str]],
    project_id: str = _PROJECT,
) -> None:
    """Seed one failed execution with explicit (offset, severity, message) lines."""
    session.add(
        RuntimeExecutionRow(
            project_id=project_id,
            execution_id=execution_id,
            job_name=job_name,
            project_name=project_id,
            state="failed",
            source_key="mock",
            started_at=datetime.datetime(2026, 6, 18, 9, 0, tzinfo=UTC),
            ended_at=datetime.datetime(2026, 6, 18, 9, 1, tzinfo=UTC),
            duration_ms=60000,
            ingested_at=_SYNCED_AT,
        )
    )
    for offset, severity, message in lines:
        ts = datetime.datetime(2026, 6, 18, 9, 0, offset, tzinfo=UTC)
        fingerprint = hashlib.sha256(
            f"{ts.isoformat()}|{severity}|{message}".encode()
        ).hexdigest()[:64]
        session.add(
            RuntimeLogEventRow(
                project_id=project_id,
                execution_id=execution_id,
                source_key="mock",
                severity=severity,
                message=message,
                event_fingerprint=fingerprint,
                timestamp=ts,
                ingested_at=_SYNCED_AT,
            )
        )
    await session.flush()


class TestEndpointRedaction:
    @pytest.mark.parametrize("value", _LEAKED_ENDPOINTS)
    def test_every_leaked_endpoint_shape_is_normalised(self, value):
        _assert_no_identifier(redact_endpoint(value))

    def test_scheme_survives_because_it_is_the_diagnosis(self):
        """"A GCS object was not found" is the finding; the bucket name is not."""
        assert redact_endpoint("gs://vaxxinova-talend/raw.csv") == "gs://<bucket>/<path>"
        assert redact_endpoint("https://volkerinfra.relaticsonline.com/x").startswith(
            "https://"
        )

    def test_driver_chain_survives_a_jdbc_url(self):
        """`jdbc:oracle:thin` names the driver -- keep it, drop the address.

        `:@//` sits between the scheme chain and the authority, so this shape
        once escaped the generic `scheme://` rule entirely, and `db-prod-scan`
        (single label) is invisible to the FQDN rule as well -- it survived both
        passes before `redact_endpoint` existed. The shared rule now captures the
        separator instead of assuming `://`, so the case is handled at source and
        the driver chain still survives.

        The trailing database name goes with the path. It is dropped rather than
        kept: a database name can identify a customer estate as readily as a
        host, and documentation gets connection and database names from scan
        evidence, not from runtime error text.
        """
        assert redact_endpoint("jdbc:oracle:thin:@//db-prod-scan:1521/ORCLPDB1") == (
            "jdbc:oracle:thin:@//<host>/..."
        )

    def test_redaction_is_idempotent(self):
        once = redact_endpoint("https://volkerinfra.relaticsonline.com/dataexchange.asmx")
        assert redact_endpoint(once) == once

    def test_parser_writes_a_redacted_endpoint(self):
        parsed = parse_runtime_log_message(
            "tRESTClient_1 invoking "
            "https://mas.manage.infranl.almhosting.com/maximo/api/os/mxapiwo "
            "javax.ws.rs.ProcessingException: read timed out"
        )
        assert parsed.endpoint is not None
        _assert_no_identifier(parsed.endpoint)


class TestHostRedaction:
    @pytest.mark.parametrize("value", _LEAKED_HOSTS)
    def test_every_stored_host_becomes_the_placeholder(self, value):
        assert redact_host(value) == "<host>"

    def test_absence_is_preserved_as_absence(self):
        """`host is None` drives family inference and confidence -- it must not
        become the placeholder."""
        assert redact_host(None) is None
        assert redact_host("") == ""

    def test_presence_signal_survives_for_downstream_consumers(self):
        parsed = parse_runtime_log_message(
            "[tOracleConnection_1] jdbc:oracle:thin:@//db-prod-scan:1521/ORCLPDB1 "
            "ORA-12170: TNS:Connect timeout"
        )
        assert parsed.host == "<host>"
        # The two decisions that read this field still land the same way.
        assert parsed.message_family == "jdbc"
        assert parsed.confidence in ("medium", "high")


class TestCausalMessageRedaction:
    def test_bucket_uri_in_the_runtimes_own_sentence(self):
        parsed = parse_runtime_log_message(
            "tBigQueryOutput_1_tBQBE com.google.cloud.bigquery.BigQueryException: "
            "Not found: Files gs://vaxxinova-talend/jpn_vjp_rawitemApp.csv"
        )
        assert parsed.causal_message is not None
        _assert_no_identifier(parsed.causal_message)

    def test_internal_fqdn_in_the_runtimes_own_sentence(self):
        parsed = parse_runtime_log_message(
            "Interceptor for {https://SRVA0673:9200/talend-esb-monitoring-alias}WebClient "
            "has thrown exception, unwinding now"
        )
        assert parsed.causal_message is not None
        _assert_no_identifier(parsed.causal_message)


class TestPreservedIdentifiers:
    """These are load-bearing downstream. Losing one is a defect, not caution."""

    @pytest.mark.parametrize(
        "component",
        [
            "tMSSqlConnection_2",
            "tBigQueryOutput_1_tBQBE",
            "tRESTClient_1",
            "cMessagingEndpoint_1",
            "Create_Business_Central_Token_v2_1_tExtractJSONFields_1",
        ],
    )
    def test_component_names_survive_verbatim(self, component):
        text = f"Exception in component {component} (SomeJob) failed"
        assert component in redact_log_text(text)

    @pytest.mark.parametrize(
        "exception_class",
        [
            "java.net.SocketException",
            "java.sql.SQLRecoverableException",
            "com.google.cloud.bigquery.BigQueryException",
            "javax.ws.rs.ProcessingException",
            "System.Data.SqlClient.SqlException",
            "org.apache.camel.CamelExecutionException",
        ],
    )
    def test_qualified_class_names_survive_verbatim(self, exception_class):
        assert exception_class in redact_log_text(f"Caused by: {exception_class}: boom")

    @pytest.mark.parametrize("scheme", ["https", "http", "gs", "jdbc:jtds:sqlserver"])
    def test_uri_schemes_survive(self, scheme):
        assert redact_log_text(f"connecting to {scheme}://host.example.com/x").startswith(
            f"connecting to {scheme}://"
        )

    def test_the_full_parse_keeps_component_and_exception(self):
        parsed = parse_runtime_log_message(
            "Execution 2909d999-35ea-483b-a0ac-61d3c0a1dc35 failed with code 1 and error "
            "Job stopped with errors or unable to run. Exception in component "
            "tBigQueryOutput_1_tBQBE (VJP_Raw_itemApplicationEntries) "
            "com.google.cloud.bigquery.BigQueryException: Not found: Files "
            "gs://vaxxinova-talend/jpn_vjp_raw.csv"
        )
        assert parsed.component_name == "tBigQueryOutput_1_tBQBE"
        assert parsed.exception_class == "BigQueryException"
        assert "com.google.cloud.bigquery.BigQueryException" in (parsed.causal_message or "")
        assert "gs://<bucket>" in (parsed.causal_message or "")
        _assert_no_identifier(parsed.causal_message or "")


class TestIdempotency:
    """The lane redacts twice by design -- on write and again on read -- and the
    builder re-redacts the parser's already-redacted sentence. A second pass must
    therefore be a byte-for-byte no-op.

    It is not free: ``redact_entities`` stops a URI authority at ``>``, so a naive
    second pass turns ``https://<host>/...`` into ``https://<host>>/...`` and adds
    another ``>`` every time. ``redact_log_text`` collapses that artefact.
    """

    @pytest.mark.parametrize(
        "value",
        [
            "invoking https://volkerinfra.relaticsonline.com/x failed",
            "Not found: Files gs://vaxxinova-talend/raw.csv",
            "invoking https://mas.manage.infranl.almhosting.com",
            "jdbc:oracle:thin:@//db-prod-scan:1521/ORCLPDB1",
            "tRESTClient_1 java.net.SocketException at 10.20.30.40",
        ],
    )
    def test_three_passes_equal_one(self, value):
        once = redact_log_text(value)
        assert redact_log_text(once) == once
        assert redact_log_text(redact_log_text(once)) == once

    def test_endpoint_helper_is_idempotent_too(self):
        for value in _LEAKED_ENDPOINTS:
            once = redact_endpoint(value)
            assert redact_endpoint(once) == once


class TestDegradationOverFailure:
    """Redaction must never be the reason an ingest batch dies."""

    @pytest.mark.parametrize("value", [None, ""])
    def test_empty_values_pass_through(self, value):
        assert redact_log_text(value) == value
        assert redact_endpoint(value) == value
        assert redact_host(value) == value

    @pytest.mark.parametrize(
        "value",
        [
            "://///",
            "\x00\x01 gs://bucket",
            "a" * 20000,
            "…unicode… gs://vaxxinova-talend/x",
            "https://",
            "//",
        ],
    )
    def test_odd_input_never_raises(self, value):
        assert isinstance(redact_log_text(value), str)
        assert isinstance(redact_endpoint(value), str)

    def test_a_non_string_degrades_instead_of_raising(self):
        class Hostile:
            def __str__(self):
                raise RuntimeError("no")

        assert redact_log_text(Hostile()) == "<redacted>"


class TestImportOrder:
    """`redaction` <-> `insights.parser` previously resolved or crashed depending
    on which module the interpreter loaded first. Both orders must work from a
    cold interpreter -- an in-process import proves nothing once either module is
    already in ``sys.modules``.
    """

    @pytest.mark.parametrize(
        "first, second",
        [
            ("src.runtime.insights.parser", "src.runtime.redaction"),
            ("src.runtime.redaction", "src.runtime.insights.parser"),
            ("src.runtime.insights.repository", "src.runtime.insights.parser"),
            ("src.runtime.store.repository", "src.runtime.insights.parser"),
        ],
    )
    def test_no_circular_import(self, first, second):
        code = (
            f"import {first}; import {second}; "
            "from src.runtime.insights.parser import redact_log_text; "
            "assert redact_log_text('gs://b/o') == 'gs://<bucket>/<path>'"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr


class TestPersistedRowsAreClean:
    """Write path: what lands in the database carries no identifier."""

    async def test_enriched_event_endpoint_and_host(self, db_session):
        await _seed_failed_execution(
            db_session,
            execution_id="redaction-write-exec",
            job_name="VJP_Raw_itemApplicationEntries",
            lines=[
                (10, "INFO", "[route_1] Route starting"),
                (
                    11,
                    "ERROR",
                    "tBigQueryOutput_1_tBQBE "
                    "com.google.cloud.bigquery.BigQueryException: Not found: Files "
                    "gs://vaxxinova-talend/jpn_vjp_rawitemApplicationEntriesDelta.csv",
                ),
                (
                    12,
                    "ERROR",
                    "[tOracleConnection_1] Opening connection "
                    "jdbc:oracle:thin:@//db-prod-scan:1521/ORCLPDB1 failed: "
                    "ORA-12170: TNS:Connect timeout",
                ),
            ],
        )
        await build_runtime_insights_for_execution(
            db_session,
            project_id=_PROJECT,
            execution_id="redaction-write-exec",
            source_key="mock",
            ingested_at=_SYNCED_AT,
        )
        rows = (
            await db_session.execute(
                select(RuntimeEnrichedEventRow).where(
                    RuntimeEnrichedEventRow.execution_id == "redaction-write-exec"
                )
            )
        ).scalars().all()
        assert rows
        endpoints = [row.endpoint for row in rows if row.endpoint]
        assert endpoints, "the fixture must produce at least one endpoint to be meaningful"
        for row in rows:
            for value in (row.endpoint, row.host):
                if value:
                    _assert_no_identifier(value)
        # The component name is still on the row that carries it.
        assert any(row.component_name == "tBigQueryOutput_1_tBQBE" for row in rows)

    async def test_incident_text_and_evidence_excerpts(self, db_session):
        await _seed_failed_execution(
            db_session,
            execution_id="redaction-incident-exec",
            job_name="Maximo2RHM_Workorders",
            lines=[
                (10, "INFO", "[route_1] Route starting"),
                (
                    11,
                    "ERROR",
                    "tRESTClient_1 javax.ws.rs.ProcessingException: "
                    "java.net.SocketException: SocketException invoking "
                    "https://mas.manage.infranl.almhosting.com/maximo/api/os/mxapiwo",
                ),
            ],
        )
        await build_runtime_insights_for_execution(
            db_session,
            project_id=_PROJECT,
            execution_id="redaction-incident-exec",
            source_key="mock",
            ingested_at=_SYNCED_AT,
        )
        incident = (
            await db_session.execute(
                select(RuntimeIncidentRow).where(
                    RuntimeIncidentRow.execution_id == "redaction-incident-exec"
                )
            )
        ).scalars().first()
        assert incident is not None
        for value in (incident.causal_message, incident.root_cause_candidate):
            if value:
                _assert_no_identifier(value)
        for check in incident.next_checks or []:
            _assert_no_identifier(check)

        excerpts = (
            await db_session.execute(
                select(RuntimeIncidentEvidenceRow).where(
                    RuntimeIncidentEvidenceRow.incident_id == incident.incident_id
                )
            )
        ).scalars().all()
        assert excerpts
        for row in excerpts:
            _assert_no_identifier(row.excerpt or "")
        # The diagnosis is intact: component and exception class both survive.
        joined = " ".join(row.excerpt or "" for row in excerpts)
        assert "tRESTClient_1" in joined
        assert "java.net.SocketException" in joined

    async def test_an_excerpt_is_redacted_before_it_is_truncated(self, db_session):
        """Truncating first strands a partial FQDN the entity rule cannot see.

        `volkerinfra.relaticsonline.com` cut at 300 chars becomes
        `volkerinfra.relatics` -- two labels, unrecognised suffix, not a host by
        any structural rule -- so the customer name survives. Redacting the whole
        line first is the only ordering that closes it.
        """
        padding = "x" * 280
        await _seed_failed_execution(
            db_session,
            execution_id="redaction-truncation-exec",
            job_name="relatics_push",
            lines=[
                (10, "INFO", "[route_1] Route starting"),
                (
                    11,
                    "ERROR",
                    f"tRESTClient_1 org.apache.cxf.interceptor.Fault: {padding} "
                    "invoking https://volkerinfra.relaticsonline.com/dataexchange.asmx",
                ),
            ],
        )
        await build_runtime_insights_for_execution(
            db_session,
            project_id=_PROJECT,
            execution_id="redaction-truncation-exec",
            source_key="mock",
            ingested_at=_SYNCED_AT,
        )
        excerpts = (
            await db_session.execute(
                select(RuntimeIncidentEvidenceRow).where(
                    RuntimeIncidentEvidenceRow.project_id == _PROJECT
                )
            )
        ).scalars().all()
        for row in excerpts:
            assert "volkerinfra" not in (row.excerpt or "")


class TestReadPathProtectsRowsAlreadyStored:
    """Read path: the 10k rows written before the fix are the reason this exists.

    Rows are inserted UNREDACTED on purpose -- that is exactly the state of the
    stored corpus -- and the repository must still hand back clean text.
    """

    async def _seed_unredacted_incident(self, db_session) -> str:
        await _seed_failed_execution(
            db_session,
            execution_id="legacy-exec",
            job_name="legacy_job",
            lines=[(10, "ERROR", "[tRESTClient_1] failed")],
        )
        db_session.add(
            RuntimeIncidentRow(
                project_id=_PROJECT,
                incident_id="legacy-incident",
                execution_id="legacy-exec",
                signature_hash="a" * 64,
                symptom_summary="Execution 'legacy-exec' of job 'legacy_job' failed.",
                root_cause_candidate=(
                    "Confirm host 'SRVA0673' is resolvable and reachable from the "
                    "runtime host"
                ),
                causal_message=(
                    "tRESTClient_1 java.net.SocketException: SocketException invoking "
                    "https://mas.manage.infranl.almhosting.com/maximo/api/os/mxapiwo"
                ),
                confidence="low",
                cause_origin="deterministic",
                next_checks=["Confirm host 'db-prod-scan' is reachable"],
            )
        )
        db_session.add(
            RuntimeIncidentEvidenceRow(
                project_id=_PROJECT,
                incident_id="legacy-incident",
                log_event_id=(
                    await db_session.execute(
                        select(RuntimeLogEventRow.id).where(
                            RuntimeLogEventRow.execution_id == "legacy-exec"
                        )
                    )
                ).scalars().first(),
                evidence_role="causal",
                excerpt=(
                    "tBigQueryOutput_1_tBQBE Not found: Files "
                    "gs://vaxxinova-talend/jpn_vjp_raw.csv"
                ),
            )
        )
        await db_session.flush()
        return "legacy-incident"

    async def test_incident_read_is_redacted(self, db_session):
        incident_id = await self._seed_unredacted_incident(db_session)
        insight = await RuntimeInsightRepository(db_session).get_incident(None, incident_id)
        _assert_no_identifier(insight.causal_message or "")
        # Still useful: the component the log named is still named.
        assert "tRESTClient_1" in (insight.causal_message or "")
        assert "java.net.SocketException" in (insight.causal_message or "")

    async def test_read_redaction_cannot_recover_a_bare_host_baked_into_prose(
        self, db_session
    ):
        """The honest limit of the read path, pinned so nobody assumes otherwise.

        A legacy `root_cause_candidate` is a catalog template whose `{host}` slot
        was already filled at write time, so `SRVA0673` is now indistinguishable
        from any other word in the sentence -- no structural rule can recover it
        (it is single-label, so not even the FQDN rule applies). The read path
        catches address SHAPES; single-label hosts already interpolated into
        prose are cleaned only by rebuilding the row through the fixed write
        path (`python -m src.runtime.insights.backfill`).

        Measured on the live mirror when this was written: 3 incident rows of 827.
        """
        incident_id = await self._seed_unredacted_incident(db_session)
        insight = await RuntimeInsightRepository(db_session).get_incident(None, incident_id)
        # FQDN and URI shapes ARE recovered on read.
        assert "almhosting" not in (insight.causal_message or "")
        # A single-label host in prose is not, and that is a documented gap.
        assert "SRVA0673" in (insight.root_cause_candidate or "")

    async def test_incident_evidence_read_is_redacted(self, db_session):
        incident_id = await self._seed_unredacted_incident(db_session)
        evidence = await RuntimeInsightRepository(db_session).get_incident_evidence(
            None, incident_id
        )
        assert evidence
        for item in evidence:
            _assert_no_identifier(item.excerpt or "")
        assert "tBigQueryOutput_1_tBQBE" in (evidence[0].excerpt or "")

    async def test_read_redaction_leaves_already_clean_text_byte_identical(
        self, db_session
    ):
        """Layering read redaction on top of the fixed write path is a no-op."""
        await _seed_failed_execution(
            db_session,
            execution_id="clean-exec",
            job_name="clean_job",
            lines=[
                (
                    10,
                    "ERROR",
                    "tRESTClient_1 javax.ws.rs.ProcessingException: "
                    "java.net.SocketException invoking "
                    "https://mas.manage.infranl.almhosting.com/x",
                )
            ],
        )
        await build_runtime_insights_for_execution(
            db_session,
            project_id=_PROJECT,
            execution_id="clean-exec",
            source_key="mock",
            ingested_at=_SYNCED_AT,
        )
        row = (
            await db_session.execute(
                select(RuntimeIncidentRow).where(
                    RuntimeIncidentRow.execution_id == "clean-exec"
                )
            )
        ).scalars().first()
        assert row is not None
        insight = await RuntimeInsightRepository(db_session).get_incident(
            None, row.incident_id
        )
        assert insight.causal_message == row.causal_message
        assert insight.root_cause_candidate == row.root_cause_candidate
