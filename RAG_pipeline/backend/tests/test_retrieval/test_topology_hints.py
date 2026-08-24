from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from src.retrieval.topology_hints import (
    MAX_ENRICHED_QUERY_CHARS,
    TopologyHintStrength,
    TopologyRetrievalHints,
    resolve_topology_retrieval_hints,
)
from src.topology.provider import TopologyProvider


FIXTURES = Path(__file__).parents[1] / "test_topology" / "fixtures"


def _werkorder_provider() -> TopologyProvider:
    landscape = json.loads((FIXTURES / "werkorder_composite_landscape.json").read_text())
    project_graph = json.loads((FIXTURES / "werkorder_project_graph.json").read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/landscape"):
            return httpx.Response(200, json=landscape)
        if request.url.path.endswith("/graph"):
            return httpx.Response(200, json=project_graph)
        return httpx.Response(404)

    return TopologyProvider(
        base_url="https://mcp.test/mcp",
        transport=httpx.MockTransport(handler),
    )


def _provider_for(landscape: dict, project_graph: dict) -> TopologyProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = landscape if request.url.path.endswith("/landscape") else project_graph
        return httpx.Response(200, json=payload)

    return TopologyProvider(
        base_url="https://mcp.test/mcp",
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_real_quintiq_maximo_fixture_adds_design_and_source_root_terms() -> None:
    hints = await resolve_topology_retrieval_hints(
        query="How does Quintiq send planned work orders to Maximo?",
        project_ids=["werkorder"],
        access_token="request-token",
        provider=_werkorder_provider(),
    )

    assert hints is not None
    assert hints.project_id == "werkorder"
    assert hints.strength is TopologyHintStrength.EXACT
    assert hints.exclusive_filter_allowed is False
    assert set(hints.matched_entity_names) == {"Maximo", "Quintiq"}
    assert "VQ_Quintiq2MX" in hints.artifact_names
    assert "Quintiq2Maximo_PlannedWorkOrder" in hints.job_names
    assert any("VQ_Quintiq2MX" in root for root in hints.source_roots)
    enriched = hints.enriched_query("How does Quintiq send planned work orders to Maximo?")
    assert "Quintiq2Maximo_PlannedWorkOrder" in enriched
    assert len(enriched) <= MAX_ENRICHED_QUERY_CHARS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("project_ids", "access_token"),
    [
        (None, "token"),
        ([], "token"),
        (["allowed", "also-allowed"], "token"),
        (["allowed"], None),
    ],
)
async def test_hints_require_one_authorized_project_and_request_identity(
    project_ids: list[str] | None,
    access_token: str | None,
) -> None:
    provider = AsyncMock(spec=TopologyProvider)

    hints = await resolve_topology_retrieval_hints(
        query="Tell me about Maximo",
        project_ids=project_ids,
        access_token=access_token,
        provider=provider,
    )

    assert hints is None
    provider.get_landscape.assert_not_awaited()
    provider.get_project_graph.assert_not_awaited()


@pytest.mark.asyncio
async def test_topology_failure_degrades_to_normal_document_retrieval() -> None:
    provider = AsyncMock(spec=TopologyProvider)
    provider.get_landscape.side_effect = RuntimeError("provider unavailable")
    provider.get_project_graph.side_effect = RuntimeError("provider unavailable")

    hints = await resolve_topology_retrieval_hints(
        query="Tell me about Maximo",
        project_ids=["allowed"],
        access_token="request-token",
        provider=provider,
    )

    assert hints is None
    provider.get_landscape.assert_awaited_once_with(
        project_id="allowed", access_token="request-token"
    )
    provider.get_project_graph.assert_awaited_once_with(
        project_id="allowed", access_token="request-token"
    )


@pytest.mark.asyncio
async def test_flat_job_without_source_root_is_a_valid_hint() -> None:
    provider = _provider_for(
        {
            "business_topology": {
                "systems": [{"id": "sys-billing", "name": "Billing"}],
            },
            "topology_state": {"available": True, "validated": True, "stale": False},
        },
        {
            "project_id": "allowed",
            "manifest": {
                "job_groups": {"BillingSync": "Operations"},
                "artifact_kinds": {"BillingSync": "job"},
            },
            "job_metadata": {"BillingSync": {"summary": "Synchronizes billing"}},
        },
    )

    hints = await resolve_topology_retrieval_hints(
        query="How does BillingSync update Billing?",
        project_ids=["allowed"],
        access_token="request-token",
        provider=provider,
    )

    assert hints is not None
    assert hints.job_names == ("BillingSync",)
    assert hints.source_roots == ()


@pytest.mark.asyncio
async def test_duplicate_generic_scanner_labels_resolve_as_ambiguous_boosts() -> None:
    provider = _provider_for(
        {
            "systems": [
                {"id": "log-a", "name": "Log", "kind": "database"},
                {"id": "log-b", "name": "Log", "kind": "database"},
                {"id": "talend", "name": "Talend", "kind": "platform"},
                {"id": "quintiq", "name": "Quintiq", "kind": "system"},
            ],
        },
        {"project_id": "allowed", "manifest": {}},
    )

    hints = await resolve_topology_retrieval_hints(
        query="Explain Log, Talend, and Quintiq",
        project_ids=["allowed"],
        access_token="request-token",
        provider=provider,
    )

    assert hints is not None
    assert hints.strength is TopologyHintStrength.AMBIGUOUS
    assert hints.ambiguous is True
    assert set(hints.generic_labels) == {"Log", "Talend", "Quintiq"}
    assert hints.exclusive_filter_allowed is False


def test_generic_or_ambiguous_hint_contract_can_never_enable_filtering() -> None:
    hints = TopologyRetrievalHints(
        project_id="allowed",
        strength=TopologyHintStrength.AMBIGUOUS,
        ambiguous=True,
        matched_entity_names=("Log", "Talend", "Quintiq"),
        generic_labels=("Log", "Talend", "Quintiq"),
        retrieval_terms=("Log", "Talend", "Quintiq"),
    )

    assert hints.exclusive_filter_allowed is False
    assert hints.enriched_query("What do these systems do?").startswith(
        "What do these systems do?"
    )
