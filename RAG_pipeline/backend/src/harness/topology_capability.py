"""One read-only harness capability over the published topology artifacts.

Shape follows ``STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY``: a single
``CapabilityDefinition`` whose ``operation`` enum selects one topology tool,
rather than one capability per tool. The model already chooses from ~30 semantic
tools; several near-identical structural readers would compete with each other
for the same question.

The per-operation descriptions that actually steer the choice live one level down
in ``src.topology.registry``. They used to reach the model only on the legacy
loop, which merges ``list_topology_tools()`` into the model's catalogue
verbatim. On the harness path the model-visible text is assembled by
``semantic_tools._semantic_description`` from ``purpose`` / ``use_when`` /
``do_not_use_when`` / ``preconditions`` / ``output_contracts`` /
``failure_recovery`` and nothing else, so a bare ``{"enum": [...]}`` left the
model choosing between six opaque names. Measured effect: over 145 observed
harness tool steps, ``get_component_detail`` -- the only operation that returns
column-level mapping tables and expressions -- was chosen zero times.

``TOPOLOGY_OPERATION_GUIDE`` closes that gap by rendering the registry's own
descriptions into ``purpose``, so they travel on the harness path too. It is
built from ``list_topology_tool_definitions()`` rather than restated here: a
second copy of the tool copy would drift from the dispatcher that validates the
call, and drift in steering text is invisible until a whole operation stops
being used.

Registration is unconditional; EXPOSURE is not. The capability is always in the
registry, and ``src.harness.surface_policy`` then withholds it from every surface
except the topology canvas. That is a deliberate product boundary: a structural
question asked in ``/chat`` is answered from documentation, or referred to the
topology page, rather than answered from this artifact without the diagram beside
it. ``src.harness.topology_guidance`` supplies the matching prompt text on each
surface. Do not re-read this paragraph as "available everywhere" -- an earlier
version of it said exactly that, and stayed there after the gate was added.

Definition and handler are co-located here (the ``agent_plans`` /
``task_controls`` pattern) because both are pure functions of the topology
package and neither needs a request-scoped service.

Identity discipline: ``project_scope`` and ``access_token`` come from
``CapabilityExecutionContext`` — the server-owned turn identity — and are
forwarded to ``execute_topology_tool`` on every call. They are never read from
``request.arguments``, so a model-supplied value cannot widen access. The
executor's own policy gate additionally rejects an out-of-scope ``project_id``
before this handler runs; the dispatcher re-checks scope independently, so
dropping either layer cannot silently widen access.

No tool logic is reimplemented here and the dispatcher is never bypassed: this
module only translates between the harness capability contract and the
``RuntimeToolCall``/``RuntimeToolResult`` contract.
"""

from __future__ import annotations

import logging
from typing import Any

from src.harness.capability_runtime import (
    CapabilityExecutionContext,
    CapabilityExecutionRequest,
    CapabilityHandlerServices,
    CapabilityObservation,
)
from src.harness.models import (
    CapabilityDefinition,
    HarnessProviderType,
    HarnessRiskTier,
)
from src.runtime.models import (
    RuntimeToolCall,
    RuntimeToolErrorCode,
    RuntimeToolResult,
)
from src.topology.client import TOPOLOGY_TOOL_NAMES, execute_topology_tool
from src.topology.registry import list_topology_tool_definitions

logger = logging.getLogger(__name__)

#: Operations in the enum are exactly the dispatcher's allowlist, sorted so the
#: rendered schema (and therefore the manifest snapshot hash) is deterministic.
#: Derived, never restated: a new tool added to the dispatcher becomes reachable
#: here without an edit, and an operation this module invents that the dispatcher
#: does not accept cannot exist.
TOPOLOGY_OPERATIONS: tuple[str, ...] = tuple(sorted(TOPOLOGY_TOOL_NAMES))


