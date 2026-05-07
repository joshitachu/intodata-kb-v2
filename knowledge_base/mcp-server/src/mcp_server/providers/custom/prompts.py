"""Prompt builders for bounded custom-harness calls."""

from __future__ import annotations

from mcp_server.providers.custom.models import FactsManifest
from mcp_server.platforms import get_platform_strategy
from mcp_server.providers.custom.prompt_packs import ANALYSIS_SECTION_NAMES
from mcp_server.services.pack_loader import InstructionPack


REQUIRED_DOCS = [
    "00_EXECUTIVE_SUMMARY",
    "01_PROJECT_OVERVIEW",
    "02_ARCHITECTURE_INVENTORY",
    "03_DATA_FLOWS_LINEAGE",
    "04_BUSINESS_LOGIC",
    "05a_OPERATIONS",
    "05b_SECURITY",
    "05c_RISK",
    "05d_CHANGE_MANAGEMENT",
    "06_ENVIRONMENT_EXTERNAL",
    "07_SECURITY_PERFORMANCE",
    "08_PATTERNS_STANDARDS",
    "09_RUNBOOK",
    "10_ONBOARDING_FAQ",
    "11_MIGRATION_ASSESSMENT",
]


DOC_ASSIGNMENTS = {
    "core": [
        "00_EXECUTIVE_SUMMARY",
        "01_PROJECT_OVERVIEW",
        "02_ARCHITECTURE_INVENTORY",
        "03_DATA_FLOWS_LINEAGE",
    ],
    "business_ops": [
        "04_BUSINESS_LOGIC",
        "05a_OPERATIONS",
        "05b_SECURITY",
        "05c_RISK",
        "05d_CHANGE_MANAGEMENT",
        "06_ENVIRONMENT_EXTERNAL",
    ],
    "patterns_user": [
        "07_SECURITY_PERFORMANCE",
        "08_PATTERNS_STANDARDS",
        "09_RUNBOOK",
        "10_ONBOARDING_FAQ",
        "11_MIGRATION_ASSESSMENT",
    ],
}


DOC_BUNDLE_MAP = {
    doc: bundle
    for bundle, docs in DOC_ASSIGNMENTS.items()
    for doc in docs
}


DOC_SECTION_REQUIREMENTS = {
    "00_EXECUTIVE_SUMMARY": ["Executive Overview", "Key Findings", "Recommendations", "FAQ", "Glossary"],
    "01_PROJECT_OVERVIEW": ["Scope", "Groups", "Inventory"],
    "02_ARCHITECTURE_INVENTORY": ["Architecture", "Component", "Connectivity", "Trigger", "Route"],
    "03_DATA_FLOWS_LINEAGE": ["Data Flow", "Lineage", "Transformation"],
    "04_BUSINESS_LOGIC": ["Business", "Transformation", "Rule"],
    "05a_OPERATIONS": ["Operations", "Execution", "Monitoring"],
    "05b_SECURITY": ["Security", "Credential", "Access"],
    "05c_RISK": ["Risk", "Severity", "Mitigation"],
    "05d_CHANGE_MANAGEMENT": ["Change", "Impact", "Testing"],
    "06_ENVIRONMENT_EXTERNAL": ["Environment", "External", "Configuration", "Deployment"],
    "07_SECURITY_PERFORMANCE": ["Security", "Performance", "Risk"],
    "08_PATTERNS_STANDARDS": ["Pattern", "Standard", "Anti-Pattern"],
    "09_RUNBOOK": ["Runbook", "Troubleshooting", "Recovery"],
    "10_ONBOARDING_FAQ": ["Onboarding", "FAQ", "Glossary"],
    "11_MIGRATION_ASSESSMENT": ["Migration", "Readiness", "Plan", "Scenario", "Effort"],
}


def facts_summary(facts: FactsManifest) -> str:
    groups = ", ".join(f"{name}={count}" for name, count in sorted(facts.groups.items()))
    units = "\n".join(
        f"- {u.name} | group={u.group} | type={u.unit_type} | components={u.component_count}"
        for u in facts.units
    )
    return (
        f"Platform: {facts.platform}\n"
        f"Canonical unit label: {facts.canonical_unit_label}\n"
        f"Canonical unit count: {facts.unit_count}\n"
        f"Groups: {groups}\n\n"
        f"Canonical units:\n{units}\n"
    )


def platform_analysis_requirements(facts: FactsManifest) -> str:
    try:
        return get_platform_strategy(facts.platform).analysis_requirements()
    except ValueError:
        return get_platform_strategy("generic").analysis_requirements()


