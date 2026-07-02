IMPORTANT: Do not echo this request. Begin immediately with the updated section content.

You are patching documentation for `${DOC_NAME}` for group `${GROUP}` on platform `${PLATFORM}`.

PATCH CONTRACT:
- Rewrite ONLY the sections listed in HEADINGS_TO_REWRITE below.
- Do NOT alter any section not in that list.
- For each section to rewrite, emit the section heading (## Heading) followed by updated
  content. Preserve all BR-${GROUP}-NNN tags and Evidence Register rows for topics not
  covered by the changed analysis sections.
- Return ONLY the rewritten sections -- not the full document.
- Do not invent facts absent from the updated analysis sections or the existing doc.

HEADINGS_TO_REWRITE:
${HEADINGS_TO_REWRITE}

UPDATED ANALYSIS SECTIONS (sections that changed):
${UPDATED_ANALYSIS_SECTIONS}

EXISTING DOCUMENT (full text for context; do not repeat unchanged sections):
${EXISTING_DOC}
