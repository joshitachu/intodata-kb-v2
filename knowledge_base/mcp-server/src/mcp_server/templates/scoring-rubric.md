# Integration Assessment Scoring Rubric

Score each dimension 1-5 based on the criteria below. Cite specific evidence from the analysis files.

## Dimensions

### 1. Error Handling & Resilience

| Level | Score | Criteria |
|-------|-------|----------|
| Critical | 1 | No error handling in any job; failures cause silent data loss or corruption |
| Basic | 2 | <50% of jobs have error handling; no centralized logging; errors caught but not routed |
| Developing | 3 | 50-80% of jobs have error handling; some centralized logging; retry logic exists in critical paths |
| Mature | 4 | >80% of jobs have error handling; centralized logging with severity levels; retry + alerting in place |
| Excellent | 5 | All jobs have error handling; circuit breakers; dead-letter queues; automated recovery; full audit trail |

**What to look for**: tLogCatcher/tLogRow components, try-catch blocks, error routing subjobs, reject flows, OnComponentError triggers, error tables/queues, retry logic, alert configurations.

### 2. Security & Access Control

| Level | Score | Criteria |
|-------|-------|----------|
| Critical | 1 | Hardcoded credentials; no access control; sensitive data unprotected |
| Basic | 2 | Some credentials externalized; minimal access control; no encryption |
| Developing | 3 | Most credentials externalized; role-based access exists; basic encryption for sensitive fields |
| Mature | 4 | All credentials externalized; comprehensive RBAC; encryption at rest and in transit; audit logging |
| Excellent | 5 | Zero hardcoded secrets; vault integration; field-level encryption; data masking; PII handling; full compliance audit trail |

**What to look for**: context variables with credential values, hardcoded passwords/connection strings, encryption components, access control patterns, PII handling, data masking, vault/secret manager references.

### 3. Data Flow Quality

| Level | Score | Criteria |
|-------|-------|----------|
| Critical | 1 | No lineage tracking; undocumented transformations; missing schema definitions |
| Basic | 2 | Some lineage exists; <50% of transformations documented; schemas partially defined |
| Developing | 3 | Column-level lineage for major flows; 50-80% transformations have expressions; schemas mostly defined |
| Mature | 4 | 80%+ column-level lineage; clean transformation patterns; comprehensive schemas; data quality checks |
| Excellent | 5 | Full column-level lineage; all transformations documented with business context; schema evolution tracked; data quality gates |

**What to look for**: tMap expressions, column mappings, schema definitions, lookup patterns, reject handling, data quality components (tSchemaComplianceCheck), null handling, type casting, deduplication logic.

### 4. Architecture & Design

| Level | Score | Criteria |
|-------|-------|----------|
| Critical | 1 | No naming conventions; monolithic jobs; circular dependencies; no reuse |
| Basic | 2 | Inconsistent naming; some modular design; few shared components |
| Developing | 3 | Naming conventions followed >50%; modular job design; some shared routines/context groups |
| Mature | 4 | Consistent naming; clear separation of concerns; shared component library; well-defined job dependencies |
| Excellent | 5 | Consistent naming across all regions; layered architecture; comprehensive shared library; documented patterns; clear dependency graph |

**What to look for**: naming conventions (prefixes, suffixes), job organization (folders, groups), shared routines, context groups, child jobs, component reuse patterns, circular dependencies, job complexity (component count).

### 5. Operations & Monitoring

| Level | Score | Criteria |
|-------|-------|----------|
| Critical | 1 | No scheduling metadata; no monitoring; no logging; manual triggers only |
| Basic | 2 | Basic scheduling exists; minimal logging; no alerting; limited monitoring |
| Developing | 3 | Scheduled jobs with dependencies; structured logging; basic alerting; some monitoring |
| Mature | 4 | Comprehensive scheduling with error handling; monitoring dashboards; proactive alerting; SLA tracking |
| Excellent | 5 | Orchestrated pipelines; full observability; automated incident response; SLA compliance; capacity planning |

