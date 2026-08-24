---
name: envelope-depth-blocks-relationship-targets
description: Two harness sanitizers are one level too shallow to carry a topology relationship target's streamKeys; both need +1 before pair-view navigation works
metadata:
  type: project
---

The `{kind:"relationship", source, target, streamKeys}` topology target cannot
survive the harness envelope as of this wave. Its `streamKeys` strings sit one
container level deeper than either sanitizer allows, so they arrive as truncation
markers:

- `src/harness/agent_runs.py` `_MAX_PUBLIC_PAYLOAD_DEPTH = 5` → keys become `"[TRUNCATED]"`
- `src/harness/semantic_tools.py` `MAX_CONTAINER_DEPTH = 6` → keys become `{"truncated": true, "reason": "depth_limit"}`

Both need raising by one. Depth chain to a stream key: `data → targets → target
→ level → selection → streamKeys → key`. The existing comment in `agent_runs.py`
counted only to the flat id (`selection.systemId`), which is one shallower.

**Why:** the depth-wave agent could not fix it — both files are outside the
topology wave's file ownership. The tool layer emits correct keys; the damage is
purely in the sanitizers.

**How to apply:** a strict-xfail test in
`tests/test_harness/test_topology_capability.py::test_relationship_stream_keys_survive_the_public_payload`
flips to a failure the moment the constants are raised, which is the signal to
delete the marker. Until then, "go back to the view between X and Y" produces an
inert chip, not a working one. Note separately that
`frontend/app/topology/page.tsx` has no renderer for `kind: "relationship"`
either — it falls through to the "no longer present in the current scan" branch.
