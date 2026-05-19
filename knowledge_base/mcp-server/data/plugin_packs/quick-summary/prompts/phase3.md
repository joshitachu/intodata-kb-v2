Generate `{doc_name}.md` as a concise executive summary for a non-technical business stakeholder.
This document will be read by business owners and project sponsors, not developers.

Writing rules:
- Plain English throughout. Spell out any abbreviation on first use.
- No raw technical identifiers (no tMap, tMSSqlInput, tRunJob, etc.) -- translate to plain names
  like "data transformation step", "SQL Server read", "child job call".
- No multi-page technical inventories. The document must fit comfortably in two printed pages.
- Every statement must trace back to supplied analysis evidence -- do not invent findings.
- If a file or system name is relevant to a business owner (e.g. a database name, a file path used
  in a nightly feed), include it. If it is only a technical implementation detail, omit it.
- Risks must be expressed as business consequences: "If this job fails, the daily sales report
  will show incorrect totals until manually corrected" -- not "job lacks tLogCatcher".
- Next steps must be actionable: name a responsible owner type (IT, data team, business analyst)
  and a concrete action, not a vague recommendation.
