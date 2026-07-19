import { describe, it, expect } from "vitest";
import {
  confidenceStyle,
  isResolvedConfidence,
  lineageColumnKey,
  componentLineageHighlight,
  tableLineageProjection,
  buildLineageOverlayEdges,
  jobLineageHighlight,
  projectLineageHighlight,
  summarizeLineage,
  LINEAGE_EDGE_TYPE,
} from "@/lib/lineageHighlight";
import { tableNodeId, columnHandleId, TABLE_SOURCE_HANDLE } from "@/lib/componentGraphTransform";
import { LineageFieldResponseSchema, type LineageFieldResponse } from "@/types/lineage";

const JOB = "sjItem_OffRamp_DB";

// A response spanning three components in one job:
// - tFixedFlowInput_1 (row17.message) -- upstream, outside tMap_1
// - tMap_1 (Var.Message -> out1.logMessage) -- the component under test
// - tMSSqlOutput_1 (logTable.logMessage) -- downstream, outside tMap_1
// Plus one genuinely unresolved (dynamic) hop into out1.logId, with no
// resolved source - modelling a real globalMap.get(...) reference.
function fixture(): LineageFieldResponse {
  return LineageFieldResponseSchema.parse({
    project_id: "proj1",
    job_name: JOB,
    start: { job_name: JOB, table: "row17", column: "message", component: "tFixedFlowInput_1" },
    direction: "both",
    max_depth: 10,
    truncated: false,
    nodes: [
      { job_name: JOB, table: "row17", column: "message", component: "tFixedFlowInput_1" },
      { job_name: JOB, table: "Var", column: "Message", component: "tMap_1" },
      { job_name: JOB, table: "out1", column: "logMessage", component: "tMap_1" },
      { job_name: JOB, table: "logTable", column: "logMessage", component: "tMSSqlOutput_1" },
    ],
    edges: [
      {
        job_name: JOB,
        source_table: "row17",
        source_column: "message",
        source_component: "tFixedFlowInput_1",
        target_table: "Var",
        target_column: "Message",
        target_component: "tMap_1",
        confidence: "certain",
        resolved: true,
        dynamic_refs: [],
        transformations: [
          { transformation_type: "direct_map", expression: "row17.message.trim()", human_explanation: "" },
        ],
      },
      {
        job_name: JOB,
        source_table: "Var",
        source_column: "Message",
        source_component: "tMap_1",
        target_table: "out1",
        target_column: "logMessage",
        target_component: "tMap_1",
        confidence: "likely",
        resolved: true,
        dynamic_refs: [],
        transformations: [
          { transformation_type: "direct_map", expression: "Var.Message", human_explanation: "" },
        ],
      },
      {
        job_name: JOB,
        source_table: "out1",
        source_column: "logMessage",
        source_component: "tMap_1",
        target_table: "logTable",
        target_column: "logMessage",
        target_component: "tMSSqlOutput_1",
        confidence: "likely",
        resolved: true,
        dynamic_refs: [],
        transformations: [],
      },
      {
        job_name: JOB,
        source_table: "",
        source_column: "",
        source_component: "",
        target_table: "out1",
        target_column: "logId",
        target_component: "tMap_1",
        confidence: "dynamic",
        resolved: false,
        dynamic_refs: ['globalMap.get("tFileOutputDelimited_1_FILE_NAME")'],
        transformations: [
          {
            transformation_type: "dynamic",
            expression: '((String)globalMap.get("tFileOutputDelimited_1_FILE_NAME"))',
            human_explanation: "",
          },
        ],
      },
    ],
  });
}

describe("LineageFieldResponseSchema", () => {
  it("parses a full response", () => {
    expect(() => fixture()).not.toThrow();
  });

  it("applies defaults for optional fields", () => {
    const parsed = LineageFieldResponseSchema.parse({
      project_id: "proj1",
      job_name: JOB,
      start: { job_name: JOB, table: "row17", column: "message" },
      direction: "both",
      max_depth: 10,
    });
    expect(parsed.nodes).toEqual([]);
    expect(parsed.edges).toEqual([]);
    expect(parsed.truncated).toBe(false);
    expect(parsed.start.component).toBe("");
  });

  it("defaults an edge's resolved/dynamic_refs/transformations/confidence", () => {
    const parsed = LineageFieldResponseSchema.parse({
      project_id: "proj1",
      job_name: JOB,
      start: { job_name: JOB, table: "a", column: "b" },
      direction: "up",
      max_depth: 5,
      edges: [{ job_name: JOB, target_table: "a", target_column: "b" }],
    });
    expect(parsed.edges[0].confidence).toBe("opaque");
    expect(parsed.edges[0].resolved).toBe(false);
    expect(parsed.edges[0].dynamic_refs).toEqual([]);
    expect(parsed.edges[0].transformations).toEqual([]);
  });

  it("rejects a missing required field", () => {
    expect(() => LineageFieldResponseSchema.parse({ job_name: JOB })).toThrow();
  });
});

