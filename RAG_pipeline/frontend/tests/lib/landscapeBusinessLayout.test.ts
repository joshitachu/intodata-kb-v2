import { describe, expect, it } from "vitest";
import { ProjectLandscapeResponseSchema } from "@/types/landscape";
import { findLandscapeEdge, landscapeToFlow } from "@/lib/landscapeTransform";
import werkorderOrgTopology from "../fixtures/werkorder_org_business_topology.json";

/**
 * Verifies the hub-and-spoke radial layout (`layoutBusinessLandscape`) and
 * the default infrastructure/logging filter on the REAL curated werkorder
 * fixture (16 systems, 19 data streams; Maximo `sys-maximo` is the hub;
 * `sys-log`/`sys-logdb`/`sys-monitoring`/`sys-ldap` are logging/monitoring/
 * auth sinks). Fixture-wrapping mirrors compositeBusinessTopology.test.ts.
 */
function buildLandscape(showInfrastructure?: boolean) {
  const landscape = ProjectLandscapeResponseSchema.parse({
    systems: [],
    applications: [],
    edges: [],
    channels: [],
    interfaces: [],
    diagnostics: [],
    business_topology: werkorderOrgTopology,
    topology_state: {
      available: true,
      validated: true,
      stale: false,
      source: "composite_org_merge",
    },
    unmapped: [],
    summary: {},
  });
  return { landscape, flow: landscapeToFlow(landscape, showInfrastructure === undefined ? {} : { showInfrastructure }) };
}

describe("business landscape hub-and-spoke layout", () => {
  it("places every node at a finite position", () => {
    const { flow } = buildLandscape();
    expect(flow.nodes.length).toBeGreaterThan(0);
    for (const node of flow.nodes) {
      expect(Number.isFinite(node.position.x)).toBe(true);
      expect(Number.isFinite(node.position.y)).toBe(true);
    }
  });

  it("never overlaps two system nodes (rings grow to fit their node count)", () => {
    // The real regression: Maximo has ~12 direct neighbours; a fixed-radius
    // ring crammed them into an overlapping pile. Assert no two node bounding
    // boxes intersect (a small shared border is allowed).
    const { flow } = buildLandscape(true);
    const boxes = flow.nodes.map((n) => ({
      x1: n.position.x, y1: n.position.y,
      x2: n.position.x + (n.width ?? 205), y2: n.position.y + (n.height ?? 104),
    }));
    for (let i = 0; i < boxes.length; i++) {
      for (let j = i + 1; j < boxes.length; j++) {
        const a = boxes[i], b = boxes[j];
        const overlapX = Math.min(a.x2, b.x2) - Math.max(a.x1, b.x1);
        const overlapY = Math.min(a.y2, b.y2) - Math.max(a.y1, b.y1);
        // Overlap only if BOTH axes overlap by a meaningful margin.
        expect(overlapX > 8 && overlapY > 8).toBe(false);
      }
    }
  });

  it("puts Maximo at the center of its component -- every other connected node sits at least one ring out", () => {
    // showInfrastructure so Maximo's full component (including nodes only
    // reachable through logging/monitoring edges) is present for this check.
    const { flow } = buildLandscape(true);
    const nodeById = new Map(flow.nodes.map((node) => [node.id, node]));
    const maximo = nodeById.get("sys-maximo");
    expect(maximo).toBeDefined();
    const maximoCenter = {
      x: maximo!.position.x + (maximo!.width ?? 0) / 2,
      y: maximo!.position.y + (maximo!.height ?? 0) / 2,
    };

    // Reconstruct undirected connectivity from the rendered edges to find
    // every node in Maximo's connected component.
    const adjacency = new Map<string, Set<string>>();
    for (const node of flow.nodes) adjacency.set(node.id, new Set());
    for (const edge of flow.edges) {
      adjacency.get(edge.source)?.add(edge.target);
      adjacency.get(edge.target)?.add(edge.source);
    }
    const component = new Set<string>();
    const queue = ["sys-maximo"];
    component.add("sys-maximo");
    while (queue.length > 0) {
      const current = queue.shift()!;
      for (const neighbor of adjacency.get(current) ?? []) {
        if (!component.has(neighbor)) {
          component.add(neighbor);
          queue.push(neighbor);
        }
      }
    }
    // The fixture's connectivity should pull in most of the 16 systems.
    expect(component.size).toBeGreaterThan(5);

    // Tiling only translates a whole component rigidly, so distances between
    // any two nodes in the SAME component are unaffected by which component
    // ends up where on the canvas. The hub sits at local ring 0; every other
    // node in its component is placed at least one RING_GAP (360) out.
    const RING_GAP_FLOOR = 300; // below the 360 ring gap, allows for float slack
    for (const id of component) {
      if (id === "sys-maximo") continue;
      const node = nodeById.get(id);
      if (!node) continue;
      const center = { x: node.position.x + (node.width ?? 0) / 2, y: node.position.y + (node.height ?? 0) / 2 };
      const distance = Math.hypot(center.x - maximoCenter.x, center.y - maximoCenter.y);
      expect(distance).toBeGreaterThanOrEqual(RING_GAP_FLOOR);
    }
  });

  it("is deterministic across repeated layout calls", () => {
    const { flow: first } = buildLandscape();
    const { flow: second } = buildLandscape();
    const firstById = new Map(first.nodes.map((node) => [node.id, node.position]));
    for (const node of second.nodes) {
      const priorPosition = firstById.get(node.id);
      expect(priorPosition).toBeDefined();
      expect(node.position.x).toBeCloseTo(priorPosition!.x, 6);
      expect(node.position.y).toBeCloseTo(priorPosition!.y, 6);
    }
  });
});

