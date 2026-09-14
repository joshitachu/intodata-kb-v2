# intodoc Sub-project Context

Live state only. Session history is in `.claude/PROGRESS_LOG.md`; architecture decisions are in `knowledge_base/.agent/decisions/`.

## What this sub-project is

`knowledge_base/intodoc/` is the deterministic scanner and parser foundation. It establishes technical truth; it does not choose investigation goals or write client documentation.

## Current state

- Talend, Workato, Lobster and Generic platform plugins share the `PlatformPlugin` boundary.
- A complete scan produces inventory, lineage, landscape, evidence, schemas, flows and support-artifact outcomes. Unsupported, partial, dynamic and unresolved constructs remain explicit.
- The sanitized workspace mirrors textual source only after credential filtering and residual-secret verification.
- Talend parsing covers jobs/routes, component schemas, TABLE parameters, tMap/tXMLMap/TDM mappings, XSLT, Structures, services and resource references.
- `mapping_accounting.json` reports offered, mapped, written, constant, lookup and dynamic fields. `capability_ledger.json` reports supported, partial, dynamic, unresolved and unsupported constructs.
- Talend selected designs and property pairs use content-addressed parse caching. Content changes invalidate entries; renames reuse parsed content and rebind path-derived metadata.
- Resource references and cross-project identities preserve dependency evidence without treating declarations as runtime deployment proof.
- Parser outputs feed the goal-driven canonical fact graph. Methodology packs may interpret these facts but cannot overwrite them.
- Credential values and unsafe source paths are excluded from model-facing semantic facts while provenance remains available to internal validation.

## Verification

- Full scanner suite: 669 passed, 7 skipped.
- Ruff passed for the intodoc source and test scope changed by this work.
- Changed-file invisible-character scan is clean.

## Next step

Exercise the parser products in the approved focused and whole-estate ETTM pilots, then manually compare mapping accounting and resource resolution against representative sanitized Talend source.

## Unverified

- Customer-estate parser fidelity is not claimed beyond the automated fixtures until the ETTM source comparison is completed.
