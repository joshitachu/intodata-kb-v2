"""Slice 4: the agent's plain-language topology, compiled and published.

Two properties are load-bearing and both are asserted rather than assumed:

* **Topology never blocks documentation.** Every failure path returns an
  outcome. The predecessor
  (``providers/goal_driven/business_topology.py:93-102``) raised on a failed
  validation, which turned an advisory overlay into a failed run.
* **The fallback produces a renderable pair.** The dashboard hard-gates on
  ``topology_state.validated`` (``landscapeTransform.ts:258``) and
  ``projects.py::_load_published_business_topology`` requires
  ``business_topology.json`` plus a report saying ``validated: true``. An
  unproven scanner-floor fallback would take the landscape view dark for every
  project documented in this lane.
"""

from __future__ import annotations

import json
from pathlib import Path

from mcp_server.providers.agent_workspace import contract
from mcp_server.providers.agent_workspace.integration_model import (
    ImplementingUnit,
    Integration,
    IntegrationKnowledgeBase,
    Interface,
    Journey,
    JourneyStep,
    JourneyTransition,
    Mapping,
    Participant,
)
from mcp_server.providers.agent_workspace.topology import (
    TopologyUnavailableError,
    compile_agent_topology,
)
from tests.test_agent_workspace.workspace_fixture import make_workspace

_JOBS = (
    "AssetHub2FieldApp_SendTicket",
    "FieldApp_GetTicket",
    "FieldApp_GetLocation",
)

#: Plain business language. No ids, no ClaimStatus, no artifact hash, no
#: coverage arithmetic -- Nexus mints all of it.
_AGENT_TOPOLOGY = {
    "systems": [
        {"name": "AssetHub", "role": "Enterprise asset management", "kind": "application"},
        {"name": "FieldApp", "role": "Field inspection app", "kind": "application"},
    ],
    "flows": [
        {
            "name": "AssetHub -> FieldApp: ticket ingestion",
            "from": "AssetHub",
            "to": "FieldApp",
            "source_system": "AssetHub",
            "target_system": "FieldApp",
            "business_object": "WorkOrder",
            "carries": "Work orders are received and served to the field app",
            "mechanism": "REST",
            "implemented_by": ["AssetHub2FieldApp_SendTicket"],
            "implementing_designs": [
                "AssetHub2FieldApp_SendTicket",
                "FieldApp_GetTicket",
            ],
            "confidence": "high",
            "evidence": "03_DATA_FLOWS.md - 'AssetHub to FieldApp'",
        }
    ],
}


