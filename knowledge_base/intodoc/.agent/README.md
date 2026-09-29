# intodoc Documentation Index

Scanner-specific docs. Platform-wide architecture lives in
`knowledge_base/.agent/system/architecture.md`; cross-cutting ADRs live in
`knowledge_base/.agent/decisions/`.

## SOPs (Standard Operating Procedures)
- `sop/add-platform.md` -- How to add a new platform plugin

## Decisions (ADRs, scanner-local)
- `decisions/002-credential-filter-first.md` -- Why credential filtering runs before parsing
- `decisions/003-enterprise-artifact-fidelity.md` -- Structures/XSLT/services parsing, the two deterministic ledgers, identity-first design references, and the sanitized workspace

Platform-explicit scanning (formerly scanner-local ADR-001) is
`knowledge_base/.agent/decisions/027-platform-explicit-scanning.md`.
