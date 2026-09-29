# RAG Pipeline -- API Reference

Base path: `http://localhost:8000`
All protected endpoints require a valid JWT access token (httpOnly cookie `access_token`).

---

## Health

| Method | Path      | Auth | Description                                  |
|--------|-----------|------|----------------------------------------------|
| GET    | /health   | No   | Check DB, Qdrant, Ollama, Hindsight status   |

---

## Auth

| Method | Path                    | Auth     | Description                                         |
|--------|-------------------------|----------|-----------------------------------------------------|
| POST   | /api/v1/auth/login      | No       | Username/password -> sets access+refresh cookies    |
| POST   | /api/v1/auth/logout     | Yes      | Invalidates refresh token, clears cookies           |
| POST   | /api/v1/auth/refresh    | Cookie   | Rotates refresh token, issues new access token      |
| GET    | /api/v1/auth/me         | Yes      | Returns current user + permissions                  |
| POST   | /api/v1/auth/verify-mfa | MFA token| TOTP verification, exchanges mfa_token for cookies  |
| POST   | /api/v1/auth/totp/setup | Yes      | Returns TOTP provisioning URI + QR data             |
| POST   | /api/v1/auth/totp/verify| Yes      | Confirms TOTP setup and enables MFA                 |
| POST   | /api/v1/auth/totp/disable | Yes    | Disables MFA                                        |
| POST   | /api/v1/auth/otp/send   | No       | Send an email one-time password                     |
| POST   | /api/v1/auth/otp/verify | No       | Verify an email one-time password                   |
| POST   | /api/v1/auth/request-access | No   | Submit an access request                            |
| POST   | /api/v1/auth/invite/start | No     | Start the invite acceptance flow                    |
| POST   | /api/v1/auth/invite/validate | No  | Validate an invite token                            |
| POST   | /api/v1/auth/invite/accept | No    | Complete registration via invite                    |
| GET    | /api/v1/auth/bootstrap-status | No | Whether initial admin setup is needed               |
| POST   | /api/v1/auth/bootstrap-admin | No  | Create the first admin (only while no users exist)  |
| POST   | /api/v1/auth/change-password | Yes | Self-service password change                        |
| POST   | /api/v1/auth/set-password | Yes    | Create the first password for an account            |

Rate limits (slowapi): 5/min on login and most unauthenticated auth routes,
10/min on `/invite/validate`, 60/min on `/refresh` (`src/api/routes/auth.py`).

---

## RAG Query

| Method | Path                       | Auth | Description                                    |
|--------|----------------------------|------|------------------------------------------------|
| POST   | /api/v1/query              | Yes  | Non-streaming RAG query + source citations     |
| POST   | /api/v1/query/stream       | Yes  | SSE query (`token`, `sources`, `usage`, `done`, `error` events) |
| POST   | /api/v1/query/debug        | Yes  | Search scores without threshold                |
| POST   | /api/v1/conversations      | Yes  | Create conversation session                    |
| GET    | /api/v1/conversations      | Yes  | List user's conversations                      |
| GET    | /api/v1/conversations/commands | Yes | List slash commands                        |
| GET    | /api/v1/conversations/{id} | Yes  | Get conversation with message history          |
| PATCH  | /api/v1/conversations/{id} | Yes  | Update conversation                            |
| DELETE | /api/v1/conversations/{id} | Yes  | Delete conversation                            |
| POST   | /api/v1/conversations/{id}/messages | Yes | Agent chat turn (non-streaming)       |
| POST   | /api/v1/conversations/{id}/messages/stream | Yes | Agent chat turn (SSE)          |
| POST   | /api/v1/conversations/{id}/messages/save | Yes | Save a message                   |

Conversation turns run through the harness agent runtime (`src/harness/`), gated by
`ChatEntitledUser` (licence capability CHAT).

---

## Documents

| Method | Path                       | Auth | Description                                    |
|--------|----------------------------|------|------------------------------------------------|
| POST   | /api/v1/ingest             | Yes  | Upload file (PDF/DOCX/PPTX/XLSX, max 50 MB)    |
| POST   | /api/v1/ingest/md          | Bearer `MCP_INGEST_API_KEY` | Markdown ingest from the MCP server |
| GET    | /api/v1/documents          | Yes  | Paginated document list (RBAC filtered)        |
| DELETE | /api/v1/documents/{id}     | Yes  | Remove document + Qdrant vectors               |
| POST   | /api/v1/feedback           | Yes  | Submit thumbs up/down on a response            |

---

## Admin

All admin endpoints require the relevant permission (see `system/subsystems.md` RBAC section).

