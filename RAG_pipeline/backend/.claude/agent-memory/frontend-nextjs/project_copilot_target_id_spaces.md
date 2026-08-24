---
name: copilot-target-id-spaces
description: Answer targets must be validated against the canvas's active landscape id space; overlay and scanner ids have no client-side bridge and must never be translated
metadata:
  type: project
---

The landscape artifact carries two mutually exclusive id spaces, and the canvas picks one with
the rule `business_topology && topology_state.validated && !topology_state.stale`
(`lib/landscapeTransform.ts`, mirrored as `activeIdSpace` in `lib/topologyTargets.ts`):

- business-overlay: `landscape-system:*` ids, `business-stream<US><streamId>` edge keys
- scanner: `system:connection:N:*` ids, 7-part `<US>`-joined edge keys

Overlay ids are derived by server-side hashing, so there is **no** client-side bridge. A target
whose id belongs to the inactive space is reported unresolvable and passed through untouched;
translating or re-prefixing it invents an identity that resolves to nothing.

Applications (`talend-project:*`) are outside the split — `landscape.applications` is present in
both spaces and the integration detail canvas resolves `applicationId` against it either way.

**Why:** a chip that navigates into an empty canvas reads as a broken product rather than as
missing data, and id "normalisation" is the most tempting wrong fix here.

**How to apply:** any new surface that consumes `targets[].level` or emits `page_scope.level`
must validate against loaded graph data and keep camelCase field names with `undefined` omitted
(not null). See [[copilot-progressive-highlight-gap]].
