"""The topology capability on the live harness chat path.

Wave 1 built and tested the tools and their dispatcher. What is under test here
is only the seam: that the capability is registered and model-visible, that the
turn's server-owned identity reaches the dispatcher, that a client-supplied page
scope grants nothing, and that navigation targets reach the client verbatim
through both transports.
"""

from __future__ import annotations

import json

import pytest
from jsonschema import Draft202012Validator
from langchain_core.messages import ToolMessage

from src.harness.agent_chat_runtime import (
    PrimaryTurnOutcome,
    _chat_denied_capability_ids,
    _turn_prompt_context,
    grounded_structured_answer,
)
from src.harness.agent_runs import sanitize_public_payload
from src.harness.capability_runtime import (
    CapabilityExecutionContext,
    CapabilityExecutionRequest,
)
from src.harness.cohort import CohortDecision, RuntimeCohort
from src.harness.executor import HarnessCapabilityExecutor
from src.harness.grounding import AnswerStatus, GroundedAnswer, GroundingValidator
from src.harness.manifest import ManifestScope, build_runtime_capability_manifest
from src.harness.readonly_runtime import (
    _collect_topology_targets,
    _emit_tool_event,
    _humanize_visible_content,
)
from src.harness.registry import base_capability_registry, default_capability_registry
from src.harness.semantic_tools import (
    SemanticSnapshotError,
    _require_closed_objects,
    _semantic_description,
    build_semantic_tool_snapshot,
    project_capability_observation,
    serialize_tool_envelope,
)
from src.harness.topology_capability import (
    TOPOLOGY_OPERATION_GUIDE,
    TOPOLOGY_OPERATIONS,
    TOPOLOGY_STRUCTURE_CAPABILITY,
    topology_targets,
)
from src.harness.topology_guidance import (
    describe_page_scope,
    topology_capability_enabled,
    topology_turn_guidance,
)
from src.runtime.models import (
    RuntimeToolError,
    RuntimeToolErrorCode,
    RuntimeToolResult,
)
from src.schemas import MessageResponse
from src.topology import provider as provider_module
from tests.test_topology.conftest import json_transport, load_fixture, make_provider


def _is_flat_selection(target: dict) -> bool:
    """Whether a target's selection carries only scalar ids.

    `relationship` is the one variant with a nested list (`streamKeys`), which is
    what makes it the only variant the envelope depth limits can damage.
    """
    selection = target.get("level", {}).get("selection")
    if not isinstance(selection, dict):
        return False
    return all(not isinstance(value, (list, dict)) for value in selection.values())


CAPABILITY_ID = TOPOLOGY_STRUCTURE_CAPABILITY.id
SEMANTIC_NAME = "read_topology_structure"

# Shape mirrors the frontend `TopologyLevel` union exactly, including its
# camelCase keys and its id-vs-name split. Nested six levels deep once wrapped
# in the dispatcher's `data`, which is what every bound in the harness has to
# carry without rewriting it.
TARGET = {
    "label": "SAP ERP",
    "level": {
        "kind": "integration",
        "selection": {"kind": "system", "systemId": "landscape-system:sap-erp"},
    },
}
EDGE_TARGET = {
    "label": "Maximo -> SAP ERP",
    "level": {
        "kind": "integration",
        "selection": {
            "kind": "edge",
            "edgeKey": "landscape-system:maximo|landscape-system:sap-erp",
        },
    },
}


def _success(tool_name: str = "get_topology_overview") -> RuntimeToolResult:
    return RuntimeToolResult(
        tool_name=tool_name,
        success=True,
        data={
            "content": {
                "project_id": "project-a",
                "active_view": "business",
                "topology_state": {"available": True, "validated": True, "stale": False},
            },
            "targets": [TARGET, EDGE_TARGET],
        },
        latency_ms=7,
    )


def _failure(code: RuntimeToolErrorCode, message: str) -> RuntimeToolResult:
    return RuntimeToolResult(
        tool_name="get_topology_overview",
        success=False,
        data=None,
        error=RuntimeToolError(code=code, message=message),
        latency_ms=3,
    )


@pytest.fixture
def dispatched(monkeypatch):
    """Replace the dispatcher with a recorder and hand the test its call log."""

    calls: list[dict] = []
    result_box: dict[str, RuntimeToolResult] = {"result": _success()}

    async def execute_topology_tool(call, *, project_scope, access_token):  # noqa: ANN001
        calls.append(
            {
                "tool_name": call.tool_name,
                "arguments": dict(call.arguments),
                "project_scope": project_scope,
                "access_token": access_token,
            }
        )
        return result_box["result"]

    monkeypatch.setattr(
        "src.harness.topology_capability.execute_topology_tool",
        execute_topology_tool,
    )
    return calls, result_box


def _executor() -> HarnessCapabilityExecutor:
    return HarnessCapabilityExecutor(base_capability_registry())


async def _execute(arguments: dict, **context_kwargs) -> object:
    context = CapabilityExecutionContext(user_id=1, **context_kwargs)
    return await _executor().execute(
        CapabilityExecutionRequest(capability_id=CAPABILITY_ID, arguments=arguments),
        context,
    )


class TestRegistration:
    def test_the_capability_is_registered_read_only_and_pack_visible(self) -> None:
        registry = default_capability_registry()

        capability = registry.get(CAPABILITY_ID)
        assert capability.risk_tier.value == "read_only"
        # An executable handler must exist, or the manifest validation that runs
        # on every production turn fails closed.
        assert registry.handler_for(CAPABILITY_ID) is not None

    def test_the_capability_is_visible_to_the_runtime_manifest(self) -> None:
        manifest = build_runtime_capability_manifest(
            default_capability_registry(),
            scope=ManifestScope(include_bounded_loop_only=True),
        )

        tool = next(tool for tool in manifest.tools if tool.id == CAPABILITY_ID)
        assert tool.risk_tier == "read_only"
        assert tool.evidence_output is True
        assert tool.input_schema["additionalProperties"] is False

    def test_the_model_sees_one_read_governed_semantic_tool(self) -> None:
        snapshot = build_semantic_tool_snapshot(
            default_capability_registry(),
            scope=ManifestScope(include_bounded_loop_only=True),
        )

        record = snapshot.tool_for_name(SEMANTIC_NAME)
        assert record is not None
        assert record.canonical_id == CAPABILITY_ID
        assert record.governance_class.value == "read"
        # One capability with an operation enum, not four competing tools.
        assert [item for item in snapshot.tools if item.canonical_id.startswith("topology.")] == [
            record
        ]

    def test_the_operation_enum_is_exactly_the_dispatcher_allowlist(self) -> None:
        from src.topology.client import TOPOLOGY_TOOL_NAMES

        operations = TOPOLOGY_STRUCTURE_CAPABILITY.input_schema["properties"]["operation"]
        assert set(operations["enum"]) == set(TOPOLOGY_TOOL_NAMES)
        assert set(TOPOLOGY_OPERATIONS) == set(TOPOLOGY_TOOL_NAMES)

    @pytest.mark.parametrize(
        ("arguments", "missing_field"),
        [
            (
                {"operation": "find_topology_node", "project_id": "project-a"},
                "query",
            ),
            (
                {
                    "operation": "list_topology_dependencies",
                    "project_id": "project-a",
                },
                "name_or_id",
            ),
            (
                {"operation": "get_component_detail", "project_id": "project-a"},
                "job",
            ),
        ],
    )
    def test_operation_specific_required_fields_are_in_the_semantic_schema(
        self, arguments, missing_field
    ) -> None:
        errors = list(
            Draft202012Validator(
                TOPOLOGY_STRUCTURE_CAPABILITY.input_schema
            ).iter_errors(arguments)
        )

        assert any(missing_field in error.message for error in errors)