def _platform_section_depth(facts: FactsManifest) -> str:
    """Per-section extraction requirements embedded directly in the prompt."""
    platform = facts.platform.lower()

    if platform == "talend":
        return """\
SECTION-BY-SECTION EXTRACTION REQUIREMENTS
Write every section below. Be exhaustive within each -- no placeholders, no "see above", no "N/A" without justification.

### COMPONENTS
For EVERY job:
- Job name, purpose, group membership
- All components: type (tMSSqlInput, tMap, tRunJob, etc.), unique_name, role in the job
- All connections: source -> target, type (FLOW / SUBJOB_OK / SUBJOB_ERROR / COMPONENT_OK / COMPONENT_ERROR / ITERATE / LOOKUP / ROUTE_WHEN), label
- tRunJob references: child job called, context variables passed, DIE_ON_CHILD_ERROR setting
- REPOSITORY connection references: look for PROPERTY_TYPE=REPOSITORY + REPOSITORY_PROPERTY_TYPE value
- BUILT_IN connections: note which components have hardcoded connection details
- Referenced-by: which parent jobs call this job via tRunJob
- Orphaned components: no incoming and no outgoing connections
- Missing references: REPOSITORY refs to non-existent metadata, tRunJob refs to missing jobs

### EXECUTION_FLOW
For EVERY job:
- Subjob chain in numbered execution order (tPreJob first, main body, tPostJob last)
- Every trigger connection: source subjob, target subjob, trigger type, RunIf expression (full expression if present)
- tRunJob orchestration details: child called, vars passed, DIE_ON_CHILD_ERROR
- Parallelism: subjobs with no trigger dependencies that could run concurrently
- Iteration patterns: tFlowToIterate / tLoop / tForEach -- what they iterate over and with what bounds
- Dead paths: components or subjobs with no reachable trigger chain from any entry point

### DATA_FLOWS
For EVERY tMap component:
- Source table.column -> target table.column mapping (full column-level lineage)
- The FULL expression for every mapped column -- never truncate, even if 500+ chars
- Human-readable explanation of what each expression computes
- Var table entries with their full expressions
- Filter/reject conditions: full expression text, which output table, reject=true links
- Join type (INNER / LEFT OUTER / UNIQUE MATCH) and lookup model between input tables

For all other data movement:
- Non-tMap FLOW connections between I/O components (direct pass-through)
- Data type mismatches between source schema and target schema
- Null handling: Relational.ISNULL(), default substitutions, NVL/COALESCE patterns
- Row rejection paths: reject links, where failed rows route to, whether they are logged

Data quality checklist per job:
- Validation present: null checks, range checks, regex validation, reference lookups
- Deduplication: how duplicates are detected and handled
- Missing validations: data flows with NO validation at all (flag each one explicitly)
- Default/fallback values: what happens when expected data is absent

### TRANSFORMATION_DETAIL
For every job with tMap or custom code transformations:
- Field-by-field mapping table: source field | transformation expression | target field | notes
- Conditional transformation patterns: ternary operators, ISNULL branches, environment switches
- Aggregation and grouping logic
- String manipulations: StringHandling.TRIM/CHANGE/UPCASE/etc. with purpose
- Date arithmetic: date parsing, formatting, offset calculations -- full expressions shown
- Lookup joins: reference table, match key, fallback when no match

### BUSINESS_RULES
Extract EVERY business rule with this exact format:
- BR-{GROUP}-NNN (sequential, e.g. BR-DEU-001)
- Expression: the full code implementing the rule -- NEVER truncate
- Job + component: exact names
- Domain: business area (pricing / eligibility / routing / scheduling / inventory / compliance / reporting)
- Impact: ISOLATED / MODERATE / WIDE -- what breaks if this rule is removed

Mine every source:
- tMap conditional expressions: ternary operators, Relational.ISNULL, arithmetic, string ops, date math
- tJavaRow / tJava / tJavaFlex code: every if/else branch, switch, calculation
- SQL WHERE clauses, CASE statements, JOIN conditions, HAVING clauses
- RunIf expressions on trigger connections
- tRunJob conditional execution order (structural business rules)
- tWarn / tDie conditions

Parameter catalog (separate markdown table, mandatory):
| Value | Location (job + component) | Apparent purpose | Staleness risk |
List EVERY hardcoded date, status code, threshold, magic number, string constant.

Conflict detection: flag explicitly where the same business concept is handled differently across jobs.

### SCHEMA_DICTIONARY
For EVERY table and view referenced:
- Fully-qualified name: database.schema.table where available; note what qualification is missing
- Every column: name | data type | nullable | key constraints (if visible in scan)
- Jobs that READ it (consumers) vs jobs that WRITE it (authoritative writer vs secondary writers)
- Connection used to access it (name + type: REPOSITORY or BUILT_IN)
- Schema drift risks: multiple jobs reading same table with different column expectations -- flag each
- Tables with multiple writers: flag as potential conflict and data integrity risk

### SOURCE_SQL
Every SQL query in FULL -- never truncate a single character:
- Component and job containing it
- Type: SELECT (source query) / lookup query / DML (INSERT/UPDATE/DELETE) / DDL
- Tables and columns referenced
- Dynamic parts: every context variable substitution (context.varName), string concatenation

### EXTERNAL_SYSTEMS
For every external connection:
- System type: relational DB / file system / FTP / REST API / SOAP / cloud storage / message queue
- Connection name and REPOSITORY vs BUILT_IN mode
- Host pattern, port, database name (mask passwords with ***)
- Jobs using this connection and their purpose (read / write / lookup / audit)
- Protocol details: JDBC driver class, REST endpoint pattern, file format

### ERROR_HANDLING
Rate EACH job: GOOD / ADEQUATE / POOR / NONE
- GOOD: tLogCatcher + meaningful error routing + die-on-error for critical paths
- ADEQUATE: some error handling but documented gaps
- POOR: minimal (one component at best)
- NONE: no error handling components at all

For each job document:
- tLogCatcher / tStatCatcher: present? what do they capture? where does output go?
- SUBJOB_ERROR connections: which subjobs have them, where errors route to
- DIE_ON_CHILD_ERROR on every tRunJob: true or false
- Try/catch in tJava / tJavaRow: present or absent
- Silent failures: subjobs with no SUBJOB_ERROR connection -- list every one explicitly
- tWarn / tDie usage: what conditions trigger them

### CUSTOM_CODE
For ALL tJava / tJavaRow / tJavaFlex components:
- The FULL code block (begin / main / end for tJavaFlex) -- never truncate
- Purpose: what business logic or transformation it implements
- Risk checklist, evaluated for EACH block:
  - SQL injection: string concatenation building SQL? Rate HIGH/MEDIUM/LOW
  - Unvalidated input: external data used without sanitization or null checks?
  - Hardcoded values: paths, credentials, magic strings, environment-specific constants?
  - Thread safety: shared mutable state accessed from parallel subjobs or iterations?
  - Resource leaks: connections/streams/files opened without guaranteed finally-block close?
  - Encoding issues: string operations without explicit charset on a Windows deployment?

For routines referenced by jobs:
- Routine name, purpose, which jobs reference it
- Full code listing if present in scan data

### ENVIRONMENT_CONFIG
For EVERY context variable:
- Name | type | default value
- PROD vs TEST/DEV value differences (if present in scan)
- Whether prompt-overridable (prompt=true)
- Which jobs use it

Also extract:
- Connection strings per environment (sanitized: mask passwords)
- File path and directory patterns (are they context-variable-driven or hardcoded?)
- Feature flags or environment-conditional behavior
- tContextLoad usage: jobs that load context variables dynamically from a data source

### SCHEDULING
- Trigger type per job: cron / file arrival / event-driven / tRunJob child / manual
- Cron expressions with human-readable interpretation (e.g. "0 6 * * 1-5" = weekdays at 06:00)
- Execution windows, time zones, SLA deadlines
- Job execution dependencies: which jobs must complete before others start
- Retry configuration: auto-retry counts, backoff, failure alerts
- Recovery pattern per job: incremental re-processing (watermark/checkpoint) vs full re-run
- Gap detection: mechanism to detect missed runs, or note absence
- Concurrency controls: what prevents duplicate concurrent execution, or note absence and flag risk
- SLA windows: jobs with business deadlines and consequence of missing them

### NAMING_PATTERNS
- Job naming: prefix conventions (region codes, data domains), suffix patterns (_RAW / _STG / _FINAL)
- Table / schema naming: source vs staging vs target conventions
- Context variable naming: patterns observed
- Connection naming: naming convention for metadata connections
- Consistency rating: GOOD (clear, consistent, self-documenting) / ADEQUATE (mostly consistent with outliers) / POOR (inconsistent or unclear)
- List specific outlier examples for any rating below GOOD

### CROSS_GROUP_REFS
- Shared database tables: read by one group, written by another -- list each with both groups
- Shared context variables or context groups across groups
- Cross-group tRunJob calls: which jobs in this group call jobs in other groups
- Shared metadata connections used by multiple groups
- Execution ordering dependencies: this group must complete before group X starts

### RISK_FLAGS
Rate each risk: CRITICAL / HIGH / MEDIUM / LOW

Security risks:
- Hardcoded credentials (passwords, API keys in component params or custom code)
- SQL injection vectors (string concatenation building queries -- list every instance)
- Unencrypted sensitive data in transit or at rest
- Overly broad database permissions

Performance risks:
- Full table scans (SELECT * without WHERE filtering on large tables)
- Unbounded result sets (no TOP / LIMIT)
- Memory-intensive tMap lookups loading full tables without pre-filtering
- Sequential processing where parallel execution is possible

Reliability risks:
- No error handling on critical paths (flag each job explicitly)
- Silent failures (errors swallowed without logging -- list every SUBJOB_ERROR gap)
- Missing retry logic on network I/O operations
- No dead-letter/reject handling for failed records
- Hardcoded file paths (not using context variables)
- Missing data validation on inputs

Deep preventive analysis (mandatory for every group):
- Single points of failure: classify each as RECOVERABLE (retry/restart fixes it) vs CATASTROPHIC (requires manual data repair); note whether any fallback path exists
- Cascading failure chains: trace full blast radius from each critical job failure -- e.g. "Job_A fails -> table X has partial data -> Job_B reads stale X -> Report Y shows incorrect totals"
- Recovery gaps: non-idempotent jobs where re-run would cause duplicates, corrupt running totals, or re-fire external side effects -- flag each explicitly
- Monitoring gaps: per job, what is the detection latency if it silently produces wrong output? (minutes / hours / days / never)
- Capacity risks: jobs that will fail or miss SLA as data volumes grow -- unbounded SELECTs, full in-memory sorts, tFlowToIterate over unbounded sets

For each risk: severity | affected job(s) + component(s) | specific description | recommended remediation
"""

    if platform == "workato":
        return """\
SECTION-BY-SECTION EXTRACTION REQUIREMENTS

### COMPONENTS
For EVERY recipe and callable function:
- Name, purpose, folder/group membership
- All steps: action type, connector, operation name
- Trigger type and configuration: event / schedule / webhook / callable
- Callable dependencies: which recipes call this recipe (referenced-by)
- Connector accounts used per step

### EXECUTION_FLOW
- Step sequence in full execution order
- Every conditional branch (if/unless/case): condition expression and both paths
- Repeat/loop blocks: what they iterate over and bounds
- Error monitor blocks: scope, actions on error, stop vs resume behavior
- Dead branches: conditions that evaluate to the same value always (unreachable paths)

### DATA_FLOWS
- Every data mapping: source datapill -> target field with full formula (never truncate)
- Lookup table references: table name, match key, return field, fallback when no match
- Type conversions and format transformations: full formula shown
- Null / absent-field handling: default values, conditional checks

### TRANSFORMATION_DETAIL
- Field-by-field mapping tables per recipe step
- Formula catalog: every non-trivial formula with its purpose explained
- String, date, and numeric transformation patterns

### BUSINESS_RULES
- BR-{GROUP}-NNN for every conditional, formula, or routing rule that encodes business logic
- Full formula or condition expression -- never truncate
- Lookup table entries acting as business-rule tables: document all rows if available
- Parameter catalog: hardcoded values, static lists, date constants (table with location and risk)

### EXTERNAL_SYSTEMS
- Every connector and account: type, purpose, which recipes use it
- REST endpoint patterns (sanitize credentials with ***)
- Webhook sources and their triggering conditions

### ERROR_HANDLING
- Error handlers per recipe: scope, on-error action, stop / resume / custom
- Retry configuration per action
- Notification / alerting hooks
- Recipes with no error handling on critical paths -- flag each

### ENVIRONMENT_CONFIG
- Properties and their values (note environment-specific differences)
- Lookup tables acting as configuration stores
- Connector accounts that differ per environment

### RISK_FLAGS
CRITICAL / HIGH / MEDIUM / LOW with affected recipes and specific remediation:
- Hardcoded credentials in connector configs
- Unvalidated external input used in routing or output
- Recipes with no error handling
- Concurrency risks (recipe callable from multiple triggers simultaneously)
- Rate limit exposure on high-frequency triggers
"""

    if platform == "lobster":
        return """\
SECTION-BY-SECTION EXTRACTION REQUIREMENTS

### COMPONENTS
For EVERY profile and agent:
- Name, purpose, type (input/output/mapping/route)
- Parser and format definitions
- Forward/exception profile references

### EXECUTION_FLOW
- Profile execution chain in order
- Route conditions and targets
- Exception paths

### DATA_FLOWS
- Mapping definitions: source field -> transformation -> target field (full expressions)
- Format conversions: input format -> output format
- Conditional routing: which conditions route where

### TRANSFORMATION_DETAIL
- Field mapping tables per profile
- Transformation expressions in full

### BUSINESS_RULES
- BR-{GROUP}-NNN for every routing rule, filter, or transformation condition
- Full expression or condition -- never truncate
- Parameter catalog: hardcoded values table

### EXTERNAL_SYSTEMS
- All partner connections: type, protocol, direction (inbound/outbound)
- Sanitized host/port details

### ERROR_HANDLING
- Exception profiles per route
- Forward profiles on error
- Profiles with no exception handling -- flag each

### ENVIRONMENT_CONFIG
- Variables and environment-specific values
- Schedule and trigger configuration

### RISK_FLAGS
CRITICAL / HIGH / MEDIUM / LOW with specific profiles and remediation.
"""

    return """\
SECTION-BY-SECTION EXTRACTION REQUIREMENTS

### COMPONENTS
- Unit name, purpose, type (module / service / script / config)
- All dependencies: imports, called modules, external APIs
- Entry points and exposed interfaces

### EXECUTION_FLOW
- Call chains and execution order
- Every conditional branch with condition expression
- Loops and iteration patterns
- Error propagation paths

### DATA_FLOWS
- Input -> transformation -> output for every data-handling unit
- Full transformation expressions / code -- never truncate
- Type conversions, null handling, validation present or absent

### TRANSFORMATION_DETAIL
- Field-by-field mapping per unit with full expressions

### BUSINESS_RULES
- BR-{GROUP}-NNN for every conditional, calculation, or filter (full expression)
- Parameter catalog: hardcoded value | location | purpose | staleness risk

### EXTERNAL_SYSTEMS
- All APIs, databases, file systems, queues accessed
- Auth method and connection mode per system
- Which units use each system and for what purpose

### ERROR_HANDLING
- Rate each unit: GOOD / ADEQUATE / POOR / NONE
- Error propagation paths, retry logic
- Silent failures: errors swallowed without logging -- list each

### ENVIRONMENT_CONFIG
- Config files, env vars, feature flags and their values
- Environment-specific differences

### RISK_FLAGS
CRITICAL / HIGH / MEDIUM / LOW with specific unit references and remediation.
"""


