---
name: lfe-extract-domain
description: Extract undocumented project domain knowledge. Use on blank canvas, black-box encounters, or Inspector failure recovery to interview, standardize vocabulary, and update domain docs.
---

# LFE Extract Domain

Use a structured interview to document missing domain knowledge.

## Four Questions

1. What is the single most critical entity and its definition?
2. What is the primary calculation or golden rule, step by step?
3. What absolute constraints apply: legal, safety, architectural, or operational?
4. What vocabulary must be standardized or disambiguated?

## Outputs

- `.docs/domain/domain-knowledge.md` if present or appropriate.
- `CONTEXT.md`, merged into the existing file rather than overwritten.
- ADRs when a durable trade-off warrants one.
- `.plans/pipeline_status.md` update if present.

Glossary entries should be one-sentence definitions.