**What to look for**: scheduling metadata, trigger configurations, logging patterns, alert configurations, monitoring components, job dependencies, SLA definitions, capacity settings (parallelism, batch sizes).

### 6. Business Logic Clarity

| Level | Score | Criteria |
|-------|-------|----------|
| Critical | 1 | Business rules embedded in code without documentation; no tagging; logic opaque |
| Basic | 2 | Some business rules identifiable; limited documentation; inconsistent implementation |
| Developing | 3 | Business rules tagged (BR-xxx); most rules have expressions visible; some documentation |
| Mature | 4 | All business rules tagged and documented; clear expression logic; validation rules explicit |
| Excellent | 5 | Full business rule catalog; rules linked to requirements; version-controlled; test coverage for rules |

**What to look for**: tMap filter expressions, WHERE clauses, conditional routing, lookup-based decisions, business rule tags (BR-xxx), validation components, decision tables, formula complexity.

### 7. Code Quality & Standards

| Level | Score | Criteria |
|-------|-------|----------|
| Critical | 1 | No standards; inconsistent patterns; dead code; duplicated logic |
| Basic | 2 | Some patterns visible; significant inconsistency across regions/teams; some dead code |
| Developing | 3 | Standards followed >50%; mostly consistent patterns; limited dead code; some test coverage |
| Mature | 4 | Consistent patterns; clean code; minimal dead code; good test coverage; code reviews evident |
| Excellent | 5 | Uniform standards; clean abstractions; no dead code; comprehensive testing; automated quality gates |

**What to look for**: code consistency across regions, duplicate jobs/logic, unused components, commented-out code, test jobs, version control patterns, naming consistency, code complexity metrics.

### 8. Migration Readiness

| Level | Score | Criteria |
|-------|-------|----------|
| Critical | 1 | Heavy vendor lock-in; undocumented custom code; no migration path visible |
| Basic | 2 | Some vendor-specific features identified; limited documentation; high migration effort |
| Developing | 3 | Vendor dependencies cataloged; some abstraction layers; migration effort partially estimated |
| Mature | 4 | Clear dependency map; abstraction layers for vendor-specific features; migration path documented |
| Excellent | 5 | Minimal vendor lock-in; clean abstractions; automated migration tooling feasible; comprehensive impact analysis |

**What to look for**: platform-specific components (tSAPInput, stored procedures, vendor APIs), custom code (Java routines, scripts), external system dependencies, nested tMap complexity, connection abstractions, data format dependencies.

## Scoring Output Format

Produce the scorecard in this exact format:

```markdown
# Assessment Scorecard: {project_name}

| Dimension | Score | Key Evidence |
|-----------|-------|-------------|
| Error Handling & Resilience | X/5 | Brief evidence summary |
| Security & Access Control | X/5 | Brief evidence summary |
| Data Flow Quality | X/5 | Brief evidence summary |
| Architecture & Design | X/5 | Brief evidence summary |
| Operations & Monitoring | X/5 | Brief evidence summary |
| Business Logic Clarity | X/5 | Brief evidence summary |
| Code Quality & Standards | X/5 | Brief evidence summary |
| Migration Readiness | X/5 | Brief evidence summary |

**Overall: X.X / 5 (Level N: Label)**

Level labels: 1=Critical, 2=Developing, 3=Established, 4=Mature, 5=Optimized

## Detailed Findings

### Dimension Name (X/5)
**Rubric Level N**: Description from rubric
- Specific finding with job name and component reference
- Another finding with evidence
...
(repeat for each dimension)

## Anti-Patterns Found
| ID | Category | Description | Location |
|----|----------|-------------|----------|
| AP-xxx-nnn | Category | What was found | Job/component ref |

## Red Flags
| ID | Severity | Description | Location |
|----|----------|-------------|----------|
| RF-xxx-nnn | CRITICAL/HIGH/MEDIUM | What was found | Job/component ref |

## Security Assessment
(Results from security checklist evaluation)
```