class TestOperationRouting:
    @pytest.mark.parametrize(
        ("operation", "extra"),
        [
            ("get_topology_overview", {}),
            ("find_topology_node", {"query": "sap"}),
            ("get_topology_node", {"name_or_id": "SAP ERP"}),
            (
                "list_topology_dependencies",
                {"name_or_id": "SAP ERP", "direction": "upstream", "hops": 2},
            ),
        ],
    )
    async def test_each_operation_routes_to_its_own_tool_name(
        self, dispatched, operation, extra
    ) -> None:
        calls, _ = dispatched

        observation = await _execute(
            {"operation": operation, "project_id": "project-a", **extra},
            project_scope=["project-a"],
        )

        assert observation.status == "succeeded"
        assert [call["tool_name"] for call in calls] == [operation]
        # `operation` selects the tool and must not be forwarded as an argument.
        assert calls[0]["arguments"] == {"project_id": "project-a", **extra}

    async def test_unset_optional_arguments_are_not_forwarded(self, dispatched) -> None:
        calls, _ = dispatched

        await _execute(
            {
                "operation": "get_topology_node",
                "project_id": "project-a",
                "name_or_id": "SAP ERP",
                "direction": None,
                "query": None,
            },
            project_scope=["project-a"],
        )

        assert calls[0]["arguments"] == {"project_id": "project-a", "name_or_id": "SAP ERP"}

    async def test_an_unknown_operation_is_rejected_before_the_dispatcher(self, dispatched) -> None:
        calls, _ = dispatched

        observation = await _execute(
            {"operation": "drop_topology", "project_id": "project-a"},
            project_scope=["project-a"],
        )

        assert observation.status == "needs_input"
        assert observation.error_code == "validation"
        assert calls == []

    async def test_a_missing_project_id_is_rejected_before_the_dispatcher(self, dispatched) -> None:
        calls, _ = dispatched

        observation = await _execute(
            {"operation": "get_topology_overview"}, project_scope=["project-a"]
        )

        assert observation.status == "needs_input"
        assert calls == []


class TestIdentityAndScope:
    async def test_the_turn_access_token_reaches_the_dispatcher(self, dispatched) -> None:
        """Fails if the forward is dropped: the token is the upstream authority."""
        calls, _ = dispatched

        await _execute(
            {"operation": "get_topology_overview", "project_id": "project-a"},
            project_scope=["project-a"],
            access_token="turn-token",
        )

        assert calls[0]["access_token"] == "turn-token"

    async def test_no_token_on_the_turn_forwards_none_rather_than_a_placeholder(
        self, dispatched
    ) -> None:
        calls, _ = dispatched

        await _execute(
            {"operation": "get_topology_overview", "project_id": "project-a"},
            project_scope=["project-a"],
        )

        assert calls[0]["access_token"] is None

    async def test_project_scope_comes_from_the_request_not_the_arguments(self, dispatched) -> None:
        calls, _ = dispatched

        await _execute(
            {"operation": "get_topology_overview", "project_id": "project-a"},
            project_scope=["project-a", "project-b"],
        )

        # The authenticated allow-list, verbatim -- not the single project the
        # model named, and not a value the model could have supplied.
        assert calls[0]["project_scope"] == ["project-a", "project-b"]

    async def test_an_out_of_scope_project_never_reaches_the_dispatcher(self, dispatched) -> None:
        calls, _ = dispatched

        observation = await _execute(
            {"operation": "get_topology_overview", "project_id": "project-b"},
            project_scope=["project-a"],
            access_token="turn-token",
        )

        assert observation.status == "denied"
        assert calls == []
        # Scope-uniform wording: it says nothing about whether project-b exists.
        assert "project-b" not in (observation.error_message or "")

    async def test_the_model_cannot_widen_scope_with_an_extra_argument(self, dispatched) -> None:
        calls, _ = dispatched

        observation = await _execute(
            {
                "operation": "get_topology_overview",
                "project_id": "project-a",
                "project_scope": ["project-b"],
            },
            project_scope=["project-a"],
        )

        # additionalProperties: False -- an invented identity field is a schema
        # violation, not something the handler has to defend against later.
        assert observation.status == "needs_input"
        assert calls == []


class TestPageScopeIsUntrusted:
    PAGE_SCOPE = {
        "project_id": "project-b",
        "level": {"kind": "job", "jobName": "sync_orders", "sourceRoot": "src"},
    }

    def test_a_page_scope_names_the_subject_without_granting_reach(self) -> None:
        hint = describe_page_scope(self.PAGE_SCOPE)

        assert hint is not None
        assert "sync_orders" in hint
        # Subject, not filter: the selection is stated firmly, while reach and
        # authorization are explicitly untouched. The previous wording spent two
        # sentences de-weighting the selection ("a PREFERENCE, not a
        # restriction") and the observed result was an answer scoped to the level
        # ABOVE it.
        assert "THE SELECTION IS THE SUBJECT" in hint
        assert "grants no access" in hint
        assert "answered normally" in hint

    def test_rich_page_context_is_rendered_as_bounded_prompt_only_detail(self) -> None:
        scope = {
            **self.PAGE_SCOPE,
            "selected_entity": {
                "kind": "column",
                "label": "WorkOrder.Status",
                "entity_id": "output::Status",
            },
            "active_detail_tab": "mapping",
            "filters": [
                {"name": "connections", "value": "main flow"},
                *({"name": f"ignored-{index}", "value": "x"} for index in range(20)),
            ],
        }

        hint = describe_page_scope(scope)

        assert hint is not None
        assert "SELECTED ON SCREEN: the column 'WorkOrder.Status'" in hint
        # The id still reaches the prompt -- resolving "this column" depends on it.
        # It is framed as a lookup key rather than a citation, because the answer
        # guidance on this surface forbids printing ids back to the user.
        assert "output::Status" in hint
        assert "artifact id" not in hint
        assert "Open detail tab: 'mapping'" in hint
        assert "connections=main flow" in hint
        assert "ignored-11" not in hint  # renderer independently caps at 12 filters

    def test_a_table_qualified_id_keeps_both_halves(self) -> None:
        """`table::column` is not a path: reducing it to the leaf loses the table.

        Only a PATH-qualified design id (`<root>/<group>::<name>`) is reduced,
        because there the long form cannot match the design index at all.
        """
        hint = describe_page_scope(
            {
                **self.PAGE_SCOPE,
                "selected_entity": {
                    "kind": "column",
                    "label": "Status",
                    "entity_id": "output::Status",
                },
            }
        )

        assert hint is not None
        assert "output::Status" in hint

    def test_a_malformed_page_scope_is_dropped_rather_than_trusted(self) -> None:
        assert describe_page_scope(None) is None
        assert describe_page_scope("project-b") is None
        assert describe_page_scope({"project_id": "project-b"}) is None
        assert describe_page_scope({"level": {"kind": "job"}}) is None

    def test_a_page_scope_never_enters_the_authorized_scope_projection(self) -> None:
        context = _turn_prompt_context(["project-a"], self.PAGE_SCOPE)

        projection = json.loads(context.split("\n\n", 1)[0])
        assert projection["authorized_project_scope"] == ["project-a"]
        # The hint is prose alongside the projection, never merged into it.
        assert "project-b" not in context.split("\n\n", 1)[0]
        assert "project-b" in context

    async def test_a_page_scope_naming_an_inaccessible_project_grants_no_access(
        self, dispatched
    ) -> None:
        calls, _ = dispatched

        observation = await _execute(
            {"operation": "get_topology_overview", "project_id": "project-b"},
            project_scope=["project-a"],
        )

        assert observation.status == "denied"
        assert calls == []

    def test_the_guidance_is_present_without_a_page_scope(self) -> None:
        """No page scope means no page hint, but the source rules still apply.

        The tools stay reachable off the topology page, so the honesty rule that
        keeps a structural question from being answered silently from documents
        has to travel with every turn, not only with topology-page turns.
        """
        guidance = topology_turn_guidance(None)

        assert "Structural questions" in guidance
        assert "the topology artifact did not cover it" in guidance
        assert "PAGE CONTEXT" not in guidance


