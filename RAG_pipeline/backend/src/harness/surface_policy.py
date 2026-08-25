"""Which capabilities each chat surface exposes, decided once per turn.

One assistant, one registry, two surfaces: the full-page ``/chat`` and the
copilot docked beside the topology canvas. They used to differ by exactly one
capability, which meant the canvas copilot carried the whole product surface --
pack authoring, onboarding, scan triggers -- into a conversation about a diagram.

Measured on 2026-08-25 over 330 observed tool steps on the topology surface, the
model chose three capabilities and nothing else. Not "rarely chose the rest":
zero, for all twenty-eight others. This module makes exposure match that.

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
own control tools are not. ``nexus_turn_assess`` is likewise force-called but
lives in ``interaction_protocol`` rather than the capability registry, so it is
outside this policy's reach by construction.

Untrusted input
---------------
``page_scope`` is client-supplied (see ``src.schemas``). It selects which
capabilities are OFFERED and can only ever narrow or widen the tool list -- it
is never an access decision. Project scope and permissions remain the only
authority over what any surviving capability may read, so a forged
``surface`` value cannot reach data its holder is not entitled to.
"""

from __future__ import annotations

from collections.abc import Iterable

from src.harness.models import (
    DOCUMENTATION_ANSWER_CAPABILITY,
    DOCUMENT_LIST_CAPABILITY,
    DOCUMENT_READ_CAPABILITY,
    GENERATED_DOCUMENT_READ_CAPABILITY,
    STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY,
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

#: What the topology copilot keeps, beside the control plane. Every entry is a
#: capability the model actually chose on this surface in the 2026-08-25 sample:
#: read_topology_structure 308 calls, answer_from_documentation 16,
#: read_runtime_evidence 6. Adding to this list is a product decision; it should
#: come with a reason the measurement did not already cover.
TOPOLOGY_SURFACE_CAPABILITY_IDS = frozenset(
    {
        TOPOLOGY_STRUCTURE_CAPABILITY.id,
        DOCUMENTATION_ANSWER_CAPABILITY.id,
        STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY.id,
    }
)

#: Structural reads belong to the canvas. In ``/chat`` the topology artifact is
#: unreachable and documentation answers the question instead.
CHAT_DENIED_CAPABILITY_IDS = frozenset({TOPOLOGY_STRUCTURE_CAPABILITY.id})


def _is_control_plane(capability_id: str) -> bool:
    return capability_id.startswith(CONTROL_PLANE_PREFIXES)


def surface_denied_capability_ids(
    registered_capability_ids: Iterable[str],
    page_scope: object = None,
) -> set[str]:
    """Return the capability ids this surface withholds for one turn.

    ``registered_capability_ids`` is the live registry, passed in rather than
    imported so the policy narrows whatever is actually registered -- including
    capabilities contributed by an active pack, which a hardcoded list here
    would miss.
    """

    registered = set(registered_capability_ids)
    denied = set(CHAT_NON_RAG_DOCUMENT_CAPABILITY_IDS)

    if is_topology_surface(page_scope):
        kept = TOPOLOGY_SURFACE_CAPABILITY_IDS
        denied |= {
            capability_id
            for capability_id in registered
            if capability_id not in kept and not _is_control_plane(capability_id)
        }
    else:
        denied |= CHAT_DENIED_CAPABILITY_IDS

    # Never claim to deny something that is not registered: the denied set is
    # reported in operator telemetry, and phantom ids there read as policy bugs.
    return {capability_id for capability_id in denied if capability_id in registered}


__all__ = [
    "CHAT_DENIED_CAPABILITY_IDS",
    "CHAT_NON_RAG_DOCUMENT_CAPABILITY_IDS",
    "CONTROL_PLANE_PREFIXES",
    "TOPOLOGY_SURFACE",
    "TOPOLOGY_SURFACE_CAPABILITY_IDS",
    "is_topology_surface",
    "surface_denied_capability_ids",
]
