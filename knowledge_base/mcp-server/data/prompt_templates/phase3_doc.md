IMPORTANT: Do not echo this request. Do not write "Okay", "Sure", "I will", or any
acknowledgement or meta-commentary. The first line of your output must be the document
title (a `#` heading) or a `## Table of Contents` heading. Any response that starts with
a filler word or an acknowledgement is INVALID and will be rejected.

${PREAMBLE}

CLIENT DELIVERABLE (enforced):
- This document is a client deliverable. Completeness means covering the required decisions,
  relevant canonical units, and material evidence -- not copying every row from an evidence map.
  Consolidate repetitive facts into categories, counts, and relationship summaries. Preserve
  traceability through exact evidence pointers so the reader can inspect the evidence of record.
- Produce a complete, concluded document in roughly 6,000-12,000 output tokens. The provider's
  maximum is a safety ceiling, not a target. Never sacrifice the final required sections in order
  to expand an earlier inventory or table.

FACTUAL CONSTRAINTS (enforced):
- Platform: ${PLATFORM}
- Canonical inventory: exactly ${CANONICAL_UNIT_COUNT} ${CANONICAL_UNIT_LABEL} -- never use a conflicting count
- Cover or mention all canonical units where relevant to this document
- If detail is absent for a unit, write "scanner detail is limited for this unit" -- never invent
- Synthesize from the supplied validated analysis; do not re-extract from raw scanner data

DOC-PACK TRACEABILITY RULE (enforced):
- The preloaded `_analysis/_doc_pack_${DOC_NAME}.md` evidence pack is the primary and
  sufficient evidence source for this document. You do not need any tools -- every fact you
  need is in the pack. Synthesize it into the document's required decisions and sections. Group
  repeated findings and cite their counts and scope; do not reproduce the pack or a fact map row
  by row. Do NOT re-read analysis or source artifacts already included in the pack.
- Every factual claim about a unit, flow, system, or rule must
  trace to a specific section in the doc-pack.
- If the doc-pack does not mention a unit that belongs to the canonical inventory, write the
  unit's exact name and state that doc-pack evidence is absent -- do NOT omit the unit from
  the Canonical Unit Inventory section or any inventory table.
- A unit that was newly added to the codebase may appear in the doc-pack but not in earlier
  documentation; always reflect the doc-pack state, not a prior document's state.

PIPELINE-GAP RULE (enforced):
- Some UNKNOWN entries in the analysis state "The analysis task did not complete ..."
  with a pointer to existing scan evidence. That is a DOCUMENTATION-PIPELINE failure,
  not missing source. For those units write exactly this framing: "the analysis task did
  not complete for this section (scan evidence exists at <the stated path>)". NEVER write
  that the unit, its code, or its evidence is unavailable, missing, not scanned, or not
  captured -- the scan evidence exists and the statement would be false.
- Only when the analysis states that the SCAN itself lacks the material (a genuinely
  empty or scanner-limited unit) may you describe evidence as absent or scanner-limited.

PROTECTED-FACT RULE (enforced):
- Scanner-derived facts (unit counts, per-group counts, component counts, versions,
  source paths) are PROTECTED: quote them from the supplied facts summary, the unit
  docs digest (`_analysis/_unit_docs_digest.md`, when supplied), or the evidence pack.
  Never recompute, estimate, round, or infer them. A count that matches no supplied
  scanner fact is a validation error.
- When the unit docs digest is supplied, synthesize summaries FROM the finished unit
  docs it excerpts (plus the analysis pack); do not re-derive unit behavior that a
  unit doc already states.

NARRATIVE RULES (enforced):
- Cover all material categories and affected canonical units. Use a compact coverage table when
  many units share the same finding. Enumerate unique high-impact facts; group repetitive low-level
  rows and point to the complete deterministic artifact instead of copying it.
- Every claim needs an exact unit name + component reference
- Include concrete counts, percentages, exact names
- Business rules: tag each as BR-{GROUP}-NNN with the full expression, and name the unit it
  comes from. Where the evidence states a business rule, it MUST carry a BR-{GROUP}-NNN tag.
- Risk items: label each CRITICAL / HIGH / MEDIUM / LOW and cite the specific unit(s) it
  applies to. Where the evidence supports a risk, it MUST carry a severity code and unit
  reference -- a risk section with no severity codes is non-compliant.
- Schema docs: column-level detail (name, type, nullable)
- Data flow docs: full tMap expressions with explanations
- "Error handling is weak" is UNACCEPTABLE -- cite specific job names and counts
- Each major section must be decision-complete and evidence-backed. A section that is just a
  heading plus one vague sentence is non-compliant, but completeness does not require duplicating
  exhaustive source tables or repeating the same guidance for every affected unit.
- Each section must contain at least 3 enumerated items or a table with at least 2 data rows;
  a section with only a heading or a single vague sentence is too thin

EVIDENCE EXCERPT RULE (enforced):
- Reproduce SQL, code, expressions, mappings, or configuration verbatim only when the exact text
  is necessary to explain a material behavior in this document. Put selected excerpts in fenced
  code blocks and cite their unit/component and evidence path.
- Do not copy every code/configuration block merely because it exists in the evidence pack.
  Inventory-scale detail belongs in deterministic evidence artifacts and specialized documents.

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
