import { describe, it, expect } from "vitest";
import type { Node, Edge } from "@xyflow/react";
import {
  applyDagreLayout,
  applyCompoundDagreLayout,
  applyConnectionVisibility,
  deflattenOversizedRanks,
  manifestToFlow,
  manifestToCollapsedFlow,
  getDimensionsForId,
  MAX_NODES_PER_FLAT_RANK,
  type ManifestData,
} from "@/lib/graphTransform";

function jobNode(id: string): Node {
  return { id: `job:${id}`, type: "jobNode", position: { x: 0, y: 0 }, data: { label: id } };
}

function connNode(id: string): Node {
  return { id: `conn:${id}`, type: "connectionNode", position: { x: 0, y: 0 }, data: { label: id } };
}

describe("deflattenOversizedRanks", () => {
  it("leaves a normal-size rank untouched", () => {
    const nodes: Node[] = Array.from({ length: 5 }, (_, i) => ({
      ...jobNode(`n${i}`),
      position: { x: i * 100, y: 0 },
    }));
    const result = deflattenOversizedRanks(nodes, "TB", false, 30, 80);
    expect(result).toEqual(nodes);
  });

  it("wraps an oversized rank into a grid instead of one endless line", () => {
    const count = 71; // the reported "bra" group size
    const nodes: Node[] = Array.from({ length: count }, (_, i) => ({
      ...jobNode(`n${i}`),
      position: { x: i * 150, y: 0 }, // all on rank y=0 - dagre's real output
      // when a large set of nodes has no edges between them
    }));

    const result = deflattenOversizedRanks(nodes, "TB", false, 30, 80);
    expect(result).toHaveLength(count);

    // No longer one flat line: multiple distinct y (rank) values now exist.
    const distinctY = new Set(result.map((n) => n.position.y));
    expect(distinctY.size).toBeGreaterThan(1);
    expect(distinctY.size).toBe(Math.ceil(count / MAX_NODES_PER_FLAT_RANK));

    // Every row holds at most the max-per-row count.
    const perRow = new Map<number, number>();
    for (const n of result) {
      perRow.set(n.position.y, (perRow.get(n.position.y) ?? 0) + 1);
    }
    for (const rowCount of perRow.values()) {
      expect(rowCount).toBeLessThanOrEqual(MAX_NODES_PER_FLAT_RANK);
    }

    // Order along the row is preserved (dagre's chosen left-to-right order).
    const firstRow = result.filter((n) => n.position.y === 0).sort((a, b) => a.position.x - b.position.x);
    expect(firstRow.map((n) => n.id)).toEqual(nodes.slice(0, MAX_NODES_PER_FLAT_RANK).map((n) => n.id));
  });

  it("wraps each cluster independently, never mixing two groups' members into one bucket", () => {
    const groupA: Node[] = Array.from({ length: 20 }, (_, i) => ({
      ...jobNode(`a${i}`),
      position: { x: i * 100, y: 0 },
    }));
    const groupB: Node[] = Array.from({ length: 20 }, (_, i) => ({
      ...jobNode(`b${i}`),
      position: { x: i * 100, y: 0 }, // same rank coordinate as group A
    }));
    const clusterOf = new Map<string, string>();
    for (const n of groupA) clusterOf.set(n.id, "groupA");
    for (const n of groupB) clusterOf.set(n.id, "groupB");

    // Group A's own layout, computed in isolation, is the ground truth for
    // what its wrap "should" look like with no knowledge of group B at all.
    const aAlone = deflattenOversizedRanks(groupA, "TB", false, 30, 80, new Map(
      groupA.map((n) => [n.id, "groupA"]),
    ));

    const combined = deflattenOversizedRanks([...groupA, ...groupB], "TB", false, 30, 80, clusterOf);
    const aFromCombined = combined
      .filter((n) => n.id.startsWith("job:a"))
      .sort((a, b) => a.id.localeCompare(b.id, undefined, { numeric: true }));
    const aAloneSorted = [...aAlone].sort((a, b) => a.id.localeCompare(b.id, undefined, { numeric: true }));

    // Group B being wrapped alongside A must not change A's own positions at
    // all - that's what `clusterOf` scoping guarantees.
    expect(aFromCombined.map((n) => n.position)).toEqual(aAloneSorted.map((n) => n.position));
  });
});

