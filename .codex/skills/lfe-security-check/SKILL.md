---
name: lfe-security-check
description: Run the LFE Inspector security sub-check. Use only from lfe-inspector to analyze implementation diffs against OWASP Top 10 and write .plans/checks/security_findings.md.
---

# LFE Security Check

Run a prompt-based security analysis on implementation diffs.

## Dispatch Rules

- Invoke only from `lfe-inspector`.
- Require `.plans/builder_done.md` and `.plans/tdd_report.md`, or an LFE-FORCE checks path.
- Examine only files listed in `builder_done.md`.
- Cite specific evidence for every finding.

## OWASP Categories

Check:

- Broken access control.
- Cryptographic failures.
- Injection.
- Insecure design.
- Security misconfiguration.
- Vulnerable components.
- Authentication failures.
- Integrity failures.
- Logging and monitoring gaps.
- SSRF.

## Output

Write `.plans/checks/security_findings.md` with YAML frontmatter including `status: complete`.

If no vulnerabilities are found, state: `No security concerns identified across all OWASP Top-10 categories.`

