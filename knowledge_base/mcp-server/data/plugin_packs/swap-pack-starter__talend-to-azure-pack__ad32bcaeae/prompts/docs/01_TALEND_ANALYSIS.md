Project Summary:
- State what the Talend project does, its main source and target systems,
  and the overall data flow in a short paragraph.
- Give job, connection, and context-variable counts from evidence.

Job Inventory:
- One table row per job: name, version, purpose, trigger, migration class
  (COPY, TRANSFORM, ORCHESTRATION, CUSTOM), and complexity from the
  scorecard.
- Show the tRunJob call tree for orchestration jobs as an indented list.

Component Inventory:
- Per job, list the components in execution order with their role and the
  key settings that matter for migration (queries, paths, write mode, tMap
  logic summary).
- Quote tMap expressions and custom Java code verbatim in code blocks;
  this is the reference the Azure implementation will be checked against.

Contexts And Connections:
- Table of context variables: name, purpose, per-environment values with
  credentials redacted, and the Azure parameter or Key Vault secret each
  one becomes.
- Table of repository connections: name, type, host/database or path
  (redacted where sensitive), and the future linked service name.

Orchestration And Scheduling:
- Describe evidenced schedules, execution order constraints, and
  parent-child parameter passing.
- State plainly when no scheduling evidence exists for a job.

Evidence Notes:
- List every gap by job and component. This section drives the manual
  work list, so be complete rather than reassuring.