describe("confidenceStyle / isResolvedConfidence", () => {
  it("styles certain and likely as solid (no dash)", () => {
    expect(confidenceStyle("certain").dashArray).toBeUndefined();
    expect(confidenceStyle("likely").dashArray).toBeUndefined();
    expect(isResolvedConfidence("certain")).toBe(true);
    expect(isResolvedConfidence("likely")).toBe(true);
  });

  it("styles dynamic and opaque as dashed/dotted - never resolved", () => {
    expect(confidenceStyle("dynamic").dashArray).toBeDefined();
    expect(confidenceStyle("opaque").dashArray).toBeDefined();
    expect(isResolvedConfidence("dynamic")).toBe(false);
    expect(isResolvedConfidence("opaque")).toBe(false);
  });

  it("falls back to the opaque style for an unrecognized confidence value", () => {
    expect(confidenceStyle("something-new-from-a-future-backend")).toEqual(confidenceStyle("opaque"));
  });

  it("gives every confidence tier a distinct color", () => {
    const colors = new Set(
      ["certain", "likely", "dynamic", "opaque"].map((c) => confidenceStyle(c).color),
    );
    expect(colors.size).toBe(4);
  });
});

describe("lineageColumnKey", () => {
  it("joins table and column with the shared delimiter", () => {
    expect(lineageColumnKey("out1", "logMessage")).toBe("out1::logMessage");
  });
});

describe("componentLineageHighlight", () => {
  it("scopes tableIds/columnKeys to the given component only", () => {
    const highlight = componentLineageHighlight(fixture(), "tMap_1");
    expect(highlight.tableIds).toEqual(new Set([tableNodeId("Var"), tableNodeId("out1")]));
    expect(highlight.columnKeys).toEqual(new Set(["Var::Message", "out1::logMessage"]));
  });

  it("never includes a table belonging to a different component (Var-name collisions are real)", () => {
    const highlight = componentLineageHighlight(fixture(), "tMap_1");
    expect(highlight.tableIds.has(tableNodeId("row17"))).toBe(false);
    expect(highlight.tableIds.has(tableNodeId("logTable"))).toBe(false);
  });

  it("builds a resolved overlay edge only when both endpoints are in the component", () => {
    const highlight = componentLineageHighlight(fixture(), "tMap_1");
    expect(highlight.overlayEdges).toHaveLength(1);
    expect(highlight.overlayEdges[0]).toMatchObject({
      sourceTableId: tableNodeId("Var"),
      sourceColumn: "Message",
      targetTableId: tableNodeId("out1"),
      targetColumn: "logMessage",
      confidence: "likely",
      resolved: true,
      expression: "Var.Message",
    });
  });

  it("excludes a resolved hop that crosses a component boundary from the overlay edges", () => {
    const highlight = componentLineageHighlight(fixture(), "tMap_1");
    const crossComponentEdge = highlight.overlayEdges.find(
      (e) => e.targetTableId === tableNodeId("logTable"),
    );
    expect(crossComponentEdge).toBeUndefined();
  });

  it("does not mark a resolved-but-cross-component target as unresolved", () => {
    // Var.Message is fed from outside tMap_1 (row17, resolved+certain) - that
    // is a drawability gap, not a confidence problem, so it must not appear
    // in unresolvedColumns.
    const highlight = componentLineageHighlight(fixture(), "tMap_1");
    expect(highlight.unresolvedColumns.has("Var::Message")).toBe(false);
  });

  it("records a genuinely unresolved (dynamic) hop with its confidence, not as a phantom edge", () => {
    const highlight = componentLineageHighlight(fixture(), "tMap_1");
    expect(highlight.unresolvedColumns.get("out1::logId")).toBe("dynamic");
    expect(highlight.overlayEdges.some((e) => e.targetColumn === "logId")).toBe(false);
  });

  it("returns an empty highlight for an empty component name (never fabricates a match)", () => {
    const highlight = componentLineageHighlight(fixture(), "");
    expect(highlight.tableIds.size).toBe(0);
    expect(highlight.columnKeys.size).toBe(0);
    expect(highlight.overlayEdges).toEqual([]);
    expect(highlight.unresolvedColumns.size).toBe(0);
  });
});

