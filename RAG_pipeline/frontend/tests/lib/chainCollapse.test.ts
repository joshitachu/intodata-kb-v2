import { describe, it, expect } from "vitest";
import type { Node, Edge } from "@xyflow/react";
import { collapseLinearChains, CHAIN_SUMMARY_NODE_TYPE } from "@/lib/chainCollapse";

function node(id: string, componentType = "tMap"): Node {
  return { id, type: "componentNode", position: { x: 0, y: 0 }, data: { componentType } };
}

function edge(id: string, source: string, target: string): Edge {
  return { id, source, target };
}

/** entry -> a -> b -> ... -> (n components) -> exit */
function linearChainGraph(memberCount: number): { nodes: Node[]; edges: Edge[]; memberIds: string[] } {
  const memberIds = Array.from({ length: memberCount }, (_, i) => `comp:m${i}`);
  const nodes = [node("comp:entry", "tFileInputDelimited"), ...memberIds.map((id) => node(id)), node("comp:exit", "tLogRow")];
  const chain = ["comp:entry", ...memberIds, "comp:exit"];
  const edges = chain.slice(0, -1).map((src, i) => edge(`e${i}`, src, chain[i + 1]));
  return { nodes, edges, memberIds };
}

describe("collapseLinearChains", () => {
  it("does nothing when the graph is smaller than the threshold", () => {
    const { nodes, edges } = linearChainGraph(2);
    const result = collapseLinearChains(nodes, edges, new Set(), 6);
    expect(result.nodes).toBe(nodes);
    expect(result.chains).toHaveLength(0);
  });

  it("collapses a run at or above the threshold, keeping entry/exit visible", () => {
    const { nodes, edges, memberIds } = linearChainGraph(8);
    const result = collapseLinearChains(nodes, edges, new Set(), 6);

    expect(result.chains).toHaveLength(1);
    expect(result.chains[0].memberIds).toEqual(memberIds);
    expect(result.chains[0].entryId).toBe("comp:entry");
    expect(result.chains[0].exitId).toBe("comp:exit");

    const ids = result.nodes.map((n) => n.id);
    expect(ids).toContain("comp:entry");
    expect(ids).toContain("comp:exit");
    expect(ids).toContain(result.chains[0].id);
    for (const m of memberIds) expect(ids).not.toContain(m);

    const summary = result.nodes.find((n) => n.id === result.chains[0].id)!;
    expect(summary.type).toBe(CHAIN_SUMMARY_NODE_TYPE);
    expect((summary.data as { count: number }).count).toBe(8);

    // exactly 2 edges survive: entry -> chain, chain -> exit
    expect(result.edges).toHaveLength(2);
    expect(result.edges.find((e) => e.source === "comp:entry")!.target).toBe(result.chains[0].id);
    expect(result.edges.find((e) => e.target === "comp:exit")!.source).toBe(result.chains[0].id);
  });

  it("does not collapse a run shorter than the threshold", () => {
    const { nodes, edges } = linearChainGraph(4);
    const result = collapseLinearChains(nodes, edges, new Set(), 6);
    expect(result.chains).toHaveLength(0);
    expect(result.nodes).toHaveLength(nodes.length);
    expect(result.edges).toHaveLength(edges.length);
  });

  it("stops a chain at a branch point (out-degree > 1)", () => {
    const nodes = [node("comp:a"), node("comp:b"), node("comp:c"), node("comp:d1"), node("comp:d2")];
    const edges = [
      edge("e1", "comp:a", "comp:b"),
      edge("e2", "comp:b", "comp:c"),
      edge("e3", "comp:c", "comp:d1"), // c branches to two targets
      edge("e4", "comp:c", "comp:d2"),
    ];
    const result = collapseLinearChains(nodes, edges, new Set(), 2);
    // c has out-degree 2, so it's never "simple" and no run can absorb it or
    // pass through it - a is simple(in0/out1... wait in-degree 0 excludes it too)
    expect(result.chains).toHaveLength(0);
  });

  it("stops a chain at a merge point (in-degree > 1)", () => {
    const nodes = [node("comp:s1"), node("comp:s2"), node("comp:merge"), node("comp:after")];
    const edges = [
      edge("e1", "comp:s1", "comp:merge"),
      edge("e2", "comp:s2", "comp:merge"), // merge has in-degree 2
      edge("e3", "comp:merge", "comp:after"),
    ];
    const result = collapseLinearChains(nodes, edges, new Set(), 1);
    expect(result.chains).toHaveLength(0);
  });

  it("leaves an explicitly expanded chain fully visible", () => {
    const { nodes, edges, memberIds } = linearChainGraph(8);
    const detect = collapseLinearChains(nodes, edges, new Set(), 6);
    const chainId = detect.chains[0].id;

    const expanded = collapseLinearChains(nodes, edges, new Set([chainId]), 6);
    const ids = expanded.nodes.map((n) => n.id);
    for (const m of memberIds) expect(ids).toContain(m);
    expect(ids).not.toContain(chainId);
    expect(expanded.edges).toHaveLength(edges.length);
  });

  it("retargets both edges of a parallel control+data pair at the chain boundary", () => {
    const { nodes, edges } = linearChainGraph(6);
    // add a second, parallel edge from entry straight into the first member
    // (e.g. a control-flow trigger alongside the data-flow row)
    edges.push(edge("trigger", "comp:entry", "comp:m0"));

    const result = collapseLinearChains(nodes, edges, new Set(), 6);
    const chainId = result.chains[0].id;
    const fromEntry = result.edges.filter((e) => e.source === "comp:entry" && e.target === chainId);
    expect(fromEntry).toHaveLength(2);
  });

  it("never infinite-loops on an isolated cycle among otherwise-simple nodes", () => {
    const nodes = [node("comp:x"), node("comp:y"), node("comp:z")];
    const edges = [
      edge("e1", "comp:x", "comp:y"),
      edge("e2", "comp:y", "comp:z"),
      edge("e3", "comp:z", "comp:x"),
    ];
    const result = collapseLinearChains(nodes, edges, new Set(), 2);
    // every node in the cycle has both an in-chain predecessor and
    // successor, so none of them qualifies as a run start - nothing collapses.
    expect(result.chains).toHaveLength(0);
    expect(result.nodes).toHaveLength(3);
  });
});
