import { describe, it, expect } from "vitest";
import {
  jobGraphToFlow,
  applyJobGraphLayout,
  isControlFlowEdge,
  classifyComponentDoubleClick,
  componentConnectors,
  ROLE_COLORS,
  COMPONENT_NODE_WIDTH,
  COMPONENT_NODE_HEIGHT,
  type ComponentNodeData,
} from "@/lib/jobGraphTransform";
import { JobGraphResponseSchema, type JobGraphResponse } from "@/types/jobGraph";

function fixture(): JobGraphResponse {
  return JobGraphResponseSchema.parse({
    job_name: "sjItem_OffRamp_DB",
    group: "Item",
    nodes: [
      {
        unique_name: "tFixedFlowInput_1",
        component_type: "tFixedFlowInput",
        role: "input",
        has_subgraph: false,
        subjob_target: null,
        connection_ref: "",
        param_summary: { LINE_COUNT: 1 },
        has_code: false,
      },
      {
        unique_name: "tMap_1",
        component_type: "tMap",
        role: "transform",
        has_subgraph: true,
        subjob_target: null,
        connection_ref: "",
        param_summary: {},
        has_code: false,
      },
      {
        unique_name: "tDBOutput_1",
        component_type: "tDBOutput",
        role: "output",
        has_subgraph: false,
        subjob_target: null,
        connection_ref: "BigQuery_Prod",
        param_summary: { TABLE: "items" },
        has_code: false,
        description: "Inserts rows into table Items in MSSQL",
      },
      {
        unique_name: "tRunJob_1",
        component_type: "tRunJob",
        role: "orchestration",
        has_subgraph: false,
        subjob_target: "sjItem_Child",
        connection_ref: "",
        param_summary: {},
        has_code: false,
      },
      {
        unique_name: "tJava_1",
        component_type: "tJava",
        role: "logging",
        has_subgraph: false,
        subjob_target: null,
        connection_ref: "",
        param_summary: {},
        has_code: true,
      },
    ],
    edges: [
      {
        name: "row1",
        source: "tFixedFlowInput_1",
        target: "tMap_1",
        connector_name: "FLOW",
        line_style: 0,
      },
      {
        name: "lookup1",
        source: "tFixedFlowInput_1",
        target: "tMap_1",
        connector_name: "LOOKUP",
        line_style: 0,
      },
      {
        name: "out1",
        source: "tMap_1",
        target: "tDBOutput_1",
        connector_name: "FLOW",
        line_style: 0,
      },
      {
        name: "OnComponentOk1",
        source: "tDBOutput_1",
        target: "tRunJob_1",
        connector_name: "COMPONENT_OK",
        line_style: 3,
      },
      {
        name: "RunIf1",
        source: "tRunJob_1",
        target: "tJava_1",
        connector_name: "RUN_IF",
        line_style: 5,
      },
    ],
    tmap_tables: {
      tMap_1: { input_tables: ["row1", "lookup1"], output_tables: ["out1"] },
    },
  });
}

describe("JobGraphResponseSchema", () => {
  it("parses a valid response and applies defaults", () => {
    const parsed = JobGraphResponseSchema.parse({
      job_name: "j",
      nodes: [
        {
          unique_name: "tMap_1",
          component_type: "tMap",
          role: "transform",
          has_subgraph: true,
          has_code: false,
        },
      ],
      edges: [],
    });
    expect(parsed.group).toBeNull();
    expect(parsed.tmap_tables).toEqual({});
    expect(parsed.nodes[0].subjob_target).toBeNull();
    expect(parsed.nodes[0].param_summary).toEqual({});
  });

  it("rejects an unknown role", () => {
    expect(() =>
      JobGraphResponseSchema.parse({
        job_name: "j",
        nodes: [
          {
            unique_name: "x",
            component_type: "tX",
            role: "banana",
            has_subgraph: false,
            has_code: false,
          },
        ],
        edges: [],
      }),
    ).toThrow();
  });

  it("rejects a missing required field", () => {
    expect(() =>
      JobGraphResponseSchema.parse({ job_name: "j", nodes: [] }),
    ).toThrow();
  });
});

describe("isControlFlowEdge", () => {
  it("treats FLOW and LOOKUP rows with line_style 0 as data flow", () => {
    expect(isControlFlowEdge("FLOW", 0)).toBe(false);
    expect(isControlFlowEdge("LOOKUP", 0)).toBe(false);
    expect(isControlFlowEdge("MERGE", 0)).toBe(false);
  });

  it("treats non-zero line_style as control flow regardless of connector", () => {
    expect(isControlFlowEdge("FLOW", 1)).toBe(true);
    expect(isControlFlowEdge("COMPONENT_OK", 3)).toBe(true);
    expect(isControlFlowEdge("RUN_IF", 5)).toBe(true);
  });

  it("treats trigger connectors as control flow even at line_style 0", () => {
    expect(isControlFlowEdge("COMPONENT_ERROR", 0)).toBe(true);
    expect(isControlFlowEdge("SUBJOB_OK", 0)).toBe(true);
    expect(isControlFlowEdge("ITERATE", 0)).toBe(true);
  });
});

