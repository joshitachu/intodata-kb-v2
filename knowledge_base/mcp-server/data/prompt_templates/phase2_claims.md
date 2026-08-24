Return one JSON object only. Do not use Markdown fences or explanatory prose.

You are extracting bounded evidence claims for documentation. The scanner artifacts are the
factual record; your response is a concise interpretation index, not a copy of the source.

SCHEMA
{
  "schema_version": "phase2-claims-v1",
  "group": ${GROUP_JSON},
  "task_id": ${TASK_ID_JSON},
  "concern": ${CONCERN_JSON},
  "claims": [
    {
      "section": "ONE_ALLOWED_SECTION",
      "kind": "fact|relationship|risk|unknown",
      "unit_names": ["assigned canonical unit"],
      "related_units": ["optional directly connected canonical unit"],
      "statement": "one concise evidenced statement",
      "evidence_refs": ["exact supplied source path and component/locator when available"],
      "confidence": "certain|supported|tentative|unknown",
      "claim_id": ""
    }
  ]
}

ASSIGNED CANONICAL UNITS
${CANONICAL_UNITS}

ALLOWED SECTIONS
${SECTIONS}

RULES
- Emit at least one claim for every allowed section.
- Use only assigned units in unit_names. A directly connected unit may appear in related_units.
- Every fact, relationship, or risk requires at least one exact evidence_refs entry.
- If evidence for a section is absent, emit exactly one kind=unknown claim naming the missing evidence;
  unknown claims may have an empty evidence_refs list.
- Maximum three non-unknown claims per section and maximum one claim per assigned unit per section.
- CODE-BEARING SECTIONS (TRANSFORMATION_DETAIL, SOURCE_SQL, CUSTOM_CODE): when the evidence
  contains SQL queries, tMap/transformation expressions, or custom code for an assigned unit,
  the section's claim MUST quote the decisive expression VERBATIM inside backticks (up to the
  ${MAX_CLAIM_STATEMENT_CHARS}-character statement allowance; quote the decisive portion of a
  longer expression) and explain what it does. A one-line paraphrase without
  the verbatim expression is INVALID for these sections. An unknown claim in a code-bearing
  section is INVALID when the evidence contains that category.
- Other sections: do not reproduce full component inventories, context tables, credentials, URLs,
  or configuration dumps. State the decisive interpretation and cite its source.
- Do not infer business purpose, schedules, ownership, SLAs, or relationships absent from evidence.
- Preserve canonical names exactly. The project has ${CANONICAL_UNIT_COUNT} canonical ${CANONICAL_UNIT_LABEL}.