class TestTheSelectionSurvivesTheCanvasIdSpaces:
    """Real payloads from the failing runs, each of which resolved to nothing.

    Every scope below was captured verbatim from a persisted conversation on the
    topology surface. In each case the answer that followed was scoped to the
    level above the selection, asked the user to name the object the payload had
    already named, or described a previously discussed entity as the current one.
    """

    def test_a_drilled_in_stream_exposes_its_own_resolvable_key(self) -> None:
        """`level.selection` used to be dropped entirely.

        The canvas key `business-stream<US>ds-...` resolves; the same key behind
        the `integration:edge:` render prefix does not. Both were in the payload,
        and only the broken one reached the prompt.
        """
        scope = {
            "surface": "topology",
            "project_id": "werkorder",
            "level": {
                "kind": "integration",
                "selection": {
                    "kind": "edge",
                    "edgeKey": "business-stream\x1fds-ettm-maximo--4ps-invoice",
                },
            },
            "selected_entity": {
                "kind": "edge",
                "label": "Flow",
                "entity_id": "integration:edge:business-stream\x1fds-ettm-maximo--4ps-invoice",
            },
        }

        hint = describe_page_scope(scope)

        assert hint is not None
        assert "business-stream\x1fds-ettm-maximo--4ps-invoice" in hint
        assert "integration:edge:" not in hint
        # The level must not be described as the bare enum: "the integration
        # level" told the model nothing about the depth on screen.
        assert "implement it" in hint

    def test_the_unit_separator_inside_an_edge_key_is_not_collapsed(self) -> None:
        """`str.split()` treats \\x1c-\\x1f as whitespace; edge keys use \\x1f.

        Normalising a lookup key the same way as display text rewrote
        `business-stream<US>ds-...` to `business-stream ds-...`, so forwarding the
        key at all would have shipped a corrupted one and resolved to nothing --
        the same observable failure, one layer down.
        """
        key = "business-stream\x1fds-ettm-maximo--4ps-invoice"
        hint = describe_page_scope(
            {
                "surface": "topology",
                "project_id": "werkorder",
                "level": {"kind": "integration", "selection": {"kind": "edge", "edgeKey": key}},
            }
        )

        assert hint is not None
        assert key in hint
        assert "business-stream ds-" not in hint

    def test_a_selected_design_is_looked_up_by_its_bare_name(self) -> None:
        """A path-qualified design id can never match the name-keyed index.

        This is the payload behind the run that echoed a mistyped component name
        back to the user and explained it as a Camel component id.
        """
        scope = {
            "surface": "topology",
            "project_id": "werkorder",
            "level": {"kind": "integration", "selection": {"kind": "edge", "edgeKey": "k"}},
            "selected_entity": {
                "kind": "job",
                "label": "maximoFourpsInvoice",
                "entity_id": (
                    "VolkerVessels_Workorder/Talend exports/"
                    "ETTM.__intodoc_zip__/ERP_TO_THE_MAX::maximoFourpsInvoice"
                ),
            },
        }

        hint = describe_page_scope(scope)

        assert hint is not None
        assert "maximoFourpsInvoice" in hint
        assert "__intodoc_zip__" not in hint

    def test_a_system_pair_is_rendered_as_two_resolvable_endpoints(self) -> None:
        """`pair:system:a<->system:b` is two identifiers, not one.

        Nothing in the topology graph knows the `pair:` prefix, and half a pair
        is the wrong endpoint rather than a partial answer.
        """
        scope = {
            "surface": "topology",
            "project_id": "werkorder",
            "level": {"kind": "project"},
            "selected_entity": {
                "kind": "relationship",
                "label": "AFAS ↔ Maximo",
                "entity_id": "pair:system:sys-afas<->system:sys-maximo",
            },
        }

        hint = describe_page_scope(scope)

        assert hint is not None
        assert "sys-afas" in hint
        assert "sys-maximo" in hint
        assert "pair:" not in hint

    def test_a_focused_system_is_not_described_as_the_whole_project(self) -> None:
        """Selecting a node pushes no navigation level, so `kind` stays project.

        Rendering the bare kind said "the user is viewing the project level"
        while one system was selected -- and a project-wide answer with the
        system demoted to a parenthetical is the obedient response to that.
        """
        hint = describe_page_scope(
            {
                "surface": "topology",
                "project_id": "werkorder",
                "level": {"kind": "project"},
                "selected_entity": {
                    "kind": "system",
                    "label": "AFAS",
                    "entity_id": "sys-afas",
                },
            }
        )

        assert hint is not None
        assert "SELECTED ON SCREEN: the system 'AFAS'" in hint
        assert "Never ask the user which object they mean" in hint

    def test_an_empty_selection_is_stated_rather_than_left_silent(self) -> None:
        """Silence is what let a stale selection be reported as the current one.

        With no selection the renderer emitted nothing at all, so the only thing
        in context describing a selection was the conversation history -- and a
        job examined two levels down was duly reported as "currently selected"
        after the user had navigated back to the landscape.
        """
        hint = describe_page_scope(
            {"surface": "topology", "project_id": "werkorder", "level": {"kind": "project"}}
        )

        assert hint is not None
        assert "NOTHING IS SELECTED" in hint
        assert "discussed earlier in this conversation" in hint

    def test_the_subject_is_stated_after_the_rules_not_before(self) -> None:
        """Last means nearest the question.

        The hint is a few lines against several thousand words of answer rules.
        Leading with it put every rule between the subject and the first token of
        the answer.
        """
        scope = {
            "surface": "topology",
            "project_id": "werkorder",
            "level": {"kind": "job", "jobName": "maximo_call"},
        }

        guidance = topology_turn_guidance(scope)

        assert guidance.index("PAGE CONTEXT") > guidance.index("Structural answers")


class TestFabricationRules:
    """Absence and unknown names are lookups, not narrative judgements."""

    def test_presence_may_not_be_asserted_without_a_result(self) -> None:
        guidance = topology_turn_guidance(
            {"surface": "topology", "project_id": "p", "level": {"kind": "project"}}
        )

        assert "PRESENCE AND ABSENCE ARE LOOKUPS" in guidance
        assert "appears in the landscape" in guidance

    def test_an_unknown_name_may_not_be_explained(self) -> None:
        guidance = topology_turn_guidance(
            {"surface": "topology", "project_id": "p", "level": {"kind": "project"}}
        )

        assert "NEVER EXPLAIN A NAME YOU DID NOT READ" in guidance

    def test_confidence_claims_are_per_row(self) -> None:
        guidance = topology_turn_guidance(
            {"surface": "topology", "project_id": "p", "level": {"kind": "project"}}
        )

        assert "CONFIDENCE IS PER ROW" in guidance

    def test_the_rules_also_apply_off_the_topology_surface(self) -> None:
        """Same defect class, same fix, both surfaces (no special-casing)."""
        guidance = topology_turn_guidance(None)

        assert "Presence and absence are lookups" in guidance
        assert "never explain an entity name no result contained" in guidance


