IMPORTANT: Do not echo this request. Do not write "Okay", "Sure", "I will", or any
acknowledgement or meta-commentary. Begin your output immediately with the first markdown
heading. Any response that starts with a filler word or an acknowledgement is INVALID and
will be rejected.

${PREAMBLE}

Group: ${GROUP}
Current Phase 3 document set (${DOC_COUNT} files):
${DOC_LIST}

UNIT ENUMERATION OBLIGATION (enforced before writing any section):
The following ${TASK_UNIT_COUNT} ${CANONICAL_UNIT_LABEL} are assigned to THIS analysis task
for group ${GROUP}. Every unit listed here MUST appear at least once in the ${ENUMERATION_SECTION}
section with its type, purpose, and role. If a listed unit is absent from the scanner
evidence, write its name and state exactly what evidence is missing -- do NOT silently omit
it, and do NOT reconstruct it from indirect mentions. Analyze ONLY the units listed here.

${UNIT_LIST}
${MISSING_EVIDENCE_BLOCK}${TASK_SCOPE_NOTE}

Run facts:
${FACTS_SUMMARY}

Platform evidence contract:
${PLATFORM_ANALYSIS_REQUIREMENTS}

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

MANDATORY SECTION STRUCTURE (strictly enforced):
Write EVERY section below in this exact order. Do NOT omit, rename, merge, or reorder any
section. If source detail is absent for a section, write the section heading and state
exactly what evidence is missing -- a missing or empty heading is a hard rejection.

Required sections in order:
${SECTION_HEADINGS}
- `### GROUP_SUMMARY`

OUTPUT SKELETON -- fill in every stub below with substantive content:

${PLATFORM_SECTION_DEPTH}

### GROUP_SUMMARY
Finish with a concise index for Phase 3. Include:
- Units covered and units limited at a named evidence stage
- Top flows and entrypoints
- External systems and runtime/config facts
- Business rules and transformations with evidence pointers
- Top risks, unknowns, and distinctions between redaction, absent source, unsupported parsing, packet omission, and unfinished analysis
- Deep evidence pointers to the sections above

${ANALYSIS_GUIDE_NOTE}
Your output is the analysis body only -- the Python harness writes the coverage block and output file.

BOUNDED ANALYSIS RULES (strictly enforced):
- Complete every required heading before adding depth; keep detail proportional to the assigned scope
- Every concrete claim requires an exact unit name and, when available, a component or source-path reference
- Quote only the decisive expression, SQL fragment, or code excerpt needed to support a claim; point to the raw evidence for the complete value
- "Error handling is weak" is UNACCEPTABLE
- "7 of 9 ${GROUP} jobs have no tLogCatcher" IS acceptable
- Include concrete counts, percentages, exact names throughout
- Do not repeat complete component inventories, expressions, SQL queries, custom-code blocks, or configuration tables
- For no scoped evidence, emit one explicit UNKNOWN with the evidenced failure stage rather than padding the section; an empty packet does not prove a scanner failure
- Prefer compact tables or at most three evidence bullets per assigned unit in each section

FACTUAL CONSTRAINTS:
- Use only the supplied scanner facts and preprocessed scan data
- Do not invent jobs, counts, systems, schedules, or risks not present in the data
- The exact canonical inventory for this project is ${CANONICAL_UNIT_COUNT} ${CANONICAL_UNIT_LABEL}
- Never use a conflicting unit count anywhere in your output
- If detail is absent for a specific unit, name the unit and state exactly what is missing
