# Phase 3: Cross-Group Synthesis Guide

Use this guide when assembling final documentation from multiple group analyses.
These instructions cover cross-cutting concerns that span groups.

---

## Executive Summary Structure (for 00_EXECUTIVE_SUMMARY)

Produce a customer-facing document with NO code or expressions. Structure:

1. **Platform Overview**: what the integration platform does, total jobs, groups, connections
2. **Architecture Summary**: high-level data flow (sources -> processing -> targets)
3. **Health Dashboard**: overall health scores per dimension (GOOD / ADEQUATE / POOR / NONE):
   Data Quality, Error Handling, Security, Performance, Maintainability
4. **Risk Summary**: top 5 critical/high risks across all groups
5. **Recommendations Roadmap**: prioritized actions (immediate / short-term / long-term)
6. **FAQ**: 10+ stakeholder questions
7. **Glossary**: platform-specific terms

---

## Cross-Region Comparison (for 08_PATTERNS_STANDARDS, only if 2+ groups)

Compare groups across:
1. **Structural Comparison**: jobs per group, component types, complexity
2. **Naming Standardization**: consistency of naming conventions across groups
3. **Pattern Reuse**: shared vs group-specific patterns, template jobs
4. **Error Handling Consistency**: compare ratings across groups
5. **Configuration Consistency**: variable naming, connection naming
6. **Standardization Score (1-5)**: 5=highly standardized, 1=no standardization

---

## Migration Assessment (for 11_MIGRATION_ASSESSMENT)

Per-job assessment:
1. **Complexity Score (1-5)**: 1=simple linear flow, 5=extreme custom integrations
2. **Auto-Migrate Probability**: HIGH / MEDIUM / LOW / MANUAL
3. **Blocker Classification**: HARD BLOCKER / SOFT BLOCKER / WARNING
4. **Migration Groups**: lift-and-shift / transform-and-migrate / redesign required
5. **Dependency Order**: which jobs must migrate first

---

## Onboarding Guide (for 10_ONBOARDING_FAQ)

1. Getting Started (day 1 essentials)
2. Architecture Walkthrough (end-to-end flow)
3. Common Tasks (add job, modify transformation, add connection)
4. Troubleshooting (common failures and diagnosis)
5. FAQ (20+ questions by topic)
6. Glossary (domain + platform terms)