describe("applyConnectionVisibility", () => {
  const nodes: Node[] = [jobNode("A"), jobNode("B"), connNode("LogDB")];
  const edges: Edge[] = [
    { id: "calls:A:B", source: "job:A", target: "job:B", data: { edgeType: "calls" } },
    { id: "uses:A:LogDB", source: "job:A", target: "conn:LogDB", data: { edgeType: "uses" } },
    { id: "uses:B:LogDB", source: "job:B", target: "conn:LogDB", data: { edgeType: "uses" } },
  ];

  it("returns the graph untouched when connections are shown", () => {
    const result = applyConnectionVisibility(nodes, edges, true);
    expect(result.nodes).toBe(nodes);
    expect(result.edges).toBe(edges);
  });

  it("drops connection nodes AND every edge touching one, leaving no dangling edges", () => {
    const { nodes: vNodes, edges: vEdges } = applyConnectionVisibility(nodes, edges, false);

    // No connection nodes survive.
    expect(vNodes.some((n) => n.id.startsWith("conn:"))).toBe(false);
    expect(vNodes.map((n) => n.id)).toEqual(["job:A", "job:B"]);

    // Only the job->job call edge remains.
    expect(vEdges.map((e) => e.id)).toEqual(["calls:A:B"]);

    // Every remaining edge has both endpoints present in the node set.
    const ids = new Set(vNodes.map((n) => n.id));
    for (const e of vEdges) {
      expect(ids.has(e.source)).toBe(true);
      expect(ids.has(e.target)).toBe(true);
    }
  });
});

describe("project-level edges use curved (smoothstep) routing", () => {
  const manifest: ManifestData = {
    job_calls: { A: ["B"] },
    job_called_by: { B: ["A"] },
    job_connections_used: { A: ["LogDB"] },
    connection_used_by: { LogDB: ["A"] },
    job_groups: { A: "grp1", B: "grp1" },
  };

  it("manifestToFlow emits smoothstep for both call and connection edges", () => {
    const { edges } = manifestToFlow(manifest, [], undefined);
    expect(edges.length).toBeGreaterThan(0);
    for (const e of edges) {
      expect(e.type).toBe("smoothstep");
    }
  });

  it("manifestToCollapsedFlow (expanded) emits smoothstep edges", () => {
    const { edges } = manifestToCollapsedFlow(manifest, [], undefined, new Set(["grp1"]));
    expect(edges.length).toBeGreaterThan(0);
    for (const e of edges) {
      expect(e.type).toBe("smoothstep");
    }
  });
});