def _operation_guide() -> str:
    """Render the dispatcher's own per-operation descriptions as one text block.

    Single source of truth: the text comes from ``src.topology.registry``, which
    is also what validates the resulting call. Sorted by operation name and
    whitespace-normalized so the rendered string -- and therefore the semantic
    tool snapshot hash computed from the capability -- is byte-stable across
    builds. Ordering must stay independent of ``_TOOL_SPECS`` insertion order,
    which is why this sorts rather than trusting the registry's iteration order.
    """

    return "\n".join(
        f"- {definition.name}: {' '.join(definition.description.split())}"
        for definition in sorted(list_topology_tool_definitions(), key=lambda item: item.name)
    )


#: The per-operation guidance the model reads, rendered once at import.
TOPOLOGY_OPERATION_GUIDE: str = _operation_guide()

#: Fail closed at import rather than ship an operation the model has no guidance
#: for. That silent state is exactly the defect this guide exists to fix, and it
#: is invisible in production: the operation is callable, simply never chosen.
_UNDESCRIBED_OPERATIONS: tuple[str, ...] = tuple(
    name for name in TOPOLOGY_OPERATIONS if f"- {name}:" not in TOPOLOGY_OPERATION_GUIDE
)
if _UNDESCRIBED_OPERATIONS:
    raise RuntimeError(
        "Topology operations reachable from the harness have no dispatcher "
        f"description: {', '.join(_UNDESCRIBED_OPERATIONS)}. Add a TopologyToolSpec "
        "in src/topology/registry.py for each one."
    )

TOPOLOGY_OUTPUT_TYPE = "topology_structure"

#: Source label for the capability's evidence references. Deliberately not a
#: capability id or provider id: it appears in the model-visible grounding
#: contract, so it has to read as a source, not as an implementation detail.
TOPOLOGY_EVIDENCE_SOURCE_TYPE = "topology_structure"


