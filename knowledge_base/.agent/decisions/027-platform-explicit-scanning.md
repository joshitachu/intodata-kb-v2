# ADR-027: Scanning Is Platform-Explicit, Not Auto-Detected

## Status
Accepted 2026-02-16; still current

## Context

A scanner that guesses the platform from file shapes gets it wrong on mixed exports and on
platforms whose serialization overlaps, and the failure is silent: the wrong parser
produces a plausible but shallow scan.

## Decision

The user declares `--platform talend|workato|lobster|generic`. That selection loads the
platform-specific parser, prompts and best practices. Each platform is a self-contained
plugin under `src/intodoc/platforms/<name>/` behind the `PlatformPlugin` ABC, with
`_shared/prompts/` as an overridable fallback layer.

## Rationale

"Platform-explicit (--platform flag): auto-detect is fragile; platform-specific prompts are
far more accurate."

"`platforms/<name>/` plugin architecture: each platform self-contained with parser, prompts,
best practices."

"Shared Pydantic models in models.py: single source of truth for data shapes across all
platforms."

"Credential filter before parsing: raw text redaction prevents accidental credential
exposure at any stage."

- **Plugin isolation**: each platform plugin carries its own parser, distiller, file rules
  and prompts, so platforms cannot cross-contaminate and evolve independently.
- **Extensibility**: adding a platform is a new directory under `platforms/` (see
  `intodoc/.agent/sop/add-platform.md`); there is no detection logic to change.

## Consequences

- Users must know which platform their project uses (trivial for ETL teams); there is no
  misclassification risk.
- Discovery inside a platform may still be content-based where filenames are unreliable:
  the Workato scanner classifies by JSON content rather than filename patterns, and handles
  three known export formats plus unknown variations through `or` fallback chains.
- Required section names are platform-scoped. Talend section validation is skipped for
  non-Talend platforms, because COMPONENTS / EXECUTION_FLOW / DATA_FLOWS /
  TRANSFORMATION_DETAIL are Talend concepts.
- The Universal IR (`src/intodoc/ir/`) was the platform-agnostic layer, deliberately
  decoupled from the platform-specific scan models. It has since been removed from the
  codebase; the directory no longer exists.

## Source
Promoted from `knowledge_base/intodoc/.claude/PROJECT_MEMORY.md -- Architecture Decisions` on 2026-09-04. The original file is preserved verbatim under the
owning sub-project's `.claude/archive/PROJECT_MEMORY.md`. Absorbs the former scanner-local
`intodoc/.agent/decisions/001-platform-explicit.md` (2026-02-16).