_PHASE2_PREAMBLE_DEFAULT = """\
You are generating Phase 2 group analysis. Your output is the SOLE factual foundation for
all 15 documentation files produced in Phase 3. If you omit a finding here, it will be absent
from every document. Be EXHAUSTIVE. Include every expression, every rule, every column, every risk.
Depth over brevity. Never summarize when you can enumerate."""


def group_analysis_prompt(facts: FactsManifest, group: str, pack: InstructionPack | None = None) -> str:
    unit_names = [u.name for u in facts.units if u.group == group or group.startswith(f"{u.group}_part")]
    if not unit_names:
        unit_names = [u.name for u in facts.units if u.group == group]
    section_headings = "\n".join(f"- `### {name}`" for name in ANALYSIS_SECTION_NAMES)
    preamble = pack.preambles.get("phase2_preamble", _PHASE2_PREAMBLE_DEFAULT).rstrip() if pack else _PHASE2_PREAMBLE_DEFAULT
    return f"""\
{preamble}

Group: {group}
Canonical units in this group -- mention EVERY one by exact name:
{chr(10).join(f"- {name}" for name in unit_names)}

Run facts:
{facts_summary(facts)}

Platform evidence contract:
{platform_analysis_requirements(facts)}

EngineeringFacts context:
- The harness may supply `engineering_facts.*` plus artifact-specific files such as `entity_index`,
  `usage_index`, `dependency_map`, `data_flow_map`, `api_surface`, `config_runtime_map`,
  `test_coverage_map`, and `known_risks`.
- Treat these artifacts as normalized scanner evidence. Cite them where relevant and do not invent
  relationships that are absent from both EngineeringFacts and the supplied scan/preprocess data.

Direct source context:
- If `_source_coverage.json` or redacted source bundles are supplied, treat those file contents as
  the evidence of record.
- Do not say a covered file is missing, unavailable, not scanned, or has no content.
- Values marked `[REDACTED]` are intentionally hidden secrets, not missing evidence.

MANDATORY SECTION STRUCTURE
Write every section below in this exact order. Each section must be substantive.
{section_headings}


{_platform_section_depth(facts)}
The supplied prompt pack contains the full platform analysis guide.
It is the authoritative reference for platform-specific patterns, component behavior, and additional depth requirements.
Follow all extraction rules, examples, and depth requirements defined there in addition to the instructions above.
Your output is the analysis body only -- the Python harness writes the coverage block and output file.

NARRATIVE RULES (strictly enforced):
- Never summarize when you can enumerate
- Every claim requires an exact unit name + component reference
- Show ACTUAL code / expressions / SQL verbatim, then explain -- never paraphrase without showing the raw value
- "Error handling is weak" is UNACCEPTABLE
- "7 of 9 {group} jobs have no tLogCatcher" IS acceptable
- Include concrete counts, percentages, exact names throughout
- Expressions must be shown in full -- never truncate even if 500+ chars
- SQL queries must be shown in full -- never truncate
- Custom code blocks must be shown in full
- Hardcoded values must be cataloged in a markdown table with location and risk

FACTUAL CONSTRAINTS:
- Use only the supplied scanner facts and preprocessed scan data
- Do not invent jobs, counts, systems, schedules, or risks not present in the data
- The exact canonical inventory for this project is {facts.unit_count} {facts.canonical_unit_label}
- Never use a conflicting unit count anywhere in your output
- If detail is absent for a specific unit, name the unit and state exactly what is missing

"""