TOPOLOGY_STRUCTURE_CAPABILITY = CapabilityDefinition(
    id="topology.landscape.read_structure",
    label="Read integration topology structure",
    semantic_name="read_topology_structure",
    purpose=(
        "Read bounded, complete-within-the-artifact integration-landscape structure "
        "from the project's published topology: inventory, counts, connections, "
        "dependency traversal, design and component implementation, column-level "
        "field mapping, and navigable entity identity.\n"
        "Ownership line between this source and documentation: the deterministic "
        "scan behind these operations owns WHAT THE CODE DOES, field by field -- "
        "which designs and components exist, how they are wired, which columns are "
        "populated and by which expression. Documentation owns WHY IT EXISTS -- "
        "purpose, business meaning, and business policy. An implementation question "
        "or a mapping question is a code question and is answered here.\n"
        "Operations -- choose the one that reads the level the question is about:\n"
        f"{TOPOLOGY_OPERATION_GUIDE}"
    ),
    preconditions=["A supported topology operation and an authorized project are known."],
    failure_recovery={
        "validation_failed": "Correct only the named invalid fields and retry once.",
        "denied": "Respect the authority boundary and offer a permitted alternative.",
        "unavailable": (
            "Say the project has no published topology yet and that a rescan would "
            "produce one; never invent structure."
        ),
        "failed": "Report the safe failure summary and retry only when marked retryable.",
    },
    provider_type=HarnessProviderType.RETRIEVAL,
    provider_id="nexus.topology",
    action="read_topology_structure",
    input_schema={
        "type": "object",
        "required": ["operation", "project_id"],
        "properties": {
            # A plain enum, not a per-value `oneOf`: the value-by-value guidance
            # lives in `purpose` (see TOPOLOGY_OPERATION_GUIDE), which every
            # provider renders, whereas per-branch `const` descriptions are
            # dropped or flattened by some tool-schema translators. Keeping the
            # enum flat also keeps the pre-validation error the middleware
            # produces on a bad operation a single readable "not one of" instead
            # of one failure per branch.
            "operation": {
                "type": "string",
                "enum": list(TOPOLOGY_OPERATIONS),
                "description": (
                    "Which topology operation to run. Each one reads a different level; "
                    "this tool's description lists what each reads and which arguments "
                    "it needs. Column-level mapping tables, expressions, and field "
                    "lineage exist ONLY in get_component_detail and get_field_lineage."
                ),
            },
            "project_id": {"type": "string", "minLength": 1, "maxLength": 255},
            # `find_topology_node` searches by free text; the others accept a
            # name directly in `name_or_id`, so no separate lookup call is needed.
            "query": {
                "type": ["string", "null"],
                "minLength": 1,
                "maxLength": 200,
                "description": "Required when operation is find_topology_node.",
            },
            "name_or_id": {
                "type": ["string", "null"],
                "minLength": 1,
                "maxLength": 200,
                "description": (
                    "Required when operation is get_topology_node, "
                    "list_topology_dependencies, or get_job_detail."
                ),
            },
            "direction": {
                "type": ["string", "null"],
                "enum": ["upstream", "downstream", "both", None],
            },
            # Two hops is the whole traversal budget the topology tools accept.
            "hops": {"type": "integer", "minimum": 1, "maximum": 2},
            # Depth operations. `job` also scopes find_topology_node's component
            # search to one design's full component list.
            # 500, not 200: `GetFieldLineageInput.job` accepts 500 while
            # `GetComponentDetailInput.job` and `FindTopologyNodeInput.job` accept
            # 200. One shared property has to admit the widest value the
            # dispatcher accepts, or a legitimate lineage call is refused here;
            # the two tighter operations restate their own 200 in `allOf` below,
            # so each operation's bound still mirrors its input model exactly.
            "job": {
                "type": ["string", "null"],
                "minLength": 1,
                "maxLength": 500,
                "description": (
                    "Required with component for get_component_detail (max 200 chars). "
                    "Scopes find_topology_node to one design's full component list "
                    "(max 200 chars). For get_field_lineage, supply job or group -- at "
                    "least one is mandatory, and both together is allowed."
                ),
            },
            "component": {"type": ["string", "null"], "minLength": 1, "maxLength": 200},
            # Functional-group scope for get_field_lineage: a business flow is
            # usually implemented by several designs, so lineage is asked for at
            # group level as often as at job level. One real group covers 19
            # designs and 851 field mappings, which is why the group option
            # exists at all.
            "group": {
                "type": ["string", "null"],
                "minLength": 1,
                "maxLength": 500,
                "description": (
                    "Functional group to read field lineage for, covering every design "
                    "in the group at once. For get_field_lineage, supply job or group "
                    "-- at least one is mandatory."
                ),
            },
            # Paging for the three operations whose result is a long list the tool
            # layer caps. No maximum and no page-size argument: page size is fixed
            # by the tool layer, and a second copy of either bound here would
            # drift from the one that is enforced.
            "offset": {
                "type": "integer",
                "minimum": 0,
                "description": (
                    "Zero-based start index for a paged result. get_topology_overview, "
                    "get_job_detail and get_field_lineage all report shown, total, "
                    "has_more and next_offset; pass next_offset to read the next page "
                    "rather than concluding the omitted entries do not exist. Page size "
                    "is fixed by the tool and cannot be requested."
                ),
            },
            # A composite upload carries the same design in more than one
            # partition; this pins which copy is meant. Long, because a real
            # source root is a full export path.
            "source_root": {"type": ["string", "null"], "minLength": 1, "maxLength": 500},
            # Whether the targets this call returns should move the user's canvas.
            # `reference` is the default and the safe value: the tool layer resolves
            # anything but the exact string "navigate" to it, so a wrong value can
            # never navigate unbidden.
            "navigation_intent": {
                "type": ["string", "null"],
                "enum": ["navigate", "reference", None],
            },
        },
        # Every `if` pins `operation` as required as well as constrained. Without
        # that, a payload with no `operation` at all satisfies each `if`
        # vacuously and every conditional `then` fires at once, so the model is
        # told it is missing query, name_or_id, job and component when the one
        # real fault is the missing operation.
        "allOf": [
            {
                "if": {
                    "properties": {"operation": {"const": "find_topology_node"}},
                    "required": ["operation"],
                },
                # `job` is optional here but narrower than the shared property:
                # FindTopologyNodeInput bounds it at 200.
                "then": {
                    "required": ["query"],
                    "properties": {"job": {"maxLength": 200}},
                },
            },
            {
                "if": {
                    "properties": {
                        "operation": {
                            "enum": [
                                "get_topology_node",
                                "list_topology_dependencies",
                                "get_job_detail",
                            ]
                        }
                    },
                    "required": ["operation"],
                },
                "then": {"required": ["name_or_id"]},
            },
            {
                "if": {
                    "properties": {"operation": {"const": "get_component_detail"}},
                    "required": ["operation"],
                },
                # GetComponentDetailInput bounds `job` at 200, not the 500 the
                # shared property allows for lineage.
                "then": {
                    "required": ["job", "component"],
                    "properties": {"job": {"maxLength": 200}},
                },
            },
            {
                # Field lineage is scoped by design or by functional group, and
                # at least one is mandatory: unscoped, it would be a whole-project
                # mapping dump, which no context window survives.
                "if": {
                    "properties": {"operation": {"const": "get_field_lineage"}},
                    "required": ["operation"],
                },
                "then": {"anyOf": [{"required": ["job"]}, {"required": ["group"]}]},
            },
        ],
        "additionalProperties": False,
    },
    output_type=TOPOLOGY_OUTPUT_TYPE,
    risk_tier=HarnessRiskTier.READ_ONLY,
    packs=["core", "documentation", "operations"],
    use_when=[
        "The user asks for a bounded inventory or count of what exists in a project's "
        "integration landscape, or how its systems, applications, and groups relate.",
        "The user asks what feeds, consumes, or depends on a named system, job, or "
        "component, or what breaks if it stops.",
        "The user asks which jobs or components structurally implement an integration: "
        "use get_job_detail, because the landscape rollup contains no jobs. Use "
        "documentation as a complement only when the question also asks why the "
        "integration exists or which business policy it serves.",
        "The user asks about a column, a field, an expression, or a transformation "
        "rule: use get_component_detail for one mapping component's own column tables "
        "and expressions, or get_field_lineage to follow fields across a whole design "
        "or functional group. These two operations are the only place column-level "
        "mapping exists; no document and no runtime record holds it.",
        "The user asks what data moves between two systems and how it is transformed: "
        "resolve the connection, then read the design that implements it with "
        "get_job_detail and its column-level mapping with get_component_detail or "
        "get_field_lineage. Naming the two systems without the mapping answers only "
        "half the question.",
        "The user asks for implementation detail -- what a job or component actually "
        "does, how a value is computed, which target columns are populated and from "
        "which source. The deterministic scan owns that, field by field.",
        "The user names something and you do not know what kind of thing it is: use "
        "find_topology_node, which searches systems, applications, groups, jobs, and "
        "components together.",
        "The user asks to be taken somewhere on the canvas: use navigation_intent="
        '"navigate" so the returned target moves the view.',
        "The user asks about something they are looking at on the topology canvas. "
        "When the page scope names a connection, pass its edge key or stream id to "
        "get_topology_node: that resolves the relationship itself -- both endpoints, "
        "mechanism, protocols, business object and provenance -- not just the "
        "endpoints either side of it.",
        "A mixed impact question needs structural scope before documentation explains "
        "business meaning or runtime evidence verifies current health.",
    ],
    do_not_use_when=[
        "The user asks about observed runtime state: executions, failures, health, or log content.",
        # Deliberately narrower than it was. The previous wording excluded
        # "explanatory implementation" and "mapping explanation" too, which routed
        # "tell me about the implementation details" away from the only operations
        # that hold the mapping data -- and toward documentation that, for several
        # real integrations, is an empty generation-gap stub.
        "The user asks only for purpose, why something exists, business meaning, or a "
        "business rule as policy rather than as code; use indexed documentation. Draw "
        "the line by ownership, not by vocabulary: this capability owns what the code "
        "does field by field, documentation owns why it exists. How a value is "
        "computed, which columns are mapped, and what an expression evaluates to are "
        "code questions and belong here even when the user calls them business rules, "
        "mapping explanation, or implementation detail.",
        "The user explicitly excludes topology for this turn. Honor the exclusion; "
        "if the request requires complete inventory or structural traversal, disclose "
        "that the structural portion cannot be answered reliably from documentation.",
    ],
    task_types=["topology_answer"],
    output_contracts=["grounded_answer", "structural_answer"],
    metadata={
        "execution_surface": "published_topology_artifact",
        "evidence_output": True,
        "bounded_loop_only": True,
        "activity_label": "Read integration topology",
    },
)


