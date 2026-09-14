import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import rawWerkorderTopology from "../fixtures/werkorder_org_business_topology.json";
import { ArchitectureTopologyCanvas } from "@/components/graph/ArchitectureTopologyCanvas";
import { ProjectLandscapeResponseSchema } from "@/types/landscape";

/**
 * The flow side panel is the client-facing read of one data stream, and the
 * rule it has to hold is that ABSENCE RENDERS NOTHING: 23 published topology
 * files predate the structured slots, and a heading over an empty body reads as
 * a finding about the integration rather than a gap in the analysis.
 *
 * React Flow is shimmed to a button per edge -- clicking one is how the panel
 * is reached, since the real canvas needs a layout engine and a viewport.
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
    ReactFlow: (props: {
      edges: Array<{ id: string }>;
      onEdgeClick?: (event: unknown, edge: { id: string }) => void;
      children?: ReactNode;
    }) => createElement(
      "div",
      { "data-testid": "react-flow" },
      props.edges.map((edge) => createElement(
        "button",
        { key: edge.id, type: "button", "data-testid": `edge:${edge.id}`, onClick: (event: unknown) => props.onEdgeClick?.(event, edge) },
        edge.id,
      )),
      props.children,
    ),
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

vi.mock("@/lib/architectureLayout", () => ({
  // ELK is irrelevant to what this panel says; identity layout keeps the async
  // effect that publishes the edges intact.
  layoutArchitectureNodes: async (nodes: unknown[]) => nodes,
  architectureColumnRank: (column: string) => (
    ["upstream", "anchor", "downstream", "secondary", "supporting", "integration"].indexOf(column)
  ),
}));

/**
 * Every label that only exists once a producer publishes the new slots.
 * `Contract` is a row in the always-visible fact strip; the rest are collapsed
 * disclosure rows. Both must be absent for a legacy payload.
 */
const ADDITIVE_HEADINGS = [
  "Contract",
  "In productie",
  "Velden",
  "Waardetabel",
  "Businessregels",
  "Voorwaarden",
  "Open vragen",
  "Risico's",
  "Tegenspraken",
  "Bronnen",
  "Techniek",
];

const claim = { name: "", description: "", status: "observed" as const, confidence: 0.9, evidence_ids: ["ev:1"] };

function landscapeFor(topology: unknown) {
  return ProjectLandscapeResponseSchema.parse({
    systems: [], applications: [], edges: [], channels: [], interfaces: [],
    diagnostics: [], unmapped: [], summary: {},
    business_topology: topology,
  });
}

