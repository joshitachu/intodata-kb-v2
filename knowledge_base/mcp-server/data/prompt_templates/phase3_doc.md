IMPORTANT: Do not echo this request. Do not write "Okay", "Sure", "I will", or any
acknowledgement or meta-commentary. The first line of your output must be the document
title (a `#` heading) or a `## Table of Contents` heading. Any response that starts with
a filler word or an acknowledgement is INVALID and will be rejected.

${PREAMBLE}

FACTUAL CONSTRAINTS (enforced):
- Platform: ${PLATFORM}
- Canonical inventory: exactly ${CANONICAL_UNIT_COUNT} ${CANONICAL_UNIT_LABEL} -- never use a conflicting count
- Cover or mention all canonical units where relevant to this document
- If detail is absent for a unit, write "scanner detail is limited for this unit" -- never invent
- Synthesize from the supplied validated analysis; do not re-extract from raw scanner data

DOC-PACK TRACEABILITY RULE (enforced):
- The preloaded `_analysis/_doc_pack_${DOC_NAME}.md` evidence pack is the primary evidence
  source for this document. Every factual claim about a unit, flow, system, or rule must
  trace to a specific section in the doc-pack.
- If the doc-pack does not mention a unit that belongs to the canonical inventory, write the
  unit's exact name and state that doc-pack evidence is absent -- do NOT omit the unit from
  the Canonical Unit Inventory section or any inventory table.
- A unit that was newly added to the codebase may appear in the doc-pack but not in earlier
  documentation; always reflect the doc-pack state, not a prior document's state.

NARRATIVE RULES (enforced):
- Never summarize when you can enumerate
- Every claim needs an exact unit name + component reference
- Show actual code / expressions / SQL verbatim, then explain
- Include concrete counts, percentages, exact names
- Business rules: BR-{GROUP}-NNN tags with full expressions
- Risk items: CRITICAL / HIGH / MEDIUM / LOW with specific unit references
- Schema docs: column-level detail (name, type, nullable)
- Data flow docs: full tMap expressions with explanations
- "Error handling is weak" is UNACCEPTABLE -- cite specific job names and counts
- Each section must contain at least 3 enumerated items or a table with at least 2 data rows;
  a section with only a heading or a single vague sentence is too thin

FORMAT:
- Start with a table of contents
- Use plain ASCII tables (no Unicode box-drawing characters)
- Use markdown headings, bullet lists, and fenced code blocks for expressions/SQL/code
- Reference scorecard dimension scores where relevant (e.g. "Security: 2/5 -- see Assessment Scorecard")
- Mark evidence confidence with a leading HIGH / MEDIUM / LOW token where claims are inferred or scanner-limited
- Use exact source artifact/path names where available
- Use the preloaded `_analysis/_doc_pack_${DOC_NAME}.md` evidence pack as the primary evidence source.
- Include evidence pointers from the pack when a section depends on Phase 2 findings.
- Include a `## Evidence Register` table with these exact columns:
  `| Topic | Units / Scope | Evidence Source | Source Artifact / Path | Confidence |`
- The Evidence Register must cover the major topics in this document with concrete supplied evidence,
  exact artifact/path names where available, and a confidence cell that starts with `HIGH`, `MEDIUM`,
  or `LOW`.
- Write that leading confidence token as plain unformatted text. Do not bold, italicize, quote, or
  wrap it in backticks: use `LOW (scanner-limited)`, not `**LOW**`, `_LOW_`, or `` `LOW` ``.
- Explanatory scanner-limited, inferred, or redaction notes may follow that confidence token or appear
  in evidence/topic cells. Mark those topics as such instead of guessing.

MANDATORY HEADINGS for ${DOC_NAME} -- your response MUST contain each of these as a `##` markdown
heading, using exactly this wording (do not rename, merge, omit, or reorder any of them).
A response that is missing any heading listed below is REJECTED -- write every heading even if
evidence is thin; mark the section LOW confidence and state what evidence is absent:
${HEADINGS_BLOCK}
  ## Evidence Register

${DOC_SPECIFIC}
Platform-specific evidence obligations:
${PLATFORM_DOCUMENTATION_OBLIGATIONS}
Platform documentation guidance:
${PLATFORM_ANALYSIS_REQUIREMENTS}

Facts:
${FACTS_SUMMARY}
${EXAMPLES_BLOCK}
