"""Every mutating route is either licence-gated or on a reviewed allowlist.

This prevents the defect class "a new LLM route forgot the licence gate":
adding a POST/PUT/PATCH/DELETE route fails here until someone decides which of
the two it is. The rule for deciding: if the route can start an LLM call
(chat/agent turn, RAG answer, Hindsight reflect, an agent tool call, a workflow
that resumes generation), it is GATED. Only routes that spend no LLM tokens may
be allowlisted, each with a reason category.

The gate is found structurally, by walking the route's dependency tree for the
callable ``require_capability`` builds (tagged ``__licence_capability__``), so
removing it from a route -- or moving it into the handler body, where it would
run after an SSE stream opened -- fails the test.
"""

from __future__ import annotations

from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from src.api.dependencies import LICENCE_CAPABILITY_ATTR
from src.api.main import app
from src.licensing.core import Capability

MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: (method, path) -> capability. Includes the one GET that runs an LLM.
GATED: dict[tuple[str, str], Capability] = {
    ("POST", "/api/v1/query"): Capability.CHAT,
    ("POST", "/api/v1/query/stream"): Capability.CHAT,
    ("POST", "/api/v1/conversations/{conversation_id}/messages"): Capability.CHAT,
    ("POST", "/api/v1/conversations/{conversation_id}/messages/stream"): Capability.CHAT,
    # Approving resumes the agent and executes the proposed capability.
    ("POST", "/api/v1/conversations/{conversation_id}/approvals/{proposal_id}/decision"): Capability.CHAT,
    ("POST", "/api/v1/labs/conversations/{conversation_id}/messages/stream"): Capability.CHAT,
    # The labs MCP bridge executes agent tool calls inside a running turn.
    ("POST", "/api/v1/labs/mcp"): Capability.CHAT,
    ("POST", "/api/v1/labs/mcp/{token}"): Capability.CHAT,
    # Resuming continues documentation generation / onboarding workflows.
    ("POST", "/api/v1/harness/workflows/{run_id}/resume"): Capability.CHAT,
    # Hindsight runs its LLM for reflect and to compute a mental model.
    ("POST", "/api/v1/memory/reflect-patterns"): Capability.CHAT,
    ("POST", "/api/v1/memory/mental-models"): Capability.CHAT,
    ("GET", "/api/v1/memory/banks/{bank_id}/reflect"): Capability.CHAT,
}

AUTH = "auth: login/session/MFA/invite flows; must work on an expired licence"
ADMIN = "admin/user management: stays open so an owner can administer a lapsed instance"
SETTINGS = "settings/onboarding state: configuration writes, no LLM call"
CONVERSATION_CRUD = "conversation bookkeeping: create/rename/delete/save, no LLM call"
READ_SEARCH = "read-like search: embeds the query and returns raw scores, no generation"
MACHINE = "machine ingest: service-key route called by the MCP server, no LLM call"
DOCS = "document management: delete/index maintenance, no LLM call"
FEEDBACK = "user signal capture: stores a record, no LLM call"
RUNTIME_ADMIN = "runtime source management: config/credentials/grants, no LLM call"
CONNECTOR_ADMIN = "connector management: config and remote health/discovery, no LLM call"
WORKFLOW_CONTROL = "workflow control: cancel/poll an existing run, starts no LLM work"
GUIDANCE = "guidance rules: deterministic rule/preference storage, no LLM call"
# Known gap, outside this slice's files: a sync can run the runtime-insight LLM
# fallback when RUNTIME_LLM_FALLBACK_ENABLED=true (default false). Owed a gate.
RUNTIME_SYNC_LLM_FLAGGED = (
    "runtime sync mirrors data (no tokens); its only LLM spend, the optional fallback, "
    "is licence-checked inside maybe_run_llm_fallback so the background poller is covered too"
)