function structuredTopology() {
  return {
    schema_version: "business-topology-v1",
    scan_fingerprint: "scan:synthetic",
    metadata: { project_id: "werkorder", scan_fingerprint: "scan:synthetic", generated_by: "test", model_name: "" },
    systems: [
      { ...claim, id: "sys-maximo", name: "Maximo", kind: "software_system", aliases: [] },
      { ...claim, id: "sys-4ps", name: "4PS", kind: "software_system", aliases: [] },
    ],
    integration_solutions: [],
    business_objects: [{ ...claim, id: "obj-wo", name: "WorkOrder", aliases: [] }],
    data_streams: [{
      ...claim,
      id: "ds-wo",
      source_system_id: "sys-maximo",
      destination_system_id: "sys-4ps",
      name: "Werkorders",
      business_object_ids: ["obj-wo"],
      business_summary: "Maximo levert werkorders aan 4PS.",
      integration_solution_ids: [],
      protocols: ["JMS"],
      artifact_ids: ["artifact:1"],
      control_ids: [], risk_ids: [], unknown_ids: [], conflict_ids: [],
      runtime_observation_ids: ["ro-cadence"],
      mechanism: "tRunJob child",
      generation: "Talend 7.3",
      direction_pattern: "push",
      lifecycle: "active",
      contract_width: { offered: 412, mapped: 27, written: 12, note: "" },
      conditions: [{ kind: "trigger", statement: "Alleen bij status GEREED.", evidence_note: "tJava_2" }],
      key_form: "WO-nummer",
      verdict: "Actief en in gebruik.",
      document_refs: ["03_INTEGRATION_CATALOG.md#werkorders"],
    }],
    delivery_paths: [],
    transformations: [
      {
        ...claim, id: "tr-fields", name: "Werkorder velden", kind: "field_mapping",
        stream_ids: ["ds-wo"], integration_solution_ids: [], artifact_ids: [],
        mappings: [{ source: "WORKORDER.WONUM", target: "serviceOrders.orderNo", mapping_type: "direct", reaches_target: "yes" }],
      },
      {
        ...claim, id: "tr-status", name: "Statuswaarden", kind: "value_domain", target_field: "serviceOrders.status",
        stream_ids: ["ds-wo"], integration_solution_ids: [], artifact_ids: [],
        mappings: [{ source: "GGK", target: "In Process", mapping_type: "value" }],
      },
    ],
    business_rules: [{
      ...claim, id: "br-status", name: "Statusvertaling", kind: "value_mapping",
      statement: "GGK wordt In Process.", inputs: ["WORKORDER.STATUS"], output: "serviceOrders.status",
      stream_ids: ["ds-wo"], transformation_ids: ["tr-status"],
      owner: "Servicedesk 4PS", owner_question: "Klopt het dat GGK In Process moet worden?",
    }],
    controls: [], risks: [], unknowns: [], conflicts: [],
    runtime_observations: [{
      ...claim, id: "ro-cadence", stream_ids: [], delivery_path_ids: [],
      observed_behavior: "Draait elk half uur.", observation_window: "90 dagen", kind: "cadence", source: "TMC",
      metrics: [{ label: "Runs", value: "2122", unit: "runs", window: "90 dagen", source: "TMC PROD", note: "" }],
    }],
    artifact_annotations: [], component_annotations: [], lineage_annotations: [],
    coverage: { total_artifacts: 1, cited_artifacts: 1, total_evidence: 1, cited_evidence: 1, coverage_ratio: 1, notes: [] },
  };
}

async function openFirstFlow(landscape: ReturnType<typeof landscapeFor>) {
  render(<ArchitectureTopologyCanvas landscape={landscape} onOpenFlow={vi.fn()} />);
  const edges = await waitFor(() => {
    const found = screen.getAllByTestId(/^edge:/);
    expect(found.length).toBeGreaterThan(0);
    return found;
  });
  fireEvent.click(edges[0]);
  const flows = screen.getAllByTestId(/^architecture-flow:/);
  fireEvent.click(flows[0]);
  return screen.getByLabelText("Technical flow details");
}