def _write_scan(root: Path) -> None:
    """A two-system Talend scan with one directional edge and three designs."""

    (root / "scan").mkdir(parents=True)
    (root / "scan_result.json").write_text(
        json.dumps(
            {
                "platform": "talend",
                "jobs": [
                    {
                        "name": name,
                        "job_id": name,
                        "source_root": "IMPORT/PROJECT",
                        "path": f"Orders/{name}.item",
                        "artifact_kind": "job",
                        "artifact_role": "execution",
                        "components": [],
                        "connections": [],
                    }
                    for name in _JOBS
                ],
                "metadata_connections": [],
            }
        ),
        encoding="utf-8",
    )
    (root / "scan" / "manifest.json").write_text(
        json.dumps(
            {
                "source_roots": [{"id": "IMPORT/PROJECT", "project_id": "ACME_ZIP_TEST"}],
                "artifact_relations": [],
                "artifact_resources": [],
                "artifact_endpoints": [],
                "artifact_channels": [],
            }
        ),
        encoding="utf-8",
    )
    (root / "scan" / "lineage.json").write_text(json.dumps({"entries": []}), encoding="utf-8")
    (root / "scan" / "landscape.json").write_text(
        json.dumps(
            {
                "systems": [
                    {
                        "id": "system:assethub",
                        "name": "AssetHub",
                        "kind": "software_system",
                        "source_roots": ["IMPORT/PROJECT"],
                    },
                    {
                        "id": "system:fieldapp",
                        "name": "FieldApp",
                        "kind": "software_system",
                        "source_roots": ["IMPORT/PROJECT"],
                    },
                ],
                "applications": [],
                "edges": [
                    {
                        "source": "system:assethub",
                        "target": "system:fieldapp",
                        "dependency_type": "data",
                        "mechanism": "rest",
                        "direction": "out",
                        "provenance": [{"artifact_id": "AssetHub2FieldApp_SendTicket"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _stage(tmp_path: Path, topology: dict | str | None) -> tuple[Path, Path]:
    scan_root = tmp_path / "intodoc_output" / "ACME_ZIP_TEST"
    _write_scan(scan_root)
    staging = make_workspace(tmp_path / "docs_staging")
    (staging / contract.OUTPUT / "03_DATA_FLOWS.md").write_text(
        "# Data flows\n\n## AssetHub to FieldApp\n\nWork orders move nightly.\n",
        encoding="utf-8",
    )
    if topology is not None:
        text = topology if isinstance(topology, str) else json.dumps(topology)
        (staging / contract.TOPOLOGY_FILE).write_text(text, encoding="utf-8")
    return staging, scan_root


def _published(staging: Path) -> tuple[dict, dict, dict]:
    directory = staging / "_topology"
    report = json.loads((directory / "validation_report.json").read_text(encoding="utf-8"))
    state = json.loads((directory / "state.json").read_text(encoding="utf-8"))
    topology_path = directory / "business_topology.json"
    topology = (
        json.loads(topology_path.read_text(encoding="utf-8")) if topology_path.is_file() else {}
    )
    return topology, report, state


def _compile(staging: Path, scan_root: Path):
    return compile_agent_topology(
        project_id="ACME_ZIP_TEST",
        staging_dir=staging,
        scan_output_dir=scan_root,
        document_names=("03_DATA_FLOWS.md",),
    )


def test_a_plain_name_topology_compiles_to_a_validated_contract(tmp_path):
    staging, scan_root = _stage(tmp_path, _AGENT_TOPOLOGY)

    outcome = _compile(staging, scan_root)

    assert outcome.validated and outcome.available
    assert outcome.lane == "agent_topology"
    topology, report, state = _published(staging)
    assert report["validated"] is True
    assert state["validated"] is True and state["available"] is True
    # Nexus minted every identifier; the agent supplied none.
    assert {system["name"] for system in topology["systems"]} >= {"AssetHub", "FieldApp"}
    assert all(system["id"].startswith("sys-") for system in topology["systems"])
    assert topology["data_streams"]
    assert all(stream["evidence_ids"] for stream in topology["data_streams"])


def _work_order_integration(*, steps: list[JourneyStep], **journey_fields) -> Integration:
    """One two-system integration whose journey is whatever the test needs."""

    return Integration(
        id="integration:work-orders",
        label="Work-order exchange",
        state="exact",
        capability="Work orders",
        participants=[
            Participant(id="participant:assethub", label="AssetHub", state="exact"),
            Participant(id="participant:fieldapp", label="FieldApp", state="exact"),
            Participant(id="participant:archive", label="Archive", state="exact"),
        ],
        interfaces=[
            Interface(
                id="interface:work-orders",
                label="Work-order REST interface",
                state="exact",
                interface_kind="REST",
                source_participant_id="participant:assethub",
                target_participant_id="participant:fieldapp",
            )
        ],
        implementing_units=[
            ImplementingUnit(
                id="unit:send",
                label="AssetHub2FieldApp_SendTicket",
                state="exact",
                unit_kind="job",
            ),
            ImplementingUnit(
                id="unit:read",
                label="FieldApp_GetTicket",
                state="exact",
                unit_kind="job",
            ),
        ],
        mappings=[
            Mapping(
                id="mapping:status",
                label="Status translation",
                state="exact",
                source_ref="AssetHub.STATUS",
                target_ref="FieldApp.state",
                transformation_summary="Maps the AssetHub status vocabulary.",
            )
        ],
        journeys=[
            Journey(
                id="journey:send",
                label="Publish tickets to FieldApp",
                state="exact",
                participant_ids=["participant:assethub", "participant:fieldapp"],
                steps=steps,
                **journey_fields,
            )
        ],
    )


def test_shared_integration_model_is_the_preferred_topology_source(tmp_path):
    staging, scan_root = _stage(tmp_path, _AGENT_TOPOLOGY)
    IntegrationKnowledgeBase(
        integrations=[
            _work_order_integration(
                steps=[
                    JourneyStep(
                        id="step:send",
                        sequence=1,
                        state="exact",
                        source_participant_id="participant:assethub",
                        target_participant_id="participant:fieldapp",
                        interface_ids=["interface:work-orders"],
                        unit_ids=["unit:send"],
                        mapping_ids=["mapping:status"],
                    )
                ]
            )
        ]
    ).save(staging / contract.INTEGRATION_MODEL)

    outcome = _compile(staging, scan_root)

    assert outcome.validated
    assert outcome.lane == "integration_model"
    topology, _report, _state = _published(staging)
    assert any(
        stream["name"] == "Publish tickets to FieldApp" for stream in topology["data_streams"]
    )


def test_an_ordered_journey_compiles_into_an_ordered_delivery_path(tmp_path):
    """``A -> B -> C`` reaches the overlay as two hops, in order.

    The predecessor had only ``participant_ids`` to work with, so it published
    one edge from ``participant_ids[0]`` to ``participant_ids[-1]`` and the
    middle of the journey did not exist in the output at all.
    """

    staging, scan_root = _stage(tmp_path, None)
    IntegrationKnowledgeBase(
        integrations=[
            _work_order_integration(
                steps=[
                    JourneyStep(
                        id="step:send",
                        sequence=1,
                        label="Publish the ticket",
                        state="exact",
                        source_participant_id="participant:assethub",
                        target_participant_id="participant:fieldapp",
                        interface_ids=["interface:work-orders"],
                        unit_ids=["unit:send"],
                        mapping_ids=["mapping:status"],
                    ),
                    JourneyStep(
                        id="step:archive",
                        sequence=2,
                        kind="deliver",
                        label="Archive the delivered order",
                        state="exact",
                        source_participant_id="participant:fieldapp",
                        target_participant_id="participant:archive",
                        unit_ids=["unit:read"],
                    ),
                ]
            )
        ]
    ).save(staging / contract.INTEGRATION_MODEL)

    outcome = _compile(staging, scan_root)

    assert outcome.lane == "integration_model"
    topology, _report, _state = _published(staging)
    stream = next(
        item
        for item in topology["data_streams"]
        if item["name"] == "Publish tickets to FieldApp"
    )
    systems = {system["id"]: system["name"] for system in topology["systems"]}
    assert systems[stream["source_system_id"]] == "AssetHub"
    assert systems[stream["destination_system_id"]] == "Archive"
    path = [
        segment
        for delivery in topology["delivery_paths"]
        for segment in delivery["segments"]
        if delivery["stream_id"] == stream["id"]
    ]
    assert [segment["name"] for segment in path] == [
        "Publish the ticket",
        "Archive the delivered order",
    ]
    # The transformation belongs to the hand-off that applies it, not to the
    # journey as an undifferentiated whole.
    transformations = [
        item for item in topology.get("transformations", []) if item.get("name")
    ]
    assert any(item["name"] == "Publish the ticket" for item in transformations)


def test_fan_out_publishes_every_arm_of_the_fork(tmp_path):
    """A decision step routes two ways, and both destinations are published.

    The predecessor had only ``participant_ids`` to work with, so a fork was
    published as a single edge from the first participant to the last -- one
    arbitrary arm, with the other silently gone.
    """

    staging, scan_root = _stage(tmp_path, None)
    IntegrationKnowledgeBase(
        integrations=[
            _work_order_integration(
                steps=[
                    JourneyStep(
                        id="step:route",
                        sequence=1,
                        kind="decision",
                        state="exact",
                        summary="Work orders are routed by their acceptance status.",
                        source_participant_id="participant:assethub",
                        transitions=[
                            JourneyTransition(condition="accepted", target_step_id="step:send"),
                            JourneyTransition(condition="rejected", target_step_id="step:archive"),
                        ],
                    ),
                    JourneyStep(
                        id="step:send",
                        sequence=2,
                        kind="deliver",
                        state="exact",
                        source_participant_id="participant:assethub",
                        target_participant_id="participant:fieldapp",
                        interface_ids=["interface:work-orders"],
                        unit_ids=["unit:send"],
                    ),
                    JourneyStep(
                        id="step:archive",
                        sequence=2,
                        kind="error",
                        state="exact",
                        source_participant_id="participant:assethub",
                        target_participant_id="participant:archive",
                        unit_ids=["unit:read"],
                    ),
                ]
            )
        ]
    ).save(staging / contract.INTEGRATION_MODEL)

    outcome = _compile(staging, scan_root)

    assert outcome.lane == "integration_model"
    topology, _report, _state = _published(staging)
    systems = {system["id"]: system["name"] for system in topology["systems"]}
    destinations = {
        systems[stream["destination_system_id"]]
        for stream in topology["data_streams"]
        if stream["name"].startswith("Publish tickets to FieldApp")
    }
    assert destinations == {"FieldApp", "Archive"}


def test_fan_in_publishes_both_sources(tmp_path):
    """Two steps whose transitions name one step: a merge, and both arrive."""

    staging, scan_root = _stage(tmp_path, None)
    IntegrationKnowledgeBase(
        integrations=[
            _work_order_integration(
                steps=[
                    JourneyStep(
                        id="step:from-assethub",
                        sequence=1,
                        kind="extract",
                        state="exact",
                        source_participant_id="participant:assethub",
                        transitions=[
                            JourneyTransition(condition="always", target_step_id="step:deliver")
                        ],
                    ),
                    JourneyStep(
                        id="step:from-archive",
                        sequence=1,
                        kind="extract",
                        state="exact",
                        source_participant_id="participant:archive",
                        transitions=[
                            JourneyTransition(condition="always", target_step_id="step:deliver")
                        ],
                    ),
                    JourneyStep(
                        id="step:deliver",
                        sequence=2,
                        kind="deliver",
                        state="exact",
                        target_participant_id="participant:fieldapp",
                        interface_ids=["interface:work-orders"],
                        unit_ids=["unit:send"],
                    ),
                ]
            )
        ]
    ).save(staging / contract.INTEGRATION_MODEL)

    outcome = _compile(staging, scan_root)

    assert outcome.lane == "integration_model"
    topology, _report, _state = _published(staging)
    systems = {system["id"]: system["name"] for system in topology["systems"]}
    arriving = {
        systems[stream["source_system_id"]]
        for stream in topology["data_streams"]
        if stream["name"].startswith("Publish tickets to FieldApp")
    }
    assert arriving == {"AssetHub", "Archive"}


def test_a_step_with_no_established_order_still_reaches_the_overlay(tmp_path):
    """PRD 11.4. An unplaced step is carried, not dropped for lacking a sequence."""

    staging, scan_root = _stage(tmp_path, None)
    IntegrationKnowledgeBase(
        integrations=[
            _work_order_integration(
                steps=[
                    JourneyStep(
                        id="step:send",
                        sequence=1,
                        kind="deliver",
                        state="exact",
                        source_participant_id="participant:assethub",
                        target_participant_id="participant:fieldapp",
                        interface_ids=["interface:work-orders"],
                        unit_ids=["unit:send"],
                    ),
                    JourneyStep(
                        id="step:unplaced",
                        kind="transform",
                        state="unresolved",
                        label="Status translation, position not established",
                        unit_ids=["unit:read"],
                        mapping_ids=["mapping:status"],
                    ),
                ]
            )
        ]
    ).save(staging / contract.INTEGRATION_MODEL)

    outcome = _compile(staging, scan_root)

    assert outcome.lane == "integration_model"
    topology, _report, _state = _published(staging)
    stream = next(
        item
        for item in topology["data_streams"]
        if item["name"] == "Publish tickets to FieldApp"
    )
    segments = [
        segment["name"]
        for delivery in topology["delivery_paths"]
        for segment in delivery["segments"]
        if delivery["stream_id"] == stream["id"]
    ]
    assert "Status translation, position not established" in segments


def test_more_than_six_documents_all_stay_citable(tmp_path):
    """The removed ceiling. ``_MAX_EVIDENCE_DOCUMENTS = 6`` silently dropped
    the documents sorting last by filename, so a journey whose only citation
    was one of them was pruned for citing evidence that "does not exist".
    """

    staging, scan_root = _stage(tmp_path, None)
    names = [f"{index:02d}_SECTION.md" for index in range(1, 10)]
    for name in names:
        (staging / contract.OUTPUT / name).write_text(
            f"# {name}\n\n## AssetHub to FieldApp\n\nWork orders move nightly.\n",
            encoding="utf-8",
        )
    # The LAST document by filename -- the first one the ceiling used to drop.
    IntegrationKnowledgeBase(
        integrations=[
            _work_order_integration(
                document_paths=[names[-1]],
                steps=[
                    JourneyStep(
                        id="step:send",
                        sequence=1,
                        state="exact",
                        source_participant_id="participant:assethub",
                        target_participant_id="participant:fieldapp",
                        interface_ids=["interface:work-orders"],
                        unit_ids=["unit:send"],
                    )
                ],
            )
        ]
    ).save(staging / contract.INTEGRATION_MODEL)

    outcome = compile_agent_topology(
        project_id="ACME_ZIP_TEST",
        staging_dir=staging,
        scan_output_dir=scan_root,
        document_names=("03_DATA_FLOWS.md", *names),
    )

    assert outcome.validated
    assert outcome.lane == "integration_model"
    topology, _report, _state = _published(staging)
    stream = next(
        item
        for item in topology["data_streams"]
        if item["name"] == "Publish tickets to FieldApp"
    )
    assert stream["evidence_ids"]
    # Every published document is registered, not the six that sort first.
    registered = _registered_documents(staging)
    assert set(names).issubset(registered)
    # The journey cited the ninth document by name, and it resolved: the
    # citation's own evidence id is on the stream.
    from mcp_server.services.business_topology.doc_evidence import doc_evidence_id

    assert doc_evidence_id(names[-1], "") in stream["evidence_ids"]


def test_an_unwritable_topology_directory_never_blocks_publication(tmp_path):
    """The one path that used to escape as an unrelated exception type.

    ``topology_dir.mkdir`` sat OUTSIDE the guard, so its ``OSError`` walked
    past the runner's single ``except WorkspaceUnavailableError`` -- and
    ``_publish`` calls this BEFORE ``finalize_publication``, so a documentation
    set that had already passed every safety check was lost to it.
    """

    staging, scan_root = _stage(tmp_path, _AGENT_TOPOLOGY)
    (staging / "_topology").write_text("not a directory", encoding="utf-8")

    outcome = compile_agent_topology(
        project_id="ACME_ZIP_TEST",
        staging_dir=staging,
        scan_output_dir=scan_root,
    )

    assert not outcome.validated and not outcome.available
    assert outcome.errors


def test_a_topology_failure_names_its_stage():
    """One exception family, and the stage is carried rather than inferred."""

    error = TopologyUnavailableError("the topology directory could not be created")

    assert isinstance(error, contract.WorkspaceUnavailableError)
    assert error.stage == "topology"
    assert str(error).startswith("[topology]")


def test_an_unreadable_integration_model_is_reported_not_swallowed(tmp_path):
    staging, scan_root = _stage(tmp_path, None)
    (staging / contract.INTEGRATION_MODEL).write_text(
        json.dumps({"schema_version": "integration-knowledge-v1", "integrations": []}),
        encoding="utf-8",
    )

    outcome = _compile(staging, scan_root)

    # Advisory: the scanner floor still publishes.
    assert outcome.lane == "deterministic"
    _topology, report, _state = _published(staging)
    assert any("integration-knowledge-v1" in warning for warning in report["warnings"])


def test_a_dropped_journey_is_named_in_the_topology_report(tmp_path):
    """Partial acceptance is LOUD. A dropped record used to leave no trace."""

    staging, scan_root = _stage(tmp_path, None)
    payload = json.loads(
        IntegrationKnowledgeBase(
            integrations=[
                _work_order_integration(
                    steps=[
                        JourneyStep(
                            id="step:send",
                            sequence=1,
                            state="exact",
                            source_participant_id="participant:assethub",
                            target_participant_id="participant:fieldapp",
                            interface_ids=["interface:work-orders"],
                            unit_ids=["unit:send"],
                        )
                    ]
                )
            ]
        ).to_json()
    )
    payload["integrations"][0]["journeys"].append(
        {"id": "journey:broken", "label": "Broken", "state": "not-a-state"}
    )
    (staging / contract.INTEGRATION_MODEL).write_text(json.dumps(payload), encoding="utf-8")

    outcome = _compile(staging, scan_root)

    assert outcome.validated
    _topology, report, _state = _published(staging)
    dropped = [warning for warning in report["warnings"] if "unusable record" in warning]
    assert dropped and "journey:broken" in dropped[0]


def _registered_documents(staging: Path) -> set[str]:
    """Which documents the compiler admitted as the evidence tier."""

    from mcp_server.services.business_topology.doc_evidence import collect_doc_evidence

    from mcp_server.providers.agent_workspace import topology as topology_module

    names = tuple(
        sorted(path.name for path in (staging / contract.OUTPUT).glob("*.md"))
    )
    records = collect_doc_evidence(
        staging / contract.OUTPUT,
        filenames=topology_module._evidence_documents(names),
    )
    return {str(record["document"]) for record in records.values()}


def test_one_malformed_flow_is_pruned_and_the_rest_publishes(tmp_path):
    payload = json.loads(json.dumps(_AGENT_TOPOLOGY))
    payload["flows"].append(
        {
            "name": "unusable",
            "source_system": "unclear",
            "target_system": "FieldApp",
            "business_object": "Thing",
            "confidence": "high",
        }
    )
    payload["flows"].append(
        {
            "name": "self loop",
            "source_system": "AssetHub",
            "target_system": "assethub",
            "business_object": "Thing",
            "confidence": "high",
        }
    )
    staging, scan_root = _stage(tmp_path, payload)

    outcome = _compile(staging, scan_root)

    assert outcome.validated
    topology, _report, _state = _published(staging)
    names = {stream["name"] for stream in topology["data_streams"]}
    assert not any("unusable" in name or "self loop" in name for name in names)
    assert topology["data_streams"]


def test_an_absent_topology_file_falls_back_to_the_scanner_floor(tmp_path):
    staging, scan_root = _stage(tmp_path, None)

    outcome = _compile(staging, scan_root)

    assert outcome.lane == "deterministic"
    # The pair the dashboard hard-gates on, produced by the fallback.
    topology, report, state = _published(staging)
    assert outcome.validated and report["validated"] is True
    assert state["validated"] is True
    assert topology["data_streams"]


def test_wholly_invalid_json_falls_back_to_the_scanner_floor(tmp_path):
    staging, scan_root = _stage(tmp_path, "this is not JSON at all")

    outcome = _compile(staging, scan_root)

    assert outcome.lane == "deterministic"
    assert outcome.validated
    assert (staging / "_topology" / "business_topology.json").is_file()


def test_a_broken_scan_never_raises_and_records_the_absence(tmp_path):
    """The overlay is advisory; documentation must publish regardless."""

    staging = make_workspace(tmp_path / "docs_staging")
    (staging / contract.TOPOLOGY_FILE).write_text(json.dumps(_AGENT_TOPOLOGY), encoding="utf-8")

    outcome = compile_agent_topology(
        project_id="ACME_ZIP_TEST",
        staging_dir=staging,
        scan_output_dir=tmp_path / "no-such-scan",
    )

    assert not outcome.validated and not outcome.available
    assert outcome.errors
    _topology, report, state = _published(staging)
    assert report["validated"] is False
    assert state["available"] is False
    assert not (staging / "_topology" / "business_topology.json").exists()


def test_a_stale_overlay_is_removed_when_the_new_one_fails(tmp_path):
    staging = make_workspace(tmp_path / "docs_staging")
    stale = staging / "_topology"
    stale.mkdir()
    (stale / "business_topology.json").write_text('{"stale": true}', encoding="utf-8")

    compile_agent_topology(
        project_id="ACME_ZIP_TEST",
        staging_dir=staging,
        scan_output_dir=tmp_path / "no-such-scan",
    )

    assert not (stale / "business_topology.json").exists()


def test_nested_documents_are_citable_evidence(tmp_path):
    """``discover_doc_filenames`` only sees a directory's top level.

    The agent chooses the structure, so the publisher's accepted paths are
    passed through explicitly; otherwise an ``integrations/*.md`` citation
    resolves to nothing and its flow is pruned for lacking evidence.
    """

    staging, scan_root = _stage(tmp_path, None)
    nested = staging / contract.OUTPUT / "integrations" / "orders.md"
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_text(
        "# Orders\n\n## AssetHub to FieldApp\n\nWork orders move nightly.\n",
        encoding="utf-8",
    )
    payload = json.loads(json.dumps(_AGENT_TOPOLOGY))
    payload["flows"][0]["evidence"] = "integrations/orders.md - 'AssetHub to FieldApp'"
    (staging / contract.TOPOLOGY_FILE).write_text(json.dumps(payload), encoding="utf-8")

    outcome = compile_agent_topology(
        project_id="ACME_ZIP_TEST",
        staging_dir=staging,
        scan_output_dir=scan_root,
        document_names=("integrations/orders.md",),
    )

    assert outcome.validated
    topology, _report, _state = _published(staging)
    cited = {
        evidence_id for stream in topology["data_streams"] for evidence_id in stream["evidence_ids"]
    }
    assert any(item.startswith("doc:") for item in cited)
