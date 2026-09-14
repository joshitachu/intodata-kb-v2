## Phase: REVIEW

Goal: review every drafted client document at the workspace root against the validated knowledge
model and the exact tables. Report each finding once. Blocking kinds: `unsupported_claim` (prose the
model and evidence do not support), `missing_required_content` (a required topic absent),
`contradiction` (an open contradiction not disclosed), `leakage` (internal paths, ids or pipeline
language), `inconsistent_with_model`. Use `shallow` or `other` with severity `advisory` for quality
notes. A correct document with no findings is a valid result: submit an empty list.

Assignment: ${ASSIGNMENT}

Contract (JSON object):
{
  "schema_version": "enterprise-review-1",
  "findings": [{"doc": "<document name>", "section": "<section key or empty>", "severity": "blocking|advisory",
    "kind": "unsupported_claim|missing_required_content|contradiction|leakage|shallow|inconsistent_with_model|other",
    "detail": "<what is wrong and what would fix it>", "claim_ids": ["ek-..."]}]
}
