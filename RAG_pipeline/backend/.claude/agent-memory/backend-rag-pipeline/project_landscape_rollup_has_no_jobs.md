---
name: landscape-rollup-has-no-jobs
description: The MCP landscape artifact contains zero job/component nodes — job names appear only as evidence pointers, which is why depth questions must use the graph-manifest endpoints
metadata:
  type: project
---

The published landscape artifact (`GET /projects/{id}/landscape`) is a
whole-project rollup of systems, applications and their relationships. It has NO
job or component nodes. A real job name like
`Quintiq2Maximo_WorkOrderPickedUpXML` appears in it only inside
`edges[].provenance[].artifact_id` — an evidence pointer, not a node.

Jobs, groups and components live in a separate family:
`/projects/{id}/graph` (design manifest), `/projects/{id}/jobs/{job}/graph`,
`/projects/{id}/jobs/{job}/components/{comp}/graph`.

**Why:** a user asked "tell me about the Quintiq to Maximo implementation design"
on the topology page. The rollup-only tools found nothing, so the model answered
from the RAG document corpus with invented detail and no source label — canvas and
answer disagreed visibly. The two honesty rules in
`src/harness/topology_guidance.py` (never substitute a source silently; state when
a source is empty) exist because of that incident.

**How to apply:** never answer a job- or component-level question from the
landscape rollup, and never treat "the rollup has no entry" as licence to switch
source. Component coverage without a job scope is partial by construction — only
components that appear in the manifest's `artifact_channels` /
`artifact_endpoints` / `artifact_resources` are indexed; full coverage requires
that job's own graph. Also relevant when sizing projections: the richest real job
in the `werkorder` upload has 251 components (142 KB raw) and the richest mapping
component is 27 KB raw, against a 12 000-char tool-result ceiling. See
[[envelope-depth-blocks-relationship-targets]].
