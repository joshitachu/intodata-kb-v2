# VW Reverse Engineering Pack

A Talend reverse-engineering pack for the intodoc / Nexus documentation harness.
It turns a scoped Talend export into evidence-traceable interface documentation
suitable for a rationalisation programme.

## Output

The pack produces one client reverse-engineering template (MAS2 Fase 2) per
scanned Talend job, filled from evidence.

| Doc | Purpose |
|---|---|
| `01_INTERFACE_TEMPLATE__<group>__<job>` | **The filled template**, per job: integration name, contact info, business context & criticality, functional requirements, integration patterns, technical implementation, quality requirements, data mapping, interface specification, error handling & monitoring, testing, open items |

Security, risks, SQL, field mapping, and per-interface open questions are captured
*inside* the template (under Quality Requirements, Error Handling & Monitoring,
Interface Specification, Data Mapping, and Open Items) — there are no separate docs.

Each generated filename includes the scanned job's group and name. A job's
`tRunJob` dependencies may be described as dependencies, but each scanned job
still receives its own document.

## Design principles

- **Evidence discipline**: every claim is marked `[FACT]` (with a
  `(file, component)` source reference), `[ASSUMPTION]` (with reasoning and
  confidence), or `[VALIDATION QUESTION]` (with an audience). Enforced by the
  `required_terms` quality gate.
- **`unknown_policy: explicit`**: sections that cannot be filled from the source
  must say `UNKNOWN` and raise a validation question rather than fabricate.
- **No runtime invention**: throughput, error rates, real schedules, and
  monitoring thresholds are absent from a Talend export and are recorded as
  validation questions, not estimated.
- **No secret leakage**: credential variable names are kept, values never
  reproduced; a cleartext secret becomes a risk-register finding.

## How to run

1. Scan the scoped Talend project with intodoc
   (`intodoc scan <project> --platform talend`).
2. Register/select this pack in the Nexus dashboard (or point the harness at this
   pack directory) and generate documentation for the scanned project.
3. Review against source; unfilled items surface as `UNKNOWN` + validation
   questions.

## Scoping note

The harness analyses whatever is in the scanned project. To keep the deliverable
to a specific set of interfaces (e.g. the Werkorder interfaces), scan a scoped
export containing only those job call-trees and their referenced context groups
and connections, rather than the full multi-project export.

## Provenance

Modeled on the VolkerWessels reverse-engineering template (MAS2 Fase 2) and the
seven required outputs defined for the Werkorder POC. Structurally based on the
Talend-to-Azure swap pack.
