# Phase 2: Universal Intermediate Representation (IR)

**Status:** COMPLETE (2026-02-18) -- 9 modules, 62 tests, 99.4% expression parse rate on 24,837 expressions
**Depends on:** Phase 1 (partially — P1-F07 Expression Catalog feeds into P2-F02)

## Objective
Build a platform-agnostic intermediate representation that normalizes Talend-specific models into universal pipeline/step/expression abstractions, enabling automated migration to any target platform.

## Core IR Models (new package: `intodoc/src/intodoc/ir/`)

| Model | Purpose |
|-------|---------|
| `NormalizedPipeline` | Maps to one Talend ParsedJob (platform-agnostic) |
| `PipelineStep` | One operation: SOURCE, SINK, TRANSFORM, FILTER, JOIN, AGGREGATE, CUSTOM_CODE |
| `DataSource / DataSink` | Vendor-neutral connection + table/path definition |
| `NormalizedExpression` | Expression with optional AST, human explanation, complexity score |
| `ExpressionNode` | AST node: FIELD_REF, FUNCTION_CALL, TERNARY, LITERAL, BINARY_OP, CAST |
| `OrchestrationNode` | Parent/child pipeline relationships, triggers, error strategy |
| `JoinSpec / FilterSpec` | Structured join/filter definitions |

## Key Normalization Mappings

| Talend Concept | IR Target |
|----------------|-----------|
| `ParsedJob` | `NormalizedPipeline` |
| `Component(tMSSqlInput)` | `PipelineStep(type=SOURCE)` + `DataSource` |
| `Component(tMap)` | `PipelineStep(type=TRANSFORM)` + `NormalizedExpression[]` |
| `TRunJobRef` | `OrchestrationNode` |
| `MetadataConnection` | Normalized `ConnectionDefinition` |

## Expression AST Parser
Parse tMap Java-like expressions into platform-agnostic AST:
- `row1.fieldName` → `FieldRef(source="input_0", column="fieldName")`
- `StringHandling.TRIM(x)` → `FunctionCall(name="trim", args=[x])`
- `x != null ? x : y` → `NullCoalesce(value=x, default=y)`

## Feature List

| ID | Feature | Depends On |
|----|---------|------------|
| P2-F01 | IR core data models | — |
| P2-F02 | Expression AST parser | — |
| P2-F03 | Talend-to-IR normalizer | P2-F01, P2-F02 |
| P2-F04 | Round-trip validation | P2-F03 |
| P2-F05 | Expression type system | P2-F02 |
| P2-F06 | Orchestration DAG model | P2-F01 |
| P2-F07 | LLM enrichment of IR | P2-F03 |
| P2-F08 | IR persistence & versioning | P2-F01 |

## New Files
- `ir/__init__.py` — Package init
- `ir/models.py` — All IR Pydantic models
- `ir/expression_parser.py` — Expression → AST parser
- `ir/normalizer.py` — ScanResult → IR converter
- `ir/dag.py` — Orchestration DAG builder
- `ir/validator.py` — Round-trip validation
- `ir/pseudocode.py` — Pseudocode renderer for validation

## Verification
Run `intodoc normalize` → verify all 257 jobs produce valid IR, round-trip fidelity >95%.
