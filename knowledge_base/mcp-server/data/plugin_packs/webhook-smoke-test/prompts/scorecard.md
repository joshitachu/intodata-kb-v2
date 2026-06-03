Generate a tiny webhook smoke-test scorecard.

Return only:
- Overall: PASS / WARN / FAIL
- Evidence seen: 3-6 file or artifact names
- Reason: one short paragraph
- Next action: one concrete step

Scoring:
- PASS: repo scanned and enough evidence exists to generate a small report.
- WARN: repo scanned but evidence is thin or change context is missing.
- FAIL: scan evidence is missing, unreadable, or generation cannot be trusted.

Do not produce a full technical quality scorecard.