ALLOWLIST: dict[tuple[str, str], str] = {
    # auth
    ("POST", "/api/v1/auth/request-access"): AUTH,
    ("POST", "/api/v1/auth/otp/send"): AUTH,
    ("POST", "/api/v1/auth/otp/verify"): AUTH,
    ("POST", "/api/v1/auth/invite/start"): AUTH,
    ("POST", "/api/v1/auth/invite/validate"): AUTH,
    ("POST", "/api/v1/auth/invite/accept"): AUTH,
    ("POST", "/api/v1/auth/login"): AUTH,
    ("POST", "/api/v1/auth/verify-mfa"): AUTH,
    ("POST", "/api/v1/auth/totp/setup"): AUTH,
    ("POST", "/api/v1/auth/totp/verify"): AUTH,
    ("POST", "/api/v1/auth/totp/disable"): AUTH,
    ("POST", "/api/v1/auth/bootstrap-admin"): AUTH,
    ("POST", "/api/v1/auth/logout"): AUTH,
    ("POST", "/api/v1/auth/refresh"): AUTH,
    ("POST", "/api/v1/auth/change-password"): AUTH,
    ("POST", "/api/v1/auth/set-password"): AUTH,
    # admin / user management
    ("POST", "/api/v1/admin/access-requests/{request_id}/approve"): ADMIN,
    ("POST", "/api/v1/admin/access-requests/{request_id}/reject"): ADMIN,
    ("POST", "/api/v1/admin/invites/{invite_id}/resend"): ADMIN,
    ("POST", "/api/v1/admin/invites/{invite_id}/revoke"): ADMIN,
    ("POST", "/api/v1/admin/email/test"): ADMIN,
    ("POST", "/api/v1/admin/users"): ADMIN,
    ("PUT", "/api/v1/admin/users/{user_id}"): ADMIN,
    ("DELETE", "/api/v1/admin/users/{user_id}"): ADMIN,
    ("POST", "/api/v1/admin/users/{user_id}/reset-password"): ADMIN,
    ("POST", "/api/v1/admin/roles"): ADMIN,
    ("PUT", "/api/v1/admin/roles/{role_id}"): ADMIN,
    ("DELETE", "/api/v1/admin/roles/{role_id}"): ADMIN,
    ("POST", "/api/v1/admin/projects"): ADMIN,
    ("POST", "/api/v1/admin/project-assignments"): ADMIN,
    ("DELETE", "/api/v1/admin/project-assignments/{assignment_id}"): ADMIN,
    ("POST", "/api/v1/admin/auth/rotate-key"): ADMIN,
    ("PUT", "/api/v1/admin/instance-settings"): SETTINGS,
    # read-like search
    ("POST", "/api/v1/query/debug"): READ_SEARCH,
    # document management
    ("DELETE", "/api/v1/documents/{document_id}"): DOCS,
    # machine ingest (MCP server -> backend)
    ("POST", "/api/v1/ingest/revisions/prepare"): MACHINE,
    ("POST", "/api/v1/ingest/revisions/{project_id}/{knowledge_revision_id}/activate"): MACHINE,
    ("POST", "/api/v1/ingest/revisions/{project_id}/{knowledge_revision_id}/rollback"): MACHINE,
    ("POST", "/api/v1/ingest/runtime-static-topology"): MACHINE,
    ("POST", "/api/v1/ingest"): MACHINE,
    ("DELETE", "/api/v1/ingest/by-project/{project_id}"): MACHINE,
    ("DELETE", "/api/v1/ingest/by-project/{project_id}/internal"): MACHINE,
    ("POST", "/api/v1/ingest/entity-aliases"): MACHINE,
    ("DELETE", "/api/v1/ingest/by-project/{project_id}/document/{filename}"): MACHINE,
    ("POST", "/api/v1/ingest/md"): MACHINE,
    ("POST", "/api/v1/usage/record"): MACHINE,
    ("POST", "/api/v1/runtime/tools/{tool_name}"): MACHINE,
    ("POST", "/api/v1/runtime/elastic/probe/describe-fields"): MACHINE,
    ("POST", "/api/v1/runtime/elastic/probe/list-indices"): MACHINE,
    ("POST", "/api/v1/runtime/elastic/probe/count-matching"): MACHINE,
    ("POST", "/api/v1/runtime/elastic/probe/size-distribution"): MACHINE,
    # conversation bookkeeping
    ("POST", "/api/v1/conversations"): CONVERSATION_CRUD,
    ("PATCH", "/api/v1/conversations/{conversation_id}"): CONVERSATION_CRUD,
    ("DELETE", "/api/v1/conversations/{conversation_id}"): CONVERSATION_CRUD,
    ("POST", "/api/v1/conversations/{conversation_id}/messages/save"): CONVERSATION_CRUD,
    ("POST", "/api/v1/labs/conversations"): CONVERSATION_CRUD,
    ("DELETE", "/api/v1/labs/conversations/{conversation_id}"): CONVERSATION_CRUD,
    # onboarding / settings / notifications
    ("PUT", "/api/v1/onboarding/step/workspace"): SETTINGS,
    ("PUT", "/api/v1/onboarding/step/platforms"): SETTINGS,
    ("PUT", "/api/v1/onboarding/step/comm_tools"): SETTINGS,
    ("PUT", "/api/v1/onboarding/step/source"): SETTINGS,
    ("POST", "/api/v1/onboarding/complete"): SETTINGS,
    ("POST", "/api/v1/onboarding/tour/complete"): SETTINGS,
    ("PUT", "/api/v1/onboarding/tour/step"): SETTINGS,
    ("POST", "/api/v1/onboarding/reset"): SETTINGS,
    ("PUT", "/api/v1/onboarding/company/step/workspace"): SETTINGS,
    ("PUT", "/api/v1/onboarding/company/step/platforms"): SETTINGS,
    ("PUT", "/api/v1/onboarding/company/step/comm_tools"): SETTINGS,
    ("PUT", "/api/v1/onboarding/company/step/source"): SETTINGS,
    ("POST", "/api/v1/onboarding/company/complete"): SETTINGS,
    ("POST", "/api/v1/onboarding/company/reset"): SETTINGS,
    ("PUT", "/api/v1/notifications/{notification_id}/read"): SETTINGS,
    ("PUT", "/api/v1/notifications/read-all"): SETTINGS,
    ("POST", "/api/v1/telemetry/wizard-event"): FEEDBACK,
    ("POST", "/api/v1/feedback"): FEEDBACK,
    # runtime source management
    ("POST", "/api/v1/runtime/job-scan-maps"): RUNTIME_ADMIN,
    ("DELETE", "/api/v1/runtime/job-scan-maps/{project_id}/{source_key}/{runtime_job_id}"): RUNTIME_ADMIN,
    ("POST", "/api/v1/runtime/sources/discover"): RUNTIME_ADMIN,
    ("POST", "/api/v1/runtime/sources/{source_id}/scopes"): RUNTIME_ADMIN,
    ("PATCH", "/api/v1/runtime/sources/{source_id}/config"): RUNTIME_ADMIN,
    ("POST", "/api/v1/runtime/sources"): RUNTIME_ADMIN,
    ("PATCH", "/api/v1/runtime/sources/{source_id}"): RUNTIME_ADMIN,
    ("POST", "/api/v1/runtime/sources/{source_id}/credentials/rotate"): RUNTIME_ADMIN,
    ("POST", "/api/v1/runtime/sources/{source_id}/sync"): RUNTIME_SYNC_LLM_FLAGGED,
    ("DELETE", "/api/v1/runtime/sources/{source_id}"): RUNTIME_ADMIN,
    ("POST", "/api/v1/runtime/sources/{source_id}/workspace-map"): RUNTIME_ADMIN,
    ("DELETE", "/api/v1/runtime/sources/{source_id}/workspace-map/{container_key}"): RUNTIME_ADMIN,
    ("POST", "/api/v1/runtime/sources/{source_id}/readers"): RUNTIME_ADMIN,
    ("DELETE", "/api/v1/runtime/sources/{source_id}/readers/{project_id}"): RUNTIME_ADMIN,
    # workflow control
    ("POST", "/api/v1/harness/workflows/{run_id}/cancel"): WORKFLOW_CONTROL,
    ("POST", "/api/v1/harness/workflows/{run_id}/reconcile"): WORKFLOW_CONTROL,
    # guidance
    ("POST", "/api/v1/guidance/org"): GUIDANCE,
    ("DELETE", "/api/v1/guidance/org/{rule_id}"): GUIDANCE,
    ("POST", "/api/v1/guidance/projects/{project_id}"): GUIDANCE,
    ("DELETE", "/api/v1/guidance/projects/{project_id}/rules/{rule_id}"): GUIDANCE,
    ("POST", "/api/v1/guidance/projects/{project_id}/rules/{rule_id}/approve"): GUIDANCE,
    ("PUT", "/api/v1/guidance/projects/{project_id}/preferences"): GUIDANCE,
    # connectors
    ("POST", "/api/v1/connectors/existing-elastic/discover"): CONNECTOR_ADMIN,
    ("POST", "/api/v1/connectors/existing-elastic/use"): CONNECTOR_ADMIN,
    ("POST", "/api/v1/connectors/remote-mcp"): CONNECTOR_ADMIN,
    ("PUT", "/api/v1/connectors/remote-mcp/{instance_id}/grants"): CONNECTOR_ADMIN,
    ("POST", "/api/v1/connectors"): CONNECTOR_ADMIN,
    ("PATCH", "/api/v1/connectors/{instance_id}"): CONNECTOR_ADMIN,
    ("DELETE", "/api/v1/connectors/{instance_id}"): CONNECTOR_ADMIN,
    ("POST", "/api/v1/connectors/{instance_id}/health"): CONNECTOR_ADMIN,
    ("POST", "/api/v1/connectors/{instance_id}/capabilities/discover"): CONNECTOR_ADMIN,
    ("POST", "/api/v1/connectors/{instance_id}/preview"): CONNECTOR_ADMIN,
    ("POST", "/api/v1/connectors/{instance_id}/freshness"): CONNECTOR_ADMIN,
    ("POST", "/api/v1/connectors/{instance_id}/bindings"): CONNECTOR_ADMIN,
    ("DELETE", "/api/v1/connectors/{instance_id}/bindings/{binding_id}"): CONNECTOR_ADMIN,
    ("PATCH", "/api/v1/connectors/{instance_id}/bindings/{binding_id}"): CONNECTOR_ADMIN,
}


