"""The durable knowledge model, and the ordered journey that replaced a bag.

Until ``integration-knowledge-v2`` a journey was four unordered id lists. It
could not say that ``A -> B -> C`` runs in that order, it could not say which
transformation happens at which hand-off, and the topology compiler had to
guess a direction from ``participant_ids[0]`` and ``participant_ids[-1]``.
Every test below that names a shape -- linear, reverse, fan-out, fan-in, a
shared unit, a missing intermediate participant -- exists because the previous
model represented all six of them identically.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from mcp_server.providers.agent_workspace import contract
from mcp_server.providers.agent_workspace.integration_model import (
    JOURNEY_STEP_KINDS,
    SCHEMA_VERSION,
    ConfigurationRef,
    Dependency,
    DesignDifference,
    EvidenceRef,
    Finding,
    ImplementingUnit,
    Integration,
    IntegrationKnowledgeBase,
    IntegrationModelError,
    Interface,
    Journey,
    JourneyStep,
    JourneyTransition,
    Mapping,
    Participant,
    Risk,
    RuntimeRef,
    Unknown,
    integration_filename,
    load_report,
    load_workspace_model,
    stable_id,
)


def _integration(*, label: str = "Customer synchronization") -> Integration:
    evidence_id = stable_id("evidence", "scan", "customer-job")
    participant_id = stable_id("participant", "crm")
    interface_id = stable_id("interface", "crm-customer-api")
    unit_id = stable_id("unit", "project-a", "customer-job")
    mapping_id = stable_id("mapping", "customer-status")
    config_id = stable_id("configuration", "crm-api-url")
    dependency_id = stable_id("dependency", unit_id, interface_id)
    runtime_id = stable_id("runtime", "customer-job", "last-seen")
    finding_id = stable_id("finding", "customer-upsert")
    risk_id = stable_id("risk", "missing-retry")
    difference_id = stable_id("difference", "customer-status")
    unknown_id = stable_id("unknown", "customer-owner")

    return Integration(
        id=stable_id("integration", "customer-sync"),
        label=label,
        state="partial",
        capability="Customer master data",
        evidence_ref_ids=[evidence_id],
        evidence_refs=[
            EvidenceRef(
                id=evidence_id,
                label="Parsed customer job",
                state="exact",
                evidence_type="parsed-design",
                artifact="scan/designs/customer.json",
                locator="components",
            )
        ],
        participants=[
            Participant(
                id=participant_id,
                label="CRM",
                state="exact",
                role="source",
                evidence_ref_ids=[evidence_id],
            )
        ],
        interfaces=[
            Interface(
                id=interface_id,
                label="Customer API",
                state="dynamic",
                interface_kind="REST",
                source_participant_id=participant_id,
                evidence_ref_ids=[evidence_id],
            )
        ],
        implementing_units=[
            ImplementingUnit(
                id=unit_id,
                label="customer-job",
                state="exact",
                unit_kind="job",
                platform="Talend",
                project_ref="project-a",
            )
        ],
        mappings=[
            Mapping(
                id=mapping_id,
                label="Customer status",
                state="partial",
                source_ref="CRM.STATUS",
                target_ref="ERP.CustomerStatus",
                transformation_summary="Maps the source status to the ERP status vocabulary.",
            )
        ],
        configuration_refs=[
            ConfigurationRef(
                id=config_id,
                label="CRM API location",
                state="dynamic",
                key_ref="crm.api.url",
                environment_refs=["production", "test"],
            )
        ],
        dependencies=[
            Dependency(
                id=dependency_id,
                label="Job calls API",
                state="exact",
                source_id=unit_id,
                target_id=interface_id,
                relation="calls",
            )
        ],
        runtime_refs=[
            RuntimeRef(
                id=runtime_id,
                label="Last observed execution",
                state="unresolved",
                source_ref="runtime/customer-job",
            )
        ],
        findings=[
            Finding(
                id=finding_id,
                label="Upsert behavior",
                state="exact",
                statement="The target operation is an upsert.",
            )
        ],
        risks=[
            Risk(
                id=risk_id,
                label="Retry behavior",
                state="unsupported",
                description="The sanitized evidence cannot establish retry behavior.",
            )
        ],
        design_differences=[
            DesignDifference(
                id=difference_id,
                label="Status vocabulary",
                state="partial",
                designed="Design expects three statuses.",
                implemented="Parsed mapping exposes four statuses.",
            )
        ],
        unknowns=[
            Unknown(
                id=unknown_id,
                label="Business owner",
                state="unresolved",
                question="Who owns the customer status vocabulary?",
            )
        ],
        journeys=[
            Journey(
                id=stable_id("journey", "customer-sync", "main"),
                label="CRM to ERP customer update",
                state="partial",
                participant_ids=[participant_id],
                document_paths=["integrations/customer_master.md"],
                steps=[
                    JourneyStep(
                        id=stable_id("step", "customer-sync", "load"),
                        sequence=1,
                        kind="deliver",
                        label="Load the customer into the ERP",
                        summary="The customer record is submitted to the ERP.",
                        state="partial",
                        source_participant_id=participant_id,
                        interface_ids=[interface_id],
                        unit_ids=[unit_id],
                        mapping_ids=[mapping_id],
                        mechanism="REST",
                    )
                ],
                configuration_ref_ids=[config_id],
                dependency_ids=[dependency_id],
                runtime_ref_ids=[runtime_id],
                finding_ids=[finding_id],
                risk_ids=[risk_id],
                design_difference_ids=[difference_id],
                unknown_ids=[unknown_id],
            )
        ],
    )


def _linear_integration() -> Integration:
    """``A -> B -> C``: three participants, two hand-offs, one shared unit.

    The shared unit is deliberate: ``transform`` runs at both hand-offs, so it
    has to appear on both steps and exactly once in the journey's derived view.
    """

    return Integration(
        id="integration:orders",
        label="Order distribution",
        state="exact",
        participants=[
            Participant(id="p:a", label="A", state="exact"),
            Participant(id="p:b", label="B", state="exact"),
            Participant(id="p:c", label="C", state="exact"),
        ],
        implementing_units=[
            ImplementingUnit(id="u:transform", label="Transform", state="exact"),
            ImplementingUnit(id="u:deliver", label="Deliver", state="exact"),
        ],
        mappings=[
            Mapping(id="m:ab", label="A to B", state="exact", source_ref="A.x", target_ref="B.x"),
            Mapping(id="m:bc", label="B to C", state="exact", source_ref="B.x", target_ref="C.x"),
        ],
        journeys=[
            Journey(
                id="journey:forward",
                label="A to C",
                state="exact",
                steps=[
                    JourneyStep(
                        id="step:a-b",
                        sequence=1,
                        kind="extract",
                        summary="Records are selected from A and staged in B.",
                        state="exact",
                        source_participant_id="p:a",
                        target_participant_id="p:b",
                        unit_ids=["u:transform"],
                        mapping_ids=["m:ab"],
                        sql_refs=["a-selection-query"],
                    ),
                    JourneyStep(
                        id="step:b-c",
                        sequence=2,
                        kind="deliver",
                        summary="The staged records are submitted to C.",
                        state="exact",
                        source_participant_id="p:b",
                        target_participant_id="p:c",
                        unit_ids=["u:transform", "u:deliver"],
                        mapping_ids=["m:bc"],
                    ),
                ],
            )
        ],
    )


def test_stable_ids_do_not_depend_on_editable_labels() -> None:
    first = _integration(label="Customer synchronization")
    renamed = _integration(label="CRM customer replication")

    assert first.id == renamed.id
    assert stable_id("unit", "project-a", "customer-job") == stable_id(
        "unit", "project-a", "customer-job"
    )
    assert stable_id("unit", "project-a", "other-job") != stable_id(
        "unit", "project-a", "customer-job"
    )


def test_all_epistemic_states_are_accepted_and_unknown_values_are_rejected() -> None:
    for state in ("exact", "partial", "dynamic", "unresolved", "unsupported"):
        Participant(id=f"participant-{state}", label=state, state=state)

    with pytest.raises(ValidationError):
        Participant(id="participant-bad", label="bad", state="probably")


def test_incremental_merge_updates_labels_and_preserves_prior_nested_knowledge() -> None:
    original = IntegrationKnowledgeBase(integrations=[_integration()])
    integration_id = original.integrations[0].id
    new_participant = Participant(
        id=stable_id("participant", "erp"),
        label="ERP",
        state="exact",
        role="target",
    )
    increment = Integration(
        id=integration_id,
        label="Renamed customer synchronization",
        state="partial",
        participants=[new_participant],
    )

    merged = original.merge(IntegrationKnowledgeBase(integrations=[increment]))

    assert original.integrations[0].label == "Customer synchronization"
    assert merged.integrations[0].label == "Renamed customer synchronization"
    assert {item.label for item in merged.integrations[0].participants} == {"CRM", "ERP"}
    assert len(merged.integrations[0].mappings) == 1


def test_nested_upsert_merges_by_id_without_erasing_unspecified_fields() -> None:
    integration = _integration()
    mapping = integration.mappings[0]
    integration.upsert(
        "mappings",
        Mapping(
            id=mapping.id,
            label="Customer status vocabulary",
            state="exact",
            source_ref=mapping.source_ref,
            target_ref=mapping.target_ref,
        ),
    )

    updated = integration.mappings[0]
    assert updated.label == "Customer status vocabulary"
    assert updated.state == "exact"
    assert updated.transformation_summary == mapping.transformation_summary


def test_save_is_deterministic_atomic_and_round_trips(tmp_path: Path) -> None:
    first = _integration()
    second = Integration(
        id=stable_id("integration", "orders"),
        label="Order synchronization",
        state="unresolved",
    )
    knowledge = IntegrationKnowledgeBase(integrations=[second, first])
    checkpoint = tmp_path / "checkpoints" / "knowledge.json"

    knowledge.save(checkpoint)
    first_bytes = checkpoint.read_bytes()
    loaded = IntegrationKnowledgeBase.load(checkpoint)
    loaded.checkpoint(checkpoint)

    assert checkpoint.read_bytes() == first_bytes
    assert loaded == knowledge
    assert not (checkpoint.parent / f".{checkpoint.name}.tmp").exists()
    assert [item["id"] for item in loaded.to_canonical_dict()["integrations"]] == sorted(
        [first.id, second.id]
    )


def test_structural_validation_reports_dangling_ids_but_never_matches_prose() -> None:
    integration = _integration()
    # The mapping is attached to the STEP now, so the dangling reference is
    # reported against the hand-off that carries it rather than against the
    # whole journey -- the difference between "this journey is wrong" and
    # "this hand-off is wrong", which is the one an author can act on.
    step = integration.journeys[0].steps[0]
    step.mapping_ids = [*step.mapping_ids, "mapping-that-does-not-exist"]
    integration.findings[0].evidence_ref_ids.append("evidence-that-does-not-exist")

    issues = IntegrationKnowledgeBase(integrations=[integration]).validate_structure()

    assert [(issue.code, issue.message) for issue in issues] == [
        (
            "dangling_evidence_ref",
            "evidence reference 'evidence-that-does-not-exist' does not exist",
        ),
        (
            "dangling_reference",
            "referenced record 'mapping-that-does-not-exist' does not exist",
        ),
    ]
    assert issues[1].path.endswith(f"/journeys/{integration.journeys[0].id}/steps/{step.id}/mapping_ids")


def test_duplicate_ids_are_structurally_invalid() -> None:
    integration = _integration()
    integration.participants.append(integration.participants[0].model_copy())

    with pytest.raises(ValidationError, match="duplicate IDs in participants"):
        Integration.model_validate(integration.model_dump())


def test_completion_and_catalogue_are_deterministic_data_views() -> None:
    integration = _integration()
    knowledge = IntegrationKnowledgeBase(integrations=[integration])

    summary = knowledge.completion_summary()
    catalogue = knowledge.catalogue()

    assert summary.integration_count == 1
    assert summary.journey_count == 1
    # Steps are records with their own epistemic state and count as such: a
    # journey of six unresolved hand-offs must not read like six exact ones.
    assert summary.step_count == 1
    assert summary.record_count == 14
    assert summary.state_counts == {
        "dynamic": 2,
        "exact": 5,
        "partial": 5,
        "unresolved": 2,
        "unsupported": 1,
    }
    assert summary.structural_issue_count == 0
    assert summary.unknown_count == 1
    assert catalogue[0].id == integration.id
    assert catalogue[0].mapping_count == 1
    assert catalogue[0].participant_labels == ["CRM"]


def test_models_have_no_provider_or_source_code_escape_hatches() -> None:
    prohibited = {"provider", "provider_options", "prompt", "source_code", "code_body"}

    for model in (
        IntegrationKnowledgeBase,
        Integration,
        Journey,
        JourneyStep,
        Mapping,
        Finding,
    ):
        assert prohibited.isdisjoint(model.model_fields)

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Mapping(
            id="mapping-secret",
            label="Unsafe",
            state="exact",
            source_ref="a",
            target_ref="b",
            source_code="secret-bearing source",
        )


# ---------------------------------------------------------------------------
# The ordered journey
# ---------------------------------------------------------------------------


class TestOrderedJourneys:
    def test_a_linear_journey_preserves_its_order(self) -> None:
        journey = _linear_integration().journeys[0]

        assert [step.id for step in journey.ordered_steps] == ["step:a-b", "step:b-c"]
        # Order survives the canonical round trip, where the array itself is
        # sorted by id for byte-stability: `sequence` is what carries it.
        reloaded = Integration.model_validate(
            json.loads(
                IntegrationKnowledgeBase(integrations=[_linear_integration()]).to_json()
            )["integrations"][0]
        )
        assert [step.id for step in reloaded.journeys[0].ordered_steps] == [
            "step:a-b",
            "step:b-c",
        ]
        assert reloaded.journeys[0].endpoints == ("p:a", "p:c")

    def test_a_journey_states_its_endpoints_from_the_steps_not_the_participant_list(
        self,
    ) -> None:
        """The verified defect: direction guessed from ``participant_ids``.

        ``topology._extraction_from_integration_model`` read
        ``participant_ids[0]`` and ``participant_ids[-1]``, so the journey's
        direction was whatever order the agent happened to list them in.
        """

        journey = _linear_integration().journeys[0]
        journey.participant_ids = ["p:c", "p:b", "p:a"]

        assert journey.endpoints == ("p:a", "p:c")

    def test_a_step_is_typed_by_what_it_does(self) -> None:
        """A trigger has no source and a transform has no destination.

        The endpoint pair cannot express either, which is why ``kind`` is the
        step's identity and both participants are optional. A schema that
        demanded endpoints here would be asking for invented ones.
        """

        journey = Journey(
            id="journey:export",
            label="Item master export",
            state="partial",
            steps=[
                JourneyStep(id="trigger", sequence=1, kind="trigger", unit_ids=["u:scheduler"]),
                JourneyStep(
                    id="transform", sequence=2, kind="transform", mapping_ids=["m:item"]
                ),
            ],
        )

        assert [step.kind for step in journey.ordered_steps] == ["trigger", "transform"]
        assert journey.ordered_steps[0].target_participant_id is None
        # An unlisted role is KEPT: dropping the step would lose evidence the
        # agent established, which is worse than a role this module has not
        # heard of.
        assert "enrich" not in JOURNEY_STEP_KINDS
        assert JourneyStep(id="s", kind=" Enrich ").kind == "enrich"
        assert JourneyStep(id="s", kind="").kind == "unknown"

    def test_the_reverse_direction_is_its_own_journey(self) -> None:
        """Opposite directions are two journeys, never a flag on one."""

        integration = _linear_integration()
        integration.upsert(
            "journeys",
            Journey(
                id="journey:acknowledgement",
                label="C to A",
                state="exact",
                steps=[
                    JourneyStep(
                        id="step:c-a",
                        sequence=1,
                        kind="acknowledge",
                        state="exact",
                        source_participant_id="p:c",
                        target_participant_id="p:a",
                        unit_ids=["u:deliver"],
                    )
                ],
            ),
        )

        forward = {journey.id: journey for journey in integration.journeys}["journey:forward"]
        reverse = {journey.id: journey for journey in integration.journeys}[
            "journey:acknowledgement"
        ]
        assert forward.endpoints == ("p:a", "p:c")
        assert reverse.endpoints == ("p:c", "p:a")
        assert IntegrationKnowledgeBase(integrations=[integration]).validate_structure() == []
        # No field on the model can express "this journey also runs backwards".
        assert "direction" not in Journey.model_fields

    def test_fan_out_is_a_decision_step_with_transitions(self) -> None:
        integration = _linear_integration()
        integration.participants.append(Participant(id="p:d", label="D", state="exact"))
        journey = integration.journeys[0]
        journey.steps = [
            journey.steps[0],
            JourneyStep(
                id="step:route",
                sequence=2,
                kind="decision",
                state="exact",
                summary="Records are routed by their status.",
                transitions=[
                    JourneyTransition(condition="accepted", target_step_id="step:b-c"),
                    JourneyTransition(condition="rejected", target_step_id="step:b-d"),
                ],
            ),
            JourneyStep(
                id="step:b-c",
                sequence=3,
                kind="deliver",
                state="exact",
                source_participant_id="p:b",
                target_participant_id="p:c",
                unit_ids=["u:deliver"],
            ),
            JourneyStep(
                id="step:b-d",
                sequence=3,
                kind="error",
                state="exact",
                source_participant_id="p:b",
                target_participant_id="p:d",
                unit_ids=["u:deliver"],
            ),
        ]

        decision = {step.id: step for step in journey.steps}["step:route"]
        assert decision.target_step_ids == ["step:b-c", "step:b-d"]
        # Two steps at one sequence is a FORK, not an ambiguity: refusing it
        # would make fan-out unrepresentable.
        assert [step.id for step in journey.ordered_steps] == [
            "step:a-b",
            "step:route",
            "step:b-c",
            "step:b-d",
        ]
        assert IntegrationKnowledgeBase(integrations=[integration]).validate_structure() == []

    def test_fan_in_is_two_transitions_naming_one_step(self) -> None:
        integration = _linear_integration()
        integration.participants.append(Participant(id="p:d", label="D", state="exact"))
        journey = integration.journeys[0]
        journey.steps = [
            JourneyStep(
                id="step:a-b",
                sequence=1,
                kind="extract",
                state="exact",
                source_participant_id="p:a",
                target_participant_id="p:b",
                transitions=[JourneyTransition(condition="always", target_step_id="step:b-c")],
            ),
            JourneyStep(
                id="step:d-b",
                sequence=1,
                kind="extract",
                state="exact",
                source_participant_id="p:d",
                target_participant_id="p:b",
                transitions=[JourneyTransition(condition="always", target_step_id="step:b-c")],
            ),
            JourneyStep(
                id="step:b-c",
                sequence=2,
                kind="deliver",
                state="exact",
                source_participant_id="p:b",
                target_participant_id="p:c",
                unit_ids=["u:deliver"],
            ),
        ]

        merging = [
            step.id
            for step in journey.ordered_steps
            if "step:b-c" in step.target_step_ids
        ]
        assert merging == ["step:a-b", "step:d-b"]
        assert IntegrationKnowledgeBase(integrations=[integration]).validate_structure() == []

    def test_units_mappings_and_sql_attach_to_the_step_that_runs_them(self) -> None:
        journey = _linear_integration().journeys[0]
        first, second = journey.ordered_steps

        assert first.unit_ids == ["u:transform"]
        assert first.mapping_ids == ["m:ab"]
        assert first.sql_refs == ["a-selection-query"]
        assert second.unit_ids == ["u:transform", "u:deliver"]
        assert second.mapping_ids == ["m:bc"]
        # The journey-level view is DERIVED: a shared unit appears once, in
        # step order, and cannot be recorded separately and drift.
        assert journey.implementing_unit_ids == ["u:transform", "u:deliver"]
        assert journey.mapping_ids == ["m:ab", "m:bc"]
        assert journey.sql_refs == ["a-selection-query"]
        for derived in ("implementing_unit_ids", "mapping_ids", "interface_ids", "sql_refs"):
            assert derived not in Journey.model_fields, derived
        with pytest.raises((AttributeError, ValueError)):
            journey.implementing_unit_ids = ["u:deliver"]

    def test_a_missing_intermediate_participant_is_marked_not_dropped(self) -> None:
        """"We could not name this end" is a deliverable, not a reason to lie.

        Dropping the step would publish ``A -> C``, which is a shorter journey
        that is simply wrong.
        """

        integration = _linear_integration()
        journey = integration.journeys[0]
        journey.steps = [
            JourneyStep(
                id="step:a-unknown",
                sequence=1,
                kind="extract",
                state="unresolved",
                summary="The records leave A; the staging system could not be named.",
                source_participant_id="p:a",
                target_participant_id=None,
                unit_ids=["u:transform"],
            ),
            JourneyStep(
                id="step:unknown-c",
                sequence=2,
                kind="deliver",
                state="unresolved",
                source_participant_id=None,
                target_participant_id="p:c",
                unit_ids=["u:deliver"],
            ),
        ]

        assert len(journey.ordered_steps) == 2
        assert journey.endpoints == ("p:a", "p:c")
        # A null endpoint is not a dangling reference.
        assert IntegrationKnowledgeBase(integrations=[integration]).validate_structure() == []
        assert (
            IntegrationKnowledgeBase(integrations=[integration]).completion_summary().state_counts[
                "unresolved"
            ]
            == 2
        )

    def test_unknown_ordering_is_representable_and_visible(self) -> None:
        """PRD 11.4. A step whose position could not be established stays.

        A required ``sequence`` would leave the agent two options: invent a
        position, or drop a step it has evidence for. Both are worse than
        saying so.
        """

        integration = _linear_integration()
        journey = integration.journeys[0]
        journey.steps = [
            *journey.steps,
            JourneyStep(
                id="step:unplaced",
                kind="transform",
                state="unresolved",
                summary="A transformation runs somewhere in this journey.",
                unit_ids=["u:deliver"],
            ),
        ]

        assert journey.ordered_steps[-1].id == "step:unplaced"
        assert [step.id for step in journey.unordered_steps] == ["step:unplaced"]
        assert IntegrationKnowledgeBase(integrations=[integration]).validate_structure() == []

    def test_an_unresolved_or_dynamic_transition_target_is_allowed(self) -> None:
        """Runtime routing has no target step, and must not need an invented one."""

        integration = _linear_integration()
        journey = integration.journeys[0]
        journey.steps[0].transitions = [
            JourneyTransition(
                condition="destination is chosen at runtime from a context variable",
                target_step_id=None,
                state="dynamic",
            )
        ]

        assert IntegrationKnowledgeBase(integrations=[integration]).validate_structure() == []
        # A target that NAMES a step which is not here is still a defect.
        journey.steps[0].transitions = [
            JourneyTransition(condition="valid", target_step_id="step:absent")
        ]
        issues = IntegrationKnowledgeBase(integrations=[integration]).validate_structure()
        assert [issue.code for issue in issues] == ["dangling_reference"]

    def test_one_integration_may_hold_several_journeys(self) -> None:
        integration = _linear_integration()
        integration.upsert(
            "journeys",
            Journey(id="journey:retry", label="Retry", state="partial"),
        )

        assert len(integration.journeys) == 2
        assert IntegrationKnowledgeBase(integrations=[integration]).completion_summary(
        ).journey_count == 2

    def test_a_journey_links_every_document_that_covers_it(self) -> None:
        journey = Journey(
            id="journey:orders",
            label="Orders",
            state="exact",
            # The agent writes to `output/`, so it types that prefix; the same
            # file under its published name is not a different document.
            document_paths=["output/integrations/orders.md", "mappings.md"],
        )

        assert journey.document_paths == ["integrations/orders.md", "mappings.md"]
        for refused in ("facts/units.json", "/etc/passwd", "../secrets.md", "notes/progress.md"):
            with pytest.raises(ValidationError):
                Journey(id="j", label="j", state="exact", document_paths=[refused])

    def test_a_repeated_step_id_is_refused(self) -> None:
        """Transitions point at step ids, so a repeat makes routing undecidable.

        The same class of defect as two records sharing an id, and refused the
        same way. A repeated SEQUENCE is not refused: that is how a fork is
        expressed.
        """

        with pytest.raises(ValidationError, match="duplicate journey step IDs"):
            Journey(
                id="journey:x",
                label="x",
                state="exact",
                steps=[
                    JourneyStep(id="step:1", sequence=1),
                    JourneyStep(id="step:1", sequence=2),
                ],
            )

    def test_a_self_referencing_transition_is_reported(self) -> None:
        integration = _linear_integration()
        journey = integration.journeys[0]
        journey.steps = [
            JourneyStep(
                id="step:one",
                sequence=1,
                transitions=[JourneyTransition(condition="retry", target_step_id="step:one")],
            ),
            JourneyStep(
                id="step:two",
                sequence=2,
                transitions=[JourneyTransition(condition="next", target_step_id="step:absent")],
            ),
        ]

        codes = sorted(
            issue.code
            for issue in IntegrationKnowledgeBase(integrations=[integration]).validate_structure()
        )
        assert codes == ["dangling_reference", "self_referencing_step"]

    def test_a_step_merges_by_id_like_every_other_record(self) -> None:
        integration = _linear_integration()
        integration.upsert(
            "journeys",
            Journey(
                id="journey:forward",
                label="A to C",
                state="exact",
                steps=[
                    JourneyStep(
                        id="step:a-b",
                        sequence=1,
                        label="Transform and publish",
                        state="exact",
                    )
                ],
            ),
        )

        first = integration.journeys[0].ordered_steps[0]
        assert first.label == "Transform and publish"
        # The merge improved the label without erasing the work the step
        # already carried.
        assert first.unit_ids == ["u:transform"]
        assert first.mapping_ids == ["m:ab"]


# ---------------------------------------------------------------------------
# Reading a persisted model: loud, partial, and version-bound
# ---------------------------------------------------------------------------


class TestLoadingAPersistedModel:
    def _write(self, tmp_path: Path, payload: object) -> Path:
        path = tmp_path / "integration_model.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_the_schema_version_moved_with_the_journey_change(self) -> None:
        assert SCHEMA_VERSION == "integration-knowledge-v2"
        assert IntegrationKnowledgeBase().schema_version == SCHEMA_VERSION

    def test_a_file_from_the_previous_schema_is_refused_by_name(self, tmp_path: Path) -> None:
        """An in-flight run's v1 file must be a clean restart, not a crash.

        Half-reading it would publish every journey with no path at all, which
        looks like an investigation that established nothing.
        """

        path = self._write(
            tmp_path,
            {
                "schema_version": "integration-knowledge-v1",
                "integrations": [
                    {
                        "id": "integration:orders",
                        "label": "Orders",
                        "state": "exact",
                        "journeys": [
                            {
                                "id": "journey:orders",
                                "label": "Orders",
                                "state": "exact",
                                "implementing_unit_ids": ["u:1"],
                            }
                        ],
                    }
                ],
            },
        )

        with pytest.raises(IntegrationModelError) as excinfo:
            IntegrationKnowledgeBase.load(path)
        assert "integration-knowledge-v1" in str(excinfo.value)
        assert SCHEMA_VERSION in str(excinfo.value)

    @pytest.mark.parametrize(
        "payload,fragment",
        [
            ("{not json", "not valid JSON"),
            ('["a list"]', "not a JSON object"),
            ('{"integrations": {}}', "non-list"),
        ],
    )
    def test_a_whole_file_defect_raises_a_typed_error(
        self, tmp_path: Path, payload: str, fragment: str
    ) -> None:
        path = tmp_path / "integration_model.json"
        path.write_text(payload, encoding="utf-8")

        with pytest.raises(IntegrationModelError, match=fragment):
            IntegrationKnowledgeBase.load(path)

    def test_one_bad_record_costs_that_record_and_nothing_else(self, tmp_path: Path) -> None:
        """Partial acceptance. The previous loader lost the whole file.

        ``model_validate_json`` over the whole document meant one malformed
        journey discarded an entire investigation's accumulated model, and the
        only trace was a ``ValidationError`` type name rendered into a
        CLIENT-FACING limitations document by ``registers.py:775``.
        """

        good = json.loads(IntegrationKnowledgeBase(integrations=[_linear_integration()]).to_json())
        good["integrations"][0]["journeys"].append(
            {"id": "journey:broken", "label": "Broken", "state": "not-a-state"}
        )
        good["integrations"][0]["participants"].append({"id": "p:e", "state": "exact"})
        good["integrations"][0]["journeys"][0]["steps"].append(
            {"id": "step:broken", "sequence": "second"}
        )
        good["integrations"].append({"id": "integration:bad", "label": "Bad", "state": "???"})
        path = self._write(tmp_path, good)

        report = load_report(path)

        assert [item.id for item in report.knowledge.integrations] == ["integration:orders"]
        journey = report.knowledge.integrations[0].journeys[0]
        assert [step.id for step in journey.ordered_steps] == ["step:a-b", "step:b-c"]
        assert len(report.knowledge.integrations[0].participants) == 3
        assert len(report.rejected) == 4
        reasons = " | ".join(item.describe() for item in report.rejected)
        assert "journeys[1]" in reasons and "'journey:broken'" in reasons
        assert "steps[2]" in reasons and "sequence" in reasons
        assert "participants[3]" in reasons and "label" in reasons
        assert "integrations[1]" in reasons

    def test_a_rejection_never_repeats_the_offending_value(self, tmp_path: Path) -> None:
        """The reason reaches a log line; the value came out of a client estate."""

        path = self._write(
            tmp_path,
            {
                "schema_version": SCHEMA_VERSION,
                "integrations": [
                    {
                        "id": "integration:x",
                        "label": "X",
                        "state": "exact",
                        "participants": [
                            {
                                "id": "p:secret",
                                "label": "P",
                                "state": "exact",
                                "role": "source",
                                "password": "hunter2-from-the-estate",
                            }
                        ],
                    }
                ],
            },
        )

        report = load_report(path)

        assert len(report.rejected) == 1
        assert "hunter2-from-the-estate" not in report.rejected[0].describe()
        assert "password" in report.rejected[0].describe()

    def test_dropping_a_record_is_loud(self, tmp_path: Path, caplog) -> None:
        path = self._write(
            tmp_path,
            {
                "schema_version": SCHEMA_VERSION,
                "integrations": [{"id": "i", "label": "I", "state": "nope"}],
            },
        )

        with caplog.at_level(logging.WARNING):
            knowledge = IntegrationKnowledgeBase.load(path)

        assert knowledge.integrations == []
        assert "dropped 1 unusable record" in caplog.text
        assert "integrations[0]" in caplog.text

    def test_a_duplicate_id_drops_the_repeat_rather_than_the_file(self, tmp_path: Path) -> None:
        integration = json.loads(
            IntegrationKnowledgeBase(integrations=[_linear_integration()]).to_json()
        )["integrations"][0]
        path = self._write(
            tmp_path,
            {"schema_version": SCHEMA_VERSION, "integrations": [integration, integration]},
        )

        report = load_report(path)

        assert len(report.knowledge.integrations) == 1
        assert "duplicate integration id" in report.rejected[0].reason


# ---------------------------------------------------------------------------
# One file per integration
#
# Every test in this section exists because of ONE verified run. The agent
# hand-wrote 15 KB of nested JSON into `notes/integration_model.json`, omitted
# a single `"steps": [` on line 17, and `json.loads` died at character 1952 --
# 5 integrations and 6 journeys became unreadable. Per-item partial acceptance
# did not help, because it only runs once `json.loads` has succeeded on the
# WHOLE file, and the agent has no JSON parser in its sandbox so it could not
# have known. The directory bounds the damage to one record; the tests below
# pin that bound, not the happy path.
# ---------------------------------------------------------------------------


#: The verified shape, reduced: the `"steps": [` line is missing, so the step
#: objects dangle inside the journey and the `]` has nothing to close. Written
#: as a literal rather than generated, because a generated defect tends to be
#: the defect the generator finds easy rather than the one that happened.
_MISSING_BRACKET = """{
  "id": "integration:billing",
  "label": "Billing extract",
  "state": "exact",
  "journeys": [
    {
      "id": "journey:billing",
      "label": "Billing extract",
      "state": "exact",
        {"id": "step:one", "sequence": 1, "kind": "deliver"}
      ]
    }
  ]
}
"""


def _integration_text(integration_id: str, label: str) -> str:
    """One integration object, as the agent writes one: not wrapped."""

    return json.dumps(
        {
            "id": integration_id,
            "label": label,
            "state": "exact",
            "participants": [
                {"id": f"{integration_id}/source", "label": "Source", "state": "exact"}
            ],
        },
        indent=2,
    )


class TestLoadingAWorkspaceModel:
    def _monolith(self, staging: Path, *integrations: Integration) -> Path:
        path = staging / contract.INTEGRATION_MODEL
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            IntegrationKnowledgeBase(integrations=list(integrations)).to_json(), encoding="utf-8"
        )
        return path

    def _damaged_monolith(self, staging: Path, *objects: str) -> Path:
        """A monolithic file whose ROOT never closes but whose records do.

        This is the run-252 class of damage from the outside: the integration
        objects are individually intact and the document around them is not.
        """

        path = staging / contract.INTEGRATION_MODEL
        path.parent.mkdir(parents=True, exist_ok=True)
        body = ",\n".join(objects)
        path.write_text(
            f'{{\n  "schema_version": "{SCHEMA_VERSION}",\n  "integrations": [\n{body}\n',
            encoding="utf-8",
        )
        return path

    def _file(self, staging: Path, name: str, text: str) -> Path:
        path = staging / contract.INTEGRATION_DIR / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_one_broken_file_costs_that_file_and_nothing_else(self, tmp_path: Path) -> None:
        """The whole point. One bracket used to cost the investigation."""

        self._file(tmp_path, "a_orders.json", _integration_text("integration:orders", "Order sync"))
        self._file(tmp_path, "b_billing.json", _MISSING_BRACKET)
        self._file(
            tmp_path, "c_customers.json", _integration_text("integration:customers", "Load customer")
        )

        report = load_workspace_model(tmp_path)

        assert [item.id for item in report.knowledge.integrations] == [
            "integration:customers",
            "integration:orders",
        ]
        assert len(report.rejected) == 1
        rejection = report.rejected[0]
        assert rejection.path == "notes/integrations/b_billing.json"
        assert rejection.kind == "file"
        assert "not valid JSON" in rejection.reason and "line" in rejection.reason

    def test_a_file_level_reason_never_repeats_the_file_contents(self, tmp_path: Path) -> None:
        """A damaged file's reason reaches a log; its bytes are a client estate."""

        self._file(
            tmp_path,
            "leaky.json",
            '{"id": "integration:x", "label": "X", "secret": "hunter2-from-the-estate"',
        )

        report = load_workspace_model(tmp_path)

        assert report.knowledge.integrations == []
        assert len(report.rejected) == 1
        assert "hunter2-from-the-estate" not in report.rejected[0].describe()

    def test_the_monolithic_file_still_loads_beside_the_directory(self, tmp_path: Path) -> None:
        """A resume must not lose an older run's record to the newer shape."""

        self._monolith(tmp_path, _linear_integration())
        self._file(tmp_path, "billing.json", _integration_text("integration:billing", "Billing"))

        report = load_workspace_model(tmp_path)

        assert [item.id for item in report.knowledge.integrations] == [
            "integration:billing",
            "integration:orders",
        ]
        assert report.rejected == ()
        assert report.salvaged is None

    def test_the_directory_wins_and_the_override_is_recorded(self, tmp_path: Path) -> None:
        """An id resolving to one of two records silently is how a document
        ends up citing a journey that is not in the model."""

        self._monolith(
            tmp_path, Integration(id="integration:orders", label="Stale", state="partial")
        )
        self._file(tmp_path, "orders.json", _integration_text("integration:orders", "Current"))

        report = load_workspace_model(tmp_path)

        assert [item.label for item in report.knowledge.integrations] == ["Current"]
        assert len(report.rejected) == 1
        assert report.rejected[0].path == "notes/integration_model.json"
        assert report.rejected[0].label == "Stale"
        assert "superseded by notes/integrations/orders.json" in report.rejected[0].reason

    def test_within_the_directory_the_last_filename_wins(self, tmp_path: Path) -> None:
        """Sorted by filename, so two loads of one workspace agree."""

        self._file(tmp_path, "a_orders.json", _integration_text("integration:orders", "First"))
        self._file(tmp_path, "z_orders.json", _integration_text("integration:orders", "Last"))

        report = load_workspace_model(tmp_path)

        assert [item.label for item in report.knowledge.integrations] == ["Last"]
        assert report.rejected[0].path == "notes/integrations/a_orders.json"
        assert "superseded by notes/integrations/z_orders.json" in report.rejected[0].reason

    def test_an_absent_directory_is_the_ordinary_case(self, tmp_path: Path) -> None:
        empty = load_workspace_model(tmp_path)

        assert empty.knowledge.integrations == []
        assert empty.rejected == ()
        assert empty.salvaged is None

        self._monolith(tmp_path, _linear_integration())
        (tmp_path / contract.INTEGRATION_DIR).mkdir(parents=True)

        report = load_workspace_model(tmp_path)

        assert [item.id for item in report.knowledge.integrations] == ["integration:orders"]
        assert report.rejected == ()

    def test_a_salvaged_monolith_still_reports_its_notice(self, tmp_path: Path) -> None:
        """Salvage is unchanged and its warning still reaches the operator: a
        salvaged model is a model with an unknown hole in it."""

        self._damaged_monolith(
            tmp_path,
            _integration_text("integration:orders", "Order sync"),
            _integration_text("integration:customers", "Load customer"),
        )
        self._file(tmp_path, "billing.json", _integration_text("integration:billing", "Billing"))

        report = load_workspace_model(tmp_path)

        assert [item.id for item in report.knowledge.integrations] == [
            "integration:billing",
            "integration:customers",
            "integration:orders",
        ]
        assert report.salvaged is not None
        assert "2 integration object(s) were recovered" in report.salvaged
        assert report.rejected == ()

    def test_an_unreadable_monolith_does_not_take_down_the_directory(
        self, tmp_path: Path
    ) -> None:
        """The failure this change exists to end, one level up: a broken
        neighbour must not cost the records that are perfectly readable."""

        path = tmp_path / contract.INTEGRATION_MODEL
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json at all", encoding="utf-8")
        self._file(tmp_path, "orders.json", _integration_text("integration:orders", "Order sync"))

        report = load_workspace_model(tmp_path)

        assert [item.id for item in report.knowledge.integrations] == ["integration:orders"]
        assert len(report.rejected) == 1
        assert report.rejected[0].path == "notes/integration_model.json"
        assert report.rejected[0].kind == "file"

    def test_partial_acceptance_still_applies_inside_one_file(self, tmp_path: Path) -> None:
        """Per-item acceptance did not go away; it now runs per file as well."""

        payload = json.loads(_integration_text("integration:orders", "Order sync"))
        payload["journeys"] = [
            {
                "id": "journey:forward",
                "label": "Forward",
                "state": "exact",
                "steps": [
                    {"id": "step:ok", "sequence": 1, "kind": "deliver"},
                    {"id": "step:broken", "sequence": "second"},
                ],
            },
            {"id": "journey:broken", "label": "Broken", "state": "not-a-state"},
        ]
        self._file(tmp_path, "orders.json", json.dumps(payload))

        report = load_workspace_model(tmp_path)

        integration = report.knowledge.integrations[0]
        assert [step.id for step in integration.journeys[0].steps] == ["step:ok"]
        assert len(integration.journeys) == 1
        assert len(report.rejected) == 2
        reasons = " | ".join(item.describe() for item in report.rejected)
        assert "notes/integrations/orders.json/journeys" in reasons

    @pytest.mark.parametrize(
        "text,fragment",
        [
            ('"just a sentence"', "not a JSON object"),
            ("42", "not a JSON object"),
            ('{"schema_version": "integration-knowledge-v1", "integrations": []}', "v1"),
        ],
    )
    def test_a_file_that_cannot_be_an_integration_is_rejected_by_name(
        self, tmp_path: Path, text: str, fragment: str
    ) -> None:
        self._file(tmp_path, "wrong.json", text)

        report = load_workspace_model(tmp_path)

        assert report.knowledge.integrations == []
        assert len(report.rejected) == 1
        assert report.rejected[0].path == "notes/integrations/wrong.json"
        assert fragment in report.rejected[0].reason

    @pytest.mark.parametrize("wrapped", [True, False])
    def test_the_old_wrapper_in_a_new_file_is_tolerated(
        self, tmp_path: Path, wrapped: bool
    ) -> None:
        """The agent is taught this format while the monolithic one is still in
        front of it, so writing the wrapper into a new file is the predictable
        mistake -- and refusing it would lose readable records."""

        objects = [
            json.loads(_integration_text("integration:orders", "Order sync")),
            json.loads(_integration_text("integration:billing", "Billing")),
        ]
        payload: object = (
            {"schema_version": SCHEMA_VERSION, "integrations": objects} if wrapped else objects
        )
        self._file(tmp_path, "everything.json", json.dumps(payload))

        report = load_workspace_model(tmp_path)

        assert [item.id for item in report.knowledge.integrations] == [
            "integration:billing",
            "integration:orders",
        ]
        assert report.rejected == ()

    def test_a_half_written_checkpoint_is_not_a_record(self, tmp_path: Path) -> None:
        """``save`` writes ``.<name>.tmp`` beside its target; a dot-prefixed
        name is a write in progress, not an integration."""

        self._file(tmp_path, ".orders.json", _integration_text("integration:orders", "Half"))

        report = load_workspace_model(tmp_path)

        assert report.knowledge.integrations == []
        assert report.rejected == ()

    def test_the_helper_names_a_file_this_loader_reads(self, tmp_path: Path) -> None:
        """The round trip: what the agent is taught to write is what is read."""

        integration = _linear_integration()
        self._file(
            tmp_path,
            integration_filename(integration.id),
            json.dumps(
                json.loads(IntegrationKnowledgeBase(integrations=[integration]).to_json())[
                    "integrations"
                ][0]
            ),
        )

        report = load_workspace_model(tmp_path)

        assert [item.id for item in report.knowledge.integrations] == ["integration:orders"]
        journey = report.knowledge.integrations[0].journeys[0]
        assert [step.id for step in journey.ordered_steps] == ["step:a-b", "step:b-c"]