describe("business landscape infrastructure/logging filter", () => {
  // The evidence-grounded werkorder fixture excludes logging/monitoring/auth as
  // infrastructure UPSTREAM (at doc-extraction time), so it has no infra sink
  // systems. The infra display-filter is still generic logic, so exercise it on
  // a small synthetic topology that DOES contain an audit-log sink.
  const sys = (id: string, name: string) => ({
    id, name, aliases: [], kind: "software_system", description: "", purpose: "",
    owner: "", status: "confirmed", confidence: 0.9, evidence_ids: [],
  });
  const stream = (id: string, source: string, dest: string, name: string) => ({
    id, source_system_id: source, destination_system_id: dest, name, description: "",
    business_object_ids: [], business_summary: "", integration_solution_ids: [],
    delivery_path_id: null, status: "confirmed", confidence: 0.9, control_ids: [],
    risk_ids: [], unknown_ids: [], runtime_observation_ids: [], conflict_ids: [],
    evidence_ids: [], artifact_ids: [],
  });
  function buildSynthetic(showInfrastructure?: boolean) {
    const landscape = ProjectLandscapeResponseSchema.parse({
      systems: [], applications: [], edges: [], channels: [], interfaces: [],
      diagnostics: [], unmapped: [], summary: {},
      topology_state: { available: true, validated: true, stale: false, source: "test" },
      business_topology: {
        schema_version: "1.0", scan_fingerprint: "test",
        metadata: { project_id: "t", scan_fingerprint: "test", generated_by: "test", model_name: "" },
        systems: [sys("sys-maximo", "Maximo"), sys("sys-rhm", "RHM"), sys("sys-log", "Log")],
        integration_solutions: [], business_objects: [], data_streams: [
          stream("ds-wo", "sys-maximo", "sys-rhm", "Work orders: Maximo -> RHM"),
          stream("ds-log", "sys-maximo", "sys-log", "Writes audit log"),
        ],
        delivery_paths: [], controls: [], risks: [], unknowns: [],
        artifact_annotations: [], component_annotations: [], lineage_annotations: [],
        coverage: { total_artifacts: 0, cited_artifacts: 0, total_evidence: 0, cited_evidence: 0, coverage_ratio: 0, notes: [] },
      },
    });
    return { landscape, flow: landscapeToFlow(landscape, showInfrastructure === undefined ? {} : { showInfrastructure }) };
  }

  function streamNames(landscape: ReturnType<typeof buildSynthetic>["landscape"], flow: ReturnType<typeof buildSynthetic>["flow"]): string[] {
    return flow.edges.flatMap((edge) => {
      const data = edge.data as { streamKeys?: string[] } | undefined;
      return (data?.streamKeys ?? []).map((key) => findLandscapeEdge(landscape, key)?.businessStream?.name ?? "");
    });
  }

  it("hides the audit-log sink system and stream by default", () => {
    const { landscape, flow } = buildSynthetic();
    expect(new Set(flow.nodes.map((n) => n.id)).has("sys-log")).toBe(false);
    expect(streamNames(landscape, flow).some((n) => /audit log/i.test(n))).toBe(false);
  });

  it("reveals the audit-log sink system and stream when showInfrastructure is true", () => {
    const { landscape, flow } = buildSynthetic(true);
    expect(new Set(flow.nodes.map((n) => n.id)).has("sys-log")).toBe(true);
    expect(streamNames(landscape, flow).some((n) => /audit log/i.test(n))).toBe(true);
  });
});

