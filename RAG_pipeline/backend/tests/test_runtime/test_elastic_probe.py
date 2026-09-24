"""Contract for the bounded, read-only Elasticsearch probe primitives.

No test reaches a live cluster: `_FakeCluster` is an `httpx.MockTransport`
handler that answers `_search` and `_field_caps` the way a real cluster does,
including the two behaviours the whole module exists to survive --
`_field_caps` returning 403 to a near-permissionless connector key, and an
aggregation over an UNMAPPED field returning zero buckets rather than an error.

Two invariants are pinned here rather than merely described:

* **No document content crosses the boundary.** Every issued query body is
  inspected for `size: 0` / `_source: false`; a cluster that returns hits anyway
  is refused rather than filtered; and the response models are walked for any
  untyped passthrough field through which a raw payload could travel.
* **A zero is never ambiguous.** A count of zero over an unmapped field returns
  `resolution="field_unmapped"` with `matching_documents=None`, never `0`.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any, Literal, Union, get_args, get_origin

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import BaseModel

from src.api.main import app
from src.config import settings
from src.runtime import elastic_probe
from src.runtime.elastic_probe import (
    CountMatchingRequest,
    CountMatchingResponse,
    DescribeFieldsRequest,
    DescribeFieldsResponse,
    ElasticProbeContractError,
    ElasticProbeService,
    ElasticProbeSourceError,
    FieldEvidence,
    ListIndicesRequest,
    ListIndicesResponse,
    ProbeResolution,
    SizeDistributionRequest,
    SizeDistributionResponse,
)
from src.runtime.store import RuntimeStoreRepository

_PROJECT = "werkorder"
_ENDPOINT = "https://logs.example.internal:9200"
_PATTERN = "talend-esb-monitoring-*"

#: Stand-in for the confidential payload this estate actually carries. It only
#: ever appears where a real cluster would put document content, so any test
#: that finds it in a response or an error message has found a leak.
_POISON = "PATIENT-RECORD-BSN-000000000"


# --- fake cluster ------------------------------------------------------------


class _FakeCluster:
    """A minimal, honest Elasticsearch over `httpx.MockTransport`.

    It answers from the SHAPE of the query the probe emitted -- a terms agg on
    `_index`, a `matching` filter agg, a `measured` filter agg, or a bare
    `exists` probe -- so a change to how the probe asks its question shows up
    here as a wrong answer rather than as a silently accepted stub.
    """

    def __init__(
        self,
        *,
        total: int = 0,
        matching: int = 0,
        index_buckets: tuple[tuple[str, int], ...] = (),
        sum_other: int = 0,
        exists_counts: dict[str, int] | None = None,
        measured: dict[str, Any] | None = None,
        field_caps: dict[str, Any] | None = None,
        field_caps_status: int = 200,
        search_status: int = 200,
        search_error_type: str | None = None,
        leak_documents: bool = False,
        timed_out: bool = False,
    ) -> None:
        self.total = total
        self.matching = matching
        self.index_buckets = index_buckets
        self.sum_other = sum_other
        self.exists_counts = exists_counts or {}
        self.measured = measured
        self.field_caps = field_caps or {}
        self.field_caps_status = field_caps_status
        self.search_status = search_status
        self.search_error_type = search_error_type
        self.leak_documents = leak_documents
        self.timed_out = timed_out
        #: Every request the probe made: (method, path, params, parsed body).
        self.calls: list[tuple[str, str, dict[str, str], dict[str, Any] | None]] = []

    # -- transport ---------------------------------------------------------

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append(
            (request.method, request.url.path, dict(request.url.params), body)
        )
        if request.url.path.endswith("/_field_caps"):
            if self.field_caps_status != 200:
                return httpx.Response(
                    self.field_caps_status,
                    json={"error": {"type": "security_exception", "reason": _POISON}},
                )
            requested = request.url.params.get("fields", "*")
            if requested == "*":
                fields = dict(self.field_caps)
            else:
                wanted = set(requested.split(","))
                fields = {k: v for k, v in self.field_caps.items() if k in wanted}
            return httpx.Response(200, json={"indices": ["idx-1"], "fields": fields})
        if request.url.path.endswith("/_search"):
            if self.search_status != 200:
                return httpx.Response(
                    self.search_status,
                    json={
                        "error": {
                            "type": self.search_error_type or "illegal_argument_exception",
                            "reason": _POISON,
                        }
                    },
                )
            return httpx.Response(200, json=self._search_payload(body or {}))
        return httpx.Response(404, json={"error": {"type": "no_handler"}})

    # -- search ------------------------------------------------------------

    def _search_payload(self, body: dict[str, Any]) -> dict[str, Any]:
        aggs = body.get("aggs") or {}
        aggregations: dict[str, Any] = {}
        hits_total = self.total

        if "indices" in aggs:
            aggregations["indices"] = {
                "buckets": [
                    {"key": name, "doc_count": count} for name, count in self.index_buckets
                ],
                "sum_other_doc_count": self.sum_other,
            }
        if "matching" in aggs:
            aggregations["matching"] = {"doc_count": self.matching}
        if "measured" in aggs:
            aggregations["measured"] = self.measured or {"doc_count": 0}
        if not aggs:
            filters = (body.get("query") or {}).get("bool", {}).get("filter") or []
            if filters:
                # The `_field_caps`-refused fallback: one `exists` count per
                # candidate name.
                field = (filters[0].get("exists") or {}).get("field")
                hits_total = self.exists_counts.get(str(field), 0)

        payload: dict[str, Any] = {
            "took": 3,
            "timed_out": self.timed_out,
            "_shards": {"total": 1, "successful": 1, "skipped": 0, "failed": 0},
            "hits": {"total": {"value": hits_total, "relation": "eq"}, "hits": []},
        }
        if aggregations:
            payload["aggregations"] = aggregations
        if self.leak_documents:
            payload["hits"]["hits"] = [
                {"_index": "idx-1", "_id": "1", "_source": {"Body": _POISON}}
            ]
        return payload

    # -- assertions helpers -------------------------------------------------

    @property
    def searches(self) -> list[dict[str, Any]]:
        return [body for _m, path, _p, body in self.calls if path.endswith("/_search") and body]

    @property
    def paths(self) -> list[str]:
        return [path for _m, path, _p, _b in self.calls]


_SIZE_MEASURED: dict[str, Any] = {
    "doc_count": 40,
    "percentiles": {
        "values": {
            "50.0": 512.0,
            "75.0": 2048.0,
            "90.0": 20480.0,
            "95.0": 40960.0,
            "99.0": 81920.0,
        }
    },
    "stats": {"count": 40, "min": 118.0, "max": 98304.0, "avg": 15000.0, "sum": 600000.0},
    "ranges": {
        "buckets": [
            {"key": "0", "to": 1.0, "doc_count": 0},
            {"key": "1-511", "from": 1.0, "to": 512.0, "doc_count": 18},
            {"key": "512-2047", "from": 512.0, "to": 2048.0, "doc_count": 4},
            {"key": "2048-16383", "from": 2048.0, "to": 16384.0, "doc_count": 6},
            {"key": "16384-131071", "from": 16384.0, "to": 131072.0, "doc_count": 12},
            {"key": "131072-1048575", "from": 131072.0, "to": 1048576.0, "doc_count": 0},
            {"key": "1048576+", "from": 1048576.0, "doc_count": 0},
        ]
    },
}


def _grouped_measured() -> dict[str, Any]:
    measured = dict(_SIZE_MEASURED)
    measured["groups"] = {
        "sum_other_doc_count": 0,
        "buckets": [
            {
                "key": "MessageIn",
                "doc_count": 22,
                "percentiles": {"values": {"50.0": 24576.0}},
                "stats": {"count": 22, "min": 4096.0, "max": 98304.0, "avg": 30000.0, "sum": 660000.0},
                "ranges": {"buckets": [{"key": "16384-131071", "from": 16384.0, "to": 131072.0, "doc_count": 22}]},
            },
            {
                "key": "Envelope",
                "doc_count": 18,
                "percentiles": {"values": {"50.0": 220.0}},
                "stats": {"count": 18, "min": 118.0, "max": 480.0, "avg": 240.0, "sum": 4320.0},
                "ranges": {"buckets": [{"key": "1-511", "from": 1.0, "to": 512.0, "doc_count": 18}]},
            },
        ],
    }
    return measured


_FIELD_CAPS = {
    "Body": {"text": {"type": "text", "searchable": True, "aggregatable": False}},
    "Body.keyword": {"keyword": {"type": "keyword", "searchable": True, "aggregatable": True}},
    "MessageSize": {"long": {"type": "long", "searchable": True, "aggregatable": True}},
    "MessageType": {"keyword": {"type": "keyword", "searchable": True, "aggregatable": True}},
}


# --- fixtures ----------------------------------------------------------------


@pytest.fixture(autouse=True)
def credential_key(monkeypatch):
    monkeypatch.setattr(
        settings, "source_credentials_encryption_key", Fernet.generate_key().decode()
    )
    monkeypatch.setattr(settings, "source_credentials_encryption_key_previous", "")
    monkeypatch.setattr(settings, "source_credentials_encryption_key_id", "primary")
    monkeypatch.setattr(settings, "source_credentials_encryption_key_previous_id", "previous")


@pytest.fixture(autouse=True)
def keyless_dev(monkeypatch):
    """Default posture for these tests: local dev, no machine key configured."""
    monkeypatch.setattr(settings, "mcp_ingest_api_key", "", raising=False)
    monkeypatch.setattr(settings, "app_env", "development", raising=False)


async def _register_elastic_source(
    db_session, *, project_id: str = _PROJECT, credentials: dict[str, Any] | None = None
) -> int:
    repo = RuntimeStoreRepository(db_session)
    source = await repo.create_source_config(
        project_id=project_id,
        source_type="elastic",
        label="ESB monitoring",
    )
    await repo.write_credentials(
        source_id=source.id,
        plaintext_payload=credentials
        or {"endpoint_url": _ENDPOINT, "api_key": "probe-key-under-test"},
        credential_type="elastic",
        rotated_by="1",
    )
    return source.id


@pytest.fixture
async def source_id(db_session) -> int:
    return await _register_elastic_source(db_session)


def _service(db_session, cluster: _FakeCluster) -> ElasticProbeService:
    return ElasticProbeService(db_session, transport=httpx.MockTransport(cluster))


# --- primitive 1: describe-fields -------------------------------------------


class TestDescribeFields:
    async def test_field_caps_reports_types_and_capabilities(self, db_session, source_id):
        cluster = _FakeCluster(total=1200, field_caps=_FIELD_CAPS)
        result = await _service(db_session, cluster).describe_fields(
            DescribeFieldsRequest(project_id=_PROJECT, index_pattern=_PATTERN)
        )

        assert isinstance(result, DescribeFieldsResponse)
        assert result.resolution is ProbeResolution.OK
        assert result.method == "field_caps"
        assert result.field_caps_refused is False
        assert result.document_count == 1200
        by_name = {field.name: field for field in result.fields}
        assert by_name["Body"].types == ["text"]
        assert by_name["Body"].searchable is True
        assert by_name["Body"].aggregatable is False
        assert by_name["MessageSize"].aggregatable is True
        assert all(field.mapped is True for field in result.fields)
        # `_mapping` is forbidden on the estate this exists for and must never
        # be the discovery route.
        assert not any("_mapping" in path for path in cluster.paths)

    async def test_a_named_candidate_that_is_absent_is_reported_unmapped(
        self, db_session, source_id
    ):
        cluster = _FakeCluster(total=1200, field_caps=_FIELD_CAPS)
        result = await _service(db_session, cluster).describe_fields(
            DescribeFieldsRequest(
                project_id=_PROJECT,
                index_pattern=_PATTERN,
                candidate_fields=["MessageSize", "Log_PayloadBody"],
            )
        )

        by_name = {field.name: field for field in result.fields}
        assert by_name["MessageSize"].mapped is True
        assert by_name["Log_PayloadBody"].mapped is False
        # The probe asked `_field_caps` for exactly the named candidates.
        caps_call = next(call for call in cluster.calls if call[1].endswith("/_field_caps"))
        assert caps_call[2]["fields"] == "MessageSize,Log_PayloadBody"

    async def test_field_caps_403_falls_back_to_candidate_probing(self, db_session, source_id):
        """The door-closed path: `_field_caps` is 403, `_search` is not."""
        cluster = _FakeCluster(
            total=1200,
            field_caps_status=403,
            exists_counts={"MessageSize": 900, "Log_PayloadBody": 0},
        )
        result = await _service(db_session, cluster).describe_fields(
            DescribeFieldsRequest(
                project_id=_PROJECT,
                index_pattern=_PATTERN,
                candidate_fields=["MessageSize", "Log_PayloadBody"],
            )
        )

        assert result.method == "candidate_probe"
        assert result.field_caps_refused is True
        by_name = {field.name: field for field in result.fields}
        # A match PROVES the name resolves.
        assert by_name["MessageSize"].mapped is True
        assert by_name["MessageSize"].evidence is FieldEvidence.EXISTS_PROBE
        assert by_name["MessageSize"].matching_documents == 900
        # A non-match proves NOTHING: unmapped and mapped-but-empty are
        # indistinguishable without the mapping, so it is not reported as False.
        assert by_name["Log_PayloadBody"].mapped is None
        assert by_name["Log_PayloadBody"].evidence is FieldEvidence.EXISTS_PROBE_INCONCLUSIVE
        assert any("_field_caps was refused" in note for note in result.notes)

    async def test_field_caps_403_without_candidates_says_so(self, db_session, source_id):
        cluster = _FakeCluster(total=1200, field_caps_status=403)
        result = await _service(db_session, cluster).describe_fields(
            DescribeFieldsRequest(project_id=_PROJECT, index_pattern=_PATTERN)
        )

        assert result.resolution is ProbeResolution.FIELD_MAPPING_UNKNOWN
        assert result.fields == []
        assert any("Supply candidate_fields" in note for note in result.notes)

    async def test_an_empty_pattern_is_named_not_implied(self, db_session, source_id):
        cluster = _FakeCluster(total=0, field_caps={})
        result = await _service(db_session, cluster).describe_fields(
            DescribeFieldsRequest(project_id=_PROJECT, index_pattern=_PATTERN)
        )

        assert result.resolution is ProbeResolution.PATTERN_MATCHED_NO_INDICES

    async def test_a_refusal_never_echoes_the_clusters_reason(self, db_session, source_id):
        """The 403 body carries a `reason`; only the TYPE token may be read."""
        cluster = _FakeCluster(total=10, field_caps_status=403, exists_counts={"a_field": 0})
        result = await _service(db_session, cluster).describe_fields(
            DescribeFieldsRequest(
                project_id=_PROJECT, index_pattern=_PATTERN, candidate_fields=["a_field"]
            )
        )
        assert _POISON not in result.model_dump_json()


# --- primitive 2: list-indices ----------------------------------------------


class TestListIndices:
    async def test_concrete_indices_come_from_an_index_aggregation(self, db_session, source_id):
        cluster = _FakeCluster(
            total=3392,
            index_buckets=(("talend-esb-monitoring-2026.09", 2100), ("talend-esb-monitoring-2026.08", 1292)),
        )
        result = await _service(db_session, cluster).list_indices(
            ListIndicesRequest(project_id=_PROJECT, index_pattern=_PATTERN)
        )

        assert isinstance(result, ListIndicesResponse)
        assert result.resolution is ProbeResolution.OK
        assert [bucket.index for bucket in result.indices] == [
            "talend-esb-monitoring-2026.09",
            "talend-esb-monitoring-2026.08",
        ]
        assert result.indices[0].document_count == 2100
        assert result.document_count == 3392

        # The load-bearing part: the answer came from a terms agg on the
        # `_index` metadata field, not from any index-metadata API.
        body = cluster.searches[-1]
        assert body["aggs"]["indices"]["terms"]["field"] == "_index"
        for forbidden in ("_alias", "_cat", "_resolve", "_mapping"):
            assert not any(forbidden in path for path in cluster.paths)

    async def test_truncation_is_reported_not_silent(self, db_session, source_id):
        cluster = _FakeCluster(total=5000, index_buckets=(("idx-a", 10),), sum_other=4990)
        result = await _service(db_session, cluster).list_indices(
            ListIndicesRequest(project_id=_PROJECT, index_pattern=_PATTERN, max_indices=1)
        )

        assert result.truncated is True
        assert result.documents_outside_listed_indices == 4990
        assert any("beyond the 1 listed" in note for note in result.notes)

    async def test_no_buckets_is_an_explicit_finding(self, db_session, source_id):
        cluster = _FakeCluster(total=0, index_buckets=())
        result = await _service(db_session, cluster).list_indices(
            ListIndicesRequest(project_id=_PROJECT, index_pattern=_PATTERN)
        )

        assert result.resolution is ProbeResolution.PATTERN_MATCHED_NO_INDICES
        assert result.indices == []
        assert any("empty population" in note for note in result.notes)


# --- primitive 3: count-matching --------------------------------------------


class TestCountMatching:
    async def test_match_phrase_count_carries_its_denominator(self, db_session, source_id):
        cluster = _FakeCluster(total=1000, matching=72, field_caps=_FIELD_CAPS)
        result = await _service(db_session, cluster).count_matching(
            CountMatchingRequest(
                project_id=_PROJECT, index_pattern=_PATTERN, field="Body", phrase="OrderCreated"
            )
        )

        assert isinstance(result, CountMatchingResponse)
        assert result.resolution is ProbeResolution.OK
        assert result.predicate == "match_phrase"
        assert result.matching_documents == 72
        assert result.document_count == 1000
        assert result.matching_fraction == pytest.approx(0.072)
        body = cluster.searches[0]
        assert body["aggs"]["matching"]["filter"] == {"match_phrase": {"Body": "OrderCreated"}}

    async def test_exists_only_counts_documents_carrying_the_field(self, db_session, source_id):
        cluster = _FakeCluster(total=1000, matching=72, field_caps=_FIELD_CAPS)
        result = await _service(db_session, cluster).count_matching(
            CountMatchingRequest(
                project_id=_PROJECT, index_pattern=_PATTERN, field="Body", exists_only=True
            )
        )

        assert result.predicate == "exists"
        assert result.matching_documents == 72
        # This is the statement the primitive exists to support: the field is
        # mapped and empty in 92.8% of messages.
        assert round(1 - (result.matching_fraction or 0), 3) == 0.928
        body = cluster.searches[0]
        assert body["aggs"]["matching"]["filter"] == {"exists": {"field": "Body"}}

    async def test_a_real_zero_over_a_mapped_field_survives_as_zero(self, db_session, source_id):
        """The mapped-and-empty case is a FACT and must not be blurred."""
        cluster = _FakeCluster(total=1000, matching=0, field_caps=_FIELD_CAPS)
        result = await _service(db_session, cluster).count_matching(
            CountMatchingRequest(
                project_id=_PROJECT, index_pattern=_PATTERN, field="Body", exists_only=True
            )
        )

        assert result.resolution is ProbeResolution.OK
        assert result.matching_documents == 0
        assert result.field_mapped is True
        assert result.matching_fraction == 0.0

    async def test_zero_over_an_unmapped_field_is_field_unmapped_not_zero(
        self, db_session, source_id
    ):
        """INVARIANT B, pinned.

        An `exists` filter over an unmapped field returns zero hits rather than
        an error, so a bare `0` would conflate "this field does not exist" with
        "this field exists and is empty". The probe must resolve the mapping
        first and report the distinction structurally.
        """
        cluster = _FakeCluster(total=1000, matching=0, field_caps=_FIELD_CAPS)
        result = await _service(db_session, cluster).count_matching(
            CountMatchingRequest(
                project_id=_PROJECT,
                index_pattern=_PATTERN,
                field="Log_PayloadBody",
                exists_only=True,
            )
        )

        assert result.resolution is ProbeResolution.FIELD_UNMAPPED
        # Not 0. A consumer that ignores `resolution` must not be handed a
        # number that reads as a finding.
        assert result.matching_documents is None
        assert result.matching_fraction is None
        assert result.field_mapped is False
        assert any("is not mapped" in note for note in result.notes)

    async def test_zero_with_field_caps_refused_is_explicitly_unknown(self, db_session, source_id):
        cluster = _FakeCluster(
            total=1000, matching=0, field_caps_status=403, exists_counts={"Log_PayloadBody": 0}
        )
        result = await _service(db_session, cluster).count_matching(
            CountMatchingRequest(
                project_id=_PROJECT,
                index_pattern=_PATTERN,
                field="Log_PayloadBody",
                exists_only=True,
            )
        )

        assert result.resolution is ProbeResolution.FIELD_MAPPING_UNKNOWN
        assert result.matching_documents is None
        assert result.field_mapped is None
        assert result.field_evidence is FieldEvidence.EXISTS_PROBE_INCONCLUSIVE

    async def test_an_empty_pattern_outranks_the_field_question(self, db_session, source_id):
        cluster = _FakeCluster(total=0, matching=0, field_caps={})
        result = await _service(db_session, cluster).count_matching(
            CountMatchingRequest(
                project_id=_PROJECT, index_pattern=_PATTERN, field="Body", exists_only=True
            )
        )

        assert result.resolution is ProbeResolution.PATTERN_MATCHED_NO_INDICES
        assert result.matching_documents is None

    async def test_a_timed_out_search_marks_the_count_partial(self, db_session, source_id):
        cluster = _FakeCluster(total=1000, matching=72, timed_out=True, field_caps=_FIELD_CAPS)
        result = await _service(db_session, cluster).count_matching(
            CountMatchingRequest(
                project_id=_PROJECT, index_pattern=_PATTERN, field="Body", phrase="OrderCreated"
            )
        )
        assert result.partial is True


# --- primitive 4: size-distribution -----------------------------------------


class TestSizeDistribution:
    async def test_percentiles_and_buckets_without_retrieving_a_payload(
        self, db_session, source_id
    ):
        cluster = _FakeCluster(total=120, measured=_SIZE_MEASURED, field_caps=_FIELD_CAPS)
        result = await _service(db_session, cluster).size_distribution(
            SizeDistributionRequest(
                project_id=_PROJECT, index_pattern=_PATTERN, size_field="MessageSize"
            )
        )

        assert isinstance(result, SizeDistributionResponse)
        assert result.resolution is ProbeResolution.OK
        assert result.document_count == 120
        assert result.measured_documents == 40
        assert [item.percentile for item in result.overall.percentiles] == [50.0, 75.0, 90.0, 95.0, 99.0]
        assert result.overall.percentiles[0].value == 512.0
        assert result.overall.max_value == 98304.0
        # The finding the primitive exists for: envelopes AND real payloads.
        by_key = {bucket.key: bucket.document_count for bucket in result.overall.buckets}
        assert by_key["1-511"] == 18
        assert by_key["16384-131071"] == 12

    async def test_grouping_splits_the_distribution(self, db_session, source_id):
        cluster = _FakeCluster(total=120, measured=_grouped_measured(), field_caps=_FIELD_CAPS)
        result = await _service(db_session, cluster).size_distribution(
            SizeDistributionRequest(
                project_id=_PROJECT,
                index_pattern=_PATTERN,
                size_field="MessageSize",
                group_by="MessageType",
            )
        )

        assert result.resolution is ProbeResolution.OK
        assert [group.key for group in result.groups] == ["MessageIn", "Envelope"]
        assert result.groups[0].percentiles[0].value == 24576.0
        assert result.groups[1].percentiles[0].value == 220.0
        assert result.group_by_mapped is True
        body = cluster.searches[0]
        terms = body["aggs"]["measured"]["aggs"]["groups"]["terms"]
        assert terms == {"field": "MessageType", "size": 25}

    async def test_an_unmapped_size_field_is_not_a_distribution_of_zero(
        self, db_session, source_id
    ):
        """INVARIANT B for aggregations.

        A percentiles agg over an unmapped field returns null values and an
        `exists` filter returns zero hits -- no error anywhere. Reporting that
        as an empty distribution would assert the estate carries no payloads.
        """
        cluster = _FakeCluster(
            total=120, measured={"doc_count": 0}, field_caps=_FIELD_CAPS
        )
        result = await _service(db_session, cluster).size_distribution(
            SizeDistributionRequest(
                project_id=_PROJECT, index_pattern=_PATTERN, size_field="Log_PayloadSize"
            )
        )

        assert result.resolution is ProbeResolution.FIELD_UNMAPPED
        assert result.size_field_mapped is False
        assert result.measured_documents == 0
        assert any("Log_PayloadSize" in note for note in result.notes)

    async def test_an_unmapped_group_by_is_named(self, db_session, source_id):
        """A terms agg on an unmapped field returns zero buckets, not an error."""
        measured = dict(_SIZE_MEASURED)
        measured["groups"] = {"buckets": [], "sum_other_doc_count": 0}
        cluster = _FakeCluster(total=120, measured=measured, field_caps=_FIELD_CAPS)
        result = await _service(db_session, cluster).size_distribution(
            SizeDistributionRequest(
                project_id=_PROJECT,
                index_pattern=_PATTERN,
                size_field="MessageSize",
                group_by="Log_Channel",
            )
        )

        assert result.resolution is ProbeResolution.FIELD_UNMAPPED
        assert result.size_field_mapped is True
        assert result.group_by_mapped is False
        assert any("Log_Channel" in note for note in result.notes)

    async def test_a_non_numeric_field_is_a_distinct_finding(self, db_session, source_id):
        cluster = _FakeCluster(total=120, search_status=400, search_error_type="illegal_argument_exception")
        result = await _service(db_session, cluster).size_distribution(
            SizeDistributionRequest(
                project_id=_PROJECT, index_pattern=_PATTERN, size_field="Body"
            )
        )

        assert result.resolution is ProbeResolution.FIELD_NOT_AGGREGATABLE
        assert any("illegal_argument_exception" in note for note in result.notes)
        # The cluster's `reason` carried document text; only the type token may
        # be surfaced.
        assert _POISON not in result.model_dump_json()

    async def test_custom_percents_are_bounded_and_honoured(self, db_session, source_id):
        cluster = _FakeCluster(total=120, measured=_SIZE_MEASURED, field_caps=_FIELD_CAPS)
        result = await _service(db_session, cluster).size_distribution(
            SizeDistributionRequest(
                project_id=_PROJECT,
                index_pattern=_PATTERN,
                size_field="MessageSize",
                percents=[50.0, 99.0],
            )
        )
        assert [item.percentile for item in result.overall.percentiles] == [50.0, 99.0]
        body = cluster.searches[0]
        assert body["aggs"]["measured"]["aggs"]["percentiles"]["percentiles"]["percents"] == [50.0, 99.0]


# --- invariant A: no document content ---------------------------------------


class TestNoDocumentContent:
    async def _run_every_primitive(self, db_session, cluster: _FakeCluster) -> None:
        service = _service(db_session, cluster)
        await service.describe_fields(
            DescribeFieldsRequest(project_id=_PROJECT, index_pattern=_PATTERN)
        )
        await service.list_indices(
            ListIndicesRequest(project_id=_PROJECT, index_pattern=_PATTERN)
        )
        await service.count_matching(
            CountMatchingRequest(
                project_id=_PROJECT, index_pattern=_PATTERN, field="Body", phrase="OrderCreated"
            )
        )
        await service.size_distribution(
            SizeDistributionRequest(
                project_id=_PROJECT, index_pattern=_PATTERN, size_field="MessageSize"
            )
        )

    async def test_every_issued_query_suppresses_documents(self, db_session, source_id):
        cluster = _FakeCluster(
            total=1000,
            matching=72,
            index_buckets=(("idx-1", 1000),),
            measured=_SIZE_MEASURED,
            field_caps=_FIELD_CAPS,
        )
        await self._run_every_primitive(db_session, cluster)

        assert cluster.searches, "no search was issued; the assertion below would be vacuous"
        for body in cluster.searches:
            assert body["size"] == 0, body
            assert body["_source"] is False, body
            # A cluster-side ceiling so a probe cannot become an expensive query.
            assert body["timeout"] == elastic_probe.ES_QUERY_TIMEOUT, body

    async def test_a_cluster_that_returns_documents_anyway_is_refused(
        self, db_session, source_id
    ):
        """Refuse, do not filter.

        If `size: 0` did not suppress documents the reason is unknown -- a
        rewriting proxy, a search template, a future ES behaviour -- and the
        only safe response to an unknown reason is to not pass the payload on.
        """
        cluster = _FakeCluster(
            total=1000, index_buckets=(("idx-1", 1000),), leak_documents=True
        )
        with pytest.raises(ElasticProbeContractError) as excinfo:
            await _service(db_session, cluster).list_indices(
                ListIndicesRequest(project_id=_PROJECT, index_pattern=_PATTERN)
            )
        assert _POISON not in str(excinfo.value)

    @pytest.mark.parametrize(
        "model",
        [
            DescribeFieldsResponse,
            ListIndicesResponse,
            CountMatchingResponse,
            SizeDistributionResponse,
        ],
    )
    def test_no_response_model_has_an_untyped_passthrough(self, model: type[BaseModel]):
        """Invariant A is enforced by the response SHAPE as well as the query.

        A `dict[str, Any]` or `Any` field anywhere in these models would be a
        channel through which a raw Elasticsearch payload could travel, and no
        query-side guard would catch it. This walks every nested model and
        fails on the first one.
        """
        _assert_only_declared_leaves(model, set())


_ALLOWED_LEAVES = {str, int, float, bool, type(None)}


def _assert_only_declared_leaves(model: type[BaseModel], seen: set[type]) -> None:
    seen.add(model)
    for name, field in model.model_fields.items():
        _assert_annotation(field.annotation, f"{model.__name__}.{name}", seen)


def _assert_annotation(annotation: Any, where: str, seen: set[type]) -> None:
    origin = get_origin(annotation)
    if origin is None:
        if annotation in _ALLOWED_LEAVES:
            return
        if isinstance(annotation, type) and issubclass(annotation, Enum):
            return
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            if annotation not in seen:
                _assert_only_declared_leaves(annotation, seen)
            return
        raise AssertionError(
            f"{where} is annotated `{annotation!r}`, which is not a declared leaf. "
            "An untyped field is a channel for raw document content."
        )
    if origin is Literal:
        return
    if origin in (list, tuple, set, frozenset) or str(origin) in {
        "typing.Union",
        "<class 'types.UnionType'>",
    } or origin is Union:
        for arg in get_args(annotation):
            _assert_annotation(arg, where, seen)
        return
    raise AssertionError(
        f"{where} is annotated `{annotation!r}` (origin {origin!r}). "
        "Mapping and Any fields are forbidden: they would let raw Elasticsearch "
        "payload reach an LLM context."
    )


# --- bounds and request validation ------------------------------------------


class TestBounds:
    @pytest.mark.parametrize("pattern", ["*", "_all", "-*", "a*", "../etc", "logs,other", "<x-{now/d}>"])
    def test_broad_or_unsafe_index_expressions_are_refused(self, pattern):
        with pytest.raises(ValueError):
            ListIndicesRequest(project_id=_PROJECT, index_pattern=pattern)

    def test_candidate_fields_are_capped(self):
        names = [f"field_{index}" for index in range(elastic_probe.MAX_CANDIDATE_FIELDS + 1)]
        with pytest.raises(ValueError):
            DescribeFieldsRequest(
                project_id=_PROJECT, index_pattern=_PATTERN, candidate_fields=names
            )

    def test_index_buckets_are_capped(self):
        with pytest.raises(ValueError):
            ListIndicesRequest(
                project_id=_PROJECT,
                index_pattern=_PATTERN,
                max_indices=elastic_probe.MAX_INDEX_BUCKETS + 1,
            )

    def test_a_phrase_is_length_bounded(self):
        with pytest.raises(ValueError):
            CountMatchingRequest(
                project_id=_PROJECT,
                index_pattern=_PATTERN,
                field="Body",
                phrase="x" * (elastic_probe.MAX_PHRASE_CHARS + 1),
            )

    def test_a_count_needs_exactly_one_predicate(self):
        with pytest.raises(ValueError):
            CountMatchingRequest(project_id=_PROJECT, index_pattern=_PATTERN, field="Body")
        with pytest.raises(ValueError):
            CountMatchingRequest(
                project_id=_PROJECT,
                index_pattern=_PATTERN,
                field="Body",
                phrase="x",
                exists_only=True,
            )

    def test_an_aggregated_field_may_not_be_a_wildcard(self):
        with pytest.raises(ValueError):
            CountMatchingRequest(
                project_id=_PROJECT, index_pattern=_PATTERN, field="Log_*", exists_only=True
            )

    def test_a_field_name_charset_is_enforced(self):
        with pytest.raises(ValueError):
            CountMatchingRequest(
                project_id=_PROJECT,
                index_pattern=_PATTERN,
                field='Body"]},"script":{',
                exists_only=True,
            )

    def test_percentiles_are_bounded(self):
        with pytest.raises(ValueError):
            SizeDistributionRequest(
                project_id=_PROJECT,
                index_pattern=_PATTERN,
                size_field="MessageSize",
                percents=[101.0],
            )

    def test_extra_request_fields_are_refused(self):
        with pytest.raises(ValueError):
            ListIndicesRequest(
                project_id=_PROJECT, index_pattern=_PATTERN, environment_ids=["prod"]
            )


# --- credential resolution ---------------------------------------------------


class TestSourceResolution:
    async def test_a_project_without_an_elastic_source_is_refused(self, db_session):
        cluster = _FakeCluster()
        with pytest.raises(ElasticProbeSourceError, match="no registered Elasticsearch source"):
            await _service(db_session, cluster).list_indices(
                ListIndicesRequest(project_id="no-such-project", index_pattern=_PATTERN)
            )

    async def test_two_elastic_sources_must_be_disambiguated(self, db_session, source_id):
        await _register_elastic_source(
            db_session,
            credentials={"endpoint_url": "https://other.example.internal:9200"},
        )
        cluster = _FakeCluster()
        with pytest.raises(ElasticProbeSourceError, match="source_id"):
            await _service(db_session, cluster).list_indices(
                ListIndicesRequest(project_id=_PROJECT, index_pattern=_PATTERN)
            )

    async def test_a_named_source_from_another_project_is_not_reachable(
        self, db_session, source_id
    ):
        cluster = _FakeCluster(total=1, index_buckets=(("idx-1", 1),))
        with pytest.raises(ElasticProbeSourceError, match="not registered to project"):
            await _service(db_session, cluster).list_indices(
                ListIndicesRequest(
                    project_id="another-project", index_pattern=_PATTERN, source_id=source_id
                )
            )

    async def test_a_named_non_elastic_source_is_refused(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        source = await repo.create_source_config(
            project_id=_PROJECT, source_type="mock", label="Mock"
        )
        cluster = _FakeCluster()
        with pytest.raises(ElasticProbeSourceError, match="not an Elasticsearch source"):
            await _service(db_session, cluster).list_indices(
                ListIndicesRequest(
                    project_id=_PROJECT, index_pattern=_PATTERN, source_id=source.id
                )
            )

    async def test_the_credential_is_sent_as_the_authorization_header(self, db_session, source_id):
        """Reads the stored credential rather than asking the caller for one."""
        captured: list[str | None] = []

        class _Recording(_FakeCluster):
            def __call__(self, request: httpx.Request) -> httpx.Response:
                captured.append(request.headers.get("Authorization"))
                return super().__call__(request)

        cluster = _Recording(total=1, index_buckets=(("idx-1", 1),))
        await _service(db_session, cluster).list_indices(
            ListIndicesRequest(project_id=_PROJECT, index_pattern=_PATTERN)
        )
        assert captured and captured[0] == "ApiKey probe-key-under-test"


# --- route surface -----------------------------------------------------------

_BASE = "/api/v1/runtime/elastic/probe"

#: These are POST routes, so `CSRFMiddleware` applies. A machine caller has no
#: browser cookie and must therefore send the double-submit PAIR itself -- the
#: same shape every other POST route test in this suite uses. See the route
#: module docstring: exempting the prefix (as `/api/v1/ingest/` already is)
#: belongs to whoever owns `api/middleware/csrf.py`.
_CSRF = {"cookie": "csrf_token=probe-csrf", "X-CSRF-Token": "probe-csrf"}


@pytest.fixture
async def client(db_session, source_id, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from src.api.dependencies import get_db
    from src.api.routes import runtime_elastic_probe as probe_routes

    cluster = _FakeCluster(
        total=1000,
        matching=72,
        index_buckets=(("talend-esb-monitoring-2026.09", 1000),),
        measured=_SIZE_MEASURED,
        field_caps=_FIELD_CAPS,
    )
    monkeypatch.setattr(
        probe_routes,
        "ElasticProbeService",
        lambda db: ElasticProbeService(db, transport=httpx.MockTransport(cluster)),
    )
    app.dependency_overrides[get_db] = lambda: db_session
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers=_CSRF
    ) as http:
        http.cluster = cluster  # type: ignore[attr-defined]
        yield http
    app.dependency_overrides.clear()


class TestRoutes:
    async def test_describe_fields(self, client):
        resp = await client.post(
            f"{_BASE}/describe-fields",
            json={"project_id": _PROJECT, "index_pattern": _PATTERN},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["method"] == "field_caps"

    async def test_list_indices(self, client):
        resp = await client.post(
            f"{_BASE}/list-indices", json={"project_id": _PROJECT, "index_pattern": _PATTERN}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["indices"][0]["index"] == "talend-esb-monitoring-2026.09"

    async def test_count_matching(self, client):
        resp = await client.post(
            f"{_BASE}/count-matching",
            json={
                "project_id": _PROJECT,
                "index_pattern": _PATTERN,
                "field": "Body",
                "phrase": "OrderCreated",
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["matching_documents"] == 72

    async def test_size_distribution(self, client):
        resp = await client.post(
            f"{_BASE}/size-distribution",
            json={
                "project_id": _PROJECT,
                "index_pattern": _PATTERN,
                "size_field": "MessageSize",
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["measured_documents"] == 40

    async def test_no_response_body_carries_document_content(self, client):
        for path, body in (
            ("describe-fields", {"project_id": _PROJECT, "index_pattern": _PATTERN}),
            ("list-indices", {"project_id": _PROJECT, "index_pattern": _PATTERN}),
            (
                "count-matching",
                {
                    "project_id": _PROJECT,
                    "index_pattern": _PATTERN,
                    "field": "Body",
                    "phrase": "OrderCreated",
                },
            ),
            (
                "size-distribution",
                {"project_id": _PROJECT, "index_pattern": _PATTERN, "size_field": "MessageSize"},
            ),
        ):
            resp = await client.post(f"{_BASE}/{path}", json=body)
            assert resp.status_code == 200, resp.text
            assert _POISON not in resp.text
            assert "_source" not in resp.text

    async def test_a_missing_source_is_404(self, client):
        resp = await client.post(
            f"{_BASE}/list-indices",
            json={"project_id": "no-such-project", "index_pattern": _PATTERN},
        )
        assert resp.status_code == 404

    async def test_a_broad_pattern_is_422(self, client):
        resp = await client.post(
            f"{_BASE}/list-indices", json={"project_id": _PROJECT, "index_pattern": "*"}
        )
        assert resp.status_code == 422

    async def test_machine_auth_is_enforced_when_a_key_is_configured(self, client, monkeypatch):
        monkeypatch.setattr(settings, "mcp_ingest_api_key", "machine-key", raising=False)
        resp = await client.post(
            f"{_BASE}/list-indices", json={"project_id": _PROJECT, "index_pattern": _PATTERN}
        )
        assert resp.status_code == 401

        resp = await client.post(
            f"{_BASE}/list-indices",
            json={"project_id": _PROJECT, "index_pattern": _PATTERN},
            headers={"Authorization": "Bearer machine-key"},
        )
        assert resp.status_code == 200, resp.text

    async def test_keyless_outside_dev_fails_closed(self, client, monkeypatch):
        monkeypatch.setattr(settings, "app_env", "staging", raising=False)
        resp = await client.post(
            f"{_BASE}/list-indices", json={"project_id": _PROJECT, "index_pattern": _PATTERN}
        )
        assert resp.status_code == 503