class TestIntegrationFilename:
    @pytest.mark.parametrize(
        "integration_id",
        [
            "../../etc/passwd",
            "/etc/passwd",
            "..",
            ".",
            ".hidden",
            "a/b",
            "a\\b",
            "orders/../../..",
            "",
            "\u00e9\u00e7\u00e1",  # escaped: this test file is ASCII-only
            "x" * 500,
            "integration:orders",
        ],
    )
    def test_an_id_can_never_escape_the_directory(
        self, tmp_path: Path, integration_id: str
    ) -> None:
        """A NAME, never a path. The id is agent-written and may be anything."""

        name = integration_filename(integration_id)

        assert name.endswith(".json")
        assert name.isascii()
        assert "/" not in name and "\\" not in name
        assert not name.startswith(".")
        assert Path(name).name == name
        assert len(name) < 120
        directory = tmp_path / "integrations"
        directory.mkdir()
        target = directory / name
        target.write_text("{}", encoding="utf-8")
        assert target.resolve().parent == directory.resolve()

    def test_the_same_id_always_names_the_same_file(self) -> None:
        """So a rewritten record lands on top of itself, not beside it."""

        assert integration_filename("integration:orders") == integration_filename(
            "integration:orders"
        )
        assert integration_filename("integration:orders") == "integration_orders.json"

    def test_ids_with_no_usable_characters_still_get_distinct_files(self) -> None:
        """Collapsing them onto one name would silently merge two integrations."""

        names = {
            integration_filename(""),
            integration_filename("\u00e9\u00e7\u00e1"),
            integration_filename("\u4e00\u4e8c"),
            integration_filename("///"),
        }

        assert len(names) == 4

    def test_a_long_id_is_bounded_and_still_distinct(self) -> None:
        first = integration_filename("integration:" + "a" * 400 + ":orders")
        second = integration_filename("integration:" + "a" * 400 + ":billing")

        assert first != second
        assert len(first) < 120 and len(second) < 120

    def test_a_stable_id_survives_unchanged(self) -> None:
        """Ids minted by ``stable_id`` are already filename-safe, so the common
        case is readable and cannot collide."""

        identifier = stable_id("integration", "project-a", "order-sync")

        assert integration_filename(identifier) == f"{identifier}.json"