/**
 * L1 readability: `businessLandscapeToFlow` must emit ONE aggregated edge per
 * directed system pair, not one per stream (the werkorder org fixture has
 * 72 raw data_streams and would otherwise render 72 crossing arrows).
 */
describe("business landscape L1 edge aggregation by system pair", () => {
  it("collapses 72 data streams into one edge per directed system pair -- far fewer than 72", () => {
    const { landscape, flow } = buildLandscape();
    const totalStreams = landscape.business_topology!.data_streams.length;
    expect(totalStreams).toBe(72);
    expect(flow.edges.length).toBeLessThan(totalStreams / 2);

    // Every aggregated edge id is a stable, distinct directed-pair key.
    expect(new Set(flow.edges.map((edge) => edge.id)).size).toBe(flow.edges.length);
    for (const edge of flow.edges) expect(edge.id.startsWith("pair:")).toBe(true);
  });

  it("aggregates all of RHM -> Smartflow's 13 streams into one edge with a business-object label", () => {
    // showInfrastructure so the two streams whose names happen to contain
    // "monitoring" (business monitoring codes, not observability plumbing)
    // aren't dropped by the unrelated infra heuristic -- this test is about
    // aggregation, not that filter.
    const { landscape, flow } = buildLandscape(true);
    const streamCount = landscape.business_topology!.data_streams.filter(
      (stream) => stream.source_system_id === "sys-rhm" && stream.destination_system_id === "sys-smartflow",
    ).length;
    expect(streamCount).toBe(13);

    const edge = flow.edges.find((item) => {
      const data = item.data as { pair?: { source: string; target: string } } | undefined;
      return data?.pair?.source === "sys-rhm" && data?.pair?.target === "sys-smartflow";
    });
    expect(edge).toBeDefined();
    const data = edge!.data as { streamKeys: string[]; pair: { source: string; target: string } };
    expect(data.streamKeys).toHaveLength(streamCount);

    // Every stream key resolves back to its own single-stream TopologyEdge
    // for the L2 chooser/detail -- aggregation never loses per-flow identity.
    const resolvedIds = data.streamKeys.map((key) => findLandscapeEdge(landscape, key)?.businessStream?.id);
    expect(resolvedIds.every(Boolean)).toBe(true);
    expect(new Set(resolvedIds).size).toBe(streamCount);

    // 13 streams carry more than 3 distinct business objects, so the label
    // truncates to 3 names plus an overflow count rather than "13 flows".
    expect(edge!.label).not.toMatch(/^\d+ flows?$/);
    expect(edge!.label).toMatch(/\+\d+$/);
    expect(String(edge!.label)).toMatch(/WorkOrder|Message|Employee/);

    // Most (not all) RHM->Smartflow streams are confirmed, so the aggregated
    // edge renders solid, not dashed.
    expect((edge!.style as { strokeDasharray?: string })?.strokeDasharray).toBeUndefined();

    // Line weight scales with how many flows are bundled into the edge.
    expect((edge!.style as { strokeWidth?: number })?.strokeWidth).toBeGreaterThan(2);
  });
});
