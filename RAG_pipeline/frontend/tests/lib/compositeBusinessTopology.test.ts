import { describe, expect, it } from "vitest";
import {
  BusinessTopologySchema,
  ProjectLandscapeResponseSchema,
} from "@/types/landscape";
import { landscapeToFlow } from "@/lib/landscapeTransform";
import workorders_demoOrgTopology from "../fixtures/workorders_demo_org_business_topology.json";

/**
 * Proves the org-level `business_topology` merged by the backend from the REAL
 * workorders_demo composite upload (union of validated per-partition topologies + the
 * deterministic cross-project index) validates against the exact frontend Zod
 * contract the UI enforces -- the object that previously arrived as `null` and
 * failed with `expected object, received null`.
 *
 * The fixture is a verbatim copy of the emitted
 * `output/workorders_demo/topology/business_topology.json`.
 */
describe("workorders_demo composite org business_topology", () => {
  it("validates against the strict BusinessTopologySchema", () => {
    const topology = BusinessTopologySchema.parse(workorders_demoOrgTopology);
    expect(topology.metadata.project_id).toBe("workorders_demo");
    // Deterministic merge ("composite_org_merge") or the analyst-curated
    // overlay written back over it ("analyst_curated...") -- both are valid
    // org-topology sources served by _load_composite_business_topology.
    expect(topology.metadata.generated_by).toMatch(/composite_org_merge|analyst_curated|evidence_grounded/);
    expect(topology.systems.length).toBeGreaterThan(0);
    expect(topology.data_streams.length).toBeGreaterThan(0);

    // AssetSys is present as the hub system.
    const assetsys = topology.systems.find((s) => s.name === "AssetSys");
    expect(assetsys).toBeTruthy();

    // Every stream endpoint resolves to a declared system (graph integrity).
    const systemIds = new Set(topology.systems.map((system) => system.id));
    for (const stream of topology.data_streams) {
      expect(systemIds.has(stream.source_system_id)).toBe(true);
      expect(systemIds.has(stream.destination_system_id)).toBe(true);
    }

    // Evidence-grounded structure: real directional flows are split by
    // direction, so AssetSys->RHM (work orders) and RHM->AssetSys (realizations)
    // both exist as SEPARATE streams -- the whole point of the rebuild.
    const names = new Map(topology.systems.map((system) => [system.name, system.id]));
    const assetsysId = names.get("AssetSys");
    const rhmId = names.get("RHM");
    const pairs = new Set(
      topology.data_streams.map((s) => `${s.source_system_id}->${s.destination_system_id}`),
    );
    expect(assetsysId && rhmId).toBeTruthy();
    expect(pairs.has(`${assetsysId}->${rhmId}`)).toBe(true);
    expect(pairs.has(`${rhmId}->${assetsysId}`)).toBe(true);

    // Business objects are populated (they were empty in the deterministic merge).
    expect(topology.business_objects.length).toBeGreaterThan(0);
    // Streams are wired to real implementing designs for the L3/L4 drill-down.
    expect(topology.data_streams.some((s) => s.artifact_ids.length > 0)).toBe(true);
  });

  it("is a non-null object inside the ProjectLandscapeResponse contract", () => {
    // The full endpoint payload: technical facts + the merged overlay. Parsing
    // fails (as it did in production) if business_topology is null.
    const landscape = ProjectLandscapeResponseSchema.parse({
      systems: [],
      applications: [],
      edges: [],
      channels: [],
      interfaces: [],
      diagnostics: [],
      business_topology: workorders_demoOrgTopology,
      topology_state: {
        available: true,
        validated: true,
        stale: false,
        source: "composite_org_merge",
      },
      unmapped: [],
      summary: {},
    });
    expect(landscape.business_topology).not.toBeNull();

    // The UI renders the business overlay (validated + not stale) as a graph.
    const flow = landscapeToFlow(landscape);
    expect(flow.nodes.length).toBeGreaterThan(0);
    expect(flow.edges.length).toBeGreaterThan(0);
  });
});