def topology_capabilities() -> tuple[CapabilityDefinition, ...]:
    """Every capability this module owns, for registry assembly."""

    return (TOPOLOGY_STRUCTURE_CAPABILITY,)


class TopologyStructureHandler:
    """Translate one capability call into one topology dispatcher call."""

    def __init__(self, capability: CapabilityDefinition = TOPOLOGY_STRUCTURE_CAPABILITY) -> None:
        self.capability_id = capability.id
        self.input_schema = capability.input_schema
        self.output_type = capability.output_type
        self.task_types = tuple(capability.task_types)

    async def __call__(
        self,
        capability: CapabilityDefinition,
        request: CapabilityExecutionRequest,
        context: CapabilityExecutionContext,
        services: CapabilityHandlerServices,
    ) -> CapabilityObservation:
        # No request-scoped service is needed: the dispatcher owns its provider.
        del services
        arguments = dict(request.arguments)
        operation = str(arguments.pop("operation", ""))
        call = RuntimeToolCall(
            tool_name=operation,
            arguments={key: value for key, value in arguments.items() if value is not None},
        )
        result = await execute_topology_tool(
            call,
            # Server-owned turn identity, never model-supplied arguments.
            project_scope=context.project_scope,
            access_token=context.access_token,
        )
        return _observation(capability, result, project_id=str(arguments.get("project_id") or ""))


