---
applies to: all platforms
artifact format: not platform-specific - this file defines vocabulary, not parsing
verified against: n/a - method and terminology, grounded in reverse-engineering literature
evidence class: literature
last verified: 2026-07-25
---

# Confidence model

Shared across all phases. Referenced by `extraction-guide.md` (Phase 1), `inventory-document.md` (Phase 1),
`scoring-rubric.md` and `scorecard-template.md` (Phase 3), `system-landscape.md` (Phases 1-2) and `SKILL.md`.
**Do not restate any part of this file elsewhere** - point at it.

## Confidence model

Three separate things. Do not merge them.

**1. Basis** - per finding, mandatory. What kind of read produced the claim.

| Basis | Definition | Rendered as | Examples |
|---|---|---|---|
| `Confirmed` | Read directly off an artifact attribute. Redocumentation. | HIGH | A hardcoded endpoint; a declared mapping; the result of a stated enumeration |
| `Inferred` | Derived by reasoning or matching across artifacts. Design recovery. | MEDIUM | Intent; ownership; liveness; a shared-state dependency; a lookup's per-row value |
| `Unknown` | Not recoverable from the artifact at all. | LOW | Schedules held outside the export; error rates; business criticality; incident history |

A design-recovery claim may never be marked `Confirmed` (see Forensic Discipline). `HIGH`/`MEDIUM`/`LOW` are a
**rendering** of Basis for numeric scores, not an independent judgement - never record a Basis and a differing
confidence for the same claim.

**Where each form may appear.** A **table cell** always carries the Basis word (`Confirmed` / `Inferred` /
`Unknown`); the rendering may appear in **prose and headline summaries** only. The gate enforces this: a bare
`HIGH`, `MEDIUM` or `LOW` occupying a whole table cell in a pipeline output is a **failure**, because in practice it
means the phase wrote the superseded vocabulary rather than rendering a graded one. The right-hand column of the
table above is the definition of the mapping, not a licence to use it in a cell.

**A finding graded `Confirmed` must carry a locus.** `Confirmed` means the claim was read directly off an
artifact attribute, so a claim that cannot say *which attribute* has not met the definition - the grade is
`Inferred` until it can. `Inferred` rows carry a locus where one exists and `derived` where the claim spans
artifacts. `Unknown` rows carry no locus, because there is no artifact to point at; they belong in the
runtime-validation register. The locus format is defined once in `extraction-guide.md`.

**2. Ceiling** - per document, derived from the input class, never hand-set.

| Input available | Classification | Ceiling |
|---|---|---|
| Description only | `PRELIMINARY - Description Only` | `Unknown` on all dimensions |
| Assessment interview, no code or runtime | `ASSESSMENT-BASED` | `Inferred`, or `Confirmed` where specific evidence is cited |
| Assessment + code inventory, no runtime | `STATIC ANALYSIS` | `Confirmed` for code-confirmed, `Inferred` for the rest |
| Assessment + runtime data | `RUNTIME-VALIDATED` | `Confirmed` where evidence supports it |

**3. Aggregation** - take the minimum, never the product.

A dimension's Basis is `min(document ceiling, dimension ceiling, worst Basis among the findings cited as driving
the score)`. Because it is a minimum, per-finding and per-document grading cannot double-discount the same doubt.

A finding with `Basis = Unknown` contributes **no value** to a score. If a dimension's criteria cannot be met
without it, the dimension is `Not assessed` - not a low score. Scoring low off an unknown launders it into a
measurement. See `scoring-rubric.md` for the difference between `Not assessed` and `Not applicable`.

**Not part of this model.** `Status` (`Live` / `Defined-but-unused` / `Declared stub`) and edge state
(`resolvable` / `unresolvable`) are findings about the artifact, not grades of our knowledge. Each is recorded in
its own column. "This connection is dead" and "we are sure it is dead" are two different cells.

`unresolvable` means one thing everywhere it appears - **the identifier is built from a runtime expression, so the
target is not determinable from the artifact** - and it appears on three: implicit dependency edges, the coupling
table, and the endpoint-kind column. It is a `Confirmed` observation about the artifact (the expression is right
there) whose *target* is `Unknown`. That is why it is a state and not a Basis: collapsing it into `Unknown` would
discard the confirmed half.

**`High` / `Med` / `Low` also means magnitude, and that is a different axis.** Business criticality, blast radius,
finding severity and transformation complexity are graded high/med/low as *magnitudes of the thing itself*. They are
not confidence and must not be converted to Basis. A cell can legitimately read `Radius: High` with
`Basis: Inferred` - a large impact we are unsure of. When a table needs both, it needs two columns.

**Absence is not `Confirmed`.** A search that returns nothing is a `Confirmed` fact about the **search** and an
`Inferred` fact about the **thing**: static analysis sees what is declared, not what happens, and a query naming the
wrong attribute returns exactly what a real absence returns. Record the search alongside the verdict. A capability
that was never searched for is `Unknown` and is a third state, not a quieter form of absent.

**Where declared and discovered facts conflict**, prefer the discovered one and record the conflict as a finding.
A declaration states intent; a discovered dependency states behaviour.