def phase2_section_repair_prompt(facts: FactsManifest, group: str, missing_sections: list[str]) -> str:
    unit_names = [u.name for u in facts.units if u.group == group or group.startswith(f"{u.group}_part")]
    if not unit_names:
        unit_names = [u.name for u in facts.units if u.group == group]
    sections = "\n".join(f"- `### {name}`" for name in missing_sections)
    return f"""\
The previous Phase 2 analysis for group `{group}` was incomplete.

Generate ONLY the missing sections listed below, in this exact order and with these exact headings.
Do not repeat sections that are not listed. Do not add a new title or coverage block.

Missing required sections:
{sections}

Canonical units in this group -- mention EVERY one by exact name where relevant:
{chr(10).join(f"- {name}" for name in unit_names)}

Run facts:
{facts_summary(facts)}

Platform evidence contract:
{platform_analysis_requirements(facts)}

Use only the supplied scanner facts, investigation evidence, and EngineeringFacts context.
Each returned section must be substantive. If source detail is absent, name the affected unit and state exactly what is missing.
"""


_SCORECARD_PREAMBLE_DEFAULT = """\
Generate a structured assessment scorecard from the supplied Phase 2 analysis files and investigation evidence.
Use ONLY the supplied analysis files, investigation evidence, and this facts manifest.
Follow the scoring rubric, anti-pattern catalog, red-flag catalog, and security checklist
in the supplied scorecard prompt pack exactly -- do not invent scoring criteria."""


