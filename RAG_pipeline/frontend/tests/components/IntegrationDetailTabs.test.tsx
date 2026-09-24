import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { IntegrationDetailCanvas } from "@/components/graph/IntegrationDetailCanvas";
import { ProjectLandscapeResponseSchema, type BusinessDataStream, type ProjectLandscapeResponse } from "@/types/landscape";
import type { TopologyEdge } from "@/lib/landscapeTransform";

/**
 * `velden` and `regels` are conditional tabs. A project whose topology carries
 * no field or rule data must not be offered them: an empty tab is a promise of
 * content that is not there, and only one of the published topologies carries
 * these slots today.
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

const baseStream = {
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
};

function buildLandscape(options: { structured: boolean }): { landscape: ProjectLandscapeResponse; edge: TopologyEdge } {
  const stream = options.structured
    ? {
      ...baseStream,
      conditions: [{ kind: "filter", statement: "Alleen werkorders met een locatie.", evidence_note: "tFilterRow_1" }],
    }
    : baseStream;
  const transformations = options.structured
    ? [
      {
        ...claim, id: "tr-fields", name: "Werkorder velden", kind: "field_mapping",
        stream_ids: ["ds-wo"], integration_solution_ids: [], artifact_ids: ["artifact:1"],
        mappings: [
          { source: "WORKORDER.WONUM", target: "serviceOrders.orderNo", mapping_type: "direct", category: "identiteit", reaches_target: "yes", note: "" },
          { source: "WORKORDER.MEMO", target: "serviceOrders.memo", mapping_type: "direct", reaches_target: "no", note: "Kolom bestaat niet in 4PS." },
        ],
      },
      {
        ...claim, id: "tr-status", name: "Statuswaarden", kind: "value_domain", target_field: "serviceOrders.status",
        stream_ids: ["ds-wo"], integration_solution_ids: [], artifact_ids: [],
        mappings: [{ source: "GGK", target: "In Process", mapping_type: "value" }],
      },
    ]
    : [];
  const businessRules = options.structured
    ? [{
      ...claim, id: "br-status", name: "Statusvertaling", kind: "value_mapping",
      statement: "GGK wordt In Process.", inputs: ["WORKORDER.STATUS"], output: "serviceOrders.status",
      stream_ids: ["ds-wo"], transformation_ids: ["tr-status"],
      owner: "Servicedesk 4PS", owner_question: "Klopt het dat GGK In Process moet worden?",
    }]
    : [];
  const runtimeObservations = options.structured
    ? [{
      ...claim, id: "ro-1", stream_ids: ["ds-wo"], delivery_path_ids: [],
      observed_behavior: "Draait elk half uur.", observation_window: "90 dagen", kind: "reliability", source: "TMC",
      metrics: [{ label: "Faalpercentage", value: "0.8", unit: "%", window: "90 dagen", source: "TMC PROD", note: "" }],
    }]
    : [];
  const landscape = ProjectLandscapeResponseSchema.parse({
    systems: [{ id: "sys-maximo", name: "Maximo", kind: "software_system", evidence: [], source_roots: [] }],
    applications: [],
    edges: [{
      source: "sys-maximo", target: "sys-4ps", source_kind: "system", target_kind: "system",
      dependency_type: "data", mechanism: "jms", direction: "out", reliability: "asynchronous",
      confidence: "certain", evidence_count: 1,
      provenance: [{ artifact_id: "SendWorkOrder", qualified_artifact_id: "A::SendWorkOrder", source_root: "A", component_name: "cJMS_1", fact_type: "artifact_channel" }],
    }],
    channels: [], interfaces: [], diagnostics: [], unmapped: [], summary: {},
    business_topology: {
      schema_version: "business-topology-v1",
      scan_fingerprint: "scan:synthetic",
      metadata: { project_id: "werkorder", scan_fingerprint: "scan:synthetic", generated_by: "test", model_name: "" },
      systems: [
        { ...claim, id: "sys-maximo", name: "Maximo", kind: "software_system", aliases: [] },
        { ...claim, id: "sys-4ps", name: "4PS", kind: "software_system", aliases: [] },
      ],
      integration_solutions: [], business_objects: [],
      data_streams: [stream],
      delivery_paths: [], transformations, business_rules: businessRules,
      controls: [], risks: [], unknowns: [], conflicts: [],
      runtime_observations: runtimeObservations,
      artifact_annotations: [], component_annotations: [], lineage_annotations: [],
      coverage: { total_artifacts: 1, cited_artifacts: 1, total_evidence: 1, cited_evidence: 1, coverage_ratio: 1, notes: [] },
    },
  });
  const edge: TopologyEdge = {
    ...landscape.edges[0],
    businessStream: landscape.business_topology!.data_streams[0] as BusinessDataStream,
  };
  return { landscape, edge };
}

function renderCanvas(structured: boolean) {
  const { landscape, edge } = buildLandscape({ structured });
  render(<IntegrationDetailCanvas landscape={landscape} edge={edge} onBack={vi.fn()} onOpenArtifact={vi.fn()} />);
}

describe("IntegrationDetailCanvas field and rule tabs", () => {
  it("does not offer velden or regels when the topology carries neither", () => {
    renderCanvas(false);

    expect(screen.getByRole("button", { name: "flow" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "velden" })).toBeNull();
    expect(screen.queryByRole("button", { name: "regels" })).toBeNull();
  });

  it("shows the full field table with reach filters, and value domains under their target field", () => {
    renderCanvas(true);

    fireEvent.click(screen.getByRole("button", { name: "velden" }));
    // The field table first, then the nested value-domain sub-tables.
    const table = screen.getAllByRole("table")[0];
    expect(within(table).getByText("Doelveld")).toBeInTheDocument();
    expect(within(table).getByText("Bron of expressie")).toBeInTheDocument();
    expect(within(table).getByRole("columnheader", { name: "Bereikt doel" })).toBeInTheDocument();
    expect(within(table).getByText("serviceOrders.orderNo")).toBeInTheDocument();
    expect(within(table).getByText("serviceOrders.memo")).toBeInTheDocument();

    // The funnel is a filter, not a footnote.
    fireEvent.click(screen.getByRole("button", { name: /Niet gelezen/ }));
    const filtered = screen.getAllByRole("table")[0];
    expect(within(filtered).getByText("serviceOrders.memo")).toBeInTheDocument();
    expect(within(filtered).queryByText("serviceOrders.orderNo")).toBeNull();

    const valueDomains = screen.getByText("Waardetabel").closest("section")!;
    expect(within(valueDomains).getByText("serviceOrders.status")).toBeInTheDocument();
    expect(within(valueDomains).getByText("GGK")).toBeInTheDocument();
    expect(within(valueDomains).getByText("In Process")).toBeInTheDocument();
  });

  it("shows the rules table with its owner question and the conditions beneath it", () => {
    renderCanvas(true);

    fireEvent.click(screen.getByRole("button", { name: "regels" }));
    expect(screen.getByText("GGK wordt In Process.")).toBeInTheDocument();
    expect(screen.getByText("Servicedesk 4PS")).toBeInTheDocument();
    expect(screen.getByText("Klopt het dat GGK In Process moet worden?")).toBeInTheDocument();

    const conditions = screen.getByText("Voorwaarden").closest("section")!;
    expect(within(conditions).getByText("Filter")).toBeInTheDocument();
    expect(within(conditions).getByText("Alleen werkorders met een locatie.")).toBeInTheDocument();
  });

  it("puts the measured runtime numbers, with their window, on the controls panel", () => {
    renderCanvas(true);

    fireEvent.click(screen.getByRole("button", { name: "controls" }));
    const runtime = screen.getByText("In productie").closest("section")!;
    expect(within(runtime).getByText("Faalpercentage")).toBeInTheDocument();
    expect(within(runtime).getByText("0.8 %")).toBeInTheDocument();
    expect(within(runtime).getByText("90 dagen")).toBeInTheDocument();
  });

  it("renders no runtime metric grid when the mirror reported none", () => {
    renderCanvas(false);

    fireEvent.click(screen.getByRole("button", { name: "controls" }));
    expect(screen.queryByText("In productie")).toBeNull();
  });

  it("shows control type, risk severity, conflict resolution, the unknown's question and document references", () => {
    const { landscape, edge } = buildLandscape({ structured: true });
    const topology = landscape.business_topology!;
    const stream = {
      ...edge.businessStream!,
      control_ids: ["ct-1"], risk_ids: ["rk-1"], unknown_ids: ["un-1"], conflict_ids: ["cf-1"],
      document_refs: ["03_CATALOG.md#werkorders"],
    };
    const enriched: ProjectLandscapeResponse = {
      ...landscape,
      business_topology: {
        ...topology,
        data_streams: [stream],
        controls: [{ ...claim, id: "ct-1", name: "Telling", stream_ids: [], control_type: "reconciliatie" }],
        risks: [{ ...claim, id: "rk-1", name: "Dubbele levering", stream_ids: [], severity: "critical" }],
        unknowns: [{ ...claim, id: "un-1", name: "Eigenaar", stream_ids: [], question: "Wie beheert deze koppeling?" }],
        conflicts: [{
          ...claim, id: "cf-1", name: "Schema wijkt af", conflict_type: "supporting_vs_implementation",
          subject_refs: [], implementation_evidence_ids: [], supporting_evidence_ids: [], resolution: "Code is leidend.",
        }],
        // Linked from the PATH side only.
        delivery_paths: [{
          ...claim, id: "dp-1", name: "Pad", stream_id: "ds-wo",
          segments: [{
            ...claim, id: "seg-1", name: "Hop", sequence: 1, source_ref: "sys-maximo", destination_ref: "sys-4ps",
            mechanism: "queue", protocol: "JMS", resource: "", component: "cJMS_1", artifact_ids: [], mode: "event_driven",
          }],
        }],
      },
    };
    render(<IntegrationDetailCanvas landscape={enriched} edge={{ ...edge, businessStream: stream }} onBack={vi.fn()} onOpenArtifact={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "controls" }));
    expect(screen.getByText("reconciliatie")).toBeInTheDocument();
    expect(screen.getByText("Kritiek")).toBeInTheDocument();
    expect(screen.getByText("Documentatie wijkt af van implementatie")).toBeInTheDocument();
    expect(screen.getByText("Resolution: Code is leidend.")).toBeInTheDocument();
    expect(screen.getByText("Wie beheert deze koppeling?")).toBeInTheDocument();
    // The runtime observation carries its kind and window, not only its prose.
    expect(screen.getByText("Betrouwbaarheid")).toBeInTheDocument();
    expect(screen.getByText("Draait elk half uur.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "delivery" }));
    expect(screen.getByText(/JMS · Event-gedreven · cJMS_1/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "evidence" }));
    expect(screen.getByText("Documentation references")).toBeInTheDocument();
    expect(screen.getByText("03_CATALOG.md#werkorders")).toBeInTheDocument();
  });
});
