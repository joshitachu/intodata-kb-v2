Generate `{doc_name}.md` as a minimal webhook smoke-test report.

Audience:
- Developers validating GitHub webhook, scan, and generation plumbing.

Rules:
- Keep the entire document under 500 words.
- Use only supplied evidence.
- Be concrete: name the repository type, key files, and whether the scan/generation appears usable.
- Do not produce broad documentation, inventories, migration notes, risk registers, or code walkthroughs.
- If evidence is limited, say so plainly.
- Satisfy deterministic pack validation: when the smoke test passes, include the exact words `scanned`, `webhook`, and `PASS` in the final document.

Required sections:
- `## Repository Snapshot`: 3-5 bullets naming repo purpose/type and the key files seen.
- `## Detected Change Summary`: summarize changed/relevant evidence if supplied; otherwise say no change details were supplied.
- `## Smoke Test Result`: one of PASS, WARN, or FAIL with a short reason and next action.
