# L1/L2 Topology Frontend Implementation Plan

Status: implementation-ready handoff
Prepared: 2026-07-30
Scope: frontend visualization and navigation only; scanner, landscape builder, and MCP landscape endpoint already exist

## 1. Outcome

Make `/topology` open on the scanner-computed application/system landscape and
support evidence-preserving drill-down:

```text
L1 Application/System landscape
  -> L2 Integration detail
    -> L3 Job/Route component graph
      -> L4 Mapping/field lineage
```

The page must use `GET /mcp/projects/{project_id}/landscape` as the authority for
L1/L2. It must not recreate landscape identities or infer new relationships in
the browser.

## 2. Existing implementation to preserve

The current topology page already contains valuable functionality:

- Project/group/job graph rendering in
  `frontend/app/topology/page.tsx` and `frontend/lib/graphTransform.ts`.
- Job/Route component drill-down through `JobGraphCanvas`.
- Mapping/table drill-down through `ComponentGraphCanvas`.
- Field lineage selection and highlighting.
- Runtime job-health overlay and downstream failure propagation.
- Search, focus mode, filters, minimap, compact mode, direction switching,
  breadcrumbs, mobile sheets, and group expansion.

The current function named `manifestToLandscapeFlow` is a legacy
functional-group rollup. It is not the new scanner-computed application/system
landscape. Keep it available as:

- a fallback for projects whose scan predates `landscape.json`; and
- a "Designs" or "Project internals" view inside an application.

Do not delete or silently repurpose it.

## 3. Backend contract

Already available:

```text
GET /mcp/projects/{project_id}/landscape
```

Response:

- `applications[]`: stable source-root application boundaries and artifact
  counts.
- `systems[]`: external-system candidates with evidence.
- `edges[]`: aggregated, typed relationships with reliability, confidence, and
  exact provenance.
- `channels[]`: exact L2 channel observations.
- `interfaces[]`: endpoint/interface observations.
- `diagnostics[]`: computed findings such as unread channels.
- `unmapped[]`: facts deliberately not resolved.
- `summary`: authoritative counts.

Important constraints:

1. Treat every ID as opaque.
2. Use `artifact_id`, `qualified_artifact_id`, `source_root`, and
   `component_name` from provenance for drill-down.
3. An interface declaration does not prove a peer system.
4. A redacted or placeholder channel must not be used to join producer and
   consumer records.
5. Never merge systems by display name.
6. The endpoint is per selected Nexus project. It represents an organization
   only when that project is a composite organization scan, such as
   `topology`.

## 4. User experience

### 4.1 L1 — Application/System landscape

The initial canvas for a project with landscape data shows:

- Application cards for every `applications[]` entry.
- System cards for every `systems[]` entry.
- Directed relationship edges from `edges[]`.
- Edge labels: dependency type, mechanism, reliability, and evidence count.
- Application badges: total artifacts and Job/Route/Joblet/Routelet breakdown.
- System badges: kind and number of evidence signals.
- Diagnostics counter and warning drawer.
- Summary counters from the response, not recomputed approximations.

Interaction:

- Single-click node or edge: select it and open the L1 evidence panel.
- Double-click node or edge, or click "Explore integration": descend to L2.
- Search matches application/system labels but selection and graph identity
  remain ID-based.
- Filters: dependency type, mechanism, application/system kind, confidence,
  diagnostics, and show/hide external systems.
- Existing runtime health may decorate application nodes only when jobs can be
  matched exactly by `manifest.job_source_roots[job] === application.source_root`.
  Do not assign runtime health to external systems.

Scale target: the real composite fixture has approximately 14 applications,
123-137 systems, 185 edges, 1,100+ channel records, and 146 interfaces.
Use `onlyRenderVisibleElements`, minimap, compact cards, bounded labels, and
dagre layout. Do not render all channel/interface records at L1.

### 4.2 L1 evidence panel

For an application:

- label/source root;
- artifact counts;
- incoming/outgoing relationships;
- mechanisms and dependency types;
- related diagnostics;
- buttons for "Explore integrations" and "View designs".

For a system:

- name/kind;
- evidence signals and source roots;
- connected applications and relationship types;
- no fabricated availability or ownership status.

For an edge:

- source and target;
- dependency type, direction, mechanism, reliability, confidence;
- evidence count;
- provenance records grouped by source root and artifact;
- "Explore integration" action.

### 4.3 L2 — Integration detail

L2 is evidence-scoped, not a second full-estate hairball.

Preferred entry is a selected L1 edge. Render:

- source boundary;
- target boundary;
- the exact jobs/routes from `edge.provenance`;
- component names and fact types when present;
- exact channels/interfaces attached to those qualified artifacts;
- direction, reliability, confidence, safe redaction descriptors, and
  diagnostics.

For an application selection, first show a relationship chooser/list. Opening
one relationship produces the bounded edge-centric graph above. Do not render
all 1,100+ channel records for an application in one canvas.

L2 tabs/panels:

- `Flow`: boundary -> implementing artifacts -> boundary.
- `Channels`: exact channel records for displayed artifacts.
- `Interfaces`: exact endpoint records for displayed artifacts.
- `Evidence`: provenance, descriptors, diagnostics, and unmapped limitations.
- `Designs`: the existing functional-group/project graph scoped to the
  selected application's source root.

Channel/interface records with the same exact type/name/direction may be grouped
visually, but the UI must preserve and expose every underlying artifact record
and count. Placeholder values remain separate evidence and never create a
relationship.

Interaction:

- Single-click artifact: show evidence and component information.
- Double-click artifact or click "Open design": descend to the existing job
  graph using its exact `artifact_id`.
- Breadcrumb back returns to the same L2 relationship and viewport.

### 4.4 L3/L4 — Preserve current behavior

Reuse the existing `JobGraphCanvas`, `ComponentGraphCanvas`, lineage hook,
lineage banner, runtime health, and inspector fallback.

The user-facing labels become:

- L1 Landscape
- L2 Integration
- L3 Job/Route
- L4 Mapping/Lineage

Internal filenames currently call the job graph "Level 2" and the component
graph "Level 3". Do not perform a broad rename unless required; update comments
only where ambiguity would cause mistakes.

### 4.5 Missing landscape compatibility

If the landscape endpoint returns 404:

- show a non-alarming "This project needs a rescan for the organization
  landscape" notice;
- render the existing functional-group topology unchanged;
- do not block job/component/lineage navigation.

For 401/403, preserve the normal authorization failure. For malformed payloads,
show a retry/rescan error rather than falling back silently.

## 5. Navigation model

Extend `useTopologyNavigation` without breaking existing group/job/component
tests:

```ts
type TopologyLevel =
  | { kind: "project" } // true L1 landscape root
  | { kind: "integration"; selection: IntegrationSelection }
  | { kind: "group"; groupName: string; sourceRoot?: string } // legacy/designs
  | { kind: "job"; jobName: string; sourceRoot?: string }
  | { kind: "component"; jobName: string; componentName: string };
```

`IntegrationSelection` must be serializable and ID-based:

```ts
type IntegrationSelection =
  | { kind: "application"; applicationId: string }
  | { kind: "system"; systemId: string }
  | { kind: "edge"; edgeKey: string };
```

Create a stable edge key from the complete non-provenance edge identity:

```text
source_kind + source + target_kind + target +
dependency_type + mechanism + direction
```

Do not use labels or array indexes as persistent navigation identity.

Required transitions:

```text
project -> integration -> job -> component
project -> group -> job -> component       (legacy/designs path)
```

Reset navigation and selection on project change. Breadcrumb jumps must restore
the selected relationship, not merely return to a blank L2 canvas.

## 6. Files to add

Frontend:

- `frontend/types/landscape.ts`
  - Exact Zod schemas mirroring `ProjectLandscapeResponse`.
  - Export inferred TypeScript types.
- `frontend/app/api/projects/[id]/landscape/route.ts`
  - Cookie-forwarding proxy matching the existing graph route.
- `frontend/hooks/useProjectLandscape.ts`
  - React Query hook.
  - Typed 404/unavailable error distinct from auth/server/schema failures.
