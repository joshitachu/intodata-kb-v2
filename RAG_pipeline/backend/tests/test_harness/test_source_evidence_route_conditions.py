"""An ESB route edge carries the rule, and the reader has to hand it back.

The measured failure this pins: asked what `cMessageRouter_3` does in
`processor_cpt_invoice_p922`, the agent returned the router's topology and
reported that it could not find the branch condition. It was right -- the
scanner read `CONDITION` off the `<connection>` element and dropped it,
because `Connection` was six closed scalars with no parameter list. Every
`choice().when(...)` predicate in every Talend route in every scan was lost
the same way: 7,270 conditions in one project, 5,125 of them on `ROUTE_WHEN`
edges.

The scanner now keeps them, which only matters if they survive the whole way
to the agent. These tests assert the last leg -- that `read_component` renders
what an edge declares and that `search_source` can find a predicate by its
text -- against the shape the scanner actually writes.

Two invariants are asserted alongside, because the fix is worthless if it
costs them:

* A DI edge declares nothing but its name, and its rendering must be
  byte-identical to before. Of pluripharm's 103,274 connections, 78,806 are
  this case, so a suffix that appears on all of them would be pure bloat.
* An XPath predicate is uninterpretable without the namespaces it binds, so
  the `NAMESPACES` TABLE rows have to come along with it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.harness.source_evidence import (
    SCAN_ROOT_ENV,
    read_component_evidence,
    search_source_evidence,
)

PROJECT = "route_conditions"

#: The predicate from the real route, verbatim. Generic Camel/Java text with
#: no client identifiers in it -- unlike the customer-number predicates in the
#: same scan (`${headers.ESB_SHIP_TO_AN8} in 102726,...`), which are business
#: data and deliberately not reproduced in a committed fixture.
EXCEPTION_PREDICATE = "\"${body} is 'java.lang.Exception'\""
XPATH_PREDICATE = "boolean(/ESB_ASN/Body/DeliveryParty/Id/text())"


def _param(
    name: str,
    value: str,
    field_type: str = "TEXT",
    rows: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """One `ComponentParam` as the scanner serializes it, on a node or an edge.

    Both carriers use this shape, which is the point: the scanner has one
    element-parameter reader now, and so does this module's subject.
    """

    return {
        "name": name,
        "value": value,
        "field_type": field_type,
        "rows": rows or [],
        "redaction_descriptor": None,
    }


def _connection(
    name: str,
    source: str,
    target: str,
    connector_name: str,
    params: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "source": source,
        "target": target,
        "connector_name": connector_name,
        "line_style": 15 if connector_name.startswith("ROUTE_WHEN") else 14,
        "active": True,
        "params": params or [],
    }


@pytest.fixture
def scan_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """One route shaped like `processor_cpt_invoice_p922`'s exception path."""

    jobs = tmp_path / PROJECT / "scan" / "jobs"
    jobs.mkdir(parents=True)
    (jobs / "processor_cpt_invoice_p922-226f.json").write_text(
        json.dumps(
            {
                "name": "processor_cpt_invoice_p922",
                "job_id": "processor_cpt_invoice_p922",
                "path": "Processors/CustomerPortal/processor_cpt_invoice_p922_0.51.item",
                "purpose": "",
                "is_esb_route": True,
                "components": [
                    {
                        "unique_name": "cMessageRouter_3",
                        "component_type": "cMessageRouter",
                        # The node-level decoy: `constant` on 325 of 325 routers
                        # in the real scan, carrying no information at all. The
                        # language that matters is ROUTETYPE, on the edge.
                        "params": [_param("ROUTE_WHEN_LANGUAGES", "constant", "LANGUAGE_CLOSED_LIST")],
                        "code": None,
                        "incoming_connections": [],
                        "outgoing_connections": [],
                        "schemas": [],
                        "active": True,
                    },
                ],
                "connections": [
                    # The DI-shaped edge: name and topology, nothing declared.
                    _connection("route43", "cLog_15", "cMessageRouter_3", "ROUTE"),
                    _connection(
                        "when3",
                        "cMessageRouter_3",
                        "cLog_16",
                        "ROUTE_WHEN",
                        [
                            _param("ROUTETYPE", "simple", "CLOSED_LIST"),
                            _param("CONDITION", EXCEPTION_PREDICATE, "MEMO_JAVA"),
                            _param("ENDOFCHOICE", "true", "CHECK"),
                        ],
                    ),
                    _connection(
                        "when4",
                        "cMessageRouter_3",
                        "cLog_9",
                        "ROUTE_WHEN",
                        [
                            _param("ROUTETYPE", "xpath", "CLOSED_LIST"),
                            _param("CONDITION", XPATH_PREDICATE, "MEMO_JAVA"),
                            _param(
                                "NAMESPACES",
                                "",
                                "TABLE",
                                [{"NAMESPACE_PREFIX": "esb", "NAMESPACE_URI": "http://example.org/esb"}],
                            ),
                        ],
                    ),
                ],
                "context_groups": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(SCAN_ROOT_ENV, str(tmp_path))
    return tmp_path


async def _router_summary(scan_root: Path) -> str:
    result = await read_component_evidence(
        project_id=PROJECT,
        job="processor_cpt_invoice_p922",
        component="cMessageRouter_3",
        project_scope=[PROJECT],
    )
    assert result.status == "succeeded"
    return "\n".join(item.summary for item in result.items)


@pytest.mark.usefixtures("scan_root")
async def test_the_branch_predicate_and_its_language_reach_the_reader(scan_root: Path) -> None:
    """The original question, answerable.

    `CONDITION` without `ROUTETYPE` is a string nobody can evaluate -- the same
    text means different things in simple, spel and xpath -- so both are
    asserted, on the edge rather than on the node.
    """

    summary = await _router_summary(scan_root)

    assert EXCEPTION_PREDICATE in summary
    assert "ROUTETYPE=simple" in summary
    assert "ENDOFCHOICE=true" in summary
    # Attached to the edge it belongs to, not floating in the component body.
    assert "cMessageRouter_3 -> cLog_16" in summary


@pytest.mark.usefixtures("scan_root")
async def test_a_di_edge_declares_nothing_and_renders_as_before(scan_root: Path) -> None:
    """78,806 of 103,274 real connections are this case; they must stay clean."""

    summary = await _router_summary(scan_root)

    line = next(line for line in summary.splitlines() if "route43" in line)
    assert line.strip() == "cLog_15 -> cMessageRouter_3  [ROUTE] route43"
    assert "{" not in line


@pytest.mark.usefixtures("scan_root")
async def test_an_xpath_predicate_carries_the_namespaces_it_binds(scan_root: Path) -> None:
    """A TABLE param leaves `value` empty; dropping its rows loses the binding."""

    summary = await _router_summary(scan_root)

    assert XPATH_PREDICATE in summary
    assert "ROUTETYPE=xpath" in summary
    assert "NAMESPACE_PREFIX=esb" in summary
    assert "http://example.org/esb" in summary


@pytest.mark.usefixtures("scan_root")
async def test_a_predicate_is_findable_by_its_text(scan_root: Path) -> None:
    """The other half: an agent that does not already know which edge to open.

    The match is reported as a connection parameter and keyed by
    `<edge>.<param>`, so a hit names the branch rather than only the job.
    """

    result = await search_source_evidence(
        project_id=PROJECT, query="java.lang.Exception", project_scope=[PROJECT]
    )

    assert result.status == "succeeded"
    hit = next(
        item for item in result.items
        if item.metadata.get("where") == "connection_param_value"
    )
    assert hit.metadata["param"] == "when3.CONDITION"
    assert hit.metadata["job"] == "processor_cpt_invoice_p922"
    assert EXCEPTION_PREDICATE in hit.summary
    # The edge is named in the summary, because a predicate without its branch
    # cannot be acted on.
    assert "cMessageRouter_3 -> cLog_16" in hit.summary


@pytest.mark.usefixtures("scan_root")
async def test_searching_a_language_name_finds_the_branches_written_in_it(
    scan_root: Path,
) -> None:
    """`ROUTETYPE` is searchable in its own right: "which branches use xpath?"."""

    result = await search_source_evidence(
        project_id=PROJECT, query="xpath", project_scope=[PROJECT]
    )

    assert result.status == "succeeded"
    params = {item.metadata.get("param") for item in result.items}
    assert "when4.ROUTETYPE" in params
