import { describe, expect, it } from "vitest";
import { findLandscapeEdge, landscapeEdgeKey, landscapeToFlow } from "@/lib/landscapeTransform";
import type { ProjectLandscapeResponse } from "@/types/landscape";

const landscape: ProjectLandscapeResponse = {
  applications: [
    { id: "application:one", source_root: "ZIP/A", source_roots: ["ZIP/A"], project_id: "one", label: "Shared label", description: "", role: "execution", artifact_count: 2, kinds_breakdown: { job: 2 } },
    { id: "application:two", source_root: "ZIP/B", source_roots: ["ZIP/B"], project_id: "two", label: "Shared label", description: "", role: "execution", artifact_count: 1, kinds_breakdown: { route: 1 } },
  ],
  systems: [{ id: "system:maximo", name: "Maximo", kind: "rest_api", evidence: [], source_roots: ["ZIP/A"] }],
  edges: [
    { source: "system:maximo", target: "application:one", source_kind: "system", target_kind: "application", dependency_type: "data", mechanism: "rest", direction: "in", reliability: "synchronous", confidence: "certain", evidence_count: 1, provenance: [] },
    { source: "system:maximo", target: "application:one", source_kind: "system", target_kind: "application", dependency_type: "control", mechanism: "rest", direction: "in", reliability: "synchronous", confidence: "certain", evidence_count: 1, provenance: [] },
  ],
  channels: [], interfaces: [], diagnostics: [], unmapped: [], summary: {},
};