describe("tableLineageProjection", () => {
  it("projects the component-wide highlight down to one table's own columns", () => {
    const highlight = componentLineageHighlight(fixture(), "tMap_1");
    const out1 = tableLineageProjection(highlight, "out1");
    expect(out1.touchedColumns).toEqual(new Set(["logMessage"]));
    expect(out1.unresolvedColumns).toEqual(new Map([["logId", "dynamic"]]));

    const varTable = tableLineageProjection(highlight, "Var");
    expect(varTable.touchedColumns).toEqual(new Set(["Message"]));
    expect(varTable.unresolvedColumns.size).toBe(0);
  });

  it("returns empty projections for a table outside the highlight", () => {
    const highlight = componentLineageHighlight(fixture(), "tMap_1");
    const row17 = tableLineageProjection(highlight, "row17");
    expect(row17.touchedColumns.size).toBe(0);
    expect(row17.unresolvedColumns.size).toBe(0);
  });
});

describe("buildLineageOverlayEdges", () => {
  it("anchors the source at the table-level handle and the target at its exact column handle", () => {
    const highlight = componentLineageHighlight(fixture(), "tMap_1");
    const edges = buildLineageOverlayEdges(highlight);
    expect(edges).toHaveLength(1);
    expect(edges[0]).toMatchObject({
      source: tableNodeId("Var"),
      sourceHandle: TABLE_SOURCE_HANDLE,
      target: tableNodeId("out1"),
      targetHandle: columnHandleId("target", "logMessage"),
      type: LINEAGE_EDGE_TYPE,
    });
  });

  it("styles the edge by confidence and carries the expression/label in data", () => {
    const highlight = componentLineageHighlight(fixture(), "tMap_1");
    const [edge] = buildLineageOverlayEdges(highlight);
    expect(edge.style).toMatchObject({ stroke: confidenceStyle("likely").color });
    expect(edge.data).toMatchObject({
      expression: "Var.Message",
      confidence: "likely",
      confidenceLabel: confidenceStyle("likely").label,
    });
  });

  it("produces no edges when nothing in the component resolved", () => {
    const empty = componentLineageHighlight(fixture(), "tFixedFlowInput_1");
    expect(buildLineageOverlayEdges(empty)).toEqual([]);
  });
});

describe("jobLineageHighlight", () => {
  it("collects every component touched anywhere in the path, across component boundaries", () => {
    const ids = jobLineageHighlight(fixture(), JOB);
    expect(ids).toEqual(
      new Set(["comp:tFixedFlowInput_1", "comp:tMap_1", "comp:tMSSqlOutput_1"]),
    );
  });

  it("returns an empty set for a job the response is not scoped to (never fabricates cross-job data)", () => {
    const ids = jobLineageHighlight(fixture(), "some_other_job");
    expect(ids.size).toBe(0);
  });
});

describe("projectLineageHighlight", () => {
  it("highlights exactly the one job this response covers", () => {
    expect(projectLineageHighlight(fixture())).toEqual(new Set([`job:${JOB}`]));
  });
});

describe("summarizeLineage", () => {
  it("counts hops, confidence tiers, and dynamic refs", () => {
    const summary = summarizeLineage(fixture());
    expect(summary.jobName).toBe(JOB);
    expect(summary.startTable).toBe("row17");
    expect(summary.startColumn).toBe("message");
    expect(summary.hopCount).toBe(4);
    expect(summary.confidenceCounts).toEqual({ certain: 1, likely: 2, dynamic: 1 });
    expect(summary.dynamicRefCount).toBe(1);
    expect(summary.truncated).toBe(false);
  });

  it("surfaces truncation so the UI never silently drops part of the path", () => {
    const truncatedResponse = { ...fixture(), truncated: true };
    expect(summarizeLineage(truncatedResponse).truncated).toBe(true);
  });
});
