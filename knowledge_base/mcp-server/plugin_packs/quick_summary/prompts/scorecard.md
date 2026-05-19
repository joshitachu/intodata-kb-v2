Generate a brief risk scorecard from the supplied Phase 2 analysis, focused on what a business
stakeholder needs to act on. Skip the full 8-dimension technical grid. Instead produce:

1. Overall risk level: LOW / MEDIUM / HIGH / CRITICAL with a one-sentence plain-English justification.
2. Top risks table (3-5 rows max):
   | Risk | Affected integration(s) | Business impact | Suggested owner |
   Use plain language -- no AP/RF codes, no Talend component type names unless unavoidable.
3. Top strengths (2-3 bullet points) -- what is working well that the business can rely on.

Scoring rules:
- Base every finding on specific evidence from the Phase 2 files -- name the integration and what was observed
- Translate technical risks into business terms: "no error handling on the daily sales sync" not "tLogCatcher absent on 4 jobs"
- Severity: CRITICAL = data loss or incorrect financial output likely; HIGH = process fails silently or recovery is manual;
  MEDIUM = failure is detectable but slow to fix; LOW = cosmetic or edge-case only
- If credentials are hardcoded or external systems have no retry logic, always escalate to at least HIGH
