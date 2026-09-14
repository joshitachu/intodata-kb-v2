import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { IntegrationDetailCanvas } from "@/components/graph/IntegrationDetailCanvas";
import { ProjectLandscapeResponseSchema, type BusinessDataStream, type ProjectLandscapeResponse } from "@/types/landscape";
import type { TopologyEdge } from "@/lib/landscapeTransform";

/**
 * The implementing-design panel used to render ONE bordered card per mapping,
 * each repeating the group label and a "type . origin" footer. A design with 28
 * mappings therefore printed the same two labels 28 times, over a long scroll,
 * for what is a two-column table. These tests pin the grouping: the label and
 * the origin belong to the group, only row-level exceptions repeat, and a value
 * table is headed by the field whose values it defines.
 */
vi.mock("@xyflow/react", async () => {
  const { createElement, useCallback, useState } = await import("react");
  function useNodesState(initial: unknown[]) {
    const [nodes, setNodes] = useState(initial);
    return [nodes, setNodes, useCallback(() => {}, [])];
  }
  function useEdgesState(initial: unknown[]) {
    const [edges, setEdges] = useState(initial);
    return [edges, setEdges, useCallback(() => {}, [])];
  }
  return {
    ReactFlow: (props: { children?: ReactNode }) => createElement("div", { "data-testid": "react-flow" }, props.children),
    Background: () => null,
    BackgroundVariant: { Dots: "dots", Lines: "lines", Cross: "cross" },
    Controls: () => null,
    Handle: () => null,
    MiniMap: () => null,
    MarkerType: { ArrowClosed: "arrowclosed" },
    Position: { Left: "left", Right: "right", Top: "top", Bottom: "bottom" },
    useEdgesState,
    useNodesState,
  };
});

const claim = { name: "", description: "", status: "observed" as const, confidence: 0.9, evidence_ids: ["ev:1"] };

function build(): { landscape: ProjectLandscapeResponse; edge: TopologyEdge } {
  const landscape = ProjectLandscapeResponseSchema.parse({
    systems: [{ id: "sys-maximo", name: "Maximo", kind: "software_system", evidence: [], source_roots: [] }],
    applications: [],
    edges: [{
      source: "sys-maximo", target: "sys-4ps", source_kind: "system", target_kind: "system",
      dependency_type: "data", mechanism: "jms", direction: "out", reliability: "asynchronous",
      confidence: "certain", evidence_count: 1,
      provenance: [{
        artifact_id: "SendWorkOrder", qualified_artifact_id: "A::SendWorkOrder",
        source_root: "A", component_name: "cJMS_1", fact_type: "artifact_channel",
      }],
    }],
    channels: [], interfaces: [], diagnostics: [], unmapped: [], summary: {},
    topology_evidence: {
      artifacts: [{
        id: "artifact:1", name: "SendWorkOrder", job_id: "SendWorkOrder",
        source_root: "A", artifact_kind: "job", artifact_role: "standalone",
      }],
      components: [],
      lineage: [
        {
          id: "lin:1", job_name: "SendWorkOrder",
          source_table: "row5", source_column: "row5/WONUM", source_component: "tFileInputXML_1",
          target_table: "resultxml", target_column: "resultxml/WONUM", target_component: "tXMLMap_1",
        },
        // One source into six targets: the same fact six times if listed flat.
        ...["YEAR", "MONTH", "DAY", "HOUR", "MINUTE", "SECOND"].map((part, index) => ({
          id: `lin:ts:${index}`, job_name: "SendWorkOrder",
          source_table: "row6", source_column: "row6/CurrentDate", source_component: "tJava_1",
          target_table: "out1", target_column: `out1/${part}`, target_component: "tXMLMap_1",
        })),
      ],
      relations: [],
    },
    business_topology: {
      schema_version: "business-topology-v1",
      scan_fingerprint: "scan:synthetic",
      metadata: { project_id: "werkorder", scan_fingerprint: "scan:synthetic", generated_by: "test", model_name: "" },
      systems: [
        { ...claim, id: "sys-maximo", name: "Maximo", kind: "software_system", aliases: [] },
        { ...claim, id: "sys-4ps", name: "4PS", kind: "software_system", aliases: [] },
      ],
      integration_solutions: [], business_objects: [],
      data_streams: [{
        ...claim,
        id: "ds-wo",
        source_system_id: "sys-maximo",
        destination_system_id: "sys-4ps",
        name: "Werkorders",
        business_object_ids: [],
        business_summary: "Maximo levert werkorders aan 4PS.",
        integration_solution_ids: [],
        protocols: ["JMS"],
        artifact_ids: ["artifact:1"],
        control_ids: [], risk_ids: [], unknown_ids: [], conflict_ids: [], runtime_observation_ids: [],
      }],
      delivery_paths: [],
      transformations: [
        {
          ...claim, id: "tr-fields", name: "Field mapping", kind: "field_mapping",
          stream_ids: ["ds-wo"], integration_solution_ids: [], artifact_ids: ["artifact:1"],
          mappings: [
            { source: "WORKORDER.WONUM", target: "serviceOrders.orderNo", mapping_type: "direct", category: "identiteit", reaches_target: "yes", note: "" },
            { source: "WORKORDER.DESCRIPTION", target: "serviceOrders.description", mapping_type: "derived", expression: "substring(DESCRIPTION, 1, 50)", reaches_target: "yes", note: "" },
            { source: "WORKORDER.MEMO", target: "serviceOrders.memo", mapping_type: "direct", reaches_target: "no", note: "Kolom bestaat niet in 4PS." },
            { source: "BC", target: "BC", mapping_type: "direct", reaches_target: "yes", note: "" },
            { source: "[REDACTED -- credential]", target: "InforUserName", mapping_type: "constant", reaches_target: "yes", note: "Credential as a field in the message. See finding 3." },
            { source: "[REDACTED -- credential]", target: "InforPassword", mapping_type: "constant", reaches_target: "yes", note: "Credential as a field in the message. See finding 3." },
          ],
        },
        {
          ...claim, id: "tr-status", name: "Value domain", kind: "value_domain", target_field: "serviceOrders.status",
          stream_ids: ["ds-wo"], integration_solution_ids: [], artifact_ids: ["artifact:1"],
          mappings: [
            { source: "GGK", target: "In Process", mapping_type: "value" },
            { source: "VOLTOOID", target: "Technical Finished", mapping_type: "value" },
          ],
        },
      ],
      business_rules: [], controls: [], risks: [], unknowns: [], conflicts: [],
      runtime_observations: [], artifact_annotations: [], component_annotations: [], lineage_annotations: [],
      coverage: { total_artifacts: 1, cited_artifacts: 1, total_evidence: 1, cited_evidence: 1, coverage_ratio: 1, notes: [] },
    },
  });
  return {
    landscape,
    edge: { ...landscape.edges[0], businessStream: landscape.business_topology!.data_streams[0] as BusinessDataStream },
  };
}

