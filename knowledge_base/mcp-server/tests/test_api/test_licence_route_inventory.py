"""Every mutating route is licence-classified (plan: "Missed LLM path").

Each POST/PUT/PATCH/DELETE route on the mcp-server app must be exactly one of:

- ``GATED``: carries ``require_capability(...)`` with the listed capability,
  found through ``LICENCE_CAPABILITY_ATTR`` on the dependency callable;
- ``CHOKEPOINT``: starts a scan or generation, so run creation
  (``ProjectRegistry.create_scan_run`` / ``create_generate_run``) refuses it;
- ``ALLOWLIST``: reviewed as neither spending tokens nor producing new
  artefacts, with the reason.

A new route fails this test until someone classifies it; removing a gate fails
it too. Stale entries fail, so the lists cannot silently rot.
"""

from __future__ import annotations

import inspect

from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from mcp_server.api.app import app
from mcp_server.api.routes.licence import LICENCE_CAPABILITY_ATTR
from mcp_server.licensing.core import Capability

_READ_METHODS = {"GET", "HEAD", "OPTIONS"}

GATED: dict[tuple[str, str], Capability] = {
    # Chat-originated doc writes (living-docs lane; activation re-embeds).
    ("POST", "/mcp/projects/{project_id}/docs/{filename}/chat-patch/preview"): Capability.CHAT,
    ("POST", "/mcp/projects/{project_id}/docs/{filename}/chat-patch/activate"): Capability.CHAT,
    ("POST", "/mcp/projects/{project_id}/docs/{filename}/evidence-update"): Capability.CHAT,
    # LLM spend with no run record.
    ("POST", "/mcp/projects/{project_id}/generate-summaries"): Capability.GENERATION,
    # Pack architect: every turn is a model call.
    ("POST", "/mcp/plugins/architect/sessions/{session_id}/messages"): Capability.GENERATION,
    ("POST", "/mcp/projects/{project_id}/git-updates/repair-latest"): Capability.GENERATION,
    ("POST", "/mcp/projects/{project_id}/replace-zip"): Capability.GENERATION,
    # Starts a counted generation in the background: 402 up front, quota included.
    ("POST", "/mcp/projects/{project_id}/git-updates/full-rebuild"): Capability.GENERATION,
    # Forced re-embed: new artefacts, any valid licence.
    ("POST", "/mcp/push-to-rag"): Capability.SCAN,
}

#: Route -> the run-creation call its handler makes (checked against the source).
CHOKEPOINT: dict[tuple[str, str], str] = {
    ("POST", "/mcp/scan"): "create_scan_run",
    ("POST", "/mcp/generate"): "create_generate_run",
    ("POST", "/mcp/generate-runs/{project_id}/{run_id}/rerun"): "create_generate_run",
    ("POST", "/ops/control/scan/{project_id}"): "create_scan_run",
    ("POST", "/ops/control/generate/{project_id}"): "create_generate_run",
    # PR -> scan run; push -> in-handler licence pre-check, then the patch lane
    # or run_git_reingestion (chokepoint). Refusals answer 2xx (no retry storm).
    ("POST", "/webhook/github/webhook"): "create_scan_run",
}

