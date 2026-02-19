# ADR-001: Platform-Explicit Architecture

**Date:** 2026-02-16
**Status:** Accepted

## Context
When scanning integration codebases, we could either auto-detect the platform from file patterns or require the user to explicitly declare it.

## Decision
Require explicit `--platform` flag (e.g., `intodoc scan --platform talend`).

## Rationale
- **Auto-detect is fragile:** Many integration platforms share similar file structures (XML configs, Java code, property files). Heuristics would be unreliable.
- **Platform-specific prompts are far more accurate:** Each platform has unique terminology, patterns, and best practices. Tailored LLM prompts produce significantly better analysis results.
- **Plugin isolation:** Each platform is a self-contained plugin with its own parser, distiller, file rules, and prompts. No cross-contamination.
- **Extensibility:** Adding a new platform is just creating a new directory under `platforms/` — no changes to detection logic.

## Consequences
- Users must know which platform their project uses (trivial for ETL teams)
- Each platform plugin is fully independent and can evolve separately
- No risk of misclassification