describe("architecture flow side panel", () => {
  it("renders no additive section for a topology published before those slots existed", async () => {
    const panel = await openFirstFlow(landscapeFor(rawWerkorderTopology));

    // The panel is really open -- otherwise the absence assertions below pass
    // for the wrong reason.
    expect(within(panel).getByText("Open implementation details")).toBeInTheDocument();
    expect(within(panel).getByTestId("disclosure:Delivery path")).toBeInTheDocument();

    for (const heading of ADDITIVE_HEADINGS) {
      expect(within(panel).queryByText(heading)).toBeNull();
    }
    // Not a heading, but the same rule: no "none established" filler either.
    expect(within(panel).queryByText("Known transformations")).toBeNull();
    expect(panel.textContent).not.toContain("Niet vastgesteld");
  });

  /**
   * A `deployment` / `contract_width` object serialized with every field at its
   * default is the shape a producer emits when it found nothing. It must not
   * open a section.
   */
  it("renders no section for structured objects whose every field is still defaulted", async () => {
    // Parsed, not typed: the point is what the STRICT parser accepts and the
    // panel then does with it.
    const topology = structuredTopology() as unknown as {
      data_streams: Array<Record<string, unknown>>;
      transformations: unknown[];
      business_rules: unknown[];
      runtime_observations: unknown[];
    };
    topology.data_streams[0] = {
      ...topology.data_streams[0],
      contract_width: { offered: null, mapped: null, written: null, note: "" },
      deployment: { supplied_version: "", deployed_version: "", built_at: "", agreement: "unknown", note: "" },
      conditions: [],
      mechanism: "",
      verdict: "",
      key_form: "",
      document_refs: [],
      runtime_observation_ids: [],
    };
    topology.transformations = [];
    topology.business_rules = [];
    topology.runtime_observations = [];

    const panel = await openFirstFlow(landscapeFor(topology));

    expect(within(panel).getByText("Open implementation details")).toBeInTheDocument();
    for (const heading of ADDITIVE_HEADINGS) {
      expect(within(panel).queryByText(heading)).toBeNull();
    }
  });

  it("summarises the stream in a short strip and keeps the detail collapsed", async () => {
    const panel = await openFirstFlow(landscapeFor(structuredTopology()));

    // The strip: the funnel as ONE line, not three cards, plus the headline
    // measurement and the verdict. This is the whole default view.
    expect(within(panel).getByText("Contract")).toBeInTheDocument();
    expect(within(panel).getByText("412 \u2192 27 \u2192 12")).toBeInTheDocument();
    expect(within(panel).getByText("2122 runs")).toBeInTheDocument();
    expect(within(panel).getByText("WO-nummer")).toBeInTheDocument();
    expect(within(panel).getByText("Actief en in gebruik.")).toBeInTheDocument();

    // Badges are short tokens only. `mechanism` is prose and must NOT be one:
    // rendered as a badge it pushed every fact above below the fold.
    expect(within(panel).getByText("Actief")).toBeInTheDocument();
    expect(within(panel).getByText("Push")).toBeInTheDocument();
    expect(within(panel).getByText("Talend 7.3")).toBeInTheDocument();

    // Detail is present but CLOSED: the row and its count are visible, the
    // content is not, until the reader asks for it.
    for (const title of ["Velden", "Waardetabel", "Businessregels", "Voorwaarden", "Techniek"]) {
      const row = within(panel).getByTestId(`disclosure:${title}`);
      expect(row).toHaveAttribute("aria-expanded", "false");
    }
    expect(within(panel).queryByText("GGK wordt In Process.")).toBeNull();
    expect(within(panel).queryByText("serviceOrders.orderNo")).toBeNull();

    // Opening one section reveals exactly that section.
    fireEvent.click(within(panel).getByTestId("disclosure:Businessregels"));
    expect(within(panel).getByText("Waardevertaling")).toBeInTheDocument();
    expect(within(panel).getByText("GGK wordt In Process.")).toBeInTheDocument();
    expect(within(panel).getByText("Servicedesk 4PS")).toBeInTheDocument();
    expect(within(panel).getByText("Klopt het dat GGK In Process moet worden?")).toBeInTheDocument();
    expect(within(panel).queryByText("serviceOrders.orderNo")).toBeNull();

    fireEvent.click(within(panel).getByTestId("disclosure:Velden"));
    expect(within(panel).getByText("serviceOrders.orderNo")).toBeInTheDocument();
    expect(within(panel).getByText("WORKORDER.WONUM")).toBeInTheDocument();
    expect(within(panel).getByText("Bereikt doel")).toBeInTheDocument();

    // A value table stays nested under the field it constrains.
    fireEvent.click(within(panel).getByTestId("disclosure:Waardetabel"));
    const group = within(panel).getByText("serviceOrders.status").parentElement!;
    expect(within(group).getByText("GGK")).toBeInTheDocument();
    expect(within(group).getByText("In Process")).toBeInTheDocument();

    // Prose that used to sit in the badge row is reachable, just not first.
    fireEvent.click(within(panel).getByTestId("disclosure:Techniek"));
    expect(within(panel).getByText("Mechanisme")).toBeInTheDocument();

    // Nothing to say about risks, discrepancies, questions or unclassified
    // transformations for this stream, so those rows stay away entirely.
    for (const title of ["Risico's", "Tegenspraken", "Open vragen", "Known transformations", "Bronnen"]) {
      expect(within(panel).queryByTestId(`disclosure:${title}`)).toBeNull();
    }
  });
});
