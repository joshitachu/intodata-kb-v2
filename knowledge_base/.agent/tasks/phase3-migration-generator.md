# Phase 3: Migration Code Generator

**Status:** Not started
**Depends on:** Phase 2

## Objective
Build a plugin-based code generation framework that translates universal IR into target platform code (Lobster, Databricks, etc.) with 3-tier expression translation (deterministic → LLM-assisted → manual).

## Plugin Architecture

```python
class TargetGenerator(ABC):
    generate_pipeline(pipeline: NormalizedPipeline) -> GeneratedArtifact
    translate_expression(expr: NormalizedExpression) -> TranslatedExpression
    generate_orchestration(dag: OrchestrationDAG) -> GeneratedArtifact
    generate_connection(conn: ConnectionDefinition) -> GeneratedArtifact
    validate(artifact: GeneratedArtifact) -> ValidationResult
    capability_matrix() -> dict[str, str]  # feature -> full|partial|none|manual
```

## Expression Translation (3-tier)
1. **Deterministic (~70%):** Function mapping table (TRIM→trim, parseDate→to_date)
2. **LLM-assisted (~20%):** AST + description → LLM generates target code, flagged for review
3. **Manual (~10%):** Complex Java routines → stub with MANUAL_REVIEW marker

## Auto-Migration Breakdown

| Category | % of Jobs | Approach |
|----------|-----------|----------|
| Simple source→transform→sink | ~60-70% | Fully automated |
| Complex tMap expressions | ~20-30% | LLM-assisted + human review |
| Custom Java routines | ~10-15% | Manual migration with SOP |

## Feature List

| ID | Feature | Depends On |
|----|---------|------------|
| P3-F01 | Generator plugin interface | P2-F01 |
| P3-F02 | Expression translator framework | P2-F02, P3-F01 |
| P3-F03 | Lobster target generator | P3-F01, P3-F02 |
| P3-F04 | Databricks target generator | P3-F01, P3-F02 |
| P3-F05 | Validation test generator | P3-F01 |
| P3-F06 | Migration complexity classifier | P2-F03 |
| P3-F07 | LLM-assisted expression translation | P3-F02 |
| P3-F08 | Migration report generator | P3-F06 |

## New Files
```
generators/
    base.py                    # TargetGenerator ABC
    registry.py                # Generator registry
    expression_translator.py   # Shared expression translation
    lobster/generator.py       # Lobster target
    databricks/generator.py    # Databricks target
```

## Verification
Run `intodoc migrate --target databricks` → verify generated code parses, test suites pass.
