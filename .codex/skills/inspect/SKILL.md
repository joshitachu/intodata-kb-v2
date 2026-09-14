---
name: inspect
description: Audit an implementation against its plan before commit - correctness, root-cause depth, generality, and OWASP security review of the diff. Use after building in a critical area, or whenever a change is larger than a small fix.
---

# Inspect

Verify the implementation against the plan and the code, not against the narrative
of what was just done. Output: `.plans/inspection.md`.

Mandatory for critical areas (see `session-start`). Optional but recommended elsewhere.

## 1. Establish what changed

```bash
REPO=$(git rev-parse --show-toplevel)
git -C "$REPO" diff --stat HEAD
git -C "$REPO" diff HEAD
```

Read the whole diff. If `.plans/plan.md` exists, read it -- that is the contract the
diff is being judged against. If it does not exist, judge the diff on its own terms
and note that there was no plan.

## 2. Correctness

For each changed file, answer concretely:

- Does it do what the plan said it would?
- What happens on the unhappy path -- empty input, missing key, failed network call,
  concurrent access? Name the specific line that handles it, or record that nothing does.
- Are there silent failures -- a bare `except`, a swallowed error, a default that
  masks a missing value?

## 3. Root cause and generality

This repo's rules require fixes to address root causes and to generalise. Check both:

- **Root cause**: does this fix the defect, or suppress its symptom? A narrow patch
  where the general defect remains is a finding, not a pass.
- **Generality**: search for the same pattern elsewhere.

```bash
git -C "$REPO" grep -n "<the pattern that was buggy>"
```

If the same defect exists at other sites and the diff does not fix them, that is a
BLOCK-level finding. Name the file and line of each unfixed site.

## 4. Security review of the diff

LLM analysis of the changed lines only -- no code execution. Walk the OWASP Top 10:
broken access control, cryptographic failures, injection, insecure design,
misconfiguration, vulnerable components, auth failures, integrity failures, logging
gaps, SSRF.

This repo has two standing rules that need explicit checks:
- **Credential safety** -- confirm no raw credential can reach an LLM prompt or a log line.
- **No hardcoded group or tenant names** -- these must come from API results.

Every finding cites a file and line. If nothing is found, say so plainly rather than
inventing a finding to look thorough.

## 5. Evidence, not vibes

Run the verification commands from the plan. Paste the real output.

"Tests pass" is not a finding unless you ran them and can show the counts. If you
could not run something, record it as `NOT VERIFIED` with the reason -- never as a pass.

## 6. Write `.plans/inspection.md`

```markdown
---
verdict: PASS | WARN | BLOCK
inspected: YYYY-MM-DD
---

# Inspection: <feature>

## Verdict
One sentence and why.

## Verification run
The commands executed and their real output.

## Findings
| Severity | File:line | Finding | Required action |
|----------|-----------|---------|-----------------|

## Not verified
Anything that could not be checked, and why.
```

Verdicts:
- **PASS** -- matches the plan, no unfixed general defects, no security findings above Low
- **WARN** -- minor findings; may commit, but the findings are recorded
- **BLOCK** -- correctness defect, unfixed recurrence of the same pattern, or a security
  finding at Medium or above

Do not proceed to `/validate` on BLOCK. Fix, then re-inspect.
