---
name: lfe-hygiene
description: Run an LFE structural hygiene audit. Use every 5 sessions to detect documentation drift, stale coordination files, orphaned docs, CHANGELOG overflow, and write .plans/hygiene_report.md.
---

# LFE Hygiene Audit

Detect documentation drift, stale coordination files, orphaned docs, and history overflow.

## Audit Domains

- Floor map and coordination layer: detect unregistered or missing files and stale execution artifacts.
- State and history: verify `.plans/pipeline_status.md` and `CHANGELOG.md` consistency.
- Skills and permissions: validate skill frontmatter and persona alignment.
- Documentation scaling: flag directories with several undocumented files and oversized docs lacking indexes.
- Coordination contract: validate frontmatter for planning and execution files.
- Retention policy: age out and archive hot-file entries if the project has this governance structure.

## Output

Write `.plans/hygiene_report.md` with findings categorized:

- Critical: blocks or seriously risks next work.
- Warning: should be scheduled.
- Clean: compliant.

Write a report even if no findings exist. Critical findings do not automatically block; humans triage timing.