describe("manifestToCollapsedFlow aggregates group->system edges in the overview", () => {
  it("(a) a collapsed group with 2 jobs both using MSSQL emits ONE group->MSSQL edge", () => {
    const manifest: ManifestData = {
      job_calls: {},
      job_called_by: {},
      job_connections_used: { JobA: ["MSSQL"], JobB: ["MSSQL"] },
      connection_used_by: { MSSQL: ["JobA", "JobB"] },
      job_groups: { JobA: "grp1", JobB: "grp1" },
    };
    // No expanded groups -> grp1 is collapsed.
    const { edges, nodes } = manifestToCollapsedFlow(manifest, [], undefined, new Set());

    const groupUses = edges.filter((e) => e.data?.edgeType === "group-uses");
    expect(groupUses).toHaveLength(1);
    expect(groupUses[0].id).toBe("grp-uses:grp1:MSSQL");
    expect(groupUses[0].source).toBe("group:grp1");
    expect(groupUses[0].target).toBe("conn:MSSQL");
    expect(groupUses[0].type).toBe("smoothstep");

    // No raw per-job uses edges for the collapsed group.
    expect(edges.some((e) => e.data?.edgeType === "uses")).toBe(false);
    // Connection node referenced by the aggregated edge is present.
    expect(nodes.some((n) => n.id === "conn:MSSQL")).toBe(true);
  });

  it("(b) two jobs using different systems emit one aggregated edge each", () => {
    const manifest: ManifestData = {
      job_calls: {},
      job_called_by: {},
      job_connections_used: { JobA: ["MSSQL"], JobB: ["REST_API"] },
      connection_used_by: { MSSQL: ["JobA"], REST_API: ["JobB"] },
      job_groups: { JobA: "grp1", JobB: "grp1" },
    };
    const { edges } = manifestToCollapsedFlow(manifest, [], undefined, new Set());

    const groupUses = edges.filter((e) => e.data?.edgeType === "group-uses");
    expect(groupUses.map((e) => e.id).sort()).toEqual([
      "grp-uses:grp1:MSSQL",
      "grp-uses:grp1:REST_API",
    ]);
    for (const e of groupUses) {
      expect(e.source).toBe("group:grp1");
    }
  });

  it("(c) an expanded group emits per-job `uses` edges and NOT the aggregated one", () => {
    const manifest: ManifestData = {
      job_calls: {},
      job_called_by: {},
      job_connections_used: { JobA: ["MSSQL"], JobB: ["MSSQL"] },
      connection_used_by: { MSSQL: ["JobA", "JobB"] },
      job_groups: { JobA: "grp1", JobB: "grp1" },
    };
    const { edges } = manifestToCollapsedFlow(manifest, [], undefined, new Set(["grp1"]));

    expect(edges.some((e) => e.data?.edgeType === "group-uses")).toBe(false);
    const perJob = edges.filter((e) => e.data?.edgeType === "uses");
    expect(perJob.map((e) => e.id).sort()).toEqual([
      "uses:JobA:MSSQL",
      "uses:JobB:MSSQL",
    ]);
  });

  it("(d) applyConnectionVisibility(false) drops the aggregated edges and conn nodes too", () => {
    const manifest: ManifestData = {
      job_calls: {},
      job_called_by: {},
      job_connections_used: { JobA: ["MSSQL"], JobB: ["MSSQL"] },
      connection_used_by: { MSSQL: ["JobA", "JobB"] },
      job_groups: { JobA: "grp1", JobB: "grp1" },
    };
    const { nodes, edges } = manifestToCollapsedFlow(manifest, [], undefined, new Set());
    const filtered = applyConnectionVisibility(nodes, edges, false);

    expect(filtered.edges.some((e) => e.data?.edgeType === "group-uses")).toBe(false);
    expect(filtered.nodes.some((n) => n.id.startsWith("conn:"))).toBe(false);
    // No dangling edges left behind.
    const ids = new Set(filtered.nodes.map((n) => n.id));
    for (const e of filtered.edges) {
      expect(ids.has(e.source) && ids.has(e.target)).toBe(true);
    }
  });

  it("mixed state: a connection used by an expanded and a collapsed group gets both edges but one conn node", () => {
    const manifest: ManifestData = {
      job_calls: {},
      job_called_by: {},
      job_connections_used: { JobA: ["MSSQL"], JobB: ["MSSQL"] },
      connection_used_by: { MSSQL: ["JobA", "JobB"] },
      job_groups: { JobA: "grpExpanded", JobB: "grpCollapsed" },
    };
    const { nodes, edges } = manifestToCollapsedFlow(
      manifest,
      [],
      undefined,
      new Set(["grpExpanded"]),
    );

    // Expanded group -> per-job edge; collapsed group -> one aggregated edge.
    expect(edges.some((e) => e.id === "uses:JobA:MSSQL")).toBe(true);
    expect(edges.some((e) => e.id === "grp-uses:grpCollapsed:MSSQL")).toBe(true);
    // The shared connection node renders exactly once.
    expect(nodes.filter((n) => n.id === "conn:MSSQL")).toHaveLength(1);
  });
});

