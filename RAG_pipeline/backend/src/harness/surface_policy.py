"""Which capabilities each chat surface exposes, decided once per turn.

One assistant, one registry, two surfaces: the full-page ``/chat`` and the
copilot docked beside the topology canvas. They differ by exactly two rules:
the canvas may not CHANGE anything, and ``/chat`` may not read the topology
artifact (there is no diagram on screen there to read it against).

Read-only in, everything else out
---------------------------------
The canvas keep-set is DERIVED from the risk tier each capability already
declares, not enumerated here. That is the whole point of this module's current
shape, and it is worth stating why, because the enumerated form is what it
replaced.

The previous version listed three capability ids by name, chosen from a
2026-08-25 sample of 330 observed tool steps on this surface in which the model
picked those three and nothing else. That measurement was sound for what it
covered -- pack authoring and scan triggers genuinely have no business in a
conversation about a diagram -- and wrong in one specific way: it also excluded
``search_documentation``, which the model had not chosen because the TERMINAL
``answer_from_documentation`` was the only documentation route it was offered
and it short-circuits the turn (``readonly_runtime._invoke_graph``). So the
canvas lost the one tool that can combine documentation with structure in a
single model-authored answer, and no one decided that.

A hand-maintained keep-list reproduces that failure for every read capability
added later: the omission is invisible, because a capability that is never
offered cannot show up as an error. Deriving from ``risk_tier`` fixes the class
rather than the instance -- a new read-only capability reaches the canvas
automatically, and a capability that writes, triggers a workflow or has an
external side effect still cannot, with no edit here either way.

Allowlist in, denylist out
--------------------------
Each surface declares what it KEEPS; the denied set is derived by subtracting
that from the live registry. The alternative -- listing what to remove -- fails
open: a capability registered next month would silently appear on a surface
nobody intended it for, and nothing would fail loudly enough to notice. The
derived form fails closed instead.

The result is expressed as ``denied_capability_ids`` rather than
``allowed_capability_ids`` because denial is the hook every cohort honours.
``planning_runtime`` builds its own ``allowed`` set (read-only plus plan
control) and never consults ``context.allowed_capability_ids``, so an allowlist
set here would apply on the read-only and effect paths and quietly not on the
plan path. Denial has no such gap -- see ``manifest._invisible_reason`` and
``agent_loop._apply_turn_capability_policy``, which both subtract it.

The control-plane exemption
---------------------------
``nexus.agent_plan.*`` is never denied by surface policy, even though the
topology surface has never used it. The plan cohort's middleware FORCES those
calls via ``tool_choice`` -- ``semantic_tools._REQUIRED_PROJECTION_TOOL`` for
objective projection and ``_required_plan_action`` for durable plan changes --
and ``SemanticToolMiddleware.__init__`` raises ``ValueError("required
projection tool is absent from the snapshot")`` when the tool it is about to
demand is not there. Cohort assignment is per-user and content-blind, so a
plan-cohort user opening the topology page would hit that on every turn.

This is a runtime structural dependency, not a product decision. Domain
capabilities -- things a user could ask for -- are all removable; the runtime's
own control tools are not. Several of them declare a write tier, so the
exemption has to be checked BEFORE the tier rule, not after.
``nexus_turn_assess`` is likewise force-called but lives in
``interaction_protocol`` rather than the capability registry, so it is outside
this policy's reach by construction.

Untrusted input
---------------
``page_scope`` is client-supplied (see ``src.schemas``). It selects which
capabilities are OFFERED and can only ever narrow or widen the tool list -- it
is never an access decision. Project scope and permissions remain the only
authority over what any surviving capability may read, so a forged ``surface``
value cannot reach data its holder is not entitled to. Note the direction of
the risk here: forging ``surface: "topology"`` NARROWS the tool list to
read-only, and omitting it yields the ordinary ``/chat`` list. There is no
value of this field that widens anything.
"""

from __future__ import annotations

from collections.abc import Iterable

from src.harness.models import (
    DOCUMENT_LIST_CAPABILITY,
    DOCUMENT_READ_CAPABILITY,
    GENERATED_DOCUMENT_READ_CAPABILITY,
    CapabilityDefinition,
    HarnessRiskTier,
)
from src.harness.topology_capability import TOPOLOGY_STRUCTURE_CAPABILITY

# Re-exported, not redefined: `topology_guidance` already owns the predicate and
# uses it to pick the prompt guidance. Two copies of "is this the canvas?" would
# eventually disagree, and the failure -- guidance for one surface, tools for the
# other -- would be silent.
from src.harness.topology_guidance import is_topology_surface

#: ``page_scope.surface`` value the docked canvas copilot sends.
TOPOLOGY_SURFACE = "topology"

#: Capability ids the runtime may force via ``tool_choice``. Exempt from every
#: surface narrowing: denying one turns a forced call into a hard failure.
CONTROL_PLANE_PREFIXES: tuple[str, ...] = ("nexus.agent_plan.",)

#: Denied on every chat surface. Chat answers from retrieval, so the raw
#: document readers stay out of the model's reach and RAG owns the path.
CHAT_NON_RAG_DOCUMENT_CAPABILITY_IDS = frozenset(
    {
        DOCUMENT_LIST_CAPABILITY.id,
        DOCUMENT_READ_CAPABILITY.id,
        GENERATED_DOCUMENT_READ_CAPABILITY.id,
    }
)

#: The tier the canvas keeps. Everything else -- reversible writes, workflow
#: triggers, content writes, external side effects -- is withheld there: the
#: panel is docked to a diagram to explain it, not to act on the project. The
#: full product surface remains available in ``/chat``.
TOPOLOGY_SURFACE_RISK_TIER = HarnessRiskTier.READ_ONLY

#: Structural reads belong to the canvas. In ``/chat`` the topology artifact is
#: unreachable and documentation answers the question instead.
CHAT_DENIED_CAPABILITY_IDS = frozenset({TOPOLOGY_STRUCTURE_CAPABILITY.id})


def _is_control_plane(capability_id: str) -> bool:
    return capability_id.startswith(CONTROL_PLANE_PREFIXES)


def surface_denied_capability_ids(
    registered_capabilities: Iterable[CapabilityDefinition],
    page_scope: object = None,
) -> set[str]:
    """Return the capability ids this surface withholds for one turn.

    ``registered_capabilities`` is the live registry, passed in rather than
    imported so the policy narrows whatever is actually registered -- including
    capabilities contributed by an active pack, which a hardcoded list here
    would miss. Whole definitions rather than ids, because the tier that decides
    canvas exposure is a property of the definition.
    """

    registered = list(registered_capabilities)
    registered_ids = {capability.id for capability in registered}
    denied = set(CHAT_NON_RAG_DOCUMENT_CAPABILITY_IDS)

    if is_topology_surface(page_scope):
        denied |= {
            capability.id
            for capability in registered
            if capability.risk_tier is not TOPOLOGY_SURFACE_RISK_TIER
            and not _is_control_plane(capability.id)
        }
    else:
        denied |= CHAT_DENIED_CAPABILITY_IDS

    # Never claim to deny something that is not registered: the denied set is
    # reported in operator telemetry, and phantom ids there read as policy bugs.
    return {capability_id for capability_id in denied if capability_id in registered_ids}


__all__ = [
    "CHAT_DENIED_CAPABILITY_IDS",
    "CHAT_NON_RAG_DOCUMENT_CAPABILITY_IDS",
    "CONTROL_PLANE_PREFIXES",
    "TOPOLOGY_SURFACE",
    "TOPOLOGY_SURFACE_RISK_TIER",
    "is_topology_surface",
    "surface_denied_capability_ids",
]
