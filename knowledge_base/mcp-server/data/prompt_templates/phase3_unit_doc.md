IMPORTANT: Do not echo this request. Do not write "Okay", "Sure", "I will", or any
acknowledgement or meta-commentary. The first line of your output must be the document
title (a `#` heading). Any response that starts with a filler word is INVALID.

${PREAMBLE}

You are writing the reference dossier for exactly ONE scanned unit: `${SCOPED_UNIT}`.
The preloaded target evidence bundle (`_analysis/_evidence_${DOC_NAME}.md`) is the
factual source of record: it was extracted deterministically from the scanner cache.
The Phase 2 analysis pack, when present, is interpretation only -- when they disagree,
the evidence bundle wins.

UNIT DOSSIER CONTRACT (enforced):
- Size the explanation to the unit's evidenced behavior. Explain distinct rules and
  failure paths; use supplied linked reference parts for exhaustive exact detail.
- Title: `# Unit Document: ${SCOPED_UNIT}`.
- Every factual sentence carries an evidence pointer in square brackets, e.g.
  `[evidence:bundle ${SCOPED_UNIT} COMPONENT INVENTORY]` or a fact tag like
  `[fact:unit.${SCOPED_UNIT}.component_count]` for protected facts.
- PROTECTED FACTS: copy the `PROTECTED SCANNER FACTS` table from the evidence bundle
  verbatim into the `## Protected Scanner Facts` section (two columns: fact | value).
  Never recompute, round, merge, or restate these values in different units or words.
- VERBATIM CODE: for components that implement logic (tJavaRow, tMap, SQL inputs),
  quote the decisive expression/statement verbatim from the bundle's
  `CUSTOM CODE (VERBATIM)` / `TMAP EXPRESSIONS (VERBATIM)` / `SQL QUERIES (VERBATIM)`
  sections. Quote only the decisive lines; cite the component name.
- FLOW: preserve the typed branches, conditions, errors and loops in the
  `COMPONENT CONNECTIONS` table. Use several paths when the graph branches.
- CONTEXT VARIABLES: reproduce the per-environment table with its provenance column
  (job-embedded vs shared group, DIVERGES flags). Keep `[REDACTED]` / `[URL_REDACTED]`
  markers exactly as given -- they are intentionally hidden values, not missing data.
- Keep this unit as the primary subject. Explain supplied dependency behavior only
  in relation to this caller, with its exact owner and evidence; this does not claim
  coverage for the dependency's other callers or sibling jobs.
- Never write that code/tMap/SQL/context evidence is unavailable when the bundle
  contains the corresponding verbatim section.

MANDATORY HEADINGS -- your response MUST contain each of these as a `##` heading,
exactly this wording, in this order:
${HEADINGS_BLOCK}

Section guidance:
- `## Overview`: 2-4 sentences -- what this unit does end to end, with one evidence pointer.
- `## Evidence Discrepancies`: where evidence sources disagree (documented config vs
  code wiring, job-embedded vs shared context DIVERGES rows, documented endpoints vs
  redacted code endpoints). If none: state that no discrepancy was observed in the
  frozen scan.
- `## Runtime Unknowns`: what static evidence cannot prove (schedules, volumes,
  success/failure rates, actual environment resolution). Static evidence is not
  runtime proof; say so explicitly.

${DOC_SPECIFIC}

FORMAT:
- Plain ASCII only. Markdown tables and fenced code blocks (```java / ```sql).
- No table of contents, no Evidence Register table -- inline evidence pointers only.

Facts (scoped to this unit):
${FACTS_SUMMARY}