def _api_routes() -> list[APIRoute]:
    return [route for route in app.routes if isinstance(route, APIRoute)]


def _capabilities(dependant: Dependant) -> set[Capability]:
    found: set[Capability] = set()
    stack = [dependant]
    while stack:
        node = stack.pop()
        capability = getattr(node.call, LICENCE_CAPABILITY_ATTR, None)
        if capability is not None:
            found.add(capability)
        stack.extend(node.dependencies)
    return found


def _route_keys(route: APIRoute) -> list[tuple[str, str]]:
    return [(method, route.path) for method in sorted(route.methods)]


def _index() -> dict[tuple[str, str], APIRoute]:
    return {key: route for route in _api_routes() for key in _route_keys(route)}


def test_every_mutating_route_is_gated_or_allowlisted():
    unreviewed = []
    for route in _api_routes():
        for method, path in _route_keys(route):
            if method not in MUTATING:
                continue
            key = (method, path)
            if _capabilities(route.dependant):
                continue
            if key in ALLOWLIST:
                continue
            unreviewed.append(f"{method} {path} ({route.endpoint.__module__}.{route.endpoint.__name__})")
    assert not unreviewed, (
        "Mutating routes with neither a licence gate nor an allowlist entry. If the route can "
        "start an LLM call, gate it with require_capability (src/api/dependencies.py); otherwise "
        "add it to ALLOWLIST with a reason:\n  " + "\n  ".join(sorted(unreviewed))
    )