| Method | Path                                   | Permission       | Description                       |
|--------|----------------------------------------|------------------|-----------------------------------|
| GET    | /api/v1/admin/users                    | manage_users     | List all users                    |
| POST   | /api/v1/admin/users                    | manage_users     | Create user                       |
| PUT    | /api/v1/admin/users/{id}               | manage_users     | Update user                       |
| DELETE | /api/v1/admin/users/{id}               | manage_users     | Delete user                       |
| POST   | /api/v1/admin/users/{id}/reset-password | manage_users    | Admin password reset              |
| GET    | /api/v1/admin/roles                    | manage_roles     | List roles                        |
| POST   | /api/v1/admin/roles                    | manage_roles     | Create role                       |
| GET    | /api/v1/admin/permissions              | manage_roles     | List all permissions              |
| GET    | /api/v1/admin/project-assignments      | assign_projects  | List project/group assignments    |
| POST   | /api/v1/admin/project-assignments      | assign_projects  | Assign group to project           |
| DELETE | /api/v1/admin/project-assignments/{id} | assign_projects  | Remove assignment                 |
| GET    | /api/v1/admin/audit-logs               | view_audit_logs  | Paginated audit log               |
| GET    | /api/v1/admin/usage/summary            | manage_users     | Aggregated token/cost usage       |
| GET    | /api/v1/admin/usage/recent             | manage_users     | Recent usage records (paginated)  |
| GET    | /api/v1/admin/usage/by-user, /usage/daily-by-user, /usage/user/{id}, /usage/recent-threads | manage_users | Per-user usage views |
| GET/POST | /api/v1/admin/projects               | assign_projects  | List / create projects            |
| POST   | /api/v1/admin/invites/{id}/resend      | manage_users     | Resend invite email               |
| POST   | /api/v1/admin/invites/{id}/revoke      | manage_users     | Revoke invite                     |
| GET    | /api/v1/admin/access-requests          | manage_users     | Pending requests + open invites   |
| POST   | /api/v1/admin/access-requests/{id}/approve | manage_users | Approve, choosing the member role |
| POST   | /api/v1/admin/access-requests/{id}/reject  | manage_users | Reject with a reason              |

### Approving access

`POST /admin/access-requests/{id}/approve` body (all fields optional):

```json
{ "role_id": 3, "project_ids": ["werkorder"], "welcome_note": "..." }
```

- `role_id` -- role the invitee arrives with. Owner-named roles are rejected
  (403). Omitting it means acceptance applies `DEFAULT_MEMBER_ROLE`.
- `project_ids` -- pre-granted as membership, never ownership. Requires
  `assign_projects` in addition to `manage_users` (403 otherwise).
- `welcome_note` -- shown on the invitee's first screen next to the approver's
  name.

The decision is stored on the invite and applied by `POST /auth/invite/accept`.
The grant is audited as `access_request_approved`. Calling with no body remains
valid, so an older client keeps working.

---

## Projects (RBAC namespaces)

| Method | Path                   | Auth | Description                                    |
|--------|------------------------|------|------------------------------------------------|
| GET    | /api/v1/projects       | Yes  | List projects visible to current user          |

Project creation is `POST /api/v1/admin/projects`; project CRUD for the
dashboard is on the MCP server (`/mcp/projects`).

---

## Notifications

| Method | Path                            | Auth | Description                          |
|--------|---------------------------------|------|--------------------------------------|
| GET    | /api/v1/notifications           | Yes  | List notifications for current user  |
| GET    | /api/v1/notifications/unread-count | Yes | Unread count                      |
| PUT    | /api/v1/notifications/{id}/read | Yes  | Mark notification as read            |
| PUT    | /api/v1/notifications/read-all  | Yes  | Mark all notifications read          |

---

## Onboarding

| Method | Path                                        | Auth | Description                        |
|--------|---------------------------------------------|------|------------------------------------|
| GET    | /api/v1/onboarding/status                   | Yes  | User onboarding state + persona    |
| PUT    | /api/v1/onboarding/step/{workspace,platforms,comm_tools,source} | Yes | Save a user onboarding step |
| POST   | /api/v1/onboarding/complete                 | Yes  | Complete the user wizard           |
| PUT    | /api/v1/onboarding/tour/step                | Yes  | Save walkthrough progress          |
| POST   | /api/v1/onboarding/tour/complete            | Yes  | Mark the walkthrough finished      |
| GET    | /api/v1/onboarding/company/status           | Yes  | Company setup state                |
| GET    | /api/v1/onboarding/company/setup-status     | No   | Whether company setup is complete  |
| PUT    | /api/v1/onboarding/company/step/{workspace,platforms,comm_tools,source} | Admin | Save a company onboarding step |
| POST   | /api/v1/onboarding/company/complete, /reset | Admin| Complete / reset company setup     |

`GET /onboarding/status` additionally returns:

- `persona` -- `owner | admin | project_owner | developer`, resolved server-side
  by `src/rbac/persona.py`. Drives which wizard and which walkthrough the user
  gets. The frontend must not re-derive it.