describe("jobGraphToFlow", () => {
  it("creates one componentNode per component (node count == component count)", () => {
    const result = jobGraphToFlow(fixture());
    expect(result.nodes).toHaveLength(5);
    expect(result.stats.componentCount).toBe(5);
    for (const node of result.nodes) {
      expect(node.id.startsWith("comp:")).toBe(true);
      expect(node.type).toBe("componentNode");
    }
  });

  it("classifies data vs control edges and styles them distinctly", () => {
    const result = jobGraphToFlow(fixture());
    expect(result.edges).toHaveLength(5);
    expect(result.stats.dataFlowEdgeCount).toBe(3);
    expect(result.stats.lookupFlowEdgeCount).toBe(1);
    expect(result.stats.controlFlowEdgeCount).toBe(2);

    const dataEdges = result.edges.filter((e) => e.data?.edgeType === "data");
    const lookupEdges = result.edges.filter((e) => e.data?.edgeType === "lookup");
    const controlEdges = result.edges.filter((e) => e.data?.edgeType === "control");
    expect(dataEdges).toHaveLength(2);
    expect(lookupEdges).toHaveLength(1);
    expect(controlEdges).toHaveLength(2);

    for (const edge of dataEdges) {
      expect(edge.style?.strokeDasharray).toBeUndefined();
      expect(edge.animated).toBe(false);
      expect(edge.label).toBeUndefined();
    }
    for (const edge of controlEdges) {
      expect(edge.style?.strokeDasharray).toBe("6 3");
      expect(edge.animated).toBe(false);
      expect(edge.label).not.toBe(edge.data?.connectorName);
    }
  });

  it("maps edge endpoints onto comp-prefixed node ids", () => {
    const result = jobGraphToFlow(fixture());
    const flowEdge = result.edges.find((e) => e.data?.rowName === "row1");
    expect(flowEdge?.source).toBe("comp:tFixedFlowInput_1");
    expect(flowEdge?.target).toBe("comp:tMap_1");
  });

  it("drops edges referencing unknown components instead of crashing", () => {
    const graph = fixture();
    graph.edges.push({
      name: "ghost",
      source: "tGhost_1",
      target: "tMap_1",
      connector_name: "FLOW",
      line_style: 0,
      explanation: "",
    });
    const result = jobGraphToFlow(graph);
    expect(result.edges.some((e) => e.data?.rowName === "ghost")).toBe(false);
  });

  it("forwards has_subgraph, subjob_target, connector badge and has_code into node data", () => {
    const result = jobGraphToFlow(fixture());
    const byId = new Map(result.nodes.map((n) => [n.id, n.data as ComponentNodeData]));

    const tMap = byId.get("comp:tMap_1");
    expect(tMap?.hasSubgraph).toBe(true);
    expect(tMap?.subjobTarget).toBeNull();

    const tRunJob = byId.get("comp:tRunJob_1");
    expect(tRunJob?.subjobTarget).toBe("sjItem_Child");

    const tDBOutput = byId.get("comp:tDBOutput_1");
    expect(tDBOutput?.connectionRef).toBe("BigQuery_Prod");

    const tJava = byId.get("comp:tJava_1");
    expect(tJava?.hasCode).toBe(true);

    expect(byId.get("comp:tFixedFlowInput_1")?.color).toEqual(ROLE_COLORS.input);
  });

  it("forwards the component description (empty string when absent)", () => {
    const result = jobGraphToFlow(fixture());
    const byId = new Map(result.nodes.map((n) => [n.id, n.data as ComponentNodeData]));
    expect(byId.get("comp:tDBOutput_1")?.description).toBe(
      "Inserts rows into table Items in MSSQL",
    );
    // A node with no doc row defaults to "" (anti-fabrication).
    expect(byId.get("comp:tMap_1")?.description).toBe("");
  });

  it("attaches tMap table counts as badge data", () => {
    const result = jobGraphToFlow(fixture());
    const tMap = result.nodes.find((n) => n.id === "comp:tMap_1")
      ?.data as ComponentNodeData;
    expect(tMap.inputTableCount).toBe(2);
    expect(tMap.outputTableCount).toBe(1);

    const other = result.nodes.find((n) => n.id === "comp:tJava_1")
      ?.data as ComponentNodeData;
    expect(other.inputTableCount).toBe(0);
    expect(other.outputTableCount).toBe(0);
  });

  it("stringifies param_summary values for display", () => {
    const result = jobGraphToFlow(fixture());
    const input = result.nodes.find((n) => n.id === "comp:tFixedFlowInput_1")
      ?.data as ComponentNodeData;
    expect(input.paramSummary).toEqual({ LINE_COUNT: "1" });
  });
});

