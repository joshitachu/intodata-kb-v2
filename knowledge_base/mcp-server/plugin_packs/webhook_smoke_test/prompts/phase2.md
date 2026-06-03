You are generating a tiny Phase 2 analysis for webhook smoke testing.

Goal:
- Confirm the repository can be scanned and summarized after a webhook or manual trigger.
- Capture only enough evidence to prove the doc generation pipeline saw the repo.
- Keep output short and cheap.

Focus only on:
- Repository type and likely language/framework.
- 3-8 key files or folders that explain what the repo is.
- Any obvious entrypoint, README purpose, test file, config file, or package manifest.
- Any changed/recently relevant evidence if supplied by the pipeline.
- Any blocker that would make the scan/generation invalid.

Do not:
- Produce exhaustive inventories.
- Enumerate every file.
- Deeply analyze architecture, security, database schemas, or business logic.
- Quote large file contents.

Required output:
- Start with `## Coverage`.
- For every canonical unit assigned to this group, include exactly one marker line:
  `- COVERED: <exact unit name>` or `- SKIPPED: <exact unit name> -- <short reason>`.
- Then include `## Model Analysis`.
- Under `## Model Analysis`, include a heading exactly `### GROUP_SUMMARY`.
- A concise repository snapshot.
- A short list of evidence files inspected.
- A PASS/WARN/FAIL smoke-test assessment with one-sentence reason.

Keep the full answer short, but do not omit the coverage markers or `### GROUP_SUMMARY`.
