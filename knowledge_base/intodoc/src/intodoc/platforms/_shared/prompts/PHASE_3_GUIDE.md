# Phase 3: Assistant-Grade Synthesis Guide

Use this guide when assembling final documentation from validated Phase 2 analysis, scorecard output, EngineeringFacts, and platform-specific analysis guides.

The audience is coding assistants, integration engineers, and developers. The docs must help them understand, debug, assess, and safely change the system. Do not write broad stakeholder prose.

## Global Rules

- Use the 9-doc assistant taxonomy only.
- Preserve exact canonical unit names and source artifacts.
- Cite evidence sources such as Phase 2 sections, EngineeringFacts artifacts, source coverage, scorecard findings, and scanner-limited gaps.
- Keep platform-specific vocabulary in the platform evidence layer. Do not force Talend, Workato, Lobster, or generic-code wording unless the scanned project uses it.
- Separate direct evidence from inference with HIGH / MEDIUM / LOW confidence.
- If evidence is absent, name the affected unit and say scanner-limited.

## 00 Agent Brief

Write onboarding and navigation for a future assistant:

1. Project Orientation
2. Documentation Map
3. Onboarding
4. Common Tasks
5. Known Traps
6. Glossary

## 01 Architecture Map

Document topology, boundaries, entrypoints, orchestration, dependency graph, shared infrastructure, and scanner-limited architecture gaps.

## 02 Component Inventory

Document every canonical unit with name, group, source artifact, unit type, purpose, internal structure, dependencies, external systems, and confidence.

## 03 Data Flows And Lineage

Document source-to-target flows, schemas/entities/fields, mappings, transformations, validation, reject/error paths, readers/writers, and lineage confidence.

## 04 Business Rules And Transformations

Document business rules, formulas, conditions, transformations, lookup behavior, hardcoded values, conflict cases, and downstream impact.

## 05 Runtime Config And Externals

Document runtime config, environment selection, external systems, connection/credential handling, endpoints, deployment assumptions, and portability risks.

## 06 Operations Testing And Debugging

Document run/debug/verify workflows, failure scenarios, monitoring/logging evidence, recovery procedures, test gaps, and validation checks.

## 07 Quality Security Risk Scorecard

Convert the scorecard into a readable deep assessment with scores, confidence, evidence, red flags, quick wins, unknowns, and platform/tooling fit.

## 08 Change Playbook

Document change-impact guidance: change target, inspect first, affected units, required tests/checks, rollback risk, and confidence.
