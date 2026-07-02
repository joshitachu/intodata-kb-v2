IMPORTANT: Do not echo this request. Begin immediately with the updated section content.

You are patching a Phase 2 group analysis for group `${GROUP}` on platform `${PLATFORM}`.

PATCH CONTRACT:
- Rewrite ONLY the sections listed in SECTIONS_TO_REWRITE below.
- Do NOT alter any section not in that list.
- For each section to rewrite, emit the section heading (### SECTION_NAME) followed by
  updated content. Preserve all BR-${GROUP}-NNN tags, evidence register rows, and
  canonical unit names that are NOT affected by the field diff.
- Return ONLY the rewritten sections -- not the full analysis.
- Do not invent facts absent from the field diff or existing analysis.

SECTIONS_TO_REWRITE:
${SECTIONS_TO_REWRITE}

FIELD DIFF (changed fields only):
${FIELD_DIFF}

EXISTING ANALYSIS (full text for context; do not repeat unchanged sections):
${EXISTING_ANALYSIS}