def topology_capability_handlers() -> tuple[TopologyStructureHandler, ...]:
    """Executable handler bindings for :func:`topology_capabilities`."""

    return (TopologyStructureHandler(TOPOLOGY_STRUCTURE_CAPABILITY),)


def topology_targets(observation_data: Any) -> list[dict[str, Any]]:
    """Extract navigation targets from one observation/envelope ``data`` value.

    Target ids belong to whichever id space the canvas is rendering (overlay or
    scanner). They are read out verbatim and never transformed, normalized, or
    re-derived: there is no bridge between the two spaces, so a rewritten id is
    a chip that navigates nowhere.
    """

    if not isinstance(observation_data, dict):
        return []
    targets = observation_data.get("targets")
    if not isinstance(targets, list):
        return []
    return [item for item in targets if isinstance(item, dict)]


def _observation(
    capability: CapabilityDefinition,
    result: RuntimeToolResult,
    *,
    project_id: str,
) -> CapabilityObservation:
    if result.success and isinstance(result.data, dict):
        targets = topology_targets(result.data)
        return CapabilityObservation(
            capability_id=capability.id,
            status="succeeded",
            output_type=capability.output_type,
            summary=_success_summary(result.tool_name, targets, truncated=result.truncated),
            # Both halves of the dispatcher payload are preserved verbatim:
            # `content` is what the model reasons over, `targets` is what the
            # canvas navigates with. Neither is re-derived downstream.
            data=dict(result.data),
            evidence_refs=_evidence_refs(project_id),
            safe_details={"truncated": bool(result.truncated)},
        )
    error = result.error
    code = error.code if error is not None else RuntimeToolErrorCode.PROVIDER_ERROR
    # The dispatcher's message is already safe and, for UNAVAILABLE, is worded
    # identically for "no artifact published" and "project out of scope" so the
    # tool cannot be used as an existence oracle. It is passed through unchanged.
    message = error.message if error is not None else "The topology read did not complete."
    if code is RuntimeToolErrorCode.VALIDATION_ERROR:
        # Pydantic's raw message contains implementation class names, field
        # internals and a documentation URL. Tool envelopes are public, so keep
        # the correction useful without exposing any of that machinery.
        message = "The topology request is missing or contains an invalid operation field."
    status, error_code, retryable = _ERROR_PROJECTION.get(code, ("failed", "handler_failure", True))
    if code is not RuntimeToolErrorCode.UNAVAILABLE:
        logger.info("Topology capability call did not complete code=%s", code.value)
    return CapabilityObservation(
        capability_id=capability.id,
        status=status,
        output_type=capability.output_type,
        summary=message,
        error_message=message,
        error_code=error_code,
        retryable=retryable,
        safe_details={"reason": error_code},
    )


