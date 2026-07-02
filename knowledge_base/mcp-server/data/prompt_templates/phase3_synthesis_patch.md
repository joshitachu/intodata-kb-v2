IMPORTANT: Do not echo this request. Begin immediately with the updated section content.

You are amending a cross-cutting SUMMARY document `${DOC_NAME}` on platform `${PLATFORM}`.
This document synthesizes information across ALL groups in the project. You are amending
ONLY the parts that reference group `${GROUP}` -- every other group's content must be left
exactly as it is.

PATCH CONTRACT:
- Rewrite ONLY the sections listed in HEADINGS_TO_REWRITE below.
- Do NOT alter any section not in that list, and do NOT introduce or reword content about
  any group other than `${GROUP}`.
- For each section to rewrite, emit the section heading (## Heading) followed by the updated
  content for that section in full (not a diff).
- Preserve all BR-${GROUP}-NNN tags and Evidence Register rows for topics not covered by the
  changed analysis sections.
- REMOVAL: if REMOVED_IDENTIFIERS below is not "(none)", delete every row, bullet, or
  sentence that references any of those identifiers -- they no longer exist in the codebase.
  Do not reword or soften the reference; remove it entirely. If removing an identifier empties
  a table row or bullet, drop that row/bullet outright (the section may shrink). Do not
  invent a replacement.
- Return ONLY the rewritten sections -- not the full document.
- Do not invent facts absent from the updated analysis sections or the existing document.

HEADINGS_TO_REWRITE:
${HEADINGS_TO_REWRITE}

REMOVED_IDENTIFIERS (must not appear anywhere in your output -- delete their references):
${REMOVED_IDENTIFIERS}

UPDATED ANALYSIS SECTIONS FOR GROUP ${GROUP} (sections that changed):
${UPDATED_ANALYSIS_SECTIONS}

EXISTING DOCUMENT (full text for context; do not repeat unchanged sections):
${EXISTING_DOC}