describe("mixed/multi collapsed-expanded layout is always overlap-free (full relayout, not incremental patch)", () => {
  // Regression: a prior version relaid expand/collapse toggles IN PLACE
  // (existing nodes frozen at saved positions, only the new cluster placed +
  // pushed just far enough to clear the ONE hole it replaced). That is only
  // provably correct for a single toggle starting from a clean dagre layout -
  // it does not compose. Confirmed live: expanding two sibling groups in
  // sequence left both groups' boundary boxes and job labels overlapping.
  // The fix: every expand/collapse now re-runs a full `applyCompoundDagreLayout`
  // over the current node set (the same path already used on initial load),
  // rather than patching positions incrementally. These tests drive that full
  // path directly with BOTH groups expanded at once - the exact broken case.

  function rectOf(node: Node, compact: boolean) {
    const { width, height } = getDimensionsForId(node.id, compact);
    return {
      x1: node.position.x,
      y1: node.position.y,
      x2: node.position.x + width,
      y2: node.position.y + height,
    };
  }
  function overlaps(a: Node, b: Node, compact: boolean): boolean {
    const ra = rectOf(a, compact);
    const rb = rectOf(b, compact);
    return ra.x1 < rb.x2 && rb.x1 < ra.x2 && ra.y1 < rb.y2 && rb.y1 < ra.y2;
  }
  function assertNoOverlaps(nodes: Node[], compact: boolean) {
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        expect(
          overlaps(nodes[i], nodes[j], compact),
          `${nodes[i].id} overlaps ${nodes[j].id}`,
        ).toBe(false);
      }
    }
  }

  function buildManifest(): ManifestData {
    const interfaceJobs = ["I1", "I2", "I3", "I4", "I5", "I6"];
    const maximoJobs = ["M1", "M2"];
    const jobConn: Record<string, string[]> = {};
    for (const j of [...interfaceJobs, ...maximoJobs]) {
      jobConn[j] = ["LogDBConnection", "LogConnection"];
    }
    const jobGroups: Record<string, string> = {};
    for (const j of interfaceJobs) jobGroups[j] = "AssetCheck_Interface";
    for (const j of maximoJobs) jobGroups[j] = "Maximo2AssetCheck";
    return {
      job_calls: {},
      job_called_by: {},
      job_connections_used: jobConn,
      connection_used_by: {
        LogDBConnection: [...interfaceJobs, ...maximoJobs],
        LogConnection: [...interfaceJobs, ...maximoJobs],
      },
      job_groups: jobGroups,
    };
  }

  function groupMembershipFor(manifest: ManifestData): Map<string, string> {
    const map = new Map<string, string>();
    for (const [job, group] of Object.entries(manifest.job_groups)) {
      map.set(`job:${job}`, group);
    }
    return map;
  }

  it("BOTH sibling groups expanded at once (the real reported bug) has zero overlaps anywhere", () => {
    const compact = true;
    const manifest = buildManifest();
    const groupMembership = groupMembershipFor(manifest);

    const bothExpanded = manifestToCollapsedFlow(
      manifest,
      [],
      undefined,
      new Set(["AssetCheck_Interface", "Maximo2AssetCheck"]),
    );
    const v = applyConnectionVisibility(bothExpanded.nodes, bothExpanded.edges, true);
    const laid = applyCompoundDagreLayout(v.nodes, v.edges, "TB", compact, groupMembership);

    expect(laid.filter((n) => n.id.startsWith("job:"))).toHaveLength(8);
    assertNoOverlaps(laid, compact);
  });

  it("one group expanded, sibling still collapsed, is overlap-free via the same full layout path", () => {
    const compact = true;
    const manifest = buildManifest();
    const groupMembership = groupMembershipFor(manifest);

    const mixed = manifestToCollapsedFlow(
      manifest,
      [],
      undefined,
      new Set(["AssetCheck_Interface"]),
    );
    const v = applyConnectionVisibility(mixed.nodes, mixed.edges, true);
    const laid = applyCompoundDagreLayout(v.nodes, v.edges, "TB", compact, groupMembership);

    expect(laid.find((n) => n.id === "group:Maximo2AssetCheck")).toBeDefined();
    expect(laid.filter((n) => n.id.startsWith("job:"))).toHaveLength(6);
    assertNoOverlaps(laid, compact);
  });

  it("sequential toggles (expand A, then also expand B) recomputed fresh each time stay overlap-free", () => {
    // Simulates the real page.tsx flow: every toggle re-runs the full layout
    // from the CURRENT expandedGroups set - never patches the previous result.
    const compact = true;
    const manifest = buildManifest();
    const groupMembership = groupMembershipFor(manifest);

    for (const expanded of [
      new Set<string>(),
      new Set(["AssetCheck_Interface"]),
      new Set(["AssetCheck_Interface", "Maximo2AssetCheck"]),
      new Set(["Maximo2AssetCheck"]),
    ]) {
      const flow = manifestToCollapsedFlow(manifest, [], undefined, expanded);
      const v = applyConnectionVisibility(flow.nodes, flow.edges, true);
      const hasJobNodes = v.nodes.some((n) => n.id.startsWith("job:"));
      const laid = hasJobNodes
        ? applyCompoundDagreLayout(v.nodes, v.edges, "TB", compact, groupMembership)
        : applyDagreLayout(v.nodes, v.edges, "TB", compact);
      assertNoOverlaps(laid, compact);
    }
  });
});

describe("applyDagreLayout with many loosely-connected nodes", () => {
  it("does not produce one flat rank for 71 disconnected job nodes", () => {
    const nodes: Node[] = Array.from({ length: 71 }, (_, i) => jobNode(`j${i}`));
    const edges: Edge[] = []; // no calls between them - the real "bra" shape

    const laid = applyDagreLayout(nodes, edges, "TB", false);
    const distinctY = new Set(laid.map((n) => Math.round(n.position.y)));
    expect(distinctY.size).toBeGreaterThan(1);
  });
});