function openDesign() {
  const { landscape, edge } = build();
  render(<IntegrationDetailCanvas landscape={landscape} edge={edge} onBack={vi.fn()} onOpenArtifact={vi.fn()} />);
  const panel = screen.getByLabelText("Integration explanation");
  fireEvent.click(within(panel).getByText("SendWorkOrder"));
  return screen.getByLabelText("Integration explanation");
}

describe("implementing design mappings", () => {
  it("states each group label and origin once, not once per row", () => {
    const panel = openDesign();

    // Two groups, five rows. The labels appear once each -- getByText throws on
    // multiple matches, which is precisely the regression being pinned.
    expect(within(panel).getByText("Field mapping")).toBeInTheDocument();
    expect(within(panel).getByText("Value domain")).toBeInTheDocument();
    expect(within(panel).getAllByText(/structured transformation/)).toHaveLength(2);

    // Every row is present.
    for (const target of [
      "serviceOrders.orderNo",
      "serviceOrders.description",
      "serviceOrders.memo",
      "In Process",
      "Technical Finished",
    ]) {
      expect(within(panel).getByText(target)).toBeInTheDocument();
    }
  });

  it("heads a value table with the field it constrains", () => {
    const panel = openDesign();

    const field = within(panel).getByText("serviceOrders.status");
    const group = field.closest("div")!.parentElement!.parentElement!;
    expect(within(group).getByText("GGK")).toBeInTheDocument();
    expect(within(group).getByText("In Process")).toBeInTheDocument();
    // The value rows must NOT be filed under the field-mapping heading.
    expect(within(group).queryByText("serviceOrders.orderNo")).toBeNull();
  });

  it("shows the verbatim rule and flags only the row that does not arrive", () => {
    const panel = openDesign();

    // The expression, not the bare source identity.
    expect(within(panel).getByText("substring(DESCRIPTION, 1, 50)")).toBeInTheDocument();

    // The field-mapping group is MIXED (direct, derived and constant), so there
    // is no common type to hoist and every row states its own.
    // Type and reach share one small label at the end of the mapping line,
    // so the table keeps four aligned columns instead of five stacked rows.
    expect(within(panel).getAllByText("direct")).toHaveLength(2);
    expect(within(panel).getAllByText("constant")).toHaveLength(2);
    expect(within(panel).getByText("derived")).toBeInTheDocument();

    // The value group is uniform, so its type is stated once in the group
    // footer and never repeated on the rows.
    expect(within(panel).getAllByText(/^value \u00b7 structured transformation$/)).toHaveLength(1);
    expect(within(panel).queryByText("value")).toBeNull();

    // Only the row that never arrives is flagged, with the reason.
    expect(within(panel).getByText("direct \u00b7 Niet gelezen")).toBeInTheDocument();
    expect(within(panel).getByText("Kolom bestaat niet in 4PS.")).toBeInTheDocument();

    // A mapping type is never rendered through the dependency-mechanism
    // vocabulary, where "direct" means a direct CALL between two systems.
    expect(panel.textContent).not.toContain("Direct call");
  });
});

