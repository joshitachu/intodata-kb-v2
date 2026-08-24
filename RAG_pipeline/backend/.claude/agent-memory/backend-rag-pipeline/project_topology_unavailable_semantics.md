---
name: topology-unavailable-semantics
description: Why the harness intentionally lets its scope gate deny out-of-scope topology reads instead of collapsing them into UNAVAILABLE like the topology dispatcher does
metadata:
  type: project
---

The topology tools on the chat path deliberately do NOT reproduce
`src/topology/client.py`'s rule that "project out of scope" and "no artifact
published" must be indistinguishable. On the harness path the generic capability
scope gate denies an out-of-scope `project_id` first, with its own uniform
"Project access required" wording, and only in-scope-but-unpublished projects
reach the dispatcher and become UNAVAILABLE.

**Why:** the wave-1 rule exists because the dispatcher had no gate above it, so
two different codes there would have been an existence oracle. The harness gate
is scope-uniform — every project outside the caller's allow-list gets the same
denial regardless of whether it exists — so it is not an oracle, and bypassing
the generic gate to force uniformity would have removed a real authorization
check. Also relevant: despite the comment in `client.py` claiming the two
messages match, they do not ("No topology data is available for project 'X'."
vs. "No topology artifact has been published for project 'X'. ..."). That
divergence is in `src/topology/` and was left alone.

**How to apply:** if a future session is asked to "make out-of-scope and missing
artifact identical" on the chat path, push back rather than weakening the scope
gate. If the wording divergence in `src/topology/client.py` is ever fixed, the
harness needs no change. Related: [[topology-roadmap-orchestration]].