- `frontend/lib/landscapeTransform.ts`
  - Pure L1 transform, filtering, edge-key generation, layout input, and
    evidence indexing.
- `frontend/lib/integrationDetailTransform.ts`
  - Pure edge/application/system selection and L2 flow construction.
- `frontend/components/graph/LandscapeApplicationNode.tsx`
- `frontend/components/graph/LandscapeSystemNode.tsx`
- `frontend/components/graph/LandscapeEdge.tsx`
- `frontend/components/graph/LandscapeEvidencePanel.tsx`
- `frontend/components/graph/IntegrationDetailCanvas.tsx`
- `frontend/components/graph/IntegrationEvidencePanel.tsx`
- `frontend/components/graph/LandscapeDiagnosticsDrawer.tsx`

Tests:

- `frontend/tests/hooks/useProjectLandscape.test.ts`
- `frontend/tests/lib/landscapeTransform.test.ts`
- `frontend/tests/lib/integrationDetailTransform.test.ts`
- `frontend/tests/components/LandscapeApplicationNode.test.tsx`
- `frontend/tests/components/LandscapeSystemNode.test.tsx`
- `frontend/tests/components/LandscapeEvidencePanel.test.tsx`
- `frontend/tests/components/IntegrationDetailCanvas.test.tsx`

## 7. Files to modify

- `frontend/app/topology/page.tsx`
  - Fetch landscape data.
  - Make scanner-computed landscape the root canvas.
  - Mount L2 canvas and selection panels.
  - Retain current project/group canvas for legacy fallback and Designs tab.
  - Preserve health, focus, lineage, and viewport behavior.
- `frontend/hooks/useTopologyNavigation.ts`
  - Add integration selection and exact source-root-aware navigation.
- `frontend/components/graph/TopologyBreadcrumb.tsx`
  - Render Integration labels without exposing opaque IDs.
- `frontend/components/graph/GraphToolbar.tsx`
  - Add L1 filters/statistics; do not overload existing group controls.
- `frontend/components/graph/GraphLegend.tsx`
  - Add application/system and dependency/mechanism legend variants.
- `frontend/types/index.ts`
  - Re-export landscape contracts.
- `frontend/lib/nodeStyles.ts`
  - Add semantic landscape colors while preserving the calm existing visual
    language.

Prefer extracting a `ProjectDesignsCanvas` from the current large
`page.tsx` rather than adding another several hundred lines to that file. This
refactor must be behavior-preserving and covered by existing tests.

## 8. Implementation sequence

### Slice F1 — Contract and proxy

1. Add the proxy route.
2. Add exact Zod schemas and exports.
3. Add the React Query hook with explicit 404 handling.
4. Test success, 404 fallback, auth failure, and schema rejection.

Exit condition: the browser can fetch and validate the real `topology`
landscape response without rendering it.

### Slice F2 — Pure L1 graph

1. Implement stable node/edge IDs and pure transform.
2. Add application/system nodes and semantic edges.
3. Add dagre layout and filters.
4. Add summary/diagnostics presentation.
5. Test the transform against a reduced fixture shaped like the real scan.

Exit condition: a standalone canvas renders the complete L1 payload with
correct counts and no channel/interface explosion.

### Slice F3 — Integrate L1 into `/topology`

1. Make L1 the default root when landscape data exists.
2. Preserve current graph as the 404 fallback.
3. Add selection evidence panel.
4. Add application health rollup by exact source-root mapping.
5. Reset correctly on project changes.

Exit condition: `/topology` visibly opens on applications/systems for the
`topology` project while older projects still work.

### Slice F4 — L2 integration detail

1. Add edge-centric detail transform and canvas.
2. Add relationship chooser for application/system entry.
3. Attach exact provenance, channel, interface, descriptor, diagnostic, and
   unmapped evidence.
4. Add tabs and bounded record presentation.
5. Add artifact -> existing job graph transition.

Exit condition: Maximo/AssetCheck-style flows can be followed from an L1
relationship to the exact implementing Talend jobs/routes.

### Slice F5 — Navigation and regression hardening