def scorecard_prompt(facts: FactsManifest, pack: InstructionPack | None = None) -> str:
    preamble = pack.preambles.get("scorecard_preamble", _SCORECARD_PREAMBLE_DEFAULT).rstrip() if pack else _SCORECARD_PREAMBLE_DEFAULT
    return f"""\
{preamble}

SCORING RULES (enforced):
- Score each of the 8 dimensions 1-5 following the rubric criteria exactly
- Every score MUST cite specific evidence: exact job names, component types, counts, expressions
- "Error handling is weak" is UNACCEPTABLE -- "7 of 12 {facts.groups and list(facts.groups.keys())[0] or 'group'} jobs have no tLogCatcher" IS acceptable
- Anti-patterns: reference by AP-xxx-nnn ID from the supplied catalog
- Red flags: reference by RF-xxx-nnn ID from the supplied catalog
- Complete the security checklist evaluation item by item with evidence

Required output structure:
1. Overall score (weighted average, 1 decimal) with one-sentence justification citing the weakest dimension
2. Dimension table (8 rows):
   | Dimension | Score | Evidence (specific job/component/count) |
   architecture | data lineage | business logic | operations | security | performance | standards | migration
3. Top risks (minimum 5, ranked by severity):
   | Severity | Affected units | Description | Recommended remediation |
4. Top strengths (minimum 3) with specific evidence for each
5. Anti-pattern findings: AP-xxx-nnn ID | description | affected jobs | remediation
6. Red flag findings: RF-xxx-nnn ID | description | affected jobs | remediation
7. Security checklist: each item as PASS / FAIL / NOT_APPLICABLE with one-line evidence citation

Facts:
{facts_summary(facts)}

Platform scoring guidance:
{platform_analysis_requirements(facts)}

Canonical inventory: exactly {facts.unit_count} {facts.canonical_unit_label} -- never use a conflicting count.
"""