describe("classifyComponentDoubleClick", () => {
  function makeData(overrides: Partial<ComponentNodeData> = {}): ComponentNodeData {
    const base: ComponentNodeData = {
      label: "tMap_1",
      technicalName: "tMap_1",
      componentType: "tMap",
      typeExplanation: "",
      role: "transform",
      color: ROLE_COLORS.transform,
      connectionRef: "",
      hasCode: false,
      hasSubgraph: false,
      subjobTarget: null,
      description: "",
      paramSummary: {},
      inputTableCount: 0,
      outputTableCount: 0,
    };
    return {
      ...base,
      ...overrides,
      technicalName: overrides.technicalName ?? base.technicalName,
      typeExplanation: overrides.typeExplanation ?? base.typeExplanation,
    };
  }

  it("descends to the child job for a tRunJob portal node", () => {
    const action = classifyComponentDoubleClick(
      makeData({ subjobTarget: "sjItem_Child" }),
    );
    expect(action).toEqual({ kind: "openJob", jobName: "sjItem_Child" });
  });

  it("descends to the level-3 component graph for has_subgraph nodes", () => {
    const action = classifyComponentDoubleClick(makeData({ hasSubgraph: true }));
    expect(action).toEqual({ kind: "openComponent" });
  });

  it("opens the inspector for every other component type - no dead ends", () => {
    const action = classifyComponentDoubleClick(
      makeData({ hasSubgraph: false, subjobTarget: null }),
    );
    expect(action).toEqual({ kind: "inspect" });
  });

  it("prefers the subjob hop over has_subgraph when somehow both are set", () => {
    const action = classifyComponentDoubleClick(
      makeData({ hasSubgraph: true, subjobTarget: "sjItem_Child" }),
    );
    expect(action).toEqual({ kind: "openJob", jobName: "sjItem_Child" });
  });
});

describe("componentConnectors", () => {
  const edges = fixture().edges;

  it("lists outgoing edges for a source component", () => {
    const result = componentConnectors(edges, "tFixedFlowInput_1");
    expect(result).toEqual([
      {
        direction: "outgoing",
        peer: "tMap_1",
        rowName: "row1",
        connectorName: "FLOW",
        isControlFlow: false,
      },
      {
        direction: "outgoing",
        peer: "tMap_1",
        rowName: "lookup1",
        connectorName: "LOOKUP",
        isControlFlow: false,
      },
    ]);
  });

  it("lists incoming and outgoing edges for a component in the middle of the chain", () => {
    const result = componentConnectors(edges, "tDBOutput_1");
    expect(result).toEqual([
      {
        direction: "incoming",
        peer: "tMap_1",
        rowName: "out1",
        connectorName: "FLOW",
        isControlFlow: false,
      },
      {
        direction: "outgoing",
        peer: "tRunJob_1",
        rowName: "OnComponentOk1",
        connectorName: "COMPONENT_OK",
        isControlFlow: true,
      },
    ]);
  });

  it("returns an empty list for a component with no edges", () => {
    expect(componentConnectors(edges, "tGhost_1")).toEqual([]);
  });
});

describe("applyJobGraphLayout", () => {
  it("assigns non-overlapping dagre positions to every node", () => {
    const { nodes, edges } = jobGraphToFlow(fixture());
    const laid = applyJobGraphLayout(nodes, edges, "LR");

    expect(laid).toHaveLength(nodes.length);
    const seen = new Set<string>();
    for (const node of laid) {
      expect(Number.isFinite(node.position.x)).toBe(true);
      expect(Number.isFinite(node.position.y)).toBe(true);
      seen.add(`${node.position.x},${node.position.y}`);
    }
    expect(seen.size).toBe(nodes.length);
  });

  it("ranks a downstream node to the right of its source in LR mode", () => {
    const { nodes, edges } = jobGraphToFlow(fixture());
    const laid = applyJobGraphLayout(nodes, edges, "LR");
    const pos = new Map(laid.map((n) => [n.id, n.position]));
    const source = pos.get("comp:tFixedFlowInput_1");
    const target = pos.get("comp:tMap_1");
    expect(source).toBeDefined();
    expect(target).toBeDefined();
    expect((target?.x ?? 0)).toBeGreaterThan(source?.x ?? 0);
  });

  it("exports the node dimensions used for dagre sizing", () => {
    expect(COMPONENT_NODE_WIDTH).toBeGreaterThan(0);
    expect(COMPONENT_NODE_HEIGHT).toBeGreaterThan(0);
  });
});