describe("landscapeToFlow", () => {
  it("uses validated business systems and readable data streams instead of technical projects", () => {
    const businessLandscape: ProjectLandscapeResponse = {
      ...landscape,
      edges: [{
        ...landscape.edges[0],
        provenance: [],
      }],
      topology_state: { available: true, validated: true, stale: false, source: "agent" },
      topology_evidence: {
        artifacts: [{ id: "artifact:fetch", name: "Fetch", job_id: "Fetch", source_root: "ZIP/A", artifact_kind: "job", artifact_role: "implementation" }],
        components: [], lineage: [], relations: [],
      },
      business_topology: {
        schema_version: "1.0",
        scan_fingerprint: "fingerprint",
        metadata: { project_id: "project", scan_fingerprint: "fingerprint", generated_by: "test", model_name: "test" },
        systems: [
          { id: "business:maximo", name: "Maximo", kind: "software_system", description: "", status: "proposed", confidence: 0.9, aliases: [], evidence_ids: [] },
          { id: "business:assetcheck", name: "AssetCheck", kind: "software_system", description: "", status: "proposed", confidence: 0.9, aliases: [], evidence_ids: [] },
        ],
        integration_solutions: [
          { id: "integration:one", name: "AssetCheck integration", description: "", platform: "talend", role: "execution", status: "proposed", confidence: 0.9, artifact_ids: ["Fetch"], evidence_ids: [] },
        ],
        business_objects: [{ id: "object:workorder", name: "Work orders", description: "", status: "proposed", confidence: 0.9, aliases: [], evidence_ids: [] }],
        data_streams: [{
          id: "stream:workorders",
          source_system_id: "business:maximo",
          destination_system_id: "business:assetcheck",
          name: "Work-order synchronization",
          description: "",
          business_object_ids: ["object:workorder"],
          business_summary: "Supplies inspectors with work orders.",
          integration_solution_ids: ["integration:one"],
          protocols: ["REST"],
          requiredness: "not_established",
          delivery_path_id: "path:workorders",
          status: "proposed",
          confidence: 0.9,
          control_ids: [], risk_ids: [],
          artifact_ids: ["artifact:fetch"],
          unknown_ids: [], evidence_ids: [],
        }],
        delivery_paths: [{ id: "path:workorders", name: "Work order delivery", description: "", stream_id: "stream:workorders", status: "proposed", confidence: 0.9, evidence_ids: [], segments: [{ id: "segment:one", name: "REST delivery", description: "", sequence: 1, source_ref: "business:maximo", destination_ref: "business:assetcheck", mechanism: "rest", protocol: "REST", resource: "", component: "", artifact_ids: [], mode: "direct", status: "proposed", confidence: 0.9, evidence_ids: [] }] }],
        transformations: [],
        controls: [], risks: [], unknowns: [], artifact_annotations: [], component_annotations: [], lineage_annotations: [], coverage: { total_artifacts: 1, cited_artifacts: 1, total_evidence: 1, cited_evidence: 1, coverage_ratio: 1, notes: [] },
      },
    };
    const result = landscapeToFlow(businessLandscape);

    expect(result.nodes.map((node) => node.id)).toEqual([
      "business:maximo",
      "business:assetcheck",
    ]);
    expect(result.nodes.every((node) => node.data.entityType === "businessSystem")).toBe(true);
    expect(result.nodes.map((node) => node.id)).not.toContain("application:one");
    expect(result.edges).toHaveLength(1);
    expect(result.edges[0].label).toBe("Work orders");
    // A single-stream system pair still aggregates to one L1 edge; the
    // constituent stream key resolves back to its own TopologyEdge via
    // findLandscapeEdge (see AggregatedRelationshipEdgeData).
    const aggregatedData = result.edges[0].data as { streamKeys: string[]; pair: { source: string; target: string } };
    expect(aggregatedData.streamKeys).toHaveLength(1);
    const resolved = findLandscapeEdge(businessLandscape, aggregatedData.streamKeys[0]);
    expect(resolved?.businessStream?.id).toBe("stream:workorders");
    expect(resolved?.provenance).toHaveLength(1);
    expect(resolved?.provenance[0]).toMatchObject({
      artifact_id: "Fetch",
      qualified_artifact_id: "ZIP/A::Fetch",
      fact_type: "business_topology_artifact",
    });
    expect(aggregatedData.pair).toEqual({ source: "business:maximo", target: "business:assetcheck" });
    expect(result.edges[0].style).toMatchObject({ strokeDasharray: "7 4" });
  });

  it("keeps identically labelled applications distinct by scanner ID", () => {
    const result = landscapeToFlow({
      ...landscape,
      edges: [
        ...landscape.edges,
        {
          source: "application:one",
          target: "application:two",
          source_kind: "application",
          target_kind: "application",
          dependency_type: "control_flow",
          mechanism: "direct",
          direction: "out",
          reliability: "synchronous",
          confidence: "certain",
          evidence_count: 1,
          provenance: [],
        },
      ],
    });
    expect(result.nodes.map((node) => node.id)).toEqual(expect.arrayContaining(["application:one", "application:two"]));
    expect(result.nodes.filter((node) => node.data.entityType === "application")).toHaveLength(2);
  });

  it("keeps dependency types distinct even when endpoints and mechanism match", () => {
    const result = landscapeToFlow(landscape);
    expect(result.edges).toHaveLength(2);
    expect(new Set(result.edges.map((edge) => edge.id)).size).toBe(2);
  });

  it("uses all non-provenance edge identity fields in the stable key", () => {
    expect(landscapeEdgeKey(landscape.edges[0])).not.toBe(landscapeEdgeKey(landscape.edges[1]));
    expect(landscapeEdgeKey(landscape.edges[0])).toContain("system:maximo");
  });

  it("can hide external systems without inventing a direct application edge", () => {
    const result = landscapeToFlow(landscape, { showSystems: false });
    expect(result.nodes.map((node) => node.id)).not.toContain("system:maximo");
    expect(result.edges).toHaveLength(0);
  });

  it("does not render unconnected technical evidence as an L1 system", () => {
    const result = landscapeToFlow({
      ...landscape,
      systems: [
        ...landscape.systems,
        {
          id: "system:orphan",
          name: "Unconnected configuration",
          kind: "unknown",
          evidence: [],
          source_roots: ["ZIP/A"],
        },
      ],
    });
    expect(result.nodes.map((node) => node.id)).not.toContain("system:orphan");
  });

  it("does not retain unrelated applications when relationships are visible", () => {
    const result = landscapeToFlow(landscape);
    expect(result.nodes.map((node) => node.id)).toContain("application:one");
    expect(result.nodes.map((node) => node.id)).not.toContain("application:two");
  });

  it("shows no unrelated applications when an edge filter has no matches", () => {
    const result = landscapeToFlow(landscape, {
      dependencyTypes: new Set(["integration"]),
    });
    expect(result.nodes).toHaveLength(0);
    expect(result.edges).toHaveLength(0);
  });

  it("searches by label while retaining only direct scanner-computed context", () => {
    const result = landscapeToFlow(landscape, { query: "Maximo" });
    expect(result.nodes.map((node) => node.id)).toEqual(expect.arrayContaining(["system:maximo", "application:one"]));
    expect(result.nodes.map((node) => node.id)).not.toContain("application:two");
  });
});
