---
name: lfe-whats-next
description: Read LFE pipeline state and output the single next action in 5 lines or fewer. Use for quick orientation from .plans/pipeline_status.md and coordination files.
---

# LFE What's Next

Read-only orientation tool.

## Workflow

1. Scan `.plans/pipeline_status.md` for active persona, mission, phase, and coordination file status.
2. Check `.plans/` for completion markers.
3. Output only the next action block.

## Output Format

```text
Phase <N>: <Name> (Slice <X> of <Y>)
  Completed: <steps>
  Next step: <skill>
  Run: $<skill-name>
```

## Common Scenarios

- No files exist: start with `lfe-boot`.
- Interrupted session: resume from last completed file.
- All slices done: run `lfe-archivist`.
- 5 or more sessions since last sweep: run `lfe-improve-architecture`.

Keep output read-only, concise, and grounded in file presence.

