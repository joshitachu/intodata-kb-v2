import { describe, it, expect } from "vitest";
import {
  componentGraphToFlow,
  applyComponentGraphLayout,
  tableNodeId,
  columnHandleId,
  TABLE_SOURCE_HANDLE,
  tableNodeHeight,
  TABLE_NODE_WIDTH,
  type TableNodeData,
} from "@/lib/componentGraphTransform";
import {
  ComponentGraphResponseSchema,
  type ComponentGraphResponse,
} from "@/types/componentGraph";

function fixture(): ComponentGraphResponse {
  return ComponentGraphResponseSchema.parse({
    project_id: "proj1",
    job_name: "sjItem_OffRamp_DB",
    component_unique_name: "tMap_1",
    input_tables: [
      { table_name: "row17", columns: [], source_component: "tFixedFlowInput_1" },
    ],
    var_table: {
      table_name: "Var",
      columns: ["Message"],
      source_component: null,
    },
    output_tables: [
      { table_name: "out1", columns: ["logMessage", "logId"], source_component: null },
    ],
    edges: [
      {
        target_table: "Var",
        target_column: "Message",
        expression: "row17.message.trim()",
        resolved_refs: [],
        confidence: "opaque",
      },
      {
        target_table: "out1",
        target_column: "logMessage",
        expression: "Var.Message",
        resolved_refs: [],
        confidence: "opaque",
      },
      {
        target_table: "out1",
        target_column: "logId",
        expression: "((String)globalMap.get(row17.id))",
        resolved_refs: [],
        confidence: "opaque",
      },
    ],
  });
}

describe("ComponentGraphResponseSchema", () => {
  it("parses a valid response and applies defaults", () => {
    const parsed = ComponentGraphResponseSchema.parse({
      project_id: "proj1",
      job_name: "j",
      component_unique_name: "tMap_1",
      var_table: { table_name: "Var" },
    });
    expect(parsed.input_tables).toEqual([]);
    expect(parsed.output_tables).toEqual([]);
    expect(parsed.edges).toEqual([]);
    expect(parsed.var_table.columns).toEqual([]);
  });

  it("defaults edge resolved_refs/confidence for pre-S4 responses (raw expression only)", () => {
    const parsed = ComponentGraphResponseSchema.parse({
      project_id: "proj1",
      job_name: "j",
      component_unique_name: "tMap_1",
      var_table: { table_name: "Var" },
      edges: [{ target_table: "out1", target_column: "b", expression: "row1.a" }],
    });
    expect(parsed.edges[0].resolved_refs).toEqual([]);
    expect(parsed.edges[0].confidence).toBe("opaque");
  });

  it("requires var_table (always present server-side)", () => {
    expect(() =>
      ComponentGraphResponseSchema.parse({
        project_id: "proj1",
        job_name: "j",
        component_unique_name: "tMap_1",
      }),
    ).toThrow();
  });

  it("rejects a missing required field", () => {
    expect(() =>
      ComponentGraphResponseSchema.parse({ job_name: "j" }),
    ).toThrow();
  });
});