describe("mapping readability", () => {
  it("keeps source and target side by side on every row", () => {
    const panel = openDesign();
    // A field that crosses under its own name still occupies BOTH columns.
    // Collapsing those rows to a single column broke the alignment of the
    // whole table, and scanning two columns is why it reads at all.
    expect(within(panel).getAllByText("BC")).toHaveLength(2);
    expect(within(panel).queryByText("unchanged")).toBeNull();
  });

  it("never merges two curated rows that share a source label", () => {
    const panel = openDesign();
    // Both credential rows carry the same redaction placeholder as their
    // source. Merging on the source fused an interface's username and its
    // password into one row: they are two facts, and an analyst wrote each.
    expect(within(panel).getByText("InforUserName")).toBeInTheDocument();
    expect(within(panel).getByText("InforPassword")).toBeInTheDocument();
    expect(within(panel).getAllByText("value withheld")).toHaveLength(2);
    expect(within(panel).queryByText(/Into 2 fields: InforUserName/)).toBeNull();
  });

  it("merges one source that fans out into many targets", () => {
    const panel = openDesign();
    fireEvent.click(within(panel).getByTestId("mapping-group:Scanner field lineage"));
    // Six lineage rows for one timestamp is the same fact six times.
    expect(within(panel).getByText(/Into 6 fields:/)).toBeInTheDocument();
    expect(within(panel).getByText("+5")).toBeInTheDocument();
  });

  it("reads a redaction as a finding, not as a field name", () => {
    const panel = openDesign();
    // "[REDACTED -- credential]" in the source column reads as a field called
    // REDACTED. What it means is that a hardcoded credential is written into
    // the message and the value was withheld.
    expect(within(panel).getAllByText("value withheld")).toHaveLength(2);
    expect(panel.textContent).not.toContain("[REDACTED");
    // The reason is shown in full, never truncated: it is the point of the row.
    expect(within(panel).getAllByText("Credential as a field in the message. See finding 3.")).toHaveLength(2);
  });

  it("names the component a scanner row was read in, and does not double the table prefix", () => {
    const panel = openDesign();
    fireEvent.click(within(panel).getByTestId("mapping-group:Scanner field lineage"));
    expect(within(panel).getByText("row5/WONUM")).toBeInTheDocument();
    expect(panel.textContent).not.toContain("row5.row5/WONUM");
    expect(within(panel).getAllByText("tXMLMap_1").length).toBeGreaterThan(0);
  });

  it("explains what each group is, once", () => {
    const panel = openDesign();
    expect(within(panel).getByText(/What this interface's transformation writes/)).toBeInTheDocument();
    expect(within(panel).getByText(/Read automatically out of the job/)).toBeInTheDocument();
  });

  it("keeps scanner lineage collapsed until asked", () => {
    const panel = openDesign();
    const header = within(panel).getByTestId("mapping-group:Scanner field lineage");
    expect(header).toHaveAttribute("aria-expanded", "false");
    expect(within(panel).queryByText("row5/WONUM")).toBeNull();
    // A field-mapping group is the reason the panel was opened, so it is open.
    expect(within(panel).getByTestId("mapping-group:Field mapping")).toHaveAttribute("aria-expanded", "true");
  });
});

describe("field paths stay readable", () => {
  it("never truncates a path, and offers break points at its own separators", () => {
    const panel = openDesign();

    // The panel is 390px with two columns of paths, so `truncate` hid the end
    // of almost every real name. The full text must be in the DOM.
    for (const path of ["WORKORDER.WONUM", "serviceOrders.orderNo", "serviceOrders.description"]) {
      const node = within(panel).getByTitle(path);
      expect(node.textContent).toBe(path);
      expect(node.className).toContain("break-words");
      expect(node.className).not.toContain("truncate");
    }

    // A break opportunity after each separator, so a long path wraps between
    // segments instead of mid-word ("descriptio n").
    const target = within(panel).getByTitle("serviceOrders.orderNo");
    expect(target.querySelectorAll("wbr").length).toBeGreaterThan(0);
  });
});
