# Phase 3: Assistant-Grade Talend Synthesis Guide

Use the shared 9-doc assistant taxonomy. This Talend-specific guide defines what concrete evidence means for Talend projects.

## Talend Evidence Requirements

- Identity: job name, group, `.item` source artifact, context group, routines where present.
- Architecture: tRunJob parent-child references, trigger chains, route/job boundaries, FLOW and trigger connections.
- Inventory: component type, unique_name, role, active/inactive status, orphaned components, missing references.
- Data lineage: tMap input/output/Var tables, full expressions, schemas, lookup joins, reject outputs, SQL queries.
- Business rules: tMap conditions, Java code, SQL WHERE/CASE/JOIN predicates, RunIf expressions, hardcoded values.
- Runtime config: context variables, REPOSITORY vs BUILT_IN connections, metadata connection refs, dynamic context loading.
- Operations: tLogCatcher/tStatCatcher, SUBJOB_ERROR, DIE_ON_CHILD_ERROR, tWarn/tDie, schedules/triggers, recovery gaps.
- Change guidance: when changing a component, mapping, context variable, connection, schema, or child job, list affected jobs and tests/checks.

Do not write generic Talend best practices unless tied to exact scanned jobs/components.