class TestTurnPlumbing:
    """The turn must carry the page hint from its caller down to the prompt."""

    PAGE_SCOPE = {
        "surface": "topology",
        "project_id": "project-a",
        "level": {"kind": "job", "jobName": "sync"},
    }

    def test_the_public_turn_entry_point_accepts_a_page_scope(self) -> None:
        import inspect

        from src.harness.agent_chat_runtime import execute_production_agent_chat_turn

        signature = inspect.signature(execute_production_agent_chat_turn)
        parameter = signature.parameters["page_scope"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is None

    @pytest.mark.parametrize(
        ("cohort", "target"),
        [
            (RuntimeCohort.V2_READONLY_PRIMARY, "_run_readonly_primary_turn"),
            (RuntimeCohort.V2_PLAN_PRIMARY, "_run_planning_primary_turn"),
            (RuntimeCohort.V2_EFFECT_PRIMARY, "_run_effect_primary_turn"),
        ],
    )
    async def test_every_v2_cohort_receives_the_page_scope(
        self, monkeypatch, cohort, target
    ) -> None:
        from src.harness import agent_chat_runtime

        seen: dict = {}

        async def recorder(**kwargs):  # noqa: ANN003
            seen.update(kwargs)
            return "outcome"

        monkeypatch.setattr(agent_chat_runtime, target, recorder)
        decision = CohortDecision(
            cohort=cohort, cohort_source="test", cohort_bucket=0, reason_code="test"
        )

        outcome = await agent_chat_runtime._run_primary_or_replay(
            db=None,
            conversation=None,
            query="what feeds this?",
            user_id=1,
            project_scope=["project-a"],
            permissions=None,
            access_token="turn-token",
            runtime=None,
            images=None,
            message_id=1,
            cohort=decision,
            page_scope=self.PAGE_SCOPE,
        )

        assert outcome == "outcome"
        assert seen["page_scope"] == self.PAGE_SCOPE

    def test_the_readonly_prompt_carries_the_scope_and_the_guidance(self) -> None:
        context = _turn_prompt_context(["project-a"], self.PAGE_SCOPE)

        # The read-only cohort appends this string to its system prompt, so both
        # halves have to be in it: the authoritative scope and the turn guidance.
        assert '"authorized_project_scope": ["project-a"]' in context
        assert "PAGE CONTEXT" in context
        # `PAGE_SCOPE` is a topology-surface scope, so the citation-suppression
        # rules are what prove the full guidance was appended -- not the
        # "(from topology)" label, which this surface deliberately no longer emits.
        assert "NO CITATION MACHINERY" in context
        assert "(from topology)" not in context


#: A turn asked from the topology canvas, which is what makes the guidance full.
TOPOLOGY_PAGE_SCOPE = {
    "surface": "topology",
    "project_id": "project-a",
    "level": {"kind": "project"},
}


class TestPromptGuidance:
    def test_the_structural_shape_is_distinguished_from_the_failure_shape(self) -> None:
        guidance = topology_turn_guidance(TOPOLOGY_PAGE_SCOPE)

        assert "Next Checks" in guidance
        assert "Do not add a next-checks section to a structural answer" in guidance

    def test_the_brevity_and_overlay_rules_are_stated(self) -> None:
        guidance = topology_turn_guidance(TOPOLOGY_PAGE_SCOPE)

        assert "360px" in guidance
        assert "unvalidated" in guidance
        assert "rescan" in guidance

    def test_source_labelling_separates_topology_from_logs(self) -> None:
        """Mixed sources stay separable; the tautological topology label does not.

        On the canvas the user is already looking at the topology, so labelling a
        structural claim "(from topology)" spends the answer's limited space saying
        nothing. A log line is different: it is a present-tense claim about a real
        execution and is NOT on screen, so it still has to be marked or it reads as
        published structure.
        """
        guidance = topology_turn_guidance(TOPOLOGY_PAGE_SCOPE)

        assert "(from topology)" not in guidance
        assert "(from logs)" in guidance
        assert "is a defect" in guidance
        assert "NO CITATION MACHINERY" in guidance

    def test_the_off_canvas_surface_keeps_the_topology_label(self) -> None:
        """The suppression is surface-scoped, not a global relaxation.

        Off the canvas there is no diagram to make the label redundant, so it is the
        only thing distinguishing published structure from a document or a log line.
        """
        from src.harness.topology_guidance import TOPOLOGY_MINIMAL_GUIDANCE

        assert "(from topology)" in TOPOLOGY_MINIMAL_GUIDANCE
        assert "(from logs)" in TOPOLOGY_MINIMAL_GUIDANCE

    def test_the_capability_contract_carries_the_name_and_ambiguity_guidance(self) -> None:
        snapshot = build_semantic_tool_snapshot(
            default_capability_registry(),
            scope=ManifestScope(include_bounded_loop_only=True),
        )

        description = snapshot.tool_for_name(SEMANTIC_NAME).description
        # The tool descriptions the dispatcher publishes are the authority on
        # name-vs-id and ambiguity; the harness must not contradict them.
        assert "Do not use" in description
        assert "runtime" in description.lower()

    def test_a_leaked_topology_evidence_key_renders_as_a_human_source(self) -> None:
        visible = _humanize_visible_content(
            "The order feed comes from topology:project-a as read_topology_structure showed.",
            [],
        )

        assert "topology:project-a" not in visible
        assert "read_topology_structure" not in visible
        assert "the project topology" in visible


class TestTargetsSurvive:
    def _record(self):  # noqa: ANN202
        snapshot = build_semantic_tool_snapshot(
            default_capability_registry(),
            scope=ManifestScope(include_bounded_loop_only=True),
        )
        return snapshot.tool_for_name(SEMANTIC_NAME)

    async def test_the_observation_preserves_both_halves_of_the_payload(self, dispatched) -> None:
        observation = await _execute(
            {"operation": "get_topology_overview", "project_id": "project-a"},
            project_scope=["project-a"],
        )

        assert observation.data["content"]["project_id"] == "project-a"
        assert observation.data["targets"] == [TARGET, EDGE_TARGET]

    async def test_targets_survive_the_model_facing_envelope_verbatim(self, dispatched) -> None:
        observation = await _execute(
            {"operation": "get_topology_overview", "project_id": "project-a"},
            project_scope=["project-a"],
        )

        envelope = project_capability_observation(observation, capability=self._record())

        assert envelope.status == "succeeded"
        assert topology_targets(envelope.data) == [TARGET, EDGE_TARGET]

    async def test_targets_survive_the_public_event_payload_verbatim(self, dispatched) -> None:
        observation = await _execute(
            {"operation": "get_topology_overview", "project_id": "project-a"},
            project_scope=["project-a"],
        )
        envelope = project_capability_observation(observation, capability=self._record())
        emitted: list[tuple[str, dict]] = []

        async def callback(event_type: str, payload: dict) -> None:
            emitted.append((event_type, payload))

        await _emit_tool_event(
            ToolMessage(
                content=serialize_tool_envelope(envelope),
                name=SEMANTIC_NAME,
                tool_call_id="call-1",
            ),
            callback,
        )

        event_type, payload = emitted[0]
        assert event_type == "tool.completed"
        # The public sanitizer is the last gate before SSE; the nested selection
        # ids have to come through it unrewritten or the chip navigates nowhere.
        public = sanitize_public_payload(event_type, payload)
        assert public["targets"] == [TARGET, EDGE_TARGET]

    async def test_targets_survive_the_turn_collector_and_are_deduplicated(
        self, dispatched
    ) -> None:
        observation = await _execute(
            {"operation": "get_topology_overview", "project_id": "project-a"},
            project_scope=["project-a"],
        )
        envelope = project_capability_observation(observation, capability=self._record())
        message = ToolMessage(
            content=serialize_tool_envelope(envelope),
            name=SEMANTIC_NAME,
            tool_call_id="call-1",
        )
        other = ToolMessage(
            content=serialize_tool_envelope(envelope),
            name="answer_from_documentation",
            tool_call_id="call-2",
        )

        collected = _collect_topology_targets([message, message, other])

        assert collected == [TARGET, EDGE_TARGET]

    def test_targets_reach_the_client_through_the_structured_answer(self) -> None:
        outcome = PrimaryTurnOutcome(
            result=None,
            grounded=None,
            cohort=CohortDecision(
                cohort=RuntimeCohort.V2_READONLY_PRIMARY,
                cohort_source="test",
                cohort_bucket=0,
                reason_code="test",
            ),
            manifest_snapshot_hash="hash",
            topology_targets=[TARGET, EDGE_TARGET],
        )
        grounded = GroundedAnswer(
            content="SAP ERP receives the order feed (from topology).",
            answer_status=AnswerStatus.GROUNDED,
        )

        structured = grounded_structured_answer(outcome, grounded)

        assert structured["response_type"] == "agent_response_v2_grounded"
        assert structured["topology_targets"] == [TARGET, EDGE_TARGET]

    def test_the_message_response_schema_passes_targets_through(self) -> None:
        response = MessageResponse.model_validate(
            {
                "id": 1,
                "role": "assistant",
                "content": "answer",
                "created_at": "2026-08-20T10:00:00+00:00",
                "structured_answer": {
                    "response_type": "agent_response_v2_grounded",
                    "topology_targets": [TARGET],
                },
            }
        )

        assert response.structured_answer["topology_targets"] == [TARGET]

    def test_a_target_id_is_never_normalized_or_re_derived(self) -> None:
        """Overlay and scanner ids share no bridge, so passthrough is the contract."""
        scanner_target = {
            "label": "Log",
            "level": {
                "kind": "integration",
                "selection": {"kind": "system", "systemId": "sys::Log::2"},
            },
        }

        assert topology_targets({"targets": [scanner_target]}) == [scanner_target]


class TestPublicPayloadSanitizer:
    """The last gate before SSE must bound targets without rewriting their ids."""

    def test_a_delimiter_bearing_edge_key_is_not_whitespace_folded(self) -> None:
        # Real regression: topology edge keys join endpoints with a unit
        # separator, which `str.split()` treats as whitespace. Folding it
        # rewrote the key and the client navigated nowhere.
        edge_key = "business-stream\x1fds-assetcheck--maximo-servicerequest"
        payload = {
            "tool_label": "read topology structure",
            "status": "succeeded",
            "targets": [
                {
                    "label": "AssetCheck -> Maximo",
                    "level": {
                        "kind": "integration",
                        "selection": {"kind": "edge", "edgeKey": edge_key},
                    },
                }
            ],
        }

        public = sanitize_public_payload("tool.completed", payload)

        assert public["targets"][0]["level"]["selection"]["edgeKey"] == edge_key

    def test_prose_fields_are_still_whitespace_normalized(self) -> None:
        public = sanitize_public_payload(
            "tool.completed", {"status": "succeeded", "summary": "one\n  two\tthree"}
        )

        assert public["summary"] == "one two three"

    def test_a_credential_inside_a_target_is_still_redacted(self) -> None:
        payload = {
            "status": "succeeded",
            "targets": [{"label": "api_key=super-secret", "level": {"kind": "group"}}],
        }

        public = sanitize_public_payload("tool.completed", payload)

        assert "super-secret" not in json.dumps(public)

    def test_targets_are_still_width_and_depth_bounded(self) -> None:
        payload = {
            "status": "succeeded",
            "targets": [{"label": str(index)} for index in range(40)]
            + [{"a": {"b": {"c": {"d": {"e": "too deep"}}}}}],
        }

        public = sanitize_public_payload("tool.completed", payload)

        assert len(public["targets"]) == 20

    def test_mapping_visual_detail_survives_public_payload_without_id_rewrites(self) -> None:
        target = {
            "label": "tMap_1",
            "level": {
                "kind": "component",
                "jobName": "sync_orders",
                "componentName": "tMap_1",
            },
            "intent": "reference",
            "visual_role": "primary",
            "visual_detail": {
                "kind": "mapping",
                "tables": ["orders_out"],
                "columns": [{"table": "orders_out", "column": "WorkOrder.Status"}],
                "edges": [
                    {"target_table": "orders_out", "target_column": "WorkOrder.Status"}
                ],
            },
        }

        public = sanitize_public_payload(
            "tool.completed", {"status": "succeeded", "targets": [target]}
        )

        assert public["targets"] == [target]

    def test_an_unlisted_field_is_still_dropped(self) -> None:
        public = sanitize_public_payload(
            "tool.completed", {"status": "succeeded", "arguments": {"project_id": "p"}}
        )

        assert "arguments" not in public


class TestUnavailableIsNarratable:
    UNAVAILABLE_MESSAGE = "No topology data is available for project 'project-a'."

    async def test_a_missing_artifact_is_unavailable_not_an_error(self, dispatched) -> None:
        _, result_box = dispatched
        result_box["result"] = _failure(RuntimeToolErrorCode.UNAVAILABLE, self.UNAVAILABLE_MESSAGE)

        observation = await _execute(
            {"operation": "get_topology_overview", "project_id": "project-a"},
            project_scope=["project-a"],
        )

        assert observation.error_code == "unavailable"
        assert observation.retryable is False
        # Passed through verbatim: the dispatcher owns this wording.
        assert observation.summary == self.UNAVAILABLE_MESSAGE

    async def test_the_envelope_projects_unavailable_rather_than_failed(self, dispatched) -> None:
        _, result_box = dispatched
        result_box["result"] = _failure(RuntimeToolErrorCode.UNAVAILABLE, self.UNAVAILABLE_MESSAGE)
        observation = await _execute(
            {"operation": "get_topology_overview", "project_id": "project-a"},
            project_scope=["project-a"],
        )
        snapshot = build_semantic_tool_snapshot(
            default_capability_registry(),
            scope=ManifestScope(include_bounded_loop_only=True),
        )

        envelope = project_capability_observation(
            observation, capability=snapshot.tool_for_name(SEMANTIC_NAME)
        )

        assert envelope.status == "unavailable"
        # The grounding validator must force an honest source-unavailable answer,
        # so the model says the project needs a rescan instead of apologising.
        requirement = GroundingValidator().required_state([envelope])
        assert requirement.status is AnswerStatus.SOURCE_UNAVAILABLE

    async def test_missing_artifact_and_out_of_scope_share_one_outcome_code(
        self, monkeypatch
    ) -> None:
        """Neither case is an error, and neither reveals whether a project exists.

        The harness must not add a distinction the dispatcher does not make: both
        arrive as UNAVAILABLE and are projected identically, so the model narrates
        "no topology here" either way. (The two dispatcher messages differ in
        wording -- see the report -- but neither names a project the caller is not
        already authorized for, so neither is an existence oracle.)
        """
        from src.runtime.models import RuntimeToolCall
        from src.topology.client import execute_topology_tool

        provider = make_provider(json_transport({"detail": "not found"}, status_code=404))
        monkeypatch.setattr(provider_module, "default_provider", lambda: provider)
        call = RuntimeToolCall(
            tool_name="get_topology_overview", arguments={"project_id": "project-a"}
        )

        missing = await execute_topology_tool(call, project_scope=["project-a"], access_token=None)
        out_of_scope = await execute_topology_tool(
            call, project_scope=["project-z"], access_token=None
        )

        assert missing.error.code is RuntimeToolErrorCode.UNAVAILABLE
        assert out_of_scope.error.code is RuntimeToolErrorCode.UNAVAILABLE
        assert "project-z" not in out_of_scope.error.message

    async def test_a_successful_read_is_citable_so_it_is_not_graded_no_evidence(
        self, dispatched
    ) -> None:
        observation = await _execute(
            {"operation": "get_topology_overview", "project_id": "project-a"},
            project_scope=["project-a"],
        )
        snapshot = build_semantic_tool_snapshot(
            default_capability_registry(),
            scope=ManifestScope(include_bounded_loop_only=True),
        )
        envelope = project_capability_observation(
            observation, capability=snapshot.tool_for_name(SEMANTIC_NAME)
        )

        validator = GroundingValidator()
        assert validator.allowed_evidence([envelope]) == {"topology:project-a"}
        assert validator.required_state([envelope]).status is None

    async def test_a_provider_failure_stays_retryable_and_leaks_nothing(self, dispatched) -> None:
        _, result_box = dispatched
        result_box["result"] = _failure(
            RuntimeToolErrorCode.PROVIDER_ERROR, "The topology service is unavailable."
        )

        observation = await _execute(
            {"operation": "get_topology_overview", "project_id": "project-a"},
            project_scope=["project-a"],
        )

        assert observation.status == "failed"
        assert observation.error_code == "handler_failure"
        assert observation.retryable is True

    async def test_a_dispatcher_validation_error_is_publicly_sanitized(self, dispatched) -> None:
        _, result_box = dispatched
        result_box["result"] = _failure(
            RuntimeToolErrorCode.VALIDATION_ERROR,
            "1 validation error for ListTopologyDependenciesInput name_or_id Field required "
            "https://errors.pydantic.dev/internal",
        )

        observation = await _execute(
            {"operation": "get_topology_overview", "project_id": "project-a"},
            project_scope=["project-a"],
        )

        assert observation.status == "needs_input"
        assert observation.summary == (
            "The topology request is missing or contains an invalid operation field."
        )
        assert "pydantic" not in observation.summary


class TestAgainstTheRealArtifact:
    """One end-to-end pass over a real published artifact, no dispatcher stub."""

    @pytest.fixture
    def werkorder(self) -> dict:
        """A real composite published artifact; every cap in the tools is exceeded."""
        return load_fixture("werkorder_composite_landscape")

    @pytest.fixture
    def installed(self, monkeypatch):
        def install(payload: dict) -> None:
            provider = make_provider(json_transport(payload))
            monkeypatch.setattr(provider_module, "default_provider", lambda: provider)

        return install

    async def test_a_hub_answer_stays_inside_the_model_facing_bounds(
        self, installed, werkorder
    ) -> None:
        installed(werkorder)
        snapshot = build_semantic_tool_snapshot(
            default_capability_registry(),
            scope=ManifestScope(include_bounded_loop_only=True),
        )

        observation = await _execute(
            {
                "operation": "list_topology_dependencies",
                "project_id": "werkorder",
                "name_or_id": "Maximo",
            },
            project_scope=["werkorder"],
        )
        envelope = project_capability_observation(
            observation, capability=snapshot.tool_for_name(SEMANTIC_NAME)
        )

        assert observation.status == "succeeded"
        serialized = serialize_tool_envelope(envelope)
        assert len(serialized.encode("utf-8")) <= 16_384
        # The projection is not silently replaced by a context-limit marker: the
        # caps in the tools already bound it, and the harness must not re-bound it.
        assert envelope.data.get("reason") != "model_context_limit"
        assert "content" in envelope.data

    async def test_real_targets_reach_the_public_payload_unrewritten(
        self, installed, werkorder
    ) -> None:
        installed(werkorder)
        snapshot = build_semantic_tool_snapshot(
            default_capability_registry(),
            scope=ManifestScope(include_bounded_loop_only=True),
        )

        observation = await _execute(
            {
                "operation": "get_topology_node",
                "project_id": "werkorder",
                "name_or_id": "Maximo",
            },
            project_scope=["werkorder"],
        )
        envelope = project_capability_observation(
            observation, capability=snapshot.tool_for_name(SEMANTIC_NAME)
        )
        emitted: list[tuple[str, dict]] = []

        async def callback(event_type: str, payload: dict) -> None:
            emitted.append((event_type, payload))

        await _emit_tool_event(
            ToolMessage(
                content=serialize_tool_envelope(envelope),
                name=SEMANTIC_NAME,
                tool_call_id="call-1",
            ),
            callback,
        )
        public = sanitize_public_payload(*emitted[0])

        produced = topology_targets(observation.data)
        assert produced, "the real artifact must yield at least one navigable target"

        # Flat selections (system / application / edge) nest four levels under
        # `targets` and survive both sanitizers byte-identically.
        flat_produced = [target for target in produced if _is_flat_selection(target)]
        flat_public = [target for target in public["targets"] if _is_flat_selection(target)]
        assert flat_produced, "the real artifact must yield at least one flat target"
        assert flat_public == flat_produced
        for target in flat_public:
            selection = target["level"]["selection"]
            # Every id token is a real string, not a truncation marker.
            assert all(isinstance(value, str) and value for value in selection.values())
            assert target["intent"] == "reference"

    # A relationship selection nests streamKeys one level deeper than a flat
    # selection id, so both sanitizers had to allow that extra level:
    # _MAX_PUBLIC_PAYLOAD_DEPTH (src/harness/agent_runs.py) and
    # MAX_CONTAINER_DEPTH (src/harness/semantic_tools.py). This test is what
    # stops either being lowered again — a truncated key is a chip that
    # navigates nowhere, which is invisible until a user clicks it.
    async def test_relationship_stream_keys_survive_the_public_payload(
        self, installed, werkorder
    ) -> None:
        installed(werkorder)

        observation = await _execute(
            {
                "operation": "get_topology_node",
                "project_id": "werkorder",
                "name_or_id": "Maximo",
            },
            project_scope=["werkorder"],
        )
        produced = topology_targets(observation.data)
        relationships = [target for target in produced if not _is_flat_selection(target)]
        assert relationships, "Maximo is a hub in the overlay view and must offer pair views"

        public = sanitize_public_payload(
            "tool.completed", {"targets": relationships, "status": "succeeded"}
        )
        for target in public["targets"]:
            keys = target["level"]["selection"]["streamKeys"]
            assert keys, "an aggregated pair view is useless without its stream keys"
            assert all(
                isinstance(key, str) and key.startswith("business-stream") for key in keys
            ), keys


class TestGuidanceSalienceBySurface:
    """The surface and question shape deterministically select availability."""

    OTHER_SURFACES = [
        None,
        {},
        {"project_id": "project-a", "level": {"kind": "project"}},
        {"surface": "documents", "project_id": "project-a", "level": {"kind": "project"}},
    ]

    def test_the_topology_surface_gets_the_full_guidance_and_the_intent_rules(self) -> None:
        guidance = topology_turn_guidance(TOPOLOGY_PAGE_SCOPE)

        assert "Do not add a next-checks section" in guidance
        assert "NEVER SUBSTITUTE A SOURCE SILENTLY" in guidance
        assert "SAY WHEN A SOURCE IS EMPTY" in guidance
        assert "Navigation intent" in guidance
        # Overlay weakness is still disclosed, but in plain language rather than by
        # naming the artifact's internal states -- "analyst_curated" and
        # "topology_state" are field values, and quoting them back at the user is
        # the citation noise this surface now suppresses.
        assert "SAY WHAT IS UNCERTAIN" in guidance
        assert "inferred" in guidance
        assert "analyst_curated" not in guidance
        assert "topology_state" not in guidance

    @pytest.mark.parametrize("page_scope", OTHER_SURFACES)
    def test_other_surfaces_get_the_short_reminder_only(self, page_scope) -> None:
        guidance = topology_turn_guidance(page_scope)

        assert "Structural questions" in guidance
        assert "the topology artifact did not cover it" in guidance
        # Not nudged toward topology in a conversation about something else.
        assert "Navigation intent" not in guidance
        assert "360px" not in guidance
        assert len(guidance) < len(topology_turn_guidance(TOPOLOGY_PAGE_SCOPE))

    def test_the_page_hint_still_travels_on_a_non_topology_surface(self) -> None:
        """The hint resolves "this job"; it is independent of guidance length."""
        guidance = topology_turn_guidance(
            {"project_id": "project-a", "level": {"kind": "job", "jobName": "sync"}}
        )

        assert "PAGE CONTEXT" in guidance
        assert "'sync'" in guidance

    def test_the_capability_is_registered_regardless_of_surface(self) -> None:
        """The registry is stable; each turn's context performs the filtering."""
        snapshot = build_semantic_tool_snapshot(
            default_capability_registry(),
            scope=ManifestScope(include_bounded_loop_only=True),
        )

        record = snapshot.tool_for_name(SEMANTIC_NAME)
        assert record is not None
        assert "surface" not in json.dumps(record.input_schema)

    @pytest.mark.parametrize(
        "query",
        [
            "What does Maximo do?",
            "Tell me about the business purpose of Maximo",
            "Which rules apply to planned work orders?",
        ],
    )
    def test_plain_chat_purpose_questions_do_not_expose_topology(self, query) -> None:
        assert topology_capability_enabled(query, None) is False
        assert TOPOLOGY_STRUCTURE_CAPABILITY.id in _chat_denied_capability_ids(
            query, None
        )

    @pytest.mark.parametrize(
        "query",
        [
            "List all systems in this project",
            "What is upstream of Maximo?",
            "What data flows from AFAR to Maximo?",
            "Which jobs implement this integration?",
            "Use the topology to show the connections",
        ],
    )
    def test_plain_chat_structural_questions_can_expose_topology(self, query) -> None:
        assert topology_capability_enabled(query, None) is True
        assert TOPOLOGY_STRUCTURE_CAPABILITY.id not in _chat_denied_capability_ids(
            query, None
        )

    def test_topology_surface_exposes_topology_for_ordinary_questions(self) -> None:
        assert topology_capability_enabled("What am I looking at?", TOPOLOGY_PAGE_SCOPE)

    @pytest.mark.parametrize(
        "query",
        [
            "Do not use topology; answer from documentation",
            "Tell me this without topology",
            "Not from the topology please",
        ],
    )
    def test_explicit_exclusion_wins_even_on_the_topology_surface(self, query) -> None:
        assert topology_capability_enabled(query, TOPOLOGY_PAGE_SCOPE) is False
        assert TOPOLOGY_STRUCTURE_CAPABILITY.id in _chat_denied_capability_ids(
            query, TOPOLOGY_PAGE_SCOPE
        )


class TestDepthOperationsOnTheCapability:
    def test_the_depth_operations_are_reachable_through_the_capability(self, dispatched) -> None:
        operations = TOPOLOGY_STRUCTURE_CAPABILITY.input_schema["properties"]["operation"]

        assert "get_job_detail" in operations["enum"]
        assert "get_component_detail" in operations["enum"]

    @pytest.mark.parametrize(
        ("operation", "extra"),
        [
            ("get_job_detail", {"name_or_id": "Quintiq2Maximo_WorkOrderPickedUpXML"}),
            (
                "get_job_detail",
                {"name_or_id": "j", "source_root": "root/a", "navigation_intent": "navigate"},
            ),
            ("get_component_detail", {"job": "j", "component": "tMap_1"}),
            ("find_topology_node", {"query": "tMap_1", "job": "j"}),
        ],
    )
    async def test_the_depth_arguments_reach_the_dispatcher_unchanged(
        self, dispatched, operation, extra
    ) -> None:
        calls, _box = dispatched

        await _execute(
            {"operation": operation, "project_id": "project-a", **extra},
            project_scope=["project-a"],
        )

        assert calls[-1]["tool_name"] == operation
        for key, value in extra.items():
            assert calls[-1]["arguments"][key] == value

    def test_the_schema_stays_closed_and_bounded(self) -> None:
        schema = TOPOLOGY_STRUCTURE_CAPABILITY.input_schema
        properties = schema["properties"]

        assert schema["additionalProperties"] is False
        assert properties["navigation_intent"]["enum"] == ["navigate", "reference", None]
        for field in ("job", "component", "source_root"):
            assert properties[field]["maxLength"] <= 500
            assert properties[field]["minLength"] == 1

    async def test_identity_is_still_never_read_from_the_arguments(self, dispatched) -> None:
        calls, _box = dispatched

        await _execute(
            {
                "operation": "get_job_detail",
                "project_id": "project-a",
                "name_or_id": "j",
            },
            project_scope=["project-a"],
            access_token="turn-jwt",
        )

        assert calls[-1]["project_scope"] == ["project-a"]
        assert calls[-1]["access_token"] == "turn-jwt"
        assert "access_token" not in calls[-1]["arguments"]
        assert "project_scope" not in calls[-1]["arguments"]


class TestOperationGuidanceReachesTheModel:
    """The gap this slice closes: per-operation guidance on the harness path.

    Observed defect: over 145 harness tool steps, ``get_component_detail`` -- the
    only operation returning column-level mapping tables and expressions -- was
    chosen zero times. The dispatcher's per-operation descriptions were rendered
    only on the legacy loop; the harness assembled its model-visible text from the
    capability contract alone and published a bare six-name enum.
    """

    def _description(self) -> str:
        snapshot = build_semantic_tool_snapshot(
            default_capability_registry(),
            scope=ManifestScope(include_bounded_loop_only=True),
        )
        return snapshot.tool_for_name(SEMANTIC_NAME).description

    def test_every_operation_description_reaches_the_rendered_tool_text(self) -> None:
        from src.topology.registry import list_topology_tool_definitions

        description = self._description()

        definitions = list_topology_tool_definitions()
        assert definitions, "the dispatcher must publish at least one tool"
        for definition in definitions:
            normalized = " ".join(definition.description.split())
            assert f"- {definition.name}: {normalized}" in description, definition.name

    def test_the_guide_is_sourced_from_the_registry_not_restated(self) -> None:
        """A second copy of the tool copy would drift; drift here is invisible."""
        from src.topology.registry import list_topology_tool_definitions

        for definition in list_topology_tool_definitions():
            normalized = " ".join(definition.description.split())
            assert normalized in TOPOLOGY_OPERATION_GUIDE

    def test_the_guide_covers_every_operation_the_enum_offers(self) -> None:
        for operation in TOPOLOGY_OPERATIONS:
            assert f"- {operation}:" in TOPOLOGY_OPERATION_GUIDE, operation

    def test_the_guide_ordering_is_stable_and_independent_of_registry_order(self) -> None:
        names = [
            line.split(":", 1)[0].removeprefix("- ")
            for line in TOPOLOGY_OPERATION_GUIDE.splitlines()
        ]

        assert names == sorted(names)

    def test_the_operation_property_names_where_mapping_data_lives(self) -> None:
        operation = TOPOLOGY_STRUCTURE_CAPABILITY.input_schema["properties"]["operation"]

        assert "get_component_detail" in operation["description"]
        assert "get_field_lineage" in operation["description"]

    def test_an_incomplete_contract_still_fails_the_description_render(self) -> None:
        """The guide must not have made the completeness check vacuous."""
        stripped = TOPOLOGY_STRUCTURE_CAPABILITY.model_copy(update={"use_when": []})

        with pytest.raises(SemanticSnapshotError):
            _semantic_description(stripped)


class TestDepthSteering:
    """Mapping and implementation questions must route here, not to documents."""

    def test_use_when_points_field_and_mapping_questions_at_the_depth_operations(
        self,
    ) -> None:
        use_when = " ".join(TOPOLOGY_STRUCTURE_CAPABILITY.use_when)

        assert "column" in use_when
        assert "get_component_detail" in use_when
        assert "get_field_lineage" in use_when
        assert "what data moves between two systems and how it is transformed" in use_when
        assert "implementation detail" in use_when

    def test_mapping_and_implementation_are_no_longer_excluded(self) -> None:
        do_not_use = " ".join(TOPOLOGY_STRUCTURE_CAPABILITY.do_not_use_when)

        # The exact exclusion that routed "tell me about the implementation
        # details" away from the only operations holding the mapping data.
        assert "explanatory implementation, business rules, or mapping explanation" not in (
            do_not_use
        )
        assert "belong here even when the user calls them business rules" in do_not_use

    def test_documentation_still_owns_purpose_and_business_meaning(self) -> None:
        do_not_use = " ".join(TOPOLOGY_STRUCTURE_CAPABILITY.do_not_use_when)

        assert "asks only for purpose" in do_not_use
        assert "use indexed documentation" in do_not_use
        assert "business rule as policy rather than as code" in do_not_use
        # Runtime state and an explicit exclusion are still not this capability's.
        assert "observed runtime state" in do_not_use
        assert "explicitly excludes topology" in do_not_use

    def test_the_turn_guidance_states_the_same_ownership_line(self) -> None:
        guidance = topology_turn_guidance(TOPOLOGY_PAGE_SCOPE)

        assert "WHAT THE CODE DOES, field by field" in guidance
        assert "WHY IT EXISTS" in guidance
        # The old wording gave "explanatory implementation" and "mapping
        # explanation" to documentation, contradicting the capability contract.
        assert "explanatory implementation" not in guidance
        assert "mapping explanation" not in guidance

    def test_the_unresolved_lineage_count_is_a_stated_defect(self) -> None:
        guidance = topology_turn_guidance(TOPOLOGY_PAGE_SCOPE)

        assert "COUNT THE UNRESOLVED MAPPINGS OUT LOUD" in guidance
        assert "39%" in guidance
        assert "decided at runtime" in guidance
        assert "understates the integration" in guidance
        # And the older rule it is a sibling of survives verbatim.
        assert "Never present a mapping absence as an absence of transformation" in guidance

    def test_the_source_honesty_rules_are_not_weakened(self) -> None:
        guidance = topology_turn_guidance(TOPOLOGY_PAGE_SCOPE)

        assert "NEVER SUBSTITUTE A SOURCE SILENTLY" in guidance
        assert "SAY WHEN A SOURCE IS EMPTY" in guidance
        assert "HONOR SOURCE INSTRUCTIONS" in guidance
        assert "360px" in guidance
        assert "Navigation intent" in guidance
        # Suppressing citation must not take these with it. Disclosing an empty
        # source and disclosing an inferred relationship both change whether a
        # sentence is TRUE, not merely where it came from -- an unannounced
        # substitution is how a question about one integration gets answered about
        # a different one, which is the failure this whole rule set exists for.
        assert "SAY WHAT IS UNCERTAIN" in guidance
        assert "NEVER COLLAPSE A PLURAL INTO A SINGULAR HEADLINE" in guidance

    def test_the_minimal_guidance_moved_mapping_to_topology(self) -> None:
        from src.harness.topology_guidance import TOPOLOGY_MINIMAL_GUIDANCE

        assert "column-level mappings" in TOPOLOGY_MINIMAL_GUIDANCE
        assert "mapping explanation" not in TOPOLOGY_MINIMAL_GUIDANCE
        # Contracts the source-ownership policy tests depend on, kept intact.
        assert "current-turn evidence summary" in TOPOLOGY_MINIMAL_GUIDANCE
        assert "not from documents" not in TOPOLOGY_MINIMAL_GUIDANCE.casefold()


class TestSchemaContract:
    """The schema must stay validator-clean, closed, and byte-stable."""

    def _snapshot(self):  # noqa: ANN202
        return build_semantic_tool_snapshot(
            default_capability_registry(),
            scope=ManifestScope(include_bounded_loop_only=True),
        )

    def test_the_schema_passes_the_draft_and_closed_object_checks(self) -> None:
        schema = TOPOLOGY_STRUCTURE_CAPABILITY.input_schema

        Draft202012Validator.check_schema(schema)
        # Raises SemanticSnapshotError on an open object or an untyped array.
        _require_closed_objects(schema, capability_id=CAPABILITY_ID)

    def test_the_rendered_schema_is_byte_stable_across_two_builds(self) -> None:
        first = self._snapshot().tool_for_name(SEMANTIC_NAME)
        second = self._snapshot().tool_for_name(SEMANTIC_NAME)

        assert json.dumps(first.input_schema) == json.dumps(second.input_schema)
        assert first.description == second.description

    def test_the_snapshot_hash_is_deterministic(self) -> None:
        """The manifest hash is computed from the rendered schema and text."""
        assert self._snapshot().snapshot_hash == self._snapshot().snapshot_hash

    def test_a_missing_operation_reports_only_the_missing_operation(self) -> None:
        """Each conditional `if` requires `operation`, so nothing fires vacuously."""
        errors = list(
            Draft202012Validator(TOPOLOGY_STRUCTURE_CAPABILITY.input_schema).iter_errors(
                {"project_id": "project-a"}
            )
        )

        assert [error.validator for error in errors] == ["required"]
        assert "operation" in errors[0].message

    def test_get_job_detail_accepts_a_bounded_offset(self) -> None:
        validator = Draft202012Validator(TOPOLOGY_STRUCTURE_CAPABILITY.input_schema)

        assert not list(
            validator.iter_errors(
                {
                    "operation": "get_job_detail",
                    "project_id": "project-a",
                    "name_or_id": "j",
                    "offset": 0,
                }
            )
        )
        assert list(
            validator.iter_errors(
                {
                    "operation": "get_job_detail",
                    "project_id": "project-a",
                    "name_or_id": "j",
                    "offset": -1,
                }
            )
        )

    def test_get_field_lineage_requires_a_job_or_a_group(self) -> None:
        """The conditional rule holds independently of enum membership.

        ``get_field_lineage`` is contributed by ``src.topology.registry``; until
        it lands there the enum will also reject the name. The scoping rule is
        asserted on its own so it is in force the moment the operation appears --
        an unscoped lineage read is a whole-project mapping dump.
        """
        validator = Draft202012Validator(TOPOLOGY_STRUCTURE_CAPABILITY.input_schema)
        base = {"operation": "get_field_lineage", "project_id": "project-a"}

        unscoped = [error.validator for error in validator.iter_errors(base)]
        assert "anyOf" in unscoped
        for scope in ({"job": "j"}, {"group": "g"}):
            scoped = [error.validator for error in validator.iter_errors({**base, **scope})]
            assert "anyOf" not in scoped

    def test_the_field_lineage_arguments_are_declared_and_bounded(self) -> None:
        properties = TOPOLOGY_STRUCTURE_CAPABILITY.input_schema["properties"]

        for field in ("job", "group"):
            assert properties[field]["minLength"] == 1
            # The widest the dispatcher accepts; the two operations that bound
            # `job` at 200 restate that in their own conditional branch.
            assert properties[field]["maxLength"] == 500
        assert properties["offset"]["minimum"] == 0
        assert properties["navigation_intent"]["enum"] == ["navigate", "reference", None]
        # Page size is the tool's to decide; offering it here would drift.
        assert "limit" not in properties

    @pytest.mark.parametrize(
        ("operation", "model_name"),
        [
            ("get_topology_overview", "GetTopologyOverviewInput"),
            ("find_topology_node", "FindTopologyNodeInput"),
            ("get_topology_node", "GetTopologyNodeInput"),
            ("list_topology_dependencies", "ListTopologyDependenciesInput"),
            ("get_job_detail", "GetJobDetailInput"),
            ("get_component_detail", "GetComponentDetailInput"),
            ("get_field_lineage", "GetFieldLineageInput"),
        ],
    )
    def test_every_operation_argument_the_tool_takes_is_declared_here(
        self, operation, model_name
    ) -> None:
        """A field the tool accepts but the schema omits is unreachable.

        `additionalProperties: False` means an argument missing from this schema
        is rejected by the harness before the dispatcher ever sees it, so the
        model simply cannot use it. That is silent: the operation still works,
        just less well -- the same failure mode as the missing operation guide.
        """
        from src.topology import models

        declared = set(TOPOLOGY_STRUCTURE_CAPABILITY.input_schema["properties"])
        accepted = set(getattr(models, model_name).model_fields)

        assert accepted <= declared, f"{operation} takes undeclared {accepted - declared}"

    @pytest.mark.parametrize(
        ("operation", "job_length", "expected_valid"),
        [
            ("get_field_lineage", 400, True),
            ("get_component_detail", 400, False),
            ("find_topology_node", 400, False),
        ],
    )
    def test_the_job_bound_mirrors_each_operation_input_model(
        self, operation, job_length, expected_valid
    ) -> None:
        """Per-operation bounds must match, so a refusal is correctable here.

        A value this schema admits but the tool rejects comes back as the
        dispatcher's generic invalid-operation message, which tells the model
        nothing about which field was too long.
        """
        validator = Draft202012Validator(TOPOLOGY_STRUCTURE_CAPABILITY.input_schema)
        arguments = {
            "operation": operation,
            "project_id": "werkorder",
            "job": "j" * job_length,
        }
        if operation == "get_component_detail":
            arguments["component"] = "tMap_1"
        if operation == "find_topology_node":
            arguments["query"] = "tMap_1"

        assert (not list(validator.iter_errors(arguments))) is expected_valid

    def test_every_enum_operation_that_needs_scoping_declares_a_rule(self) -> None:
        """Forward guard: a new operation must not arrive rule-free.

        The dispatcher's allowlist drives the enum, so a sibling adding a tool
        makes it callable here without editing this module. Any operation taking
        a scope argument needs its conditional-required rule, or the model can
        call it unscoped and get a validation error from the provider instead of
        a correctable one from the harness.
        """
        conditioned: set[str] = set()
        for branch in TOPOLOGY_STRUCTURE_CAPABILITY.input_schema["allOf"]:
            operation = branch["if"]["properties"]["operation"]
            assert branch["if"]["required"] == ["operation"]
            conditioned.update(
                [operation["const"]] if "const" in operation else operation["enum"]
            )

        # get_topology_overview is the only operation needing nothing but a project.
        assert set(TOPOLOGY_OPERATIONS) - conditioned == {"get_topology_overview"}

    @pytest.mark.parametrize(
        "extra",
        [
            {"job": "Quintiq2Maximo_WorkOrderPickedUpXML"},
            {"group": "Werkorder"},
            {"job": "j", "offset": 40, "source_root": "root/a"},
        ],
    )
    async def test_field_lineage_arguments_reach_the_dispatcher_unchanged(
        self, dispatched, extra
    ) -> None:
        calls, _box = dispatched

        observation = await _execute(
            {"operation": "get_field_lineage", "project_id": "project-a", **extra},
            project_scope=["project-a"],
        )

        assert observation.status == "succeeded"
        assert calls[-1]["tool_name"] == "get_field_lineage"
        assert calls[-1]["arguments"] == {"project_id": "project-a", **extra}

    async def test_an_unscoped_field_lineage_call_is_refused_before_the_dispatcher(
        self, dispatched
    ) -> None:
        """A lineage read with neither job nor group is a whole-project dump."""
        calls, _box = dispatched

        observation = await _execute(
            {"operation": "get_field_lineage", "project_id": "project-a"},
            project_scope=["project-a"],
        )

        assert observation.status == "needs_input"
        assert observation.error_code == "validation"
        assert calls == []

    async def test_an_overview_page_offset_reaches_the_dispatcher(self, dispatched) -> None:
        """get_topology_overview pages its applications and diagnostics too."""
        calls, _box = dispatched

        await _execute(
            {"operation": "get_topology_overview", "project_id": "project-a", "offset": 20},
            project_scope=["project-a"],
        )

        assert calls[-1]["arguments"] == {"project_id": "project-a", "offset": 20}

    async def test_a_job_detail_page_offset_reaches_the_dispatcher(self, dispatched) -> None:
        calls, _box = dispatched

        await _execute(
            {
                "operation": "get_job_detail",
                "project_id": "project-a",
                "name_or_id": "j",
                "offset": 100,
            },
            project_scope=["project-a"],
        )

        assert calls[-1]["arguments"]["offset"] == 100