def test_every_gated_route_carries_its_gate():
    routes = _index()
    missing_route = sorted(f"{m} {p}" for m, p in GATED if (m, p) not in routes)
    assert not missing_route, (
        "Gated routes no longer exist under these paths (renamed or removed?). Update GATED so "
        "coverage follows the route:\n  " + "\n  ".join(missing_route)
    )
    ungated = sorted(
        f"{m} {p} (expected {cap.value}, found {sorted(c.value for c in _capabilities(routes[(m, p)].dependant))})"
        for (m, p), cap in GATED.items()
        if cap not in _capabilities(routes[(m, p)].dependant)
    )
    assert not ungated, "Licence gate missing on LLM routes:\n  " + "\n  ".join(ungated)


def test_no_route_is_both_gated_and_allowlisted():
    assert not set(GATED) & set(ALLOWLIST)


def test_allowlist_has_no_stale_entries():
    routes = _index()
    stale = sorted(f"{m} {p}" for m, p in ALLOWLIST if (m, p) not in routes)
    assert not stale, "Allowlisted routes that no longer exist:\n  " + "\n  ".join(stale)


def test_allowlisted_routes_are_not_silently_gated():
    # A route that gained the gate must move from ALLOWLIST to GATED so the
    # second test protects it from losing the gate again.
    routes = _index()
    moved = sorted(f"{m} {p}" for m, p in ALLOWLIST if _capabilities(routes[(m, p)].dependant))
    assert not moved, "Gated routes still on ALLOWLIST; move them to GATED:\n  " + "\n  ".join(moved)


def test_every_route_carrying_a_gate_is_declared_in_gated():
    undeclared = sorted(
        f"{m} {p}"
        for route in _api_routes()
        for m, p in _route_keys(route)
        if _capabilities(route.dependant) and (m, p) not in GATED
    )
    assert not undeclared, "Gated routes missing from GATED:\n  " + "\n  ".join(undeclared)


def test_inventory_detects_a_removed_gate():
    """Self-check: the dependency walk really distinguishes gated from ungated."""
    routes = _index()
    stream = routes[("POST", "/api/v1/conversations/{conversation_id}/messages/stream")]
    listing = routes[("GET", "/api/v1/conversations")]
    assert _capabilities(stream.dependant) == {Capability.CHAT}
    assert _capabilities(listing.dependant) == set()
