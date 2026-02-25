# MCP Documentation Generation Workflow

The Claude Code agent generates documentation from scan data exposed via MCP tools.
This is a 4-phase process using parallel sub-agents for throughput.

## Workflow Overview

```
Phase 1: Setup         -- check scan status, load chunks + manifest
Phase 2: Group Analysis -- parallel sub-agents (one per group), return compact text
Phase 3: Write Docs    -- parallel sub-agents by theme, write final .md files
Phase 4: Publish       -- write index.json + run log
```

## Phase 1: Setup

1. Call `recall_process_memory()` to retrieve process lessons from previous runs.
   - Inject any returned memories into your working context (e.g. "avoid >100K token chunks").
2. Call `recall_project_memory(project_id)` to retrieve prior findings for this project.
   - If memories exist, note previous group structure, health scores, risk flags.
   - On re-scans, use this to focus on what CHANGED versus the last analysis.
3. Call `get_scan_status(project_id)` to check cache freshness.
4. If stale or missing, call `trigger_scan(project_id)`.
5. Call `get_chunks(project_id)`.
   - Returns chunks with fields: `chunk_id`, `group`, `job_count`, `job_names`, `token_estimate`.
   - Group the chunk list by the `group` field. The number and names of groups are
     determined entirely by the scan data -- never assume specific names.
6. Call `fetch_json(project_id, "manifest")` to load the dependency graph.
7. Call `fetch_json(project_id, "connections")` to load metadata connections.
8. Create `output/{project_id}/docs/` and `output/{project_id}/logs/`.

## Phase 2: Group Analysis (Parallel Sub-Agents)

Spawn one sub-agent per group. All groups run in parallel.

The number of sub-agents is dynamic:
- 1 group (small project)  -> 1 sub-agent
- 10 groups (medium)       -> 10 sub-agents
- 20 groups (large)        -> 20 sub-agents

### What each Group Sub-Agent does

Each sub-agent receives:
- The project_id and its group name
- The list of chunk_ids in its group
- Instructions to read the prompt files listed in the Analysis Prompt Reference below

Each sub-agent must:
1. Read the relevant prompt files (listed below) using the Read tool.
2. Call `fetch_chunk(project_id, chunk_id)` for every chunk in its group.
3. Analyze ALL chunks across ALL dimensions using the prompt file guidance.
4. **Return a compact text summary (~3-5KB) directly back to the calling agent.**

**The sub-agent writes NO files.** Analysis is returned as text.

### Group Summary Structure

Each group sub-agent returns a structured text block:

```
GROUP: {group_name}
CHUNKS: {chunk_ids joined by comma}
JOB_COUNT: {total jobs in group}

COMPONENTS:
  {component_type}: {count} -- jobs: {representative job names}
  ...

EXECUTION_FLOW:
  entry_points: {jobs with no callers}
  call_chains: {tRunJob relationships, e.g. A -> B -> C}
  hooks: {pre/post-job tRunJob triggers}

DATA_FLOWS:
  sources: {system_type | connection_name | tables or endpoints}
  key_transforms: {tMap expressions, lookups, joins, notable filters}
  targets: {system_type | connection_name | tables | write_mode}

BUSINESS_RULES:
  BR-{GROUP}-001: {rule description} | job: {job_name} | confidence: CERTAIN/LIKELY/INFERRED
  BR-{GROUP}-002: ...
  (Replace {GROUP} with the actual group name, uppercase, max 6 chars)

EXTERNAL_SYSTEMS:
  {type}: {connection_name} -- {tables or endpoints referenced}

ERROR_HANDLING:
  {pattern or gap}: {jobs affected}

CUSTOM_CODE:
  {job}.{component}: {language} -- {purpose}

ENVIRONMENT:
  context_vars: {variable names and what they control}
  env_specific: {values that differ by environment}

SCHEDULING:
  implied_frequency: {daily/weekly/event-driven/unknown}
  dependencies: {upstream jobs or triggers}

NAMING_PATTERNS:
  {pattern}: {examples} [VIOLATIONS: {anomalies}]

CROSS_GROUP_REFS:
  calls_out: {jobs in this group that call jobs in other groups}
  called_by: {jobs in other groups that call jobs in this group}

RISK_FLAGS:
  CRITICAL: {description}
  HIGH: {description}
  MEDIUM: {description}

PREVENTIVE_RISKS:
  latent_failures:
    LF-{GROUP}-001: {trigger} | impact: {description} | probability: {H/M/L}
  missing_safeguards:
    {safeguard}: missing in {N} jobs -- priority: {CRITICAL/HIGH/MEDIUM}
  anti_patterns:
    {AP-NNN}: {count} instances -- jobs: {names}
  fragility_scores:
    BRITTLE: {count} | FRAGILE: {count} | ADEQUATE: {count} | ROBUST: {count}
  health_inputs:
    error_handling_pct: {N}% | data_integrity_pct: {N}% | code_hygiene_pct: {N}% | observability_pct: {N}%
  cascade_chains:
    {chain description} | blast_radius: {CRITICAL/HIGH/MEDIUM}
```

