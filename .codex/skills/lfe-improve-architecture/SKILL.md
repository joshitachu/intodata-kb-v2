---
name: lfe-improve-architecture
description: Run LFE architecture improvement after hygiene. Use to identify shallow modules, apply the deletion test, present refactor candidates, and guide a design conversation.
---

# LFE Improve Architecture

Identify opportunities to deepen shallow modules.

## Vocabulary

- Module: interface plus implementation.
- Depth: significant behavior behind a minimal interface.
- Seam: an interface location for behavioral substitution.
- Adapter: concrete implementation at a seam.
- Leverage: caller benefit.
- Locality: maintainer benefit.

## Diagnostic

Apply the deletion test: if removing a module makes complexity spread across callers rather than disappear, the module earns its existence.

## Workflow

1. Explore glossary, ADRs, and code for friction such as concept-hopping, shallow modules, untested coupling, and hard-to-test interfaces.
2. Present numbered candidates with files, problem statement, plain-English solution, and benefits in locality/leverage terms.
3. Run a design conversation on the selected candidate.
4. Update `CONTEXT.md` for new or fuzzy concepts and offer an ADR when a trade-off warrants it.
5. Delete `.plans/hygiene_report.md` after review and decision to prevent stale-file detection.

