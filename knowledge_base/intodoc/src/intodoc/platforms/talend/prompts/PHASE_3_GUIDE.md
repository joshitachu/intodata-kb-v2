# Phase 3: Cross-Group Synthesis Guide

Use this guide when assembling final documentation from multiple group analyses.
These instructions cover cross-cutting concerns that span groups.

---

## Executive Summary Structure (for 00_EXECUTIVE_SUMMARY)

Produce a customer-facing document with NO code or expressions. Structure:

1. **Platform Overview**: what the integration platform does, total jobs, groups, connections
2. **Architecture Summary**: high-level data flow (sources -> processing -> targets), key systems
3. **Health Dashboard**: overall health scores per dimension:
   - Data Quality: GOOD / ADEQUATE / POOR / NONE (aggregate across groups)
   - Error Handling: GOOD / ADEQUATE / POOR / NONE
   - Security Posture: GOOD / ADEQUATE / POOR / NONE
   - Performance: GOOD / ADEQUATE / POOR / NONE
   - Maintainability: GOOD / ADEQUATE / POOR / NONE
4. **Risk Summary**: top 5 critical/high risks across all groups
5. **Recommendations Roadmap**: prioritized action items (immediate / short-term / long-term)
6. **FAQ**: 10+ questions a new team member or stakeholder would ask
7. **Glossary**: platform-specific terms (Talend components, connection types, etc.)

---

## Cross-Region Comparison (for 08_PATTERNS_STANDARDS, only if 2+ groups)

Compare groups across these dimensions:

1. **Structural Comparison**: jobs per group, component types used, complexity distribution
2. **Naming Standardization**: are naming conventions consistent across groups? Score each group
3. **Pattern Reuse**: shared patterns vs group-specific patterns
   - Identify template jobs that appear in multiple groups with minor variations
   - Identify shared utility routines
4. **Error Handling Consistency**: compare error handling ratings across groups
5. **Data Flow Patterns**: are similar data flows implemented consistently?
6. **Configuration Consistency**: context variable naming, connection naming across groups
7. **Standardization Score**: rate overall cross-group consistency (1-5 scale):
   - 5: Highly standardized, clear shared patterns
   - 4: Mostly standardized with minor deviations
   - 3: Some shared patterns but significant group-specific variation
   - 2: Minimal standardization, each group evolved independently
   - 1: No standardization, completely independent implementations

---

## Migration Assessment Methodology (for 11_MIGRATION_ASSESSMENT)

For each job, assess migration complexity:

1. **Complexity Score (1-5)**:
   - 1: Simple (linear flow, standard components, no custom code)
   - 2: Moderate (tMap transformations, context variables, standard patterns)
   - 3: Complex (custom Java code, complex tMap expressions, multiple lookups)
   - 4: Very Complex (tJavaFlex, external API calls, complex orchestration)
   - 5: Extreme (system-level integrations, undocumented dependencies, legacy patterns)

2. **Auto-Migrate Probability**:
   - HIGH: standard components, clear mappings, no custom code
   - MEDIUM: some custom code but standard patterns, translatable expressions
   - LOW: heavy custom code, platform-specific features, complex orchestration
   - MANUAL: requires complete redesign (tJavaFlex with system calls, etc.)

3. **Blocker Classification**:
   - HARD BLOCKER: no equivalent in target platform (requires redesign)
   - SOFT BLOCKER: equivalent exists but requires manual configuration
   - WARNING: works but may behave differently (data type handling, null semantics)

4. **Migration Groups**: cluster jobs by migration approach:
   - Lift-and-shift candidates (standard ETL patterns)
   - Transform-and-migrate (needs expression translation)
   - Redesign required (complex custom code or platform-specific features)

5. **Dependency Order**: which jobs must be migrated first (shared connections, parent jobs, utility routines)

---

## Onboarding Guide Structure (for 10_ONBOARDING_FAQ)

1. **Getting Started**: what a new developer needs to know on day 1
2. **Architecture Walkthrough**: how the jobs fit together end-to-end
3. **Common Tasks**: how to add a new job, modify a transformation, add a new connection
4. **Troubleshooting**: common failure modes and how to diagnose them
5. **FAQ**: 20+ questions organized by topic (architecture, data flows, operations, debugging)
6. **Glossary**: all domain-specific and platform-specific terms
