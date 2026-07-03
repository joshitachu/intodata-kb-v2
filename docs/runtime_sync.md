# Runtime Sync

Runtime ingestion is source-agnostic:

`runtime source -> adapter -> central DB -> insight/health APIs -> dashboard/chat`

## Scheduler

The backend can run a config-gated background scheduler. It is disabled by default.

Settings:

- `RUNTIME_SYNC_SCHEDULER_ENABLED=true`
- `RUNTIME_SYNC_INTERVAL_SECONDS=60`
- `RUNTIME_SYNC_SOURCE_KEY=talend_tmc`
- `RUNTIME_SYNC_PROJECT_IDS=project-a,project-b`

For Talend TMC, also set:

- `RUNTIME_PROVIDER_KEY=talend_tmc`
- `TALEND_TMC_BASE_URL=...`
- `TALEND_TMC_API_TOKEN_FILE=/path/to/token`

The scheduler runs one non-overlapping sync cycle per configured project. Failures are recorded in
`runtime_sync_state` with sanitized status; user-facing reads continue from the last DB state.

Live TMC smoke requires credentials. Fixture tests cover endpoint mapping, sync write behavior,
health rollups, and scheduler lifecycle without live access.
