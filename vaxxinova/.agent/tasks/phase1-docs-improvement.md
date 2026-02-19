# Phase 1: Documentation Quality & Migration Readiness

**Status:** Complete (2026-02-18)
**Depends on:** Phase 0

## Objective
Transform intodoc's output from technical analysis dumps into industry-standard, dual-audience, migration-ready documentation. Add 3 new analysis passes and 8 new documents (24 → 32 total).

## New Analysis Passes

| Pass | Name | Purpose |
|------|------|---------|
| P19 | Migration Complexity Assessment | Score each job: Simple/Medium/Complex/Manual |
| P20 | Column-Level Lineage | Trace every column source→target with transformations |
| P21 | Expression Catalog | Index all expressions with complexity scores |

## New Documents (8 additions → 32 total)

### Migration-oriented
- `25_MIGRATION_ASSESSMENT.md` — Per-job complexity score, auto-migrate probability, blockers
- `26_GAP_ANALYSIS.md` — Source features vs target platform capabilities
- `28_LINEAGE_MATRIX.md` — Source-column to target-column traceability
- `29_EXPRESSION_CATALOG.md` — All expressions indexed with human explanations
- `30_VALIDATION_TEST_PLAN.md` — Verification strategy per job
- `31_MIGRATION_RUNBOOK.md` — Step-by-step execution plan per job

### Business-oriented
- `24_EXECUTIVE_DASHBOARD.md` -- Visual KPIs, risk heat map (removed from MCP agent workflow -- superseded by 25_PREVENTIVE_ANALYSIS.md health score)
- `27_DATA_GOVERNANCE.md` -- Ownership, classification, retention policies

## New Pydantic Models
- `MigrationComplexity` — job_name, complexity_score, auto_migrate_probability, blockers, effort_estimate
- `LineageEntry` — source_system, source_table, source_column, transformations[], target_table, target_column
- `ExpressionCatalogEntry` — expression_id, raw_expression, human_explanation, complexity_score, location

## Feature List

| ID | Feature | Depends On |
|----|---------|------------|
| P1-F01 | Restructure docs to TOGAF/DAMA-DMBOK sections | — |
| P1-F02 | Migration complexity scoring (Pass 19) | P1-F01 |
| P1-F03 | Column-level lineage matrix (Pass 20) | — |
| P1-F04 | Gap analysis doc | P1-F02 |
| P1-F05 | Migration runbook template | P1-F02 |
| P1-F06 | Enhanced business rules catalog | — |
| P1-F07 | Expression catalog (Pass 21) | — |
| P1-F08 | Dual-audience rendering | P1-F01 |
| P1-F09 | RACI matrix generator | P1-F02 |
| P1-F10 | Doc quality scoring (completeness %) | All above |
| P1-F11 | Unify Qdrant exporter with RAG_pipeline | P1-F01 |

## Files to Modify
- `models.py` — Add 3+ new model classes
- `generator/renderer.py` — Expand TEMPLATE_MAP (24→32), add audience param
- `cli.py` — Update `_build_template_context()` for passes 19-21
- `analyzer/pass_registry.py` — Register passes 19-21
- New: 3 pass files (`p19_*.py`, `p20_*.py`, `p21_*.py`)
- New: 8 template files in `generator/templates/`

## Verification
Re-run `intodoc generate` → verify 32 docs generated, dual-audience output, migration scores present.