describe("componentGraphToFlow", () => {
  it("creates one tableNode per input/Var/output table, Var always present", () => {
    const result = componentGraphToFlow(fixture());
    expect(result.nodes).toHaveLength(3);
    expect(result.stats.tableCount).toBe(3);
    for (const node of result.nodes) {
      expect(node.type).toBe("tableNode");
    }
    expect(result.nodes.map((n) => n.id)).toEqual([
      tableNodeId("row17"),
      tableNodeId("Var"),
      tableNodeId("out1"),
    ]);
  });

  it("carries kind, source_component, and columns into node data", () => {
    const result = componentGraphToFlow(fixture());
    const byId = new Map(result.nodes.map((n) => [n.id, n.data as TableNodeData]));

    const input = byId.get(tableNodeId("row17"));
    expect(input?.kind).toBe("input");
    expect(input?.sourceComponent).toBe("tFixedFlowInput_1");
    expect(input?.columns).toEqual([]);

    const varTable = byId.get(tableNodeId("Var"));
    expect(varTable?.kind).toBe("var");
    expect(varTable?.columns).toEqual([
      { name: "Message", expression: "row17.message.trim()" },
    ]);

    const output = byId.get(tableNodeId("out1"));
    expect(output?.kind).toBe("output");
    expect(output?.columns).toEqual([
      { name: "logMessage", expression: "Var.Message" },
      { name: "logId", expression: "((String)globalMap.get(row17.id))" },
    ]);
  });

  it("joins multiple edges targeting the same column into one expression string", () => {
    const graph = fixture();
    graph.edges.push({
      target_table: "out1",
      target_column: "logMessage",
      expression: "fallback literal",
      resolved_refs: [],
      confidence: "opaque",
    });
    const result = componentGraphToFlow(graph);
    const output = result.nodes.find((n) => n.id === tableNodeId("out1"))
      ?.data as TableNodeData;
    expect(output.columns[0].expression).toBe("Var.Message | fallback literal");
  });

  it("fans a Var-targeting mapping out from every input table only (never Var itself)", () => {
    const result = componentGraphToFlow(fixture());
    const varEdges = result.edges.filter((e) => e.target === tableNodeId("Var"));
    expect(varEdges).toHaveLength(1);
    expect(varEdges[0].source).toBe(tableNodeId("row17"));
    expect(varEdges[0].sourceHandle).toBe(TABLE_SOURCE_HANDLE);
    expect(varEdges[0].targetHandle).toBe(columnHandleId("target", "Message"));
  });

  it("fans an output-targeting mapping out from input tables AND Var", () => {
    const result = componentGraphToFlow(fixture());
    const logMessageEdges = result.edges.filter(
      (e) => e.targetHandle === columnHandleId("target", "logMessage"),
    );
    expect(logMessageEdges.map((e) => e.source).sort()).toEqual(
      [tableNodeId("row17"), tableNodeId("Var")].sort(),
    );
  });

  it("never treats output tables as a candidate source (they are terminal)", () => {
    const result = componentGraphToFlow(fixture());
    expect(result.edges.some((e) => e.source === tableNodeId("out1"))).toBe(false);
  });

  it("styles unresolved (opaque) mappings distinctly and counts them", () => {
    const result = componentGraphToFlow(fixture());
    expect(result.stats.mappingCount).toBe(3);
    expect(result.stats.unresolvedMappingCount).toBe(3);
    for (const edge of result.edges) {
      expect(edge.style).toMatchObject({ strokeDasharray: "5 3" });
      expect(edge.type).toBe("mappingEdge");
    }
  });

  it("styles a resolved (certain/likely) mapping without the dashed style", () => {
    const graph = fixture();
    graph.edges[1].confidence = "certain";
    const result = componentGraphToFlow(graph);
    const resolvedEdge = result.edges.find(
      (e) => e.targetHandle === columnHandleId("target", "logMessage") && e.source === tableNodeId("Var"),
    );
    expect(resolvedEdge?.style).not.toHaveProperty("strokeDasharray");
    expect(result.stats.unresolvedMappingCount).toBe(2);
  });

  it("drops mappings targeting a table absent from the response instead of crashing", () => {
    const graph = fixture();
    graph.edges.push({
      target_table: "ghost_table",
      target_column: "x",
      expression: "ghost",
      resolved_refs: [],
      confidence: "opaque",
    });
    const result = componentGraphToFlow(graph);
    expect(result.edges.some((e) => e.data?.expression === "ghost")).toBe(false);
  });

  it("draws no fan-out edges for a Var mapping when there are no input tables", () => {
    const graph = fixture();
    graph.input_tables = [];
    const result = componentGraphToFlow(graph);
    expect(result.edges.some((e) => e.target === tableNodeId("Var"))).toBe(false);
    // The expression is still visible on the column itself.
    const varTable = result.nodes.find((n) => n.id === tableNodeId("Var"))
      ?.data as TableNodeData;
    expect(varTable.columns[0].expression).toBe("row17.message.trim()");
  });
});

describe("tableNodeHeight", () => {
  it("grows with column count and floors at the minimum height", () => {
    expect(tableNodeHeight(0)).toBeGreaterThan(0);
    expect(tableNodeHeight(5)).toBeGreaterThan(tableNodeHeight(1));
  });
});

describe("applyComponentGraphLayout", () => {
  it("assigns non-overlapping finite dagre positions to every table node", () => {
    const { nodes, edges } = componentGraphToFlow(fixture());
    const laid = applyComponentGraphLayout(nodes, edges);

    expect(laid).toHaveLength(nodes.length);
    const seen = new Set<string>();
    for (const node of laid) {
      expect(Number.isFinite(node.position.x)).toBe(true);
      expect(Number.isFinite(node.position.y)).toBe(true);
      seen.add(`${node.position.x},${node.position.y}`);
    }
    expect(seen.size).toBe(nodes.length);
  });

  it("ranks the output table to the right of the input table (LR)", () => {
    const { nodes, edges } = componentGraphToFlow(fixture());
    const laid = applyComponentGraphLayout(nodes, edges);
    const pos = new Map(laid.map((n) => [n.id, n.position]));
    const input = pos.get(tableNodeId("row17"));
    const output = pos.get(tableNodeId("out1"));
    expect(input).toBeDefined();
    expect(output).toBeDefined();
    expect(output!.x).toBeGreaterThan(input!.x);
  });

  it("exports the node width used for dagre sizing", () => {
    expect(TABLE_NODE_WIDTH).toBeGreaterThan(0);
  });
});