#: Dispatcher error code -> (observation status, policy error code, retryable).
#:
#: `unavailable` is deliberately not an error status downstream: the envelope
#: projects it to "unavailable", which the grounding validator turns into a
#: source-unavailable disclosure. The model then says the project needs a
#: rescan instead of apologising for a failure.
_ERROR_PROJECTION: dict[RuntimeToolErrorCode, tuple[str, str, bool]] = {
    RuntimeToolErrorCode.UNAVAILABLE: ("failed", "unavailable", False),
    RuntimeToolErrorCode.DISABLED: ("failed", "unavailable", False),
    RuntimeToolErrorCode.UNKNOWN_TOOL: ("denied", "unknown", False),
    RuntimeToolErrorCode.VALIDATION_ERROR: ("needs_input", "validation", True),
    RuntimeToolErrorCode.TIMEOUT: ("failed", "handler_failure", True),
    RuntimeToolErrorCode.PROVIDER_ERROR: ("failed", "handler_failure", True),
}


def _evidence_refs(project_id: str) -> list[dict[str, Any]]:
    """One citable reference per successful read: the project's topology artifact.

    Navigation targets are not evidence, so they are not projected as references.
    A successful read must still carry one reference, otherwise the grounding
    validator forces a "no relevant evidence" state onto a real structural answer.
    """

    if not project_id:
        return []
    return [
        {
            "evidence_id": f"topology:{project_id}",
            "source_type": TOPOLOGY_EVIDENCE_SOURCE_TYPE,
            "project_id": project_id,
            "uri": f"/projects/{project_id}/topology",
        }
    ]


def _success_summary(tool_name: str, targets: list[dict[str, Any]], *, truncated: bool) -> str:
    subject = tool_name.replace("_", " ")
    suffix = " Some detail was omitted to stay within bounds." if truncated else ""
    if not targets:
        return f"Read {subject}; no navigable entities matched.{suffix}"
    return f"Read {subject}; {len(targets)} navigable entity/entities returned.{suffix}"


__all__ = [
    "TOPOLOGY_EVIDENCE_SOURCE_TYPE",
    "TOPOLOGY_OPERATIONS",
    "TOPOLOGY_OPERATION_GUIDE",
    "TOPOLOGY_OUTPUT_TYPE",
    "TOPOLOGY_STRUCTURE_CAPABILITY",
    "TopologyStructureHandler",
    "topology_capabilities",
    "topology_capability_handlers",
    "topology_targets",
]
