# MCP Documentation Generation Workflow

The agent generates documentation from scan data exposed via MCP tools.
This is a 4-phase process using parallel sub-agents for throughput. The current
custom harness defaults to enterprise mode: publish to `docs/` is blocked unless
all enterprise gates pass. Use explicit draft mode only for exploratory runs.

## Workflow Overview

```
Phase 1: Setup         -- check scan status, load chunks + manifest + prompts
Phase 2: Group Analysis -- parallel sub-agents (one per group), return compact text
Phase 3: Write Docs    -- parallel sub-agents by theme, write final .md files
Phase 4: Publish       -- enterprise gates, audit bundle, index.json + run log
```

## Enterprise Publish Gates

Enterprise mode is the default for publishable runs. The server must not swap
`docs_staging/` into `docs/` or call final RAG sync unless the harness reports
all required gates as passing.

Required gate families:
- Valid fresh scan (`scan_result.json` exists and is valid)
- Complete evidence coverage
- Phase 2 analysis depth
- Phase 3 document coverage
- Phase 3 document depth
- Final redaction scan
- Source provenance
- Internal reviewer approval
- Quality threshold
- Usefulness rubric
- RAG sync status

Gate output is written to:

```
output/{project_id}/docs_staging/_assessment/enterprise_gates.json
output/{project_id}/docs_staging/_audit/
```

Failed enterprise runs preserve `docs_staging/` for repair/resume. Never treat
that directory as client-ready. Client-ready output requires a completed run with
`generate_runs.run_verdict = "client_ready"`.

## Phase 1: Setup

1. Call `recall_process_memory("doc generation best practices and common errors")` to load cross-project lessons (if Hindsight enabled).
2. Call `recall_project_memory(project_id, "previous analysis findings and structure")` to load project-specific context (if Hindsight enabled).
3. Call `get_scan_status(project_id)` to check cache freshness.
4. If stale or missing, call `trigger_scan(project_id)`.
5. Call `get_chunks(project_id)`.
   - Returns chunks with fields: `chunk_id`, `group`, `job_count`, `job_names`, `token_estimate`.
   - Each chunk targets ~30K tokens (~120KB). Overhead (connections, contexts, routines, manifest)
     is added on top but kept compact via tight filtering and no JSON indent.
   - Group the chunk list by the `group` field. The number and names of groups are
     determined entirely by the scan data -- never assume specific names.
6. Call `fetch_json(project_id, "manifest")` to load the dependency graph.
7. Call `fetch_json(project_id, "connections")` to load metadata connections.
8. Directories `output/{project_id}/docs_staging/` and `output/{project_id}/logs/` are pre-created by the orchestrator. Claude Code writes to `docs_staging/`; the server swaps it to `docs/` on success.

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
- The manifest and connections (from Phase 1 -- passed inline)

Each sub-agent must:
1. Call `get_analysis_prompts()` to load analysis guidance.
2. Call `fetch_chunk(project_id, chunk_id)` for every chunk in its group.
3. Analyze ALL chunks across ALL dimensions using the prompt guidance.
4. **Return a compact text summary (~3-5KB) directly back to the calling agent.**

**The sub-agent writes NO files.** Analysis is returned as text.

**Sub-agents must NOT:**
- Call `fetch_json` for manifest/connections (passed inline)
- Use TodoWrite, mkdir, ls, wc, or ASCII validation scripts
- Read .agent/ docs, CLAUDE.md, or mcp-workflow.md

After receiving ALL group summaries, call `retain_findings(project_id, summary_text, "group-summary")` for each group to store analysis results in Hindsight memory (if enabled).

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

## Phase 3: Write Documents (3 Parallel Sub-Agents)

After ALL Phase 2 sub-agents complete, spawn 3 doc-assembly sub-agents in parallel.
Each Phase 3 agent reads the FULL `_analysis/{group}.md` files directly (no condensation step).
Each doc agent also reads its REFERENCE DOC BUNDLE from `_preprocess/` for format templates.
Each doc agent receives the manifest inline and writes final `.md` files to `output/{project_id}/docs_staging/`.

### Doc Agent Groups (4/3/5 split)

| Agent | Docs (12 total) | Reference Bundle | Analysis Input |
|-------|------------------|------------------|----------------|
| Core | 00_EXECUTIVE_SUMMARY, 01_PROJECT_OVERVIEW, 02_ARCHITECTURE_INVENTORY, 03_DATA_FLOWS_LINEAGE | _ref_core.md | ALL _analysis/{group}.md files |
| Business+Ops | 04_BUSINESS_LOGIC, 05_OPERATIONS_RISK, 06_ENVIRONMENT_EXTERNAL | _ref_business_ops.md | ALL _analysis/{group}.md files |
| Patterns+User | 07_SECURITY_PERFORMANCE, 08_PATTERNS_STANDARDS*, 09_RUNBOOK, 10_ONBOARDING_FAQ, 11_MIGRATION_ASSESSMENT | _ref_patterns_user.md | ALL _analysis/{group}.md files |

*08_PATTERNS_STANDARDS cross-region section only if 2+ groups exist.

AUTO-FALLBACK: If total analysis size exceeds 400KB, each Phase 3 agent should
read only the analysis sections relevant to its docs (use grep/head/tail).

### Depth Targets (Minimum Line Counts)