### Prompt Files for Group Sub-Agents

Before analyzing, read these prompt files (use the Read tool -- one read per file):

| Dimension | Prompt Files |
|-----------|-------------|
| Components + Execution | `intodoc/src/intodoc/platforms/talend/prompts/p01_inventory.txt`, `p02_execution.txt` |
| External Systems | `intodoc/src/intodoc/platforms/talend/prompts/p05_external_systems.txt` |
| Data Flows + Schema | `intodoc/src/intodoc/platforms/talend/prompts/p03_data_flows.txt`, `p11_schema.txt` |
| Business Logic + Quality | `intodoc/src/intodoc/platforms/talend/prompts/p09_business_logic.txt`, `p08_custom_code.txt`, `p12_data_quality.txt` |
| Operations | `intodoc/src/intodoc/platforms/talend/prompts/p04_environment.txt`, `p06_error_handling.txt`, `p07_state.txt`, `p14_scheduling.txt`, `p15_security.txt`, `p16_performance.txt` |
| Patterns | `intodoc/src/intodoc/platforms/talend/prompts/p13_naming.txt`, `p18_reusability.txt` |
| Preventive Analysis | `intodoc/src/intodoc/platforms/talend/prompts/p22_preventive_analysis.txt` |

Also read (once, before spawning group sub-agents):
- `intodoc/src/intodoc/platforms/talend/best_practices.yaml`

## Phase 3: Write Documents (Parallel Sub-Agents by Theme)

After receiving ALL group summaries, spawn doc-writing sub-agents in parallel.

Each doc agent receives:
- The relevant group summaries (the full text blocks from Phase 2)
- The manifest JSON
- The specific reference doc(s) from `intodoc/reference-docs/` for format and depth

**Each doc agent reads the matching reference doc(s) to understand structure.**
**Each doc agent writes its .md files directly to `output/{project_id}/docs/`.**

### Doc Agent Groups

| Agent | Writes | Receives from Phase 2 |
|-------|--------|-----------------------|
| Overview | 00_PROJECT_OVERVIEW.md | All summaries + manifest |
| Architecture | 02_ARCHITECTURE.md, 03_EXECUTION_LOGIC.md, 01_COMPONENT_INVENTORY.md | COMPONENTS, EXECUTION_FLOW sections |
| Data | 04_DATA_FLOWS.md, 16_SCHEMA_DICTIONARY.md, 17_DATA_QUALITY.md | DATA_FLOWS sections |
| Business | 10_BUSINESS_LOGIC.md, 09_CUSTOM_CODE.md | BUSINESS_RULES, CUSTOM_CODE sections |
| Operations | 07_ERROR_HANDLING.md, 08_STATE_SIDE_EFFECTS.md, 11_KNOWN_ISSUES.md | ERROR_HANDLING, RISK_FLAGS sections |
| Config | 05_ENVIRONMENT_CONFIG.md, 20_SECURITY_ACCESS.md, 21_PERFORMANCE.md | ENVIRONMENT sections |
| External | 06_EXTERNAL_SYSTEMS.md, 19_SCHEDULING_SLA.md | EXTERNAL_SYSTEMS, SCHEDULING sections |
| Patterns | 18_NAMING_CONVENTIONS.md, 23_REUSABILITY.md, 22_CROSS_REGION.md* | NAMING_PATTERNS, CROSS_GROUP_REFS |
| User Docs | 14_GLOSSARY_FAQ.md, 15_ONBOARDING.md, 13_RUNBOOK.md, 12_CHANGE_LOG.md | All summaries |
| Preventive | 25_PREVENTIVE_ANALYSIS.md | PREVENTIVE_RISKS, RISK_FLAGS, ERROR_HANDLING sections from all summaries + manifest |

*Only generate 22_CROSS_REGION.md if the project has 2 or more meaningful groups.

### Adaptive Document Catalog

Not all docs are relevant to every project. Apply these rules:

