---
name: session-end
description: Log the session to PROGRESS_LOG.md and refresh context.md from real git output. Use after completing a change, before or just after committing.
---

# Session End

Write the record from the diff, not from memory. File paths and change descriptions
come from git output verbatim.

## 1. Gather real data

```bash
REPO=$(git rev-parse --show-toplevel)
git -C "$REPO" diff --stat HEAD
git -C "$REPO" diff HEAD
git -C "$REPO" log --oneline -3
```

Read the full diff. If nothing changed, say so and stop -- do not write an empty entry.

## 2. Identify the touched sub-projects

From `--stat`, determine which of `knowledge_base/`, `RAG_pipeline/`,
`knowledge_base/intodoc/` were touched. Handle each separately.

## 3. PREPEND to `<subproject>/.claude/PROGRESS_LOG.md`

The file is sorted **newest-first**. The new entry goes at the top, directly under the
file header -- never appended to the bottom. Appending is what desynchronised this
file before, making "the last 3 entries" ambiguous.

```markdown
## Session: YYYY-MM-DD -- <what changed, in one specific phrase>

- **Files changed**: every path from `git diff --stat`, none omitted
- **Problem**: what was being solved
- **Root cause**: why the defect existed (bugs) or what drove the need (features)
- **Change**: exactly what was added, modified, or removed
- **Why this approach**: the reasoning, including what was rejected
- **Verification**: the command run and its actual result
```

Use the real date. A file touched is a file logged.

## 4. Rewrite `<subproject>/.claude/context.md` in place

`context.md` is **live state only**. It has no dated sections and no history.

- Update `## Current state` to what is true now.
- Update `## Next step`.
- Delete any blocker that this session resolved.
- Keep it under 60 lines.

Do NOT add a new dated section. If you find yourself writing "## Current State
(2026-..)" you are recreating the append-only rot that made this file useless --
the history belongs in PROGRESS_LOG.md, which you just wrote.

## 5. Rotate if needed

If `PROGRESS_LOG.md` has entries older than about 30 days, move them verbatim to
`<subproject>/.claude/archive/PROGRESS_LOG-pre-<date>.md`, preserving newest-first
order in both files. Do not summarise while rotating.

## 6. Distil anything durable

Scan the diff for a non-obvious invariant, an external constraint worked around, or
an architectural decision. If you find one, add it to `knowledge_base/.agent/decisions/`
as a numbered file, continuing the existing numbering.

Only genuinely non-obvious things. Restating what the code plainly says is noise, and
noise in these files is what caused this cleanup.

## 7. Report

`Session logged. Updated: <files written>`
