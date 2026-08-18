import ELK from "elkjs/lib/elk.bundled.js";
import type { Edge, Node } from "@xyflow/react";
import type { ArchitectureColumn } from "@/lib/architectureViewModel";

const elk = new ELK();

const COLUMN_ORDER: Record<ArchitectureColumn, number> = {
  upstream: 0,
  anchor: 1,
  downstream: 2,
  secondary: 3,
  supporting: 4,
  integration: 5,
};

export function architectureColumnRank(column: ArchitectureColumn): number {
  return COLUMN_ORDER[column];
}

/** ELK owns spacing and crossing minimisation; architectural columns remain
 * explicit partitions so layout never turns back into a free network graph. */
export async function layoutArchitectureNodes<T extends { column: ArchitectureColumn }>(
  nodes: Node<T>[],
  edges: Edge[],
): Promise<Node<T>[]> {
  const graph = await elk.layout({
    id: "architecture",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.edgeRouting": "ORTHOGONAL",
      "elk.partitioning.activate": "true",
      "elk.spacing.nodeNode": "70",
      "elk.layered.spacing.nodeNodeBetweenLayers": "170",
      "elk.layered.spacing.edgeNodeBetweenLayers": "55",
      "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
      "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
      "elk.padding": "[top=80,left=80,bottom=80,right=80]",
    },
    children: nodes.map((node) => ({
      id: node.id,
      width: Number(node.width ?? 236),
      height: Number(node.height ?? 112),
      layoutOptions: {
        "elk.partitioning.partition": String(architectureColumnRank(node.data.column)),
      },
    })),
    edges: edges.map((edge) => ({
      id: edge.id,
      sources: [edge.source],
      targets: [edge.target],
    })),
  });

  const positions = new Map(
    (graph.children ?? []).map((node) => [node.id, { x: node.x ?? 0, y: node.y ?? 0 }]),
  );
  return nodes.map((node) => ({
    ...node,
    position: positions.get(node.id) ?? node.position,
  }));
}