_PHASE3_PREAMBLE_DEFAULT = """\
Generate `{doc_name}.md` from the supplied validated analysis bundle and scorecard.
Follow the Phase 3 synthesis guide, document templates, and reference docs in the supplied context.
Use supplied EngineeringFacts artifacts as normalized evidence where relevant:
- 02_ARCHITECTURE_INVENTORY: prioritize `architecture_map` and `dependency_map`
- 03_DATA_FLOWS_LINEAGE: prioritize `entity_index`, `usage_index`, and `data_flow_map`
- 05d_CHANGE_MANAGEMENT: prioritize `change_impact_matrix`
- 06_ENVIRONMENT_EXTERNAL: prioritize `api_surface` and `config_runtime_map`
- 09_RUNBOOK: prioritize `test_coverage_map` and `known_risks`
Cite EngineeringFacts artifact names when they support a claim. Do not invent relationships,
routes, fields, flows, runtime settings, or risks not present in the supplied evidence.
If `_source_coverage.json` or redacted source bundles are supplied, treat those source files as the
evidence of record. Do not say a covered file is missing/unavailable/not scanned; values marked
`[REDACTED]` are intentionally hidden secrets."""


def doc_prompt(facts: FactsManifest, doc_name: str, pack: InstructionPack | None = None) -> str:
    required_headings = DOC_SECTION_REQUIREMENTS.get(doc_name, [])
    headings_block = "\n".join(f"  ## {s}" for s in required_headings)
    doc_specific = _doc_specific_instructions(doc_name, facts)
    raw_preamble = pack.preambles.get("phase3_preamble", _PHASE3_PREAMBLE_DEFAULT) if pack else _PHASE3_PREAMBLE_DEFAULT
    preamble = raw_preamble.rstrip().format(doc_name=doc_name)
    return f"""\
{preamble}

FACTUAL CONSTRAINTS (enforced):
- Platform: {facts.platform}
- Canonical inventory: exactly {facts.unit_count} {facts.canonical_unit_label} -- never use a conflicting count
- Cover or mention all canonical units where relevant to this document
- If detail is absent for a unit, write "scanner detail is limited for this unit" -- never invent
- Synthesize from the supplied validated analysis; do not re-extract from raw scanner data

NARRATIVE RULES (enforced):
- Never summarize when you can enumerate
- Every claim needs an exact unit name + component reference
- Show actual code / expressions / SQL verbatim, then explain
- Include concrete counts, percentages, exact names
- Business rules: BR-{{GROUP}}-NNN tags with full expressions
- Risk items: CRITICAL / HIGH / MEDIUM / LOW with specific unit references
- Schema docs: column-level detail (name, type, nullable)
- Data flow docs: full tMap expressions with explanations
- "Error handling is weak" is UNACCEPTABLE -- cite specific job names and counts

FORMAT:
- Start with a table of contents
- Use plain ASCII tables (no Unicode box-drawing characters)
- Use markdown headings, bullet lists, and fenced code blocks for expressions/SQL/code
- Reference scorecard dimension scores where relevant (e.g. "Security: 2/5 -- see Assessment Scorecard")

MANDATORY HEADINGS for {doc_name} -- your response MUST contain each of these as a `##` markdown
heading, using exactly this wording (do not rename, merge, or omit any of them):
{headings_block}

{doc_specific}
Platform documentation guidance:
{platform_analysis_requirements(facts)}

Facts:
{facts_summary(facts)}
"""


