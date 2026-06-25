IMPORTANT: Do not echo this request. Do not write "Okay", "Sure", "I will", or any
acknowledgement or meta-commentary. The first line of your output must be the document
title (a `#` heading) or a `## Table of Contents` heading. Any response that starts with
a filler word or an acknowledgement is INVALID and will be rejected.

${PREAMBLE}

CLIENT DELIVERABLE (enforced):
- This document is a client deliverable. Completeness and verbatim evidence are the product;
  a thin or abbreviated doc fails the engagement. Do NOT shorten, summarize, or omit content to
  save space, tokens, or effort. When in doubt, include more detail, not less. Reproduce the
  evidence in full and explain it -- length that the evidence supports is required, not optional.

FACTUAL CONSTRAINTS (enforced):
- Platform: ${PLATFORM}
- Canonical inventory: exactly ${CANONICAL_UNIT_COUNT} ${CANONICAL_UNIT_LABEL} -- never use a conflicting count
- Cover or mention all canonical units where relevant to this document
- If detail is absent for a unit, write "scanner detail is limited for this unit" -- never invent
- Synthesize from the supplied validated analysis; do not re-extract from raw scanner data

DOC-PACK TRACEABILITY RULE (enforced):
- The preloaded `_analysis/_doc_pack_${DOC_NAME}.md` evidence pack is the primary and
  sufficient evidence source for this document. You do not need any tools -- every fact you
  need is in the pack. "Sufficient" means you have enough to write the FULL document in one
  pass; it does NOT mean you should write a short one. Be exhaustive: mine the pack for every
  unit, flow, expression, SQL statement, and rule it contains and reproduce them in full. Do
  NOT re-read analysis or source artifacts already included in the pack.
- Every factual claim about a unit, flow, system, or rule must
  trace to a specific section in the doc-pack.
- If the doc-pack does not mention a unit that belongs to the canonical inventory, write the
  unit's exact name and state that doc-pack evidence is absent -- do NOT omit the unit from
  the Canonical Unit Inventory section or any inventory table.
- A unit that was newly added to the codebase may appear in the doc-pack but not in earlier
  documentation; always reflect the doc-pack state, not a prior document's state.

NARRATIVE RULES (enforced):
- Never summarize when you can enumerate. Enumerate EVERY relevant unit, flow, and rule the
  evidence supports -- do not stop at a representative sample or the "main" ones. A section that
  covers a subset when the evidence covers more is non-compliant.
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
- Each major section must be exhaustive: enumerate every relevant unit / flow / rule the
  evidence supports rather than summarizing. A section that is just a heading plus one vague
  sentence, or a document materially shorter than the supplied evidence supports, is
  non-compliant and must be expanded with the missing detail.
- Each section must contain at least 3 enumerated items or a table with at least 2 data rows;
  a section with only a heading or a single vague sentence is too thin

VERBATIM EVIDENCE RULE (enforced -- FORMAT-level rejection):
- EVERY SQL statement, code snippet, expression, transformation, mapping, and configuration
  block present in the evidence pack MUST be reproduced VERBATIM inside a fenced code block
  (triple backticks), and then explained in prose. Copy the evidence exactly; do not paraphrase,
  truncate, or merely reference it inline.
- Referencing code inline (e.g. "the job runs a SELECT against orders") instead of showing the
  actual statement in a fenced block is non-compliant.
- A response that mentions SQL, an expression, or a code/config block that exists in the
  evidence but does NOT reproduce it in a fenced code block is REJECTED, exactly as a response
  missing a required heading is rejected. If the evidence contains code, your document MUST
  contain fenced code blocks.

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