- `tour_variant`, `tour_seen_steps` -- resumable walkthrough progress, tracked by
  step id because each persona's tour is a different length.
- `invite_context` -- `{approved_by, assigned_role, assigned_project_ids,
  welcome_note}`, present only while the user's wizard is incomplete, so the
  first screen can name who approved them.

`PUT /onboarding/tour/step` body: `{tour_step, step_id?, variant?}`. `step_id`
and `variant` are optional for backwards compatibility but required in practice
for resume to work.

Any new field must also be added to `OnboardingStatusSchema` in
`frontend/types/onboarding.ts` -- Zod strips undeclared keys, so an unlisted
field is permanently `undefined` in the browser with no error.

---

## Telemetry

| Method | Path                       | Auth  | Description                           |
|--------|----------------------------|-------|---------------------------------------|
| POST   | /api/v1/telemetry/wizard-event | Yes | Log wizard step event (WizardEvent) |
| GET    | /api/v1/telemetry/wizard/summary | Yes | Wizard telemetry summary           |

---

## Memory

| Method | Path                   | Auth | Description                               |
|--------|------------------------|------|-------------------------------------------|
| GET    | /api/v1/memory/banks   | Yes  | List Hindsight memory banks               |
| GET    | /api/v1/memory/banks/{bank_id}/memories | Yes | Memories in a bank           |
| GET    | /api/v1/memory/banks/{bank_id}/reflect | Yes | Reflect over a bank           |
| GET    | /api/v1/memory/recent-captures | Yes | Recently captured memories        |
| POST   | /api/v1/memory/mental-models, /reflect-patterns | Yes | Hindsight mental models / pattern reflection |

---

## Tenant

| Method | Path               | Auth | Description                              |
|--------|--------------------|------|------------------------------------------|
| GET    | /api/v1/tenant/config | No | Public tenant branding config             |

---

## Runtime (EP-004 slice 1)

| Method | Path                     | Auth | Description                                          |
|--------|--------------------------|------|-------------------------------------------------------|
| GET    | /api/v1/runtime/status   | Yes  | Live runtime provider status: disabled/connected/unavailable |

Provider-agnostic contract. `runtime_provider_key` empty means `disabled`. A
configured key without `runtime_provider_health_url` means `unavailable`. No
Talend API details, task IDs, secrets, or logs are exposed by this endpoint.

### Runtime logs read API (EP-005 slice 2)

Read-only access to the held runtime-data store for the logs UI.

| Method | Path                                          | Auth | Description                          |
|--------|-----------------------------------------------|------|--------------------------------------|
| GET    | /api/v1/runtime/executions                    | Yes  | List executions (bounded)            |
| GET    | /api/v1/runtime/executions/{execution_id}     | Yes  | Single execution detail              |
| GET    | /api/v1/runtime/executions/{execution_id}/logs| Yes  | Log events for one execution         |
| GET    | /api/v1/runtime/failures                      | Yes  | List failures (bounded)              |

Query params:
- `executions`: `project_id`, `job_name`, `state` (success/failed/running/unknown),
  `since`, `until` (ISO-8601 datetimes), `limit` (1–200, default 50).
- `.../logs`: `min_severity` (debug/info/warning/error), `limit` (1–1000, default 100).
- `failures`: `project_id`, `since`, `until`, `limit` (1–200, default 50).

RBAC: every endpoint derives project scope from `CurrentUser` via
`get_user_project_ids()` (None = unrestricted, [] = no access, [ids] = restricted).
`project_id` is a filter *within* the caller's scope — an out-of-scope value yields an
empty list, never another project's rows. Cross-project or unknown `execution_id`
point lookups return `404` (no existence leak). An in-scope execution with no log lines
matching `min_severity` returns an empty list with `200`, not `404`. Reads come from the
Postgres runtime store via `RuntimeStoreRepository`; logs are never fetched from Qdrant.

Response envelopes: `{executions, total}`, `{execution}`, `{execution_id, events, total}`,
`{failures, total}`. `total` is the count returned in this bounded response, not a
full-table pagination total.

---

## Usage (external recording)

| Method | Path                   | Auth (Bearer) | Description                        |
|--------|------------------------|---------------|------------------------------------|
| POST   | /api/v1/usage/record   | MCP_INGEST_API_KEY | Record external LLM usage    |

---

## Benchmarking

| Method | Path                      | Auth | Description                          |
|--------|---------------------------|------|--------------------------------------|
| GET    | /api/v1/benchmark/runs    | Admin| List benchmark runs                  |
| GET    | /api/v1/benchmark/runs/{id} | Admin| Get run details + per-question results |

Runs are started from the CLI (`python -m benchmark run`), not the API.