| Doc | Min Lines | Notes |
|-----|-----------|-------|
| 00_EXECUTIVE_SUMMARY | 200+ | Customer-facing, NO code/expressions, polished prose |
| 01_PROJECT_OVERVIEW | 300+ | Technical overview with full stats and regional breakdown |
| 02_ARCHITECTURE_INVENTORY | 600+ | Components + topology + execution logic combined |
| 03_DATA_FLOWS_LINEAGE | 600+ | Column-level lineage for all major flows + schema dictionary |
| 04_BUSINESS_LOGIC | 800+ | Every BR-tagged rule with full expression + custom code |
| 05_OPERATIONS_RISK | 800+ | Per-job error handling + all risk items + preventive analysis |
| 06_ENVIRONMENT_EXTERNAL | 400+ | Config + connections + scheduling combined |
| 07_SECURITY_PERFORMANCE | 300+ | Security assessment + performance analysis |
| 08_PATTERNS_STANDARDS | 400+ | Naming + reusability + cross-region patterns |
| 09_RUNBOOK | 400+ | Operational procedures |
| 10_ONBOARDING_FAQ | 500+ | 20+ FAQ items + onboarding guide + glossary |
| 11_MIGRATION_ASSESSMENT | 400+ | Consolidated migration assessment |

### Narrative Rules

- Never summarize when you can enumerate
- Every claim needs a job name + component reference
- Show ACTUAL code/expressions/SQL, then explain
- "Error handling is weak" is NOT acceptable
- "7 of 12 DEU jobs have no tLogCatcher" IS acceptable
- Include concrete counts, percentages, and job names

### Adaptive Document Catalog

- **Always generate**: 00-06, 09-11
- **Generate if found**: 07 (security/performance issues), 08 cross-region section (2+ groups)
- Skip any doc with genuinely no content (rare with consolidated structure)

### Quality Standards

Match the quality and depth of reference docs in `intodoc/reference-docs/`.

- Every document MUST have a table of contents
- Use plain-text ASCII tables (no Unicode box-drawing characters)
- Business rules get tagged IDs: BR-{GROUP}-{NNN}
- Include job names and component references for traceability
- Risk items get severity ratings: CRITICAL / HIGH / MEDIUM / LOW
- Include "Impact" statements where relevant
- Schema docs must include column-level detail (names, types, nullable)
- Data flow docs must include tMap expressions with explanations

## Phase 4: Publish

1. Run enterprise gates (or advisory gates for explicit draft mode).
2. Preserve audit artifacts under `_audit/`, including facts, validation reports,
   reviewer reports, gate report, provider call metadata, and final doc hashes.
3. Write `output/{project_id}/docs_staging/index.json` mapping each document to its source groups:
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
4. Call `retain_findings(project_id, run_summary, "run-summary")` to store the run results in Hindsight (if enabled).
5. If any errors or warnings occurred, call `retain_process_lesson(lesson, "error-recovery")` to remember what went wrong and how it was handled (if Hindsight enabled).
6. Write `output/{project_id}/logs/run_{project_id}_{timestamp}.log`:
   ```
   [INFO] Groups analyzed: {list}
   [INFO] Chunks processed: {n}
   [INFO] Documents generated: {list of filenames}
   [WARNING] {any warnings}
   [ERROR] {any errors}
   [INFO] Total elapsed: {time}
   ```
## Output Structure

```
output/{project_id}/
  docs/
    00_AGENT_BRIEF.md
    01_ARCHITECTURE_MAP.md
    02_COMPONENT_INVENTORY.md
    03_DATA_FLOWS_AND_LINEAGE.md
    04_BUSINESS_RULES_AND_TRANSFORMATIONS.md
    05_RUNTIME_CONFIG_AND_EXTERNALS.md
    06_OPERATIONS_TESTING_AND_DEBUGGING.md
    07_QUALITY_SECURITY_RISK_SCORECARD.md
    08_CHANGE_PLAYBOOK.md
    index.json
    _audit/
  logs/
    run_{project_id}_{timestamp}.log
```

No `_scratch/` directory. Internal validation/audit artifacts are allowed under
`_assessment/`, `_investigation/`, `_facts/`, and `_audit/`; RAG push indexes
only top-level public markdown docs.

## MCP Tools Available

| Tool | What it does |
|------|-------------|
| `add_repo(url, project_id, platform?)` | Register a project for scanning |
| `fetch_json(project_id, section?)` | Read cached scan JSON (segmented) |
| `trigger_scan(project_id, force?)` | Run intodoc scan subprocess |
| `get_scan_status(project_id)` | Check cache freshness |
| `get_chunks(project_id)` | Get chunk definitions (~30K tokens each) |
| `fetch_chunk(project_id, chunk_id)` | Get full job data for one chunk |
| `get_analysis_prompts(platform?)` | Get ALL analysis prompts bundled (replaces 16+ Reads) |
| `get_reference_docs(agent_group)` | Get reference docs for a doc agent group: Core, Business+Ops, Patterns+User |
| `insert_to_rag(project_id, docs?)` | Push docs to RAG pipeline (not used during generation -- server handles RAG push automatically) |
| `list_projects()` | List all registered projects |
| `recall_process_memory(query)` | Recall cross-project lessons before doc generation |
| `recall_project_memory(project_id, query)` | Recall prior findings before re-scan |
| `retain_findings(project_id, findings, context?)` | Store analysis results in memory |
| `retain_process_lesson(lesson, context?)` | Store operational lessons learned |

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
