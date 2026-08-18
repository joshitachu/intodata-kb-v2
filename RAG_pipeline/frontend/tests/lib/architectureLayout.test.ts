import { describe, expect, it } from "vitest";
import type { Edge, Node } from "@xyflow/react";
import { architectureColumnRank, layoutArchitectureNodes } from "@/lib/architectureLayout";
import type { ArchitectureColumn } from "@/lib/architectureViewModel";

interface TestData extends Record<string, unknown> { column: ArchitectureColumn }

describe("layoutArchitectureNodes", () => {
  it("uses stable architectural column ordering in a left-to-right ELK layout", async () => {
    const nodes: Node<TestData>[] = [
      { id: "afas", position: { x: 0, y: 0 }, width: 200, height: 100, data: { column: "upstream" } },
      { id: "maximo", position: { x: 0, y: 0 }, width: 200, height: 100, data: { column: "anchor" } },
      { id: "4ps", position: { x: 0, y: 0 }, width: 200, height: 100, data: { column: "downstream" } },
      { id: "smartflow", position: { x: 0, y: 0 }, width: 200, height: 100, data: { column: "secondary" } },
    ];
    const edges: Edge[] = [
      { id: "a-m", source: "afas", target: "maximo" },
      { id: "m-4", source: "maximo", target: "4ps" },
      { id: "4-s", source: "4ps", target: "smartflow" },
    ];
    const result = await layoutArchitectureNodes(nodes, edges);
    const x = new Map(result.map((node) => [node.id, node.position.x]));

    expect(x.get("afas")!).toBeLessThan(x.get("maximo")!);
    expect(x.get("maximo")!).toBeLessThan(x.get("4ps")!);
    expect(x.get("4ps")!).toBeLessThan(x.get("smartflow")!);
    expect(architectureColumnRank("anchor")).toBeLessThan(architectureColumnRank("downstream"));
  });
});
