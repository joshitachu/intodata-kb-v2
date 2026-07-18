import { describe, it, expect } from "vitest";
import type { Node, Edge } from "@xyflow/react";
import {
  applyDagreLayout,
  deflattenOversizedRanks,
  MAX_NODES_PER_FLAT_RANK,
} from "@/lib/graphTransform";

function jobNode(id: string): Node {
  return { id: `job:${id}`, type: "jobNode", position: { x: 0, y: 0 }, data: { label: id } };
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

describe("applyDagreLayout with many loosely-connected nodes", () => {
  it("does not produce one flat rank for 71 disconnected job nodes", () => {
    const nodes: Node[] = Array.from({ length: 71 }, (_, i) => jobNode(`j${i}`));
    const edges: Edge[] = []; // no calls between them - the real "bra" shape

    const laid = applyDagreLayout(nodes, edges, "TB", false);
    const distinctY = new Set(laid.map((n) => Math.round(n.position.y)));
    expect(distinctY.size).toBeGreaterThan(1);
  });
});
