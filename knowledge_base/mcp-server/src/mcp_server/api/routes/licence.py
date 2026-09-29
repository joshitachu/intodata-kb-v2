"""Subscription licence: status endpoint and the route-level licence gate.

Two enforcement layers exist in this service, and a mutating route needs one:

- **Run creation** (``ProjectRegistry.create_scan_run`` / ``create_generate_run``)
  is the chokepoint for every scan and generation, wherever it starts. A
  refusal there raises ``LicenceRefused``, which ``api/app.py`` maps to 402.
- **``require_capability``** (below) gates routes that spend tokens or produce
  new artefacts WITHOUT creating a run -- the living-docs patch lanes, RAG
  pushes, Ollama summaries. It is a dependency, so it refuses before the
  handler runs.

``tests/test_api/test_licence_route_inventory.py`` walks every mutating route
and fails unless it is gated (found through ``LICENCE_CAPABILITY_ATTR``),
listed as covered by the chokepoint, or allowlisted with a reason.

402, never 401: only a real 401 may log a user out.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import APIRouter, HTTPException

from mcp_server.licensing.core import Capability
from mcp_server.licensing.runtime import get_policy
from mcp_server.licensing.usage import check_capability, usage_summary

router = APIRouter(tags=["licence"])

#: Same attribute name as the RAG backend's gate, so both inventories read alike.
LICENCE_CAPABILITY_ATTR = "__licence_capability__"


def require_capability(
    capability: Capability, *, quota: bool = False
) -> Callable[[], Awaitable[None]]:
    """Dependency factory: 402 unless the licence allows ``capability``.

    Authentication is applied by the router (``include_router(dependencies=...)``),
    whose dependencies run before a route's own, so an anonymous caller still
    gets 401 and learns nothing about the licence.

    ``quota=True`` also refuses when the generation allowance is used up. Use it
    only on a route whose whole purpose is to start a counted generation in the
    background, so the caller gets the 402 instead of a silently skipped job;
    run creation re-checks under the lock either way.
    """

    async def _check_capability() -> None:
        refusal = await check_capability(capability, quota=quota)
        if refusal is not None:
            raise HTTPException(status_code=402, detail=refusal.to_detail())

    setattr(_check_capability, LICENCE_CAPABILITY_ATTR, capability)
    _check_capability.__name__ = f"require_{capability.value}_capability"
    return _check_capability


@router.get("/licence/status")
async def licence_status() -> dict[str, object]:
    """Licence state for the banner: plan, period, days left, generations used.

    Never exposes the token. Any authenticated user may read it, including on an
    expired instance -- that is precisely when the banner needs it.
    """
    policy = get_policy()
    body = policy.status().to_public_dict()
    # The live policy, not the setting: they only differ in tests, and then
    # the policy is what actually decides.
    body["enforcement"] = "enforce" if policy.enforce else "off"
    body["usage"] = await usage_summary()
    # A pushed renewal waiting for its period to start (ADR-038).
    queued = policy.next_status()
    body["next_licence"] = queued.to_public_dict() if queued is not None else None
    return body