1. Extend breadcrumbs and back/jump behavior.
2. Preserve L3/L4 lineage selection across navigation.
3. Verify runtime overlay, focus, search, minimap, mobile sheets, and legacy
   group expansion.
4. Add page-level interaction tests.

Exit condition: both navigation paths work:

```text
Landscape -> Integration -> Job -> Mapping
Legacy groups -> Job -> Mapping
```

### Slice F6 — Real-data visual acceptance and deployment

1. Rescan the `topology` project if its checked-in/output landscape is stale.
2. Validate counts against `landscape.json`.
3. Test the 14-application/123+ system composite in desktop and mobile widths.
4. Inspect an application, system, data edge, JMS integration edge, channel,
   interface, diagnostic, and unmapped record.
5. Run automated validation.
6. Rebuild/restart frontend and visually verify the deployed `/topology`.

## 9. Required tests

Pure logic:

- Exact backend schema accepts the real payload.
- IDs containing spaces, slashes, colons, and ZIP suffixes remain opaque.
- Same display labels do not merge different IDs.
- Different dependency types between the same endpoints remain distinct.
- Endpoint/interface evidence does not create a peer edge.
- Placeholder/redacted channels do not create producer/consumer joins.
- Application/system/edge selections return only exact related evidence.
- L2 record grouping preserves underlying record counts.
- Health rolls up only by exact source root.

Navigation:

- Starts at L1.
- L1 edge -> L2 -> artifact -> L3 -> mapping -> L4.
- Breadcrumb jumps preserve selection.
- Project change resets all deeper state.
- 404 landscape uses legacy group view.
- Auth/server/schema failures do not masquerade as legacy scans.

UI/accessibility:

- Nodes and panels expose accessible names.
- Every double-click path also has a visible button for keyboard/touch users.
- Edge/node evidence is readable without relying on color alone.
- Mobile uses bottom sheets and a bounded list for L2 details.
- Full values remain available via tooltips/panels when canvas labels truncate.

Regression:

- Existing `graphTransform`, job graph, component graph, lineage, health,
  focus, breadcrumb, and node tests remain green.
- Next production build succeeds.

## 10. Validation commands

From `RAG_pipeline/frontend`:

```bash
npm test
npm run build
```

From repository root:

```bash
docker compose up -d --build frontend
docker compose ps
```

Use the in-app browser for visual verification of:

```text
http://localhost:3000/topology
```

## 11. Acceptance criteria

The work is complete only when:

1. A project with `landscape.json` opens on true application/system L1.
2. L1 counts match the API response.
3. Every rendered L1 edge comes directly from `edges[]`.
4. Selecting an edge exposes its reliability, confidence, evidence count, and
   provenance.
5. L2 shows only exact related jobs/routes/channels/interfaces.
6. An L2 artifact opens the existing job graph with exact artifact identity.
7. Existing component/mapping lineage remains functional.
8. Diagnostics and unmapped evidence are visible rather than silently dropped.
9. Projects without landscape data retain their current topology via explicit
   rescan fallback.
10. The real composite topology remains interactive at its full scale.
11. No endpoint, host, credential, or private redaction marker is exposed.
12. Automated tests, production build, Docker health, and deployed browser
    verification pass.

## 12. Non-goals

- Chat over topology.
- Automatic application-boundary curation.
- Cross-project global aggregation outside a composite scan.
- Fuzzy system deduplication.
- Inventing peer systems from endpoints.
- Changing scanner or runtime-correlation semantics.
- Replacing React Flow or dagre during this slice.

## 13. Known risks

- The checked-in/local `topology` output can be older than the newest scanner
  code. Rescan before final acceptance.
- `page.tsx` is already large and heavily stateful. Extract new canvases and
  pure transforms; avoid embedding all L1/L2 logic directly into the page.
- Composite artifacts can share display names. Always navigate by opaque
  artifact IDs and source roots.
- Application selection can cover hundreds of channel records. Require
  relationship selection before rendering an L2 graph and disclose bounded
  list limits.
- The working tree contains concurrent unrelated changes. Do not format,
  rewrite, or revert files outside the scoped frontend plan.
