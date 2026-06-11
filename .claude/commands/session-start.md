Load session context, check protocol debt, recover pipeline state, and classify work
before any coding begins.

## Argument: $ARGUMENTS

Accepted values: `kb` | `rag` | empty (default: both)

## Steps

### 1. Check for open protocol debt

Read `PROTOCOL_DEBT.md` if it exists. If any item has `Resolved: false`, print this
block at the top of all output before anything else:

```
OPEN PROTOCOL DEBT — resolve before new feature work
-----------------------------------------------------
<list each open item: date + title>

Run /debt-resolve <title> after completing the required follow-up.
```

This is a warning, not a hard block — work may continue, but it must be shown first.

### 2. Check git status

```bash
git status --short
```

Identify which sub-project(s) are active. Flag if git status contradicts $ARGUMENTS.

### 3. Load context

Based on $ARGUMENTS:
- **`kb`**: Read `knowledge_base/.claude/context.md` and the last 3 entries of
  `knowledge_base/.claude/PROGRESS_LOG.md`. Read `knowledge_base/.agent/README.md`.
- **`rag`**: Read `RAG_pipeline/.claude/context.md` and the last 3 entries of
  `RAG_pipeline/.claude/PROGRESS_LOG.md`.
- **empty / both**: Read context.md and last 3 PROGRESS_LOG entries for both.

### 4. Pipeline state recovery

List all files in `.plans/`. Determine the current pipeline position and the next step:

| Files present | State | Next action |
|---|---|---|
| 05_inspection_report.md with verdict PASS | Inspection passed | Run /validate and commit |
| 05_inspection_report.md with verdict BLOCK/WARN | Inspection issues | Fix findings, re-run /inspect |
| 04_active_plan.md with verdict PASS, no report | Plan approved, build done or in progress | Build (or run /inspect if built) |
| 04_active_plan.md with verdict PENDING or BLOCK | Plan not approved | Run /plan-approve pass or revise with /plan |
| 03_slices.md, no active plan | Slices defined | Run /plan '<slice title>' |
| 02_prd.md, no slices | PRD written | Run /slices |
| 01_grill.md, no prd | Grill done | Run /prd |
| Nothing | No active pipeline | Run /grill or classify as Scout |

Print the pipeline state and next action clearly.

### 5. Complexity gate

If no pipeline is in progress (no `.plans/` files), classify the work based on what
the user describes (or the next step from context.md if no task stated):

**Scout** — minor fix, proceed directly:
- < 3 files will change
- No new classes, modules, schemas, or public API changes
- Does NOT touch: harness pipeline, MCP API routes, RAG retrieval chain, auth/RBAC
- Bug fix or small adjustment in a well-understood area

**Full pipeline** — grill/prd/slices/plan required before coding:
- New feature or capability
- Schema changes (DB migrations, Pydantic models, API contracts)
- Any change to: harness pipeline, MCP API routes, RAG retrieval, auth/RBAC
- > 3 files expected to change

When in doubt, default to full pipeline.

Output the classification:
- Scout: `Scout mode — proceed directly. Run /validate before committing.`
- Full pipeline: `Full pipeline — run /grill <description> to start.`

### 6. Output brief

Max 20 lines per active sub-project:
- **Protocol debt**: open items (if any)
- **Pipeline state**: current position and next action
- **Mode**: Scout or Full pipeline (if no pipeline active)
- **Status**: what was last completed
- **Last 3 changes**: one line each from PROGRESS_LOG
- **Blockers**: anything flagged
- **Next step**: immediate priority from context.md
- **Modified files**: from git status, grouped by sub-project

Backlog files (`backlog.md`) are NOT loaded by default — read them only if the user
asks about deferred work.
