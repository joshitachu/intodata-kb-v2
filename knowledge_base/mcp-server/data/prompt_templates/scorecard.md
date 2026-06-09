${PREAMBLE}

SCORING RULES (enforced):
- Score each of the 8 dimensions 1-5 following the rubric criteria exactly
- Every score MUST cite specific evidence: exact job names, component types, counts, expressions
- "Error handling is weak" is UNACCEPTABLE -- "7 of 12 ${FIRST_GROUP_NAME} jobs have no tLogCatcher" IS acceptable
- Anti-patterns: reference by AP-xxx-nnn ID from the supplied catalog
- Red flags: reference by RF-xxx-nnn ID from the supplied catalog
- Complete the security checklist evaluation item by item with evidence

Required output structure:
1. Overall score (weighted average, 1 decimal) with one-sentence justification citing the weakest dimension
2. Dimension table (8 rows):
   | Dimension | Score | Evidence (specific job/component/count) |
   architecture | data lineage | business logic | operations | security | performance | standards | migration
3. Top risks (minimum 5, ranked by severity):
   | Severity | Affected units | Description | Recommended remediation |
4. Top strengths (minimum 3) with specific evidence for each
5. Anti-pattern findings: AP-xxx-nnn ID | description | affected jobs | remediation
6. Red flag findings: RF-xxx-nnn ID | description | affected jobs | remediation
7. Security checklist: each item as PASS / FAIL / NOT_APPLICABLE with one-line evidence citation

Facts:
${FACTS_SUMMARY}

Platform scoring guidance:
${PLATFORM_ANALYSIS_REQUIREMENTS}

Canonical inventory: exactly ${CANONICAL_UNIT_COUNT} ${CANONICAL_UNIT_LABEL} -- never use a conflicting count.
