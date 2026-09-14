# ADR-011: Git Remote Credential Handling Is Centralized

## Status
Accepted 2026-06-18; extended 2026-08-24

## Context

Token-authenticated Git work was scattered across the poller, the diff patcher, full
reingestion and auto-doc update. Each site injected a PAT into the remote URL with
`remote set-url` and none of them reset it, so the token persisted in `.git/config` and
surfaced in journal output on any later git error.

## Decision

`services/git_remote_service.py` is the single allowed path for token-authenticated Git
remote work. Later, `services/git_credentials.py::provider_for_project()` became the
single resolution point for which credential to use.

## Rationale

"Reingestion reliability Slice 1 added the single allowed path for token-authenticated Git
remote work: strip credentials for persisted `repo_url`/`origin`, build authenticated URLs
only in memory, set non-interactive Git env (`GIT_TERMINAL_PROMPT=0`, `GIT_ASKPASS=echo`),
and restore sanitized `origin` in `finally` after fetch/pull operations. [...] Future
webhook/reingestion slices must not reintroduce direct `inject_token` + `git remote
set-url` call sites outside the helper and clone cleanup."

"In a no-TTY systemd context, any host-level credential helper causes git fetch to abort
with 'could not read Password'."

## Consequences

- Never resolve git credentials by hand. Reading `project.github_token_enc` directly skips
  GitHub App and Azure DevOps connections, whose credential lives off the project row --
  this was a real defect where App-backed projects authenticated on clone but silently
  failed every subsequent fetch and pull.
- The encrypted-string URL builders (`encryption.inject_token`,
  `git_remote_service.authenticated_url`) were deleted rather than left as a second,
  broken path.
- Readiness reports classify sanitized auth failures so an auth-failing project cannot look
  ready.

## Source
Promoted from `knowledge_base/.claude/PROJECT_MEMORY.md -- Architecture Decisions` on 2026-09-04. The original file is preserved verbatim under the
owning sub-project's `.claude/archive/PROJECT_MEMORY.md`.
