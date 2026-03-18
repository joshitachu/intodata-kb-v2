# Migration Assessment

This document provides a comprehensive migration assessment for integration platform modernization. It consolidates migration readiness scoring, platform comparison, phasing plans, effort estimation, risk assessment, dependency analysis, and recommended migration order into a single actionable reference. The structure applies to any multi-region integration codebase being migrated from a legacy platform (e.g., Talend) to a modern integration framework.

---

## Table of Contents

- [Migration Readiness Score](#migration-readiness-score)
  - [Scoring Methodology](#scoring-methodology)
  - [Dimension Scores](#dimension-scores)
  - [Regional Readiness Breakdown](#regional-readiness-breakdown)
  - [Score Interpretation](#score-interpretation)
- [Platform Comparison](#platform-comparison)
  - [Source Platform Profile](#source-platform-profile)
  - [Target Platform Requirements](#target-platform-requirements)
  - [Capability Gap Analysis](#capability-gap-analysis)
  - [Component Translation Matrix](#component-translation-matrix)
- [Migration Scope](#migration-scope)
  - [Job Inventory](#job-inventory)
  - [Component Migration Surface Area](#component-migration-surface-area)
  - [Connection and Credential Inventory](#connection-and-credential-inventory)
  - [What to Exclude](#what-to-exclude)
- [Migration Phasing Plan](#migration-phasing-plan)
  - [Phase 1: Infrastructure and Connections](#phase-1-infrastructure-and-connections)
  - [Phase 2: Shared Templates](#phase-2-shared-templates)
  - [Phase 3: Small Regions (Proof of Concept)](#phase-3-small-regions-proof-of-concept)
  - [Phase 4: Major Regions](#phase-4-major-regions)
  - [Phase 5: Most Complex Region](#phase-5-most-complex-region)
  - [Phase 6: Global Consolidation and Orchestration](#phase-6-global-consolidation-and-orchestration)
  - [Phase 7: Cleanup and Retirement](#phase-7-cleanup-and-retirement)
- [Effort Estimation](#effort-estimation)
  - [Estimation Methodology](#estimation-methodology)
  - [Effort by Phase](#effort-by-phase)
  - [Effort by Component Type](#effort-by-component-type)
  - [Total Effort Summary](#total-effort-summary)
  - [Estimation Confidence](#estimation-confidence)
- [Risk Assessment](#risk-assessment)
  - [Risk Matrix](#risk-matrix)
  - [Technical Risks](#technical-risks)
  - [Operational Risks](#operational-risks)
  - [Organizational Risks](#organizational-risks)
  - [Risk Mitigation Plan](#risk-mitigation-plan)
- [Dependency Analysis](#dependency-analysis)
  - [Cross-Region Data Dependencies](#cross-region-data-dependencies)
  - [Shared Component Dependencies](#shared-component-dependencies)
  - [External System Dependencies](#external-system-dependencies)
  - [Migration Sequencing Constraints](#migration-sequencing-constraints)
- [Recommended Migration Order by Region](#recommended-migration-order-by-region)
  - [Sequencing Rationale](#sequencing-rationale)
  - [Detailed Regional Migration Plans](#detailed-regional-migration-plans)
  - [Parallel vs Sequential Execution](#parallel-vs-sequential-execution)
- [Success Criteria](#success-criteria)
  - [Per-Phase Success Criteria](#per-phase-success-criteria)
  - [Overall Migration Success Criteria](#overall-migration-success-criteria)
  - [Go / No-Go Decision Framework](#go--no-go-decision-framework)
- [Key Architectural Decisions](#key-architectural-decisions)
  - [What to Preserve](#what-to-preserve)
  - [What to Redesign](#what-to-redesign)
  - [What to Eliminate](#what-to-eliminate)
- [Quick Wins](#quick-wins)

---

## Migration Readiness Score

### Scoring Methodology

The migration readiness score assesses how prepared the source platform is for migration across eight dimensions. Each dimension is scored 0-100 based on evidence from the scan data, code analysis, and operational assessment. The composite score is a weighted average.

| Dimension | Weight | What It Measures |
|-----------|--------|------------------|
| Documentation Completeness | 15% | Percentage of jobs, connections, and data flows that are documented |
| Code Standardization | 15% | Adherence to naming conventions, parameterization, and coding standards |
| Test Coverage | 10% | Availability of test environments, test data, and validation scripts |
| Dependency Clarity | 15% | Whether job execution order, data dependencies, and orchestration are explicit |
| Error Handling Maturity | 10% | Percentage of jobs with proper error handling, logging, and alerting |
| Technical Debt Level | 15% | Ratio of production jobs to copy/backup/debug variants, hardcoded values, abandoned code |
| Team Knowledge | 10% | Whether tribal knowledge is documented or concentrated in individuals |
| Infrastructure Readiness | 10% | Target platform availability, network connectivity, credential management |

### Dimension Scores

| Dimension | Weight | Score | Evidence |
|-----------|--------|-------|----------|
| Documentation Completeness | 15% | {N}/100 | {Describe coverage: e.g., "Architecture documented, individual job logic undocumented"} |
| Code Standardization | 15% | {N}/100 | {e.g., "Naming convention followed in 70% of jobs, parameterization mixed"} |
| Test Coverage | 10% | {N}/100 | {e.g., "TEST environment exists, no automated validation scripts"} |
| Dependency Clarity | 15% | {N}/100 | {e.g., "All jobs on-demand, no scheduling metadata, dependencies implicit"} |
| Error Handling Maturity | 10% | {N}/100 | {e.g., "11% of jobs have any error handling, 89% have none"} |
| Technical Debt Level | 15% | {N}/100 | {e.g., "48 excluded jobs (backup/debug/copy), 23 duplicated extraction jobs"} |
| Team Knowledge | 10% | {N}/100 | {e.g., "Key patterns documented in cross-region analysis, operational runbook exists"} |
| Infrastructure Readiness | 10% | {N}/100 | {e.g., "Target platform provisioned, MSSQL connectivity untested"} |

**Composite Migration Readiness Score: {N}/100**

### Regional Readiness Breakdown

Not all regions are equally ready for migration. Assess each region individually:

| Region | Jobs | Standardization | Error Handling | Technical Debt | Dependencies | Readiness |
|--------|------|-----------------|----------------|----------------|--------------|-----------|
| {region} | {n} | {High/Medium/Low} | {pct}% | {High/Medium/Low} | {Clear/Implicit} | {score}/100 |

Regions with higher readiness scores should be migrated first (they will encounter fewer surprises). Regions with lower scores need more preparation before migration.

### Score Interpretation

- **80-100: Migration-ready.** Platform is well-documented, standardized, and testable. Migration can proceed with standard risk management.
- **60-79: Conditionally ready.** Migration is feasible but specific gaps must be addressed. Plan for additional discovery and documentation effort.
- **40-59: Significant preparation needed.** Key areas (dependencies, error handling, documentation) require investment before migration can proceed safely.
- **20-39: Major preparation needed.** The platform has fundamental gaps that will cause migration failures if not addressed first.
- **0-19: Not ready.** Substantial prerequisite work (documentation, testing, standardization) is required before migration planning.

---

## Platform Comparison

### Source Platform Profile

Document the current platform's characteristics:

| Attribute | Value |
|-----------|-------|
| Platform | {e.g., Talend Open Studio / Talend Enterprise} |
| Version | {version} |
| Total Jobs | {count} |
| Total Components | {count} |
| Regions | {count and list} |
| Source Systems | {list: MSSQL, REST API, SFTP, etc.} |
| Target Systems | {list: BigQuery, Salesforce, GCS, etc.} |
| Authentication Methods | {list: SQL Auth, OAuth 2.0, JWT, username-password} |
| Scheduling | {e.g., On-demand via TAC/cron, no embedded scheduling metadata} |
| Error Handling Coverage | {pct}% of jobs |
| Logging Coverage | {pct}% of jobs |
| Version Control | {e.g., Git, manual backup copies, none} |

### Target Platform Requirements

Define what the target platform must support:

| Requirement | Priority | Notes |
|-------------|----------|-------|
| MSSQL on-premises connectivity | Must-have | VPN/private link to {N} on-premises servers |
| Cloud data warehouse read/write | Must-have | {e.g., BigQuery native support} |
| REST API extraction with OAuth 2.0 | Must-have | Business Central API, including SOAP codeunit calls |
| CRM integration | Must-have | Salesforce upsert via External ID |
| Parameterized templates | Must-have | Single job definition serving multiple regions/subsidiaries |
| Environment isolation | Must-have | PROD/TEST context switching via configuration |
| Dependency-based orchestration | Must-have | Replace implicit job ordering with explicit dependency management |
| Structured logging | Must-have | JSON-format logs with centralized aggregation |
| Error handling framework | Must-have | Retry logic, dead-letter queues, alerting |
| Secrets management | Must-have | Encrypted credential storage with rotation |
| SFTP/file ingestion | Should-have | Animalytix data pipeline (USA only) |
| Monitoring dashboards | Should-have | Job execution, data freshness, row counts |
| Native version control | Should-have | Eliminate backup/copy-based version management |

### Capability Gap Analysis

Identify gaps between the source platform's capabilities and the target platform's requirements:

| Capability | Source Platform | Target Requirement | Gap | Remediation |
|-----------|----------------|-------------------|-----|-------------|
| Error handling | 11% coverage | 100% coverage | Large | Add error handling to every migrated job |
| Logging | 40% coverage | 100% coverage | Medium | Implement structured logging framework |
| Orchestration | Implicit timing | Explicit dependencies | Large | Build dependency graph, implement triggers |
| Credential management | Flat files | Secrets manager | Large | Migrate all credentials, implement rotation |
| Parameterization | Mixed (some hardcoded) | Fully parameterized | Medium | Externalize all environment-specific values |
| Monitoring | None | Full observability | Large | Build dashboards, alerting, health checks |

### Component Translation Matrix

Map source platform components to their target platform equivalents:

| Source Component | Purpose | Target Equivalent | Translation Complexity |
|-----------------|---------|-------------------|----------------------|
| tMap | Transformation | {target mapping component} | High -- expression-by-expression |
| tBigQueryInput | Data warehouse read | {target DWH read} | Medium |
| tBigQueryOutput | Data warehouse write | {target DWH write} | Medium |
| tBigQuerySQLRow | SQL execution | {target SQL runner} | Medium |
| tHashInput/Output | In-memory lookup | {target lookup mechanism} | Medium |
| tUnite | Stream merge | {target UNION} | Low |
| tJava/tJavaRow | Custom code | {target custom code} | High -- manual review |
| tRESTClient | API calls | {target HTTP client} | High |
| tSalesforceOutput | CRM write | {target CRM connector} | High |
| tMSSqlInput | Database read | {target MSSQL connector} | Medium |
| tPostjob/tPrejob | Lifecycle hooks | {target pre/post hooks} | Low |
| Custom Joblets | Shared logic | {target shared utilities} | Low |

---

## Migration Scope

### Job Inventory

Provide a complete inventory of jobs by region, identifying which are in scope for migration and which should be excluded.

| Region | Total Jobs | In Scope | Excluded | Reason for Exclusion |
|--------|-----------|----------|----------|---------------------|
| {region} | {n} | {n} | {n} | {reason} |
| **Total** | **{N}** | **{N}** | **{N}** | |

For each excluded job, document the exclusion reason:
- **Backup variants**: superseded by primary job
- **Debug variants**: development artifacts
- **Copy_of_ jobs**: clones where the primary exists
- **Legacy compatibility**: backward-compatible transformations not needed in target
- **Scratchpad/test**: development jobs with no production value

### Component Migration Surface Area

Quantify the total component landscape that must be translated to the target platform:

| Component | Count | % of Total | Migration Complexity |
|-----------|-------|-----------|---------------------|
| {component} | {n} | {pct}% | {High/Medium/Low} |

**Total unique component types**: {N}
**Total component instances**: {N}

### Connection and Credential Inventory

List every external system connection that must be migrated:

| Connection | Type | Region(s) | Auth Method | Credential Count |
|------------|------|-----------|-------------|-----------------|
| {connection} | {type} | {regions} | {auth} | {count} |

**Total distinct connections**: {N}
**Total credential parameters**: {N}

### What to Exclude

Explicitly list what should NOT be migrated:

1. **Development/scratchpad regions** -- test artifacts
2. **Duplicate regional designations** -- consolidate into canonical regions
3. **Backup variants** -- superseded by primary versions
4. **Debug variants** -- development artifacts
5. **Legacy compatibility jobs** -- evaluate if still needed
6. **Inactive copy variants** -- where the primary job is canonical
7. **Raw credential values** -- must be migrated to secrets manager, never copied

---

## Migration Phasing Plan

### Phase 1: Infrastructure and Connections

**Duration:** 2-3 weeks
**Objective:** Establish all external system connections and shared infrastructure in the target platform before migrating any pipeline logic.

**Deliverables:**
- [ ] Secrets manager configured with all credentials
- [ ] Database connectivity validated from target platform to all on-premises servers
- [ ] Data warehouse service account configured with appropriate permissions
- [ ] Cloud ERP OAuth flow validated for each subsidiary
- [ ] CRM OAuth flow validated (preferably upgraded to JWT Bearer)
- [ ] Cloud storage access configured for temp file staging
- [ ] SFTP connectivity validated (if applicable)
- [ ] Shared logging framework deployed
- [ ] Shared error handling framework deployed
- [ ] Monitoring dashboards configured
- [ ] Environment isolation validated (PROD vs TEST switching)

**Validation criteria:**
- Each connection type tested with a simple read/write operation
- Credential rotation tested for at least one connection
- Logging output verified in centralized log aggregation system

### Phase 2: Shared Templates

**Duration:** 2-3 weeks
**Objective:** Migrate shared extraction templates. These must be migrated before any region that depends on them.

**Key challenges:**
- OAuth token management
- API payload parsing (SOAP XML, JSON, OData)
- Pagination loop with position bookmarks
- Error handling for API failures (currently silently swallowed)

**Deliverables:**
- [ ] Parameterized extraction template accepting region-specific parameters
- [ ] OAuth token generation as a shared utility
- [ ] Pagination handling as a reusable pattern
- [ ] Error handling and retry logic for API failures
- [ ] All shared template jobs migrated and tested with at least 2 subsidiary contexts

**Validation criteria:**
- Data output matches current platform output (row counts, column values)
- Pagination handles large result sets (10,000+ records)
- Token refresh works correctly for long-running extractions

### Phase 3: Small Regions (Proof of Concept)

**Duration:** 3-4 weeks
**Objective:** Migrate small, complete pipelines to build confidence and validate the migration template.

Select 2-3 small regions that together exercise the major patterns:
- A region with on-premises database extraction and CRM sync
- A region with dual-entity complexity (legacy + modern ERP)
- A region that needs consolidation (e.g., merging duplicate folders)

For each region, provide a job-level migration checklist:
- [ ] Raw extraction jobs
- [ ] Clean transformation jobs
- [ ] Output builder / Sales_Set jobs
- [ ] CRM sync jobs (if applicable)
- [ ] End-to-end pipeline test

**Validation criteria:**
- End-to-end pipeline execution from source to output table
- Row counts match current output within 1%
- Output schema matches the expected global schema
- CRM sync produces correct records in sandbox

### Phase 4: Major Regions

**Duration:** 4-6 weeks per region
**Objective:** Migrate the major regions that represent the majority of pipeline logic.

For each major region, document:
- Job count (in-scope after excluding backups)
- Key challenges unique to this region
- Specific validation criteria
- Dependencies on previously migrated phases

### Phase 5: Most Complex Region

**Duration:** 4-6 weeks
**Objective:** Migrate the largest and most complex region, which exercises every source type and the most complex transformation logic.

Document region-specific challenges:
- Dual-source architecture (legacy + modern)
- Manufacturing/production pipelines (if applicable)
- Cost analysis pipelines (if applicable)
- Region-specific data formats and regulatory requirements
- Performance risks from high component density

### Phase 6: Global Consolidation and Orchestration

**Duration:** 3-4 weeks
**Objective:** Migrate the consolidation layer (which depends on all regional outputs) and establish dependency-based orchestration.

**Orchestration deliverables:**
- [ ] Dependency graph defined: regional jobs -> global consolidation -> budget/forecast
- [ ] Trigger-based orchestration: global jobs trigger only after upstream completion
- [ ] Data freshness validation: check regional table timestamps before consolidation
- [ ] Failure handling: alert and halt if any upstream region failed
- [ ] Monitoring: end-to-end pipeline health dashboard

### Phase 7: Cleanup and Retirement

**Duration:** 1-2 weeks
**Objective:** Decommission the source platform and clean up artifacts.

**Deliverables:**
- [ ] All production traffic confirmed on target platform for 2+ weeks
- [ ] Source platform scheduled for decommission
- [ ] Development/scratchpad jobs archived (not migrated)
- [ ] Migration documentation finalized
- [ ] Post-migration review completed

---

## Effort Estimation

### Estimation Methodology

Effort is estimated using a component-weighted approach:
1. Categorize all in-scope components by migration complexity (High, Medium, Low)
2. Apply effort multipliers based on observed complexity in sample translations
3. Add phase-level overhead for infrastructure, testing, and project management
4. Apply a contingency factor based on estimation confidence

| Complexity | Effort per Instance | Examples |
|-----------|-------------------|----------|
| High | 2-4 hours | tMap with complex expressions, tJava custom code, API clients |
| Medium | 0.5-2 hours | Data warehouse I/O, lookup tables, SQL execution |
| Low | 0.25-0.5 hours | Lifecycle hooks, stream merges, file readers, filters |

### Effort by Phase

| Phase | Duration | Team Size | Effort (person-days) |
|-------|----------|-----------|---------------------|
| Phase 1: Infrastructure | 2-3 weeks | 2-3 | {N} |
| Phase 2: Shared Templates | 2-3 weeks | 2-3 | {N} |
| Phase 3: Small Regions | 3-4 weeks | 2-3 | {N} |
| Phase 4: Major Regions | 4-6 weeks each | 3-4 | {N} |
| Phase 5: Most Complex Region | 4-6 weeks | 3-4 | {N} |
| Phase 6: Global Consolidation | 3-4 weeks | 2-3 | {N} |
| Phase 7: Cleanup | 1-2 weeks | 1-2 | {N} |
| **Total** | **{N} weeks** | | **{N} person-days** |

### Effort by Component Type

| Component Type | Instance Count | Complexity | Effort/Instance | Total Effort |
|---------------|---------------|-----------|-----------------|-------------|
| tMap | {N} | High | 3 hours avg | {N} person-days |
| tBigQueryInput | {N} | Medium | 1 hour avg | {N} person-days |
| tBigQueryOutput | {N} | Medium | 1 hour avg | {N} person-days |
| tJava/tJavaRow | {N} | High | 3 hours avg | {N} person-days |
| tRESTClient | {N} | High | 4 hours avg | {N} person-days |
| tSalesforceOutput | {N} | High | 3 hours avg | {N} person-days |
| tHash (Input/Output) | {N} | Medium | 1.5 hours avg | {N} person-days |
| tUnite | {N} | Low | 0.5 hours avg | {N} person-days |
| Other (lifecycle, file I/O) | {N} | Low | 0.25 hours avg | {N} person-days |
| **Component Total** | **{N}** | | | **{N} person-days** |

### Total Effort Summary

| Category | Effort (person-days) | % of Total |
|----------|---------------------|-----------|
| Component translation | {N} | {pct}% |
| Infrastructure setup | {N} | {pct}% |
| Testing and validation | {N} | {pct}% |
| Project management | {N} | {pct}% |
| Documentation | {N} | {pct}% |
| Contingency ({pct}%) | {N} | {pct}% |
| **Total** | **{N}** | **100%** |

### Estimation Confidence

| Phase | Confidence | Variance Range | Notes |
|-------|-----------|---------------|-------|
| Phase 1 | High | +/- 15% | Infrastructure is well-understood |
| Phase 2 | Medium | +/- 25% | API complexity may vary |
| Phase 3 | Medium-High | +/- 20% | Small regions, limited unknowns |
| Phase 4 | Medium | +/- 30% | Major regions have more edge cases |
| Phase 5 | Low-Medium | +/- 40% | Most complex region, highest uncertainty |
| Phase 6 | Medium | +/- 25% | Depends on orchestration complexity |

---

## Risk Assessment

### Risk Matrix

| Likelihood / Impact | Low Impact | Medium Impact | High Impact | Critical Impact |
|-------------------|-----------|--------------|-------------|-----------------|
| **High Likelihood** | Accept | Mitigate | Mitigate urgently | Prevent |
| **Medium Likelihood** | Accept | Monitor | Mitigate | Mitigate urgently |
| **Low Likelihood** | Accept | Accept | Monitor | Mitigate |

### Technical Risks

| # | Risk | Likelihood | Impact | Category | Mitigation |
|---|------|-----------|--------|----------|------------|
| T1 | Transformation expression translation errors produce incorrect data | High | Critical | Prevent | Automated output comparison between source and target for every migrated job |
| T2 | On-premises database connectivity issues from new platform | Medium | High | Mitigate | Validate network paths in Phase 1; maintain source platform as fallback for 30 days |
| T3 | Cloud ERP API changes during migration | Low | High | Monitor | Pin API versions; test against sandbox before production |
| T4 | CRM OAuth flow migration breaks sync | Medium | Medium | Mitigate | Test upgraded auth flow in CRM sandbox; keep legacy flow as temporary fallback |
| T5 | Data warehouse cost increase during migration | Medium | Medium | Monitor | Track costs daily; implement SELECT * replacement before high-volume regions |
| T6 | Data quality regression not detected | Medium | Critical | Prevent | Establish automated quality checks (row counts, null rates, distributions) before migration |
| T7 | Implicit job dependencies not discovered | Medium | High | Mitigate | Document all dependencies through data flow analysis before migration |
| T8 | Pagination error handling masks partial extractions | High | High | Prevent | Redesign pagination loop with HTTP status validation and row count verification |
| T9 | Dual-language filters missed in translation | Low | Medium | Monitor | Document all dual-language conditions; test with production data |
| T10 | Manufacturing/cost pipeline logic errors | Medium | High | Mitigate | Allocate extra testing time; compare calculations line-by-line |

### Operational Risks

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|------------|
| O1 | Extended parallel operation period increases operational overhead | High | Medium | Define clear cutover criteria; limit parallel period to 2-4 weeks per region |
| O2 | Production incidents during migration distract from migration work | Medium | Medium | Maintain dedicated operations team separate from migration team |
| O3 | Credential exposure during migration | Medium | High | Never copy raw credentials; migrate directly to secrets manager; rotate post-migration |
| O4 | Target platform outage halts migration | Low | High | Have rollback plan to source platform for each phase |

### Organizational Risks

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|------------|
| G1 | Team knowledge loss during platform transition | Low | High | Maintain updated documentation; ensure at least 2 people know each region |
| G2 | Extended timeline exceeds budget | Medium | Medium | Prioritize by business value; phased approach allows partial production use |
| G3 | Stakeholder resistance to platform change | Low | Medium | Demonstrate target platform benefits early (Phase 3 proof of concept) |
| G4 | Skills gap on target platform | Medium | Medium | Training plan in Phase 1; pair experienced developers with target platform specialists |

### Risk Mitigation Plan

| Priority | Action | Risk Refs | Effort | When |
|----------|--------|-----------|--------|------|
| 1 | Establish automated output comparison framework | T1, T6 | 1 week | Before Phase 2 |
| 2 | Validate all network paths and connections | T2, O3 | 2-3 days | Phase 1 |
| 3 | Document all job dependencies via data flow analysis | T7 | 3-5 days | Phase 1 |
| 4 | Pin all external API versions | T3 | 1 day | Phase 1 |
| 5 | Redesign pagination with error validation | T8 | 3 days | Phase 2 |
| 6 | Set up daily cost monitoring | T5 | 1 day | Phase 1 |
| 7 | Document dual-language filter conditions | T9 | 1 day | Before Phase 4-5 |
| 8 | Create credential migration procedure | O3 | 2 days | Phase 1 |
| 9 | Define per-phase rollback procedures | O4 | 2 days | Before each phase |
| 10 | Target platform training for team | G4 | 1 week | Phase 1 |

---

## Dependency Analysis

### Cross-Region Data Dependencies

Map how data flows between regions:

```
Regional Source Systems
    |
    v
Regional Raw Layer (per region)
    |
    v
Regional Clean Layer (per region) --+--> Regional Sales_Set
    |                                |
    v                                v
Shared Reference Data            Global Consolidation
(Fiscal_Periods,                    |
 Country_Master,                    v
 Conversion_Rate,               Global Sales_Set
 Account_Mappings)                  |
    |                               v
    +-----------> Budget / Forecast / Prior-Year Pipelines
                                    |
                                    v
                              Consolidated Reporting
```

**Key dependency constraints:**
1. Shared reference data must be current before any regional clean-layer job runs
2. All regional Sales_Sets must be complete before the Global consolidation runs
3. Global consolidation must complete before budget/forecast jobs run
4. CRM sync jobs can run independently after regional clean-layer completion

### Shared Component Dependencies

Identify components shared across regions that create coupling:

| Shared Component | Used By | Migration Constraint |
|-----------------|---------|---------------------|
| {shared_template_jobs} | {regions} | Must be migrated before dependent regions |
| {shared_reference_tables} | All regions | Must be populated before any regional job |
| {shared_logging_component} | {regions} | Replace with target platform equivalent in Phase 1 |
| {shared_cleanup_component} | {regions} | Replace with target platform equivalent in Phase 1 |

### External System Dependencies

| External System | Dependency Type | Regions Affected | Migration Impact |
|----------------|----------------|-----------------|-----------------|
| On-premises databases | Network connectivity | {regions} | Must validate from target platform |
| Cloud ERP API | Authentication + API access | {regions} | Must register new app in identity provider |
| CRM | Authentication + API access | {regions} | Must create new Connected App |
| Data warehouse | Service account | All | Must configure IAM permissions |
| Cloud storage | Service account | All | Must configure access permissions |
| SFTP | Network + credentials | {region} | Must validate connectivity |

### Migration Sequencing Constraints

Based on the dependency analysis, the following sequencing constraints apply:

1. **Phase 1 (Infrastructure) must complete before anything else.** All connections and shared services must be validated.
2. **Phase 2 (Shared Templates) must complete before any region that uses them.** Regions with cloud ERP sources depend on shared extraction templates.
3. **Small regions (Phase 3) should be migrated before major regions.** They serve as proof-of-concept validation.
4. **Major regions can be migrated in parallel** if sufficient team capacity exists, since they are independent of each other.
5. **Global consolidation (Phase 6) must be last.** It depends on all regional outputs being available.
6. **CRM sync jobs within each region can be migrated independently** of the clean-layer pipeline, since they read from (not write to) the shared output tables.

---

## Recommended Migration Order by Region

### Sequencing Rationale

The recommended order balances four factors:
1. **Dependency constraints** -- regions that others depend on must go first
2. **Risk management** -- start small to validate the approach before tackling complexity
3. **Business value** -- migrate high-value regions early to realize benefits sooner
4. **Team learning** -- complexity should increase as the team gains experience

### Detailed Regional Migration Plans

**Phase 3a -- Proof of Concept: Smallest regions**

| Attribute | Details |
|-----------|---------|
| Regions | {smallest regions, e.g., SAF/ZAF consolidated} |
| Job Count | {N} |
| Duration | 1-2 weeks |
| Rationale | Smallest pipeline; includes operational best practices (logging, cleanup); low risk; validates target platform's core capabilities |
| Special Tasks | Consolidate duplicate regional designations; resolve cross-wired connection dependencies |

**Phase 3b -- Small Regions: Compact complete pipelines**

| Attribute | Details |
|-----------|---------|
| Regions | {small regions, e.g., ITA, DEU} |
| Job Count | {N} |
| Duration | 2-3 weeks |
| Rationale | Complete pipelines with manageable scope; DEU exercises dual-entity pattern; ITA exercises CRM sync; both depend on Phase 2 templates |
| Special Tasks | Validate MSSQL extraction; validate CRM sync; validate dual-entity harmonization |

**Phase 4a -- First Major Region**

| Attribute | Details |
|-----------|---------|
| Region | {first major region by size} |
| Job Count | {N} |
| Duration | 4-6 weeks |
| Rationale | Largest region; exercises every source type, every layer, and the most complex transformations; definitive stress test |
| Special Tasks | Dual-source architecture; manufacturing pipeline; cost analysis; regulatory data formats |

**Phase 4b -- Second Major Region**

| Attribute | Details |
|-----------|---------|
| Region | {second major region} |
| Job Count | {N} |
| Duration | 3-4 weeks |
| Rationale | Similar architecture to first major region, so migration patterns can be reused; unique integrations (e.g., SFTP) add one new capability |
| Special Tasks | Dual-subsidiary architecture; SFTP integration; high version numbers indicating complex logic |

**Phase 4c -- Third Major Region**

| Attribute | Details |
|-----------|---------|
| Region | {third major region} |
| Job Count | {N} |
| Duration | 2-3 weeks |
| Rationale | Structurally similar to other major regions but with unique stream-merging patterns; high component density requires performance testing |
| Special Tasks | Stream-merging pattern; performance validation |

**Phase 6 -- Global Consolidation**

| Attribute | Details |
|-----------|---------|
| Region | Global |
| Job Count | {N} |
| Duration | 3-4 weeks |
| Rationale | Depends on all regional outputs; must implement explicit dependency orchestration; includes budget/forecast/prior-year pipelines |
| Special Tasks | Build dependency graph; implement trigger-based orchestration; validate end-to-end data flow |

### Parallel vs Sequential Execution

With sufficient team capacity, certain phases can overlap:

| Parallel Track A | Parallel Track B | Constraint |
|-----------------|-----------------|------------|
| Phase 3a (Smallest regions) | Phase 3b (Small regions) | Both depend on Phase 2 completion |
| Phase 4a (First major region) | Phase 4b (Second major region) | Independent pipelines |
| Phase 4c (Third major region) | Phase 6 prep (dependency mapping) | Phase 6 execution must wait for Phase 4 |

**Maximum parallelism reduces total calendar time from {N} weeks to {N} weeks**, but requires a larger team (4-6 people vs 2-3).

---

## Success Criteria

### Per-Phase Success Criteria

| Phase | Criteria | Measurement |
|-------|----------|-------------|
| Phase 1 | All connections validated | Each connection tested with read/write operation |
| Phase 1 | Logging and monitoring operational | Test log messages appear in aggregation system |
| Phase 2 | Shared templates produce identical output | Row count and column value comparison |
| Phase 2 | Pagination handles 10,000+ records | Test with large source tables |
| Phase 3 | Small region pipelines produce matching output | End-to-end row count comparison within 1% |
| Phase 3 | CRM sync produces correct records | Record-level comparison in CRM sandbox |
| Phase 4 | Major region Sales_Sets match | Row counts, totals by dimension, sample record comparison |
| Phase 4 | Performance within acceptable bounds | Execution time within 150% of source platform |
| Phase 5 | Complex region output matches | Manufacturing/cost pipeline calculations verified |
| Phase 6 | Global consolidation totals match | Cross-check against sum of regional outputs |
| Phase 6 | Orchestration handles failures correctly | Simulated upstream failure blocks downstream execution |

### Overall Migration Success Criteria

The migration is considered successful when ALL of the following are true:

1. **Data accuracy**: All production pipelines produce output matching the source platform within defined tolerances (row counts within 1%, financial totals within 0.01%)
2. **Operational stability**: No unplanned pipeline failures for 2 consecutive weeks post-migration
3. **Performance**: End-to-end pipeline execution time within 150% of source platform baseline
4. **Observability**: 100% of migrated jobs have structured logging, error handling, and monitoring
5. **Security**: All credentials managed via secrets manager; no flat-file credential storage
6. **Orchestration**: Dependency-based execution confirmed working; no implicit timing dependencies
7. **Documentation**: All migrated jobs documented; runbook updated for target platform
8. **Team capability**: At least 2 team members can independently operate each region's pipeline

### Go / No-Go Decision Framework

Before each phase proceeds to production, apply this decision framework:

| Criterion | Go | No-Go |
|-----------|-----|-------|
| Output accuracy | Matches within tolerance | Discrepancies exceed tolerance |
| Error handling | All jobs have error handlers | Any job lacks error handling |
| Performance | Within 150% of baseline | Exceeds 200% of baseline |
| Monitoring | Dashboards operational | Any critical metric unmonitored |
| Rollback tested | Rollback procedure verified | Rollback not tested |
| Team sign-off | Both migration and operations teams approve | Either team has unresolved concerns |

---

## Key Architectural Decisions

### What to Preserve

These patterns work well and should be maintained in the target platform:

1. **The three-layer architecture (Raw -> Clean -> Global).** This separation of concerns is sound and well-understood by the team.

2. **The naming convention ({Region}_{Subsidiary}_{Layer}_{Description}).** Self-documenting, consistent, and valuable for operational monitoring. Standardize capitalization but keep the structure.

3. **The parameterized template pattern.** Proven concept for reusable extraction jobs. Extend to other job families.

4. **The shared logging and cleanup components.** Centralized operational scaffolding. Upgrade to structured logging but preserve the pattern.

5. **Context variable-based environment switching.** The concept of environment isolation via configuration is correct. Migrate to the target platform's equivalent mechanism.

6. **The Sales_Set as the canonical reporting output.** Standardized schema across regions enables global consolidation. Preserve the schema.

7. **Dataset naming alignment with job naming.** Makes it straightforward to determine which dataset a job operates on.

### What to Redesign

These patterns need improvement during migration:

1. **TRUNCATE-then-load -> Transactional swap or MERGE/UPSERT.** Eliminate the data availability gap during rebuilds.

2. **SELECT * -> Explicit column lists.** Reduce data warehouse costs by 40-70% and improve query performance.

3. **Copy-paste development -> Parameterized templates.** Extend the template pattern to output builders, CRM sync, and database extraction.

4. **Implicit job ordering -> Dependency-based orchestration.** Replace operator knowledge with automated dependency management.

5. **No error handling -> Comprehensive error handling.** Every migrated job must handle errors, retry transient failures, and alert on permanent failures.

6. **No monitoring -> Full observability.** Job execution metrics, data warehouse cost tracking, pipeline health dashboards.

7. **Duplicated code blocks -> Shared libraries.** Convert copy-pasted boilerplate (e.g., stats tracking) into centralized utilities.

8. **Legacy authentication -> Modern auth flows.** Upgrade to JWT Bearer or Client Credentials where legacy username-password auth is used.

9. **Service account key files -> Workload Identity Federation.** Eliminate long-lived credential files where possible.

10. **File-based staging -> Cloud-native staging.** Replace local temp files with cloud storage objects with lifecycle policies.

### What to Eliminate

These should not be carried forward:

1. **Backup and debug job variants.** Replace with native version control in the target platform.
2. **Copy_of_ jobs.** Consolidate into parameterized templates.
3. **Scratchpad/test jobs.** Provide a proper development environment instead.
4. **Legacy compatibility jobs.** Evaluate if downstream consumers can be migrated; if so, drop legacy wrappers.
5. **Hardcoded dataset references.** All environment-specific values must be parameterized.
6. **Duplicate regional designations.** Consolidate into canonical regions.
7. **Duplicated boilerplate code.** Replace with shared library.
8. **Manual credential management.** Replace with secrets manager and automated rotation.

---

## Quick Wins

These improvements can be made during migration with minimal additional effort:

| Quick Win | Effort | Impact | When |
|-----------|--------|--------|------|
| Replace SELECT * with explicit column lists | Low per job | 40-70% data warehouse cost reduction | During each job migration |
| Add error handling to every migrated job | Low (template-based) | Eliminates silent failures | During each job migration |
| Standardize logging across all regions | Low (shared library) | Full observability | Phase 1 |
| Rename cloned jobs to correct region codes | Trivial | Eliminates naming confusion | During regional migration |
| Strip Copy_of_ prefixes and reset versions | Trivial | Clean naming | During each job migration |
| Consolidate duplicate regions into single pipeline | Low | Eliminates organizational debt | Phase 3 |
| Replace hardcoded constants with config | Low | Single-point updates, no drift | During each job migration |
| Add data freshness checks to consolidation jobs | Medium | Prevents stale consolidation | Phase 6 |
| Standardize on snake_case for data warehouse names | Low | Consistent casing | During dataset creation |
| Add partition pruning for date-filtered queries | Medium | Major scan reduction | During query migration |

---

*Generated by intodoc -- documentation automation for integration platforms.*