- **Always generate**: 00, 01, 02, 03, 04, 06, 07, 10, 13, 14, 15, 25
- **Multiple groups only**: 22_CROSS_REGION.md (skip for single-group projects)
- **Only if found in scan**: 09_CUSTOM_CODE.md (custom Java code), 19_SCHEDULING_SLA.md
  (scheduling evidence), 20_SECURITY_ACCESS.md (security issues), 21_PERFORMANCE.md
  (performance indicators)
- Skip any doc that would have no meaningful content for this project

### Quality Standards

Match the quality and depth of reference docs in `intodoc/reference-docs/`.

- Every document MUST have a table of contents
- Use plain-text ASCII tables (no Unicode box-drawing characters)
- Business rules get tagged IDs: BR-{GROUP}-{NNN}
- Include job names and component references for traceability
- Risk items get severity ratings: CRITICAL / HIGH / MEDIUM / LOW
- Include "Impact" statements where relevant

## Phase 4: Publish

1. Write `output/{project_id}/docs/index.json` mapping each document to its source groups:
   ```json
   {
     "project_id": "...",
     "generated_at": "...",
     "documents": [
       {"file": "00_PROJECT_OVERVIEW.md", "groups": ["all"], "agent": "Overview"},
       ...
     ]
   }
   ```
2. Write `output/{project_id}/logs/run_{project_id}_{timestamp}.log`:
   ```
   [INFO] Groups analyzed: {list}
   [INFO] Chunks processed: {n}
   [INFO] Documents generated: {list of filenames}
   [WARNING] {any warnings}
   [ERROR] {any errors}
   [INFO] Total elapsed: {time}
   ```
3. Call `retain_findings(project_id, findings, context="run-summary")` with a summary:
   - Documents generated, health scores, risk flag counts, group structure.
   - Example: "Generated 22 docs. Groups: Account, SalesOrder, Monitoring. Health: error_handling=72%, data_integrity=85%. 3 CRITICAL risks."
4. If any errors occurred during generation, call `retain_process_lesson(lesson, context="error-lesson")`:
   - Example: "Phase 2 sub-agent for Monitoring timed out at 95s. Consider splitting large groups or increasing timeout."

## Output Structure

```
output/{project_id}/
  docs/
    00_PROJECT_OVERVIEW.md
    01_COMPONENT_INVENTORY.md
    ... (only docs that are relevant to this project)
    index.json
  logs/
    run_{project_id}_{timestamp}.log
```

No `_scratch/` directory. No intermediate JSON files. Only final docs.

## MCP Tools Available

| Tool | What it does |
|------|-------------|
| `add_repo(url, project_id, platform?)` | Register a project for scanning |
| `fetch_json(project_id, section?)` | Read cached scan JSON (segmented) |
| `trigger_scan(project_id, force?)` | Run intodoc scan subprocess |
| `get_scan_status(project_id)` | Check cache freshness |
| `get_chunks(project_id)` | Get chunk definitions (chunk_id, group, job_count, token_estimate) |
| `fetch_chunk(project_id, chunk_id)` | Get full job data for one chunk |
| `insert_to_rag(project_id, docs?)` | Push docs to RAG pipeline |
| `list_projects()` | List all registered projects |
| `recall_process_memory(query?)` | Get process lessons before starting (Layer A) |
| `recall_project_memory(project_id, query?)` | Get prior findings for a project (Layer B) |
| `retain_findings(project_id, findings, context?)` | Store analysis findings after completion |
| `retain_process_lesson(lesson, context?)` | Store operational lessons from errors/improvements |

## fetch_json Sections

- `meta` -- platform, repo_root, stats, scanned_at
- `manifest` -- job_calls, job_called_by, connections_used_by, routines_used_by
- `connections` -- all metadata connections
- `contexts` -- context variable groups
- `jobs/{GROUP}` -- all jobs in a specific group (large groups may exceed context)
- `all` -- meta + manifest + connections + job group summary (NOT full job data)

## Analysis Prompt Reference

| Pass | Prompt Files | Focus |
|------|-------------|-------|
| Foundation | p01_inventory, p02_execution, p05_external_systems | Components, orchestration, systems |
| Data Flows | p03_data_flows, p11_schema | Inputs, transforms, outputs, schemas |
| Business Logic | p09_business_logic, p08_custom_code, p12_data_quality | Rules, Java code, validation |
| Operations | p04_environment, p06_error_handling, p07_state, p14_scheduling, p15_security, p16_performance | Config, errors, state, security |
| Patterns | p13_naming, p18_reusability | Naming patterns, reusable logic |
| Synthesis | p10_synthesis | Executive summary, FAQ, glossary, onboarding |
| Preventive | p22_preventive_analysis | Forward-looking risk, missing safeguards, fragility, health score |