def _doc_specific_instructions(doc_name: str, facts: FactsManifest) -> str:
    instructions: dict[str, str] = {
        "00_EXECUTIVE_SUMMARY": f"""\
SPECIFIC REQUIREMENTS:
- Open with the overall quality score and dimension summary table from the scorecard (mandatory -- first section)
- Canonical inventory count ({facts.unit_count} {facts.canonical_unit_label}) must appear in the opening paragraph
- Key findings: minimum 5 findings with specific job/component references, not generic observations
- Top risks table: all CRITICAL and HIGH items with severity, affected units, and one-line description
- Recommendations: prioritized, actionable, referencing specific jobs or components
- Group-by-group summary table: group name | job count | key characteristic | top risk
- Strengths section: minimum 3 genuine strengths observed in the project with exact evidence, not generic praise
- Risk count summary: state Critical / High / Medium / Low counts when available from the scorecard or risk artifacts; if exact counts are not available, state the evidence source and uncertainty instead of inventing numbers
- Recommendations roadmap split by timeline: Immediate (fix now) | Short-term (1-3 months) | Long-term (3-6 months)
- FAQ section: minimum 10 Q&A grounded in scan evidence, covering what the platform does, what breaks on re-run, credential handling, biggest risks, known broken jobs, integration loop prevention, monitoring, migration requirements, operational ownership, and first remediation steps
- Glossary: domain-specific terms, abbreviations, component types, and named patterns found in this project
""",
        "01_PROJECT_OVERVIEW": """\
SPECIFIC REQUIREMENTS:
- Full inventory table: every group with job count and purpose
- Platform version and deployment model if present in scan data
- Scope boundaries: what is and is not covered by the scan
- Group-by-group summary with every canonical unit name listed
""",
        "02_ARCHITECTURE_INVENTORY": """\
SPECIFIC REQUIREMENTS:
- Component type census table: component type | count | purpose -- cover all groups
- Connection type census: FLOW / SUBJOB_OK / SUBJOB_ERROR / etc. with counts
- Dependency graph narrative: which groups call which via tRunJob chains
- Shared infrastructure: connections and metadata objects used by multiple groups
- Orphaned component catalog (if any found in analysis)
- Missing reference catalog: REPOSITORY refs or tRunJob targets that do not resolve
- Connection inventory table: connection name | type | protocol | auth method | groups using it | job count | REPOSITORY vs BUILT_IN
- Trigger patterns section: trigger type | job count | named examples, covering ESB/message, file event, scheduled, manual, webhook/API, and database polling patterns when present
- Entity route map: per-entity message bus or route topology showing which jobs produce and consume each message type; ASCII diagrams are acceptable when they clarify the flow
- Deactivated routes and inactive components: explicit catalog of disabled, inactive, bypassed, or test-only routes/components and their production risk
""",
        "03_DATA_FLOWS_LINEAGE": """\
SPECIFIC REQUIREMENTS:
- Full column-level lineage for every tMap: source_table.column -> expression -> target_table.column
- Full tMap expressions shown verbatim -- never truncate
- End-to-end data flow per group: source system -> transformation stages -> target system
- Data quality checkpoint locations: where validation occurs and what is checked
- Row rejection and dead-letter paths: where failed rows route
- Schema dictionary: every table referenced with columns (name / type / nullable), readers vs writers
""",
        "04_BUSINESS_LOGIC": """\
SPECIFIC REQUIREMENTS:
- Complete BR-{{GROUP}}-NNN catalog: every rule with full expression, domain, impact rating (ISOLATED/MODERATE/WIDE)
- Parameter catalog table: hardcoded value | location (job + component) | apparent purpose | staleness risk
- Conflict analysis: same business concept handled differently across jobs -- flag each explicitly
- Domain breakdown table: group rules by business area with rule count per domain
- Rules from tMap conditionals, SQL WHERE/CASE, Java code, RunIf expressions -- all sources covered
""",
        "05a_OPERATIONS": """\
SPECIFIC REQUIREMENTS:
- Reference the Operations dimension score from the scorecard
- Execution model: sequential vs parallel, scheduling topology, group execution order
- Error handling rating table: every group (or key jobs) rated GOOD / ADEQUATE / POOR / NONE with evidence
- tLogCatcher / tStatCatcher coverage table: job | has logcatcher? | captures what? | output destination
- Silent failure catalog: every job with no SUBJOB_ERROR connection -- list explicitly
- Monitoring gaps: per job, detection latency if it silently produces wrong output (minutes/hours/days/never)
- Normal execution runbook: step-by-step what happens on a successful run
""",
        "05b_SECURITY": """\
SPECIFIC REQUIREMENTS:
- Reference the Security dimension score from the scorecard
- Credential exposure audit: every hardcoded credential or BUILT_IN mode connection -- list all
- SQL injection vector catalog: every string-concatenated query with job + component reference
- Data sensitivity map: what sensitive data flows through which jobs to which systems
- Encryption status: data in transit (JDBC over TLS? FTP vs SFTP?) and at rest
- Access control: which jobs require elevated DB/file permissions
""",
        "05c_RISK": """\
SPECIFIC REQUIREMENTS:
- Complete risk register: all CRITICAL / HIGH / MEDIUM / LOW risks with specific job+component references
- Cascading failure chains: trace each critical failure to downstream impact (Job A fails -> table X stale -> Job B wrong -> Report Y incorrect)
- Single points of failure: RECOVERABLE vs CATASTROPHIC classification with fallback assessment
- Recovery gap catalog: non-idempotent jobs -- re-running would cause duplicates, corrupt totals, or re-fire side effects
- Capacity risk table: jobs that will break or miss SLA as data volumes grow
""",
        "05d_CHANGE_MANAGEMENT": """\
SPECIFIC REQUIREMENTS:
- Change impact matrix: changing component/table/variable X breaks Y -- cross-group dependencies
- Fragile parameters: hardcoded values requiring code edits rather than config changes
- Test coverage gaps: which jobs have no observable validation or data quality check
- Deployment order: correct sequence for deploying changes safely
- Rollback risks: changes that cannot be easily reversed once deployed
""",
        "06_ENVIRONMENT_EXTERNAL": """\
SPECIFIC REQUIREMENTS:
- Full context variable catalog: every variable with name | type | default | PROD vs TEST/DEV difference | prompt-overridable
- External system inventory: type | connection name | mode (REPOSITORY/BUILT_IN) | host pattern | jobs using it
- Environment-conditional behavior: jobs that behave differently per environment context group
- tContextLoad patterns: jobs loading context variables dynamically
- Missing environment separation: BUILT_IN connections that should be promoted to REPOSITORY
- Deployment portability risks: every hardcoded absolute path, machine-specific value, developer workstation artifact, host, port, schema, bucket, or environment-specific constant with job + component reference and remediation
- Environment separation gaps: connections or values that should differ between PROD/TEST/DEV but appear shared, hardcoded, unused, or impossible to switch without editing the job/profile
- Credential risk table: connection/system | auth type | credential storage location | jobs using it | rotation risk | recommended remediation
""",
        "07_SECURITY_PERFORMANCE": """\
SPECIFIC REQUIREMENTS:
- Reference Security and Performance dimension scores from the scorecard
- Security findings from the scorecard anti-pattern and red-flag catalogs with evidence
- Performance hotspots: full table scans, unbounded queries, memory-intensive lookups -- list each with job reference
- Capacity projections: at what data volume will each hotspot fail or miss its SLA window?
- Prioritized security remediation roadmap with estimated effort tier (small/medium/large)
""",
        "08_PATTERNS_STANDARDS": """\
SPECIFIC REQUIREMENTS:
- Good pattern catalog: patterns observed with specific job examples (not generic best practices)
- Anti-pattern catalog: AP-xxx-nnn IDs from scorecard, affected jobs, specific remediation
- Naming convention assessment: GOOD / ADEQUATE / POOR rating with outlier examples
- Cross-group consistency: where the same pattern is applied consistently vs inconsistently
- Standards violations: explicit list with severity and affected jobs
""",
        "09_RUNBOOK": """\
SPECIFIC REQUIREMENTS:
- Step-by-step execution procedure for each group with prerequisites
- Failure scenarios: what to do when each critical job fails (named, not generic)
- Recovery procedures: restart sequences, data repair steps for non-idempotent jobs
- Monitoring checklist: what to verify to confirm a successful run
- Common issues and diagnosis steps (grounded in risk flags found in analysis)
- Escalation paths
""",
        "10_ONBOARDING_FAQ": """\
SPECIFIC REQUIREMENTS:
- Architecture orientation: key concepts a new developer needs -- specific to this project's patterns
- Where to find things: actual file/job locations, naming convention explained with real examples
- How to make common changes: adding a job, changing a tMap mapping, updating a connection -- concrete steps
- Glossary: ALL domain-specific terms, abbreviations, platform concepts found in the scan (not generic Talend glossary)
- FAQ: minimum 8 questions with specific answers grounded in this project's scan evidence
""",
        "11_MIGRATION_ASSESSMENT": """\
SPECIFIC REQUIREMENTS:
- Reference the Migration Readiness score from the scorecard
- Dependency inventory: every external system that must be available in the target environment
- Migration risk register: CRITICAL / HIGH / MEDIUM / LOW with specific job/component references
- Non-idempotent jobs: explicitly catalog every job that is unsafe to re-run (duplicates, side effects)
- Recommended cutover sequence: ordered list of groups with rationale
- Rollback plan: what to do if migration fails at each stage
- Data validation checkpoints: how to confirm data integrity after cutover for each group
- Three migration scenarios compared side-by-side:
  A) Lift-and-shift to the same platform or upgraded/cloud version
  B) Transform-and-migrate to modern iPaaS or integration platform
  C) Redesign from requirements / greenfield
  For each scenario include effort, risk, whether existing logic is preserved, and whether it is recommended for this project
- Effort estimates table: phase | description | estimated effort (S/M/L/XL) | confidence | evidence basis
- Technical debt classification: Resolve Before Migration | Resolve During Migration | Eliminated By Migration
- Pre-migration checklist: concrete go/no-go items before cutover begins
- Post-migration verification checklist: how to confirm data integrity per group after cutover
- Go/No-Go decision framework: what must be true for each phase to proceed
""",
    }
    return instructions.get(doc_name, "")