ALLOWLIST: dict[tuple[str, str], str] = {
    # --- Deterministic renders of EXISTING docs (no LLM; verified) ---
    ("POST", "/mcp/projects/{project_id}/generate-pdfs"): "weasyprint render of published docs; no LLM",
    ("POST", "/mcp/projects/{project_id}/generate-assessment"): "markdown+jinja2 render of published docs; no LLM",
    # --- Recovery / hygiene: must work on a view-only instance ---
    ("POST", "/mcp/projects/{project_id}/docs/{filename}/evidence-revert"): "restores a prior version; recovery path",
    ("POST", "/mcp/projects/{project_id}/reingestion-readiness/repair"): "credential hygiene only; never scans/generates/patches",
    ("POST", "/mcp/reset-status"): "clears a stuck status; recovery path",
    # --- Source + project administration (no scan, no model call) ---
    ("POST", "/mcp/add-repo"): "registers a source; scan is a separate, gated step",
    ("POST", "/mcp/upload-zip"): "stores a source; scan is a separate, gated step",
    ("POST", "/mcp/uploaded-sources/verify"): "verifies an upload receipt",
    ("POST", "/mcp/projects/{project_id}/sources"): "adds a source; scan is a separate, gated step",
    ("PATCH", "/mcp/projects/{project_id}"): "project metadata",
    ("DELETE", "/mcp/projects/{project_id}"): "deletion",
    ("PATCH", "/mcp/projects/{project_id}/github-token"): "credential update",
    ("POST", "/mcp/projects/{project_id}/github-webhook/register"): "webhook registration",
    ("DELETE", "/mcp/projects/{project_id}/github-webhook"): "webhook removal",
    ("POST", "/mcp/projects/{project_id}/reviews"): "stores a human review",
    ("PUT", "/mcp/admin/instance-settings"): "operator settings; superadmin",
    ("POST", "/mcp/multi-repo/projects"): "source administration",
    ("POST", "/mcp/multi-repo/projects/{project_id}/members"): "source administration",
    ("DELETE", "/mcp/multi-repo/projects/{project_id}/members/{source_id}"): "source administration",
    ("POST", "/mcp/multi-repo/projects/{project_id}/sync"): "git fetch of member repos; no scan",
    ("POST", "/mcp/azure-devops/connections"): "connection administration",
    ("DELETE", "/mcp/azure-devops/connections/{connection_id}"): "connection administration",
    ("POST", "/mcp/azure-devops/connections/{connection_id}/test"): "connection probe",
    # --- Connectors / runtime sources (evidence plumbing, no model call here) ---
    ("POST", "/mcp/connectors"): "connector administration",
    ("PATCH", "/mcp/connectors/{connector_id}"): "connector administration",
    ("DELETE", "/mcp/connectors/{connector_id}"): "connector administration",
    ("POST", "/mcp/connectors/{connector_id}/test"): "connector probe",
    ("POST", "/mcp/connectors/{connector_id}/discover"): "connector discovery",
    ("POST", "/mcp/connectors/{connector_id}/rotate-credential"): "credential rotation",
    ("PATCH", "/mcp/connectors/{connector_id}/sources/{source_id}"): "source review",
    ("PUT", "/mcp/projects/{project_id}/connector-bindings/{connector_id}"): "binding administration",
    ("DELETE", "/mcp/projects/{project_id}/connector-bindings/{connector_id}"): "binding administration",
    ("POST", "/mcp/projects/{project_id}/connectors/{connector_id}/search"): "evidence read (Elastic query)",
    ("POST", "/mcp/projects/{project_id}/connectors/{connector_id}/freshness"): "evidence read (freshness probe)",
    ("POST", "/mcp/projects/{project_id}/connectors/{connector_id}/tools/{source_id}/call"): "remote MCP tool call; no model call in this service",
    ("POST", "/mcp/runtime-sources"): "proxied to the RAG backend, which owns its gate",
    ("DELETE", "/mcp/runtime-sources/{source_id}"): "proxied to the RAG backend",
    ("POST", "/mcp/runtime-sources/discover"): "proxied to the RAG backend",
    ("POST", "/mcp/runtime-sources/{source_id}/sync"): "proxied to the RAG backend",
    ("POST", "/mcp/runtime-sources/{source_id}/readers"): "proxied to the RAG backend",
    ("DELETE", "/mcp/runtime-sources/{source_id}/readers/{project_id}"): "proxied to the RAG backend",
    # --- Packs (authoring/administration) ---
    ("POST", "/mcp/plugins"): "pack registration",
    ("PATCH", "/mcp/plugins/{pack_id}"): "pack administration",
    ("DELETE", "/mcp/plugins/{pack_id}"): "pack administration",
    ("PUT", "/mcp/plugins/{pack_id}/files/{file_path:path}"): "hand edit of a pack file",
    ("POST", "/mcp/plugins/{pack_id}/publish"): "git push + PR",
    ("POST", "/mcp/plugins/personal"): "creates a personal pack from the starter",
    ("POST", "/mcp/plugins/discover"): "lists remote branches",
    ("POST", "/mcp/plugins/branch-files"): "reads a remote branch",
    ("POST", "/mcp/plugins/branch-file"): "reads a remote file",
    ("PATCH", "/mcp/projects/{project_id}/plugin"): "pack assignment",
    ("POST", "/mcp/plugins/architect/sessions"): "creates an empty session; no model call",
    ("POST", "/mcp/plugins/architect/sessions/{session_id}/save"): "persists the workspace",
    ("POST", "/mcp/plugins/architect/sessions/{session_id}/messages/cancel"): "cancels a turn",
    ("DELETE", "/mcp/plugins/architect/sessions/{session_id}"): "abandons a session",
}


def _mutating_routes() -> dict[tuple[str, str], APIRoute]:
    routes: dict[tuple[str, str], APIRoute] = {}
    for route in app.routes:
        if isinstance(route, APIRoute):
            for method in route.methods - _READ_METHODS:
                routes[(method, route.path)] = route
    return routes


def _capabilities(dependant: Dependant) -> set[Capability]:
    found: set[Capability] = set()
    for dep in dependant.dependencies:
        capability = getattr(dep.call, LICENCE_CAPABILITY_ATTR, None)
        if capability is not None:
            found.add(capability)
        found |= _capabilities(dep)
    return found


def test_every_mutating_route_is_classified_exactly_once() -> None:
    routes = _mutating_routes()
    classified = [*GATED, *CHOKEPOINT, *ALLOWLIST]
    assert len(classified) == len(set(classified)), "a route is listed twice"

    unclassified = sorted(set(routes) - set(classified))
    assert not unclassified, (
        "Unclassified mutating routes -- gate them with require_capability, list them "
        f"as covered by the run-creation chokepoint, or allowlist with a reason: {unclassified}"
    )
    stale = sorted(set(classified) - set(routes))
    assert not stale, f"Inventory lists routes that no longer exist: {stale}"


def test_gated_routes_carry_their_gate() -> None:
    routes = _mutating_routes()
    for key, capability in GATED.items():
        assert _capabilities(routes[key].dependant) == {capability}, f"{key} lost its licence gate"


def test_only_gated_routes_carry_a_gate() -> None:
    """A gate on an allowlisted route means the inventory is out of date."""
    routes = _mutating_routes()
    for key in (*CHOKEPOINT, *ALLOWLIST):
        assert not _capabilities(routes[key].dependant), f"{key} is gated; move it to GATED"


def test_chokepoint_routes_really_create_runs() -> None:
    routes = _mutating_routes()
    for key, call in CHOKEPOINT.items():
        source = inspect.getsource(routes[key].endpoint)
        assert f".{call}(" in source, f"{key} no longer calls {call}; re-classify it"


def test_every_allowlist_entry_has_a_reason() -> None:
    assert all(reason.strip() for reason in ALLOWLIST.values())
