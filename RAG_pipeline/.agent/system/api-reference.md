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
| POST   | /api/v1/auth/setup-mfa  | Yes      | Returns TOTP provisioning URI + QR data             |
| POST   | /api/v1/auth/confirm-mfa| Yes      | Confirms MFA setup, stores secret                   |

---

## RAG Query

| Method | Path                       | Auth | Description                                    |
|--------|----------------------------|------|------------------------------------------------|
| POST   | /api/v1/query              | Yes  | Non-streaming RAG query + source citations     |
| POST   | /api/v1/conversations      | Yes  | Create conversation session                    |
| GET    | /api/v1/conversations      | Yes  | List user's conversations                      |
| POST   | /api/v1/conversations/{id}/messages | Yes | Streaming conversational RAG          |
| GET    | /api/v1/conversations/{id} | Yes  | Get conversation with message history          |
| DELETE | /api/v1/conversations/{id} | Yes  | Delete conversation                            |

---

## Documents

| Method | Path                       | Auth | Description                                    |
|--------|----------------------------|------|------------------------------------------------|
| POST   | /api/v1/ingest             | Yes  | Upload file (PDF/DOCX/PPTX) -> async ingestion |
| GET    | /api/v1/documents          | Yes  | Paginated document list (RBAC filtered)        |
| GET    | /api/v1/documents/{id}     | Yes  | Document metadata                              |
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
| GET    | /api/v1/admin/roles                    | manage_roles     | List roles                        |
| POST   | /api/v1/admin/roles                    | manage_roles     | Create role                       |
| GET    | /api/v1/admin/permissions              | manage_roles     | List all permissions              |
| GET    | /api/v1/admin/project-assignments      | assign_projects  | List project/group assignments    |
| POST   | /api/v1/admin/project-assignments      | assign_projects  | Assign group to project           |
| DELETE | /api/v1/admin/project-assignments/{id} | assign_projects  | Remove assignment                 |
| GET    | /api/v1/admin/audit-logs               | view_audit_logs  | Paginated audit log               |
| GET    | /api/v1/admin/usage/summary            | manage_users     | Aggregated token/cost usage       |
| GET    | /api/v1/admin/usage/recent             | manage_users     | Recent usage records (paginated)  |

---

## Projects (RBAC namespaces)

| Method | Path                   | Auth | Description                                    |
|--------|------------------------|------|------------------------------------------------|
| GET    | /api/v1/projects       | Yes  | List projects visible to current user          |
| POST   | /api/v1/projects       | Yes  | Create project (manager+)                      |
| GET    | /api/v1/projects/{id}  | Yes  | Project details                                |
| PUT    | /api/v1/projects/{id}  | Yes  | Update project                                 |
| DELETE | /api/v1/projects/{id}  | Yes  | Delete project + associated data               |

---

## Notifications

| Method | Path                            | Auth | Description                          |
|--------|---------------------------------|------|--------------------------------------|
| GET    | /api/v1/notifications           | Yes  | List notifications for current user  |
| POST   | /api/v1/notifications/{id}/read | Yes  | Mark notification as read            |
| POST   | /api/v1/notifications/read-all  | Yes  | Mark all notifications read          |

---

## Onboarding

| Method | Path                                        | Auth | Description                        |
|--------|---------------------------------------------|------|------------------------------------|
| GET    | /api/v1/onboarding/status                   | Yes  | Current user onboarding state      |
| POST   | /api/v1/onboarding/step/{step}              | Yes  | Complete a user onboarding step    |
| GET    | /api/v1/company-onboarding/status           | Yes  | Company setup state                |
| GET    | /api/v1/company-onboarding/setup-status     | No   | Whether company setup is complete  |
| POST   | /api/v1/company-onboarding/step/{step}      | Admin| Complete a company onboarding step |

---

## Telemetry

| Method | Path                       | Auth  | Description                           |
|--------|----------------------------|-------|---------------------------------------|
| POST   | /api/v1/telemetry/wizard   | Yes   | Log wizard step event (WizardEvent)   |

---

## Memory

| Method | Path                   | Auth | Description                               |
|--------|------------------------|------|-------------------------------------------|
| POST   | /api/v1/memory/query   | Yes  | Query Hindsight for process memory        |
| POST   | /api/v1/memory/store   | Yes  | Store a finding in Hindsight              |

---

## Tenant

| Method | Path               | Auth | Description                              |
|--------|--------------------|------|------------------------------------------|
| GET    | /api/v1/tenant/config | No | Public tenant branding config             |

---

## Usage (external recording)

| Method | Path                   | Auth (Bearer) | Description                        |
|--------|------------------------|---------------|------------------------------------|
| POST   | /api/v1/usage/record   | MCP_INGEST_API_KEY | Record external LLM usage    |

---

## Benchmarking

| Method | Path                      | Auth | Description                          |
|--------|---------------------------|------|--------------------------------------|
| POST   | /api/v1/benchmark/runs    | Admin| Start a benchmark run                |
| GET    | /api/v1/benchmark/runs    | Admin| List benchmark runs                  |
| GET    | /api/v1/benchmark/runs/{id} | Admin| Get run details + per-question results |
