import { describe, expect, it } from "vitest";
import { implementingDesignDetail, integrationDetailForEdge } from "@/lib/integrationDetailTransform";
import type { ProjectLandscapeResponse } from "@/types/landscape";

const landscape: ProjectLandscapeResponse = {
  applications: [{ id: "application:assetcheck", source_root: "A", source_roots: ["A"], project_id: "assetcheck", label: "AssetCheck", description: "", role: "execution", artifact_count: 1, kinds_breakdown: { job: 1 } }],
  systems: [{ id: "system:maximo", name: "Maximo", kind: "rest_api", evidence: [], source_roots: [] }],
  edges: [{ source: "system:maximo", target: "application:assetcheck", source_kind: "system", target_kind: "application", dependency_type: "data", mechanism: "rest", direction: "in", reliability: "synchronous", confidence: "certain", evidence_count: 1, provenance: [{ artifact_id: "Fetch", qualified_artifact_id: "A::Fetch", source_root: "A", component_name: "tRESTClient_1", fact_type: "artifact_resource" }] }],
  channels: [
    { channel_type: "jms", channel: "configured endpoint", direction: "out", artifact_id: "Fetch", qualified_artifact_id: "A::Fetch", source_root: "A", component_name: "cJMS_1", fact_type: "artifact_channel", redaction_descriptor: { scheme: "jms", tls: true } },
    { channel_type: "jms", channel: "configured endpoint", direction: "in", artifact_id: "Other", qualified_artifact_id: "B::Other", source_root: "B", component_name: "cJMS_2", fact_type: "artifact_channel" },
  ],
  interfaces: [], diagnostics: [], unmapped: [{ reason: "dynamic" }], summary: {},
};

describe("integrationDetailForEdge", () => {
  it("includes only exact provenance-attached channel evidence", () => {
    const detail = integrationDetailForEdge(landscape, landscape.edges[0]);
    expect(detail.channels).toHaveLength(1);
    expect(detail.channels[0].qualified_artifact_id).toBe("A::Fetch");
  });

  it("does not join a matching redacted channel value into a fake peer", () => {
    const detail = integrationDetailForEdge(landscape, landscape.edges[0]);
    expect(detail.nodes.map((node) => node.id)).not.toContain("artifact:B::Other");
    expect(detail.channels.every((channel) => channel.artifact_id !== "Other")).toBe(true);
  });

  it("projects exact job context and prioritizes its mappings before technical drill-down", () => {
    const claim = { name: "", description: "", status: "observed" as const, confidence: 0.8, evidence_ids: ["artifact:fetch"] };
    const stream = {
      id: "stream:orders", name: "Orders", description: "", status: "inferred" as const,
      confidence: 0.8, evidence_ids: ["edge:orders"], source_system_id: "system:maximo",
      destination_system_id: "application:assetcheck", business_object_ids: [],
      business_summary: "Moves orders into AssetCheck.", integration_solution_ids: ["solution:ettm"],
      protocols: ["REST"], requiredness: "not_established" as const, control_ids: [], risk_ids: [],
      unknown_ids: [], artifact_ids: ["artifact:fetch"],
    };
    const enriched: ProjectLandscapeResponse = {
      ...landscape,
      business_topology: {
        schema_version: "business-topology-v1", scan_fingerprint: "f",
        metadata: { project_id: "demo", scan_fingerprint: "f", generated_by: "test", model_name: "" },
        systems: [], business_objects: [], data_streams: [stream], delivery_paths: [], controls: [], risks: [], unknowns: [],
        runtime_observations: [], conflicts: [], component_annotations: [], lineage_annotations: [],
        integration_solutions: [{ ...claim, id: "solution:ettm", name: "ETTM", platform: "Talend", role: "routing", artifact_ids: ["artifact:fetch"] }],
        transformations: [{ ...claim, id: "transform:orders", name: "Order mapping", stream_ids: [stream.id], integration_solution_ids: ["solution:ettm"], artifact_ids: ["artifact:fetch"], mappings: [{ source: "Order.Status", target: "serviceOrders.status", mapping_type: "value" }] }],
        artifact_annotations: [{ ...claim, id: "annotation:fetch", artifact_id: "artifact:fetch", business_role: "Transforms order status" }],
        coverage: { total_artifacts: 1, cited_artifacts: 1, total_evidence: 1, cited_evidence: 1, coverage_ratio: 1, notes: [] },
      },
      topology_evidence: {
        artifacts: [
          { id: "artifact:fetch", name: "Fetch", job_id: "Fetch", source_root: "A", artifact_kind: "route", artifact_role: "transformation" },
          { id: "artifact:writer", name: "Writer", job_id: "Writer", source_root: "B", artifact_kind: "job", artifact_role: "transformation" },
        ],
        components: [{ id: "component:map", artifact_id: "artifact:writer", name: "tMap_1", component_type: "tMap" }],
        lineage: [
          { id: "lineage:1", job_name: "Fetch", source_table: "Order", source_column: "Number", source_component: "tInput", target_table: "serviceOrders", target_column: "orderNo", target_component: "tMap" },
          { id: "lineage:2", job_name: "Writer", source_table: "Order", source_column: "Status", source_component: "tInput", target_table: "serviceOrders", target_column: "status", target_component: "tMap" },
        ],
        relations: [{ id: "relation:writer", source: "Fetch", target: "Writer", relation_type: "job_call", component_name: "tRunJob_1", resolution: "unique_exact_name" }],
      },
    };
    const edge = { ...enriched.edges[0], businessStream: stream };
    const detail = implementingDesignDetail(enriched, edge, edge.provenance[0]);

    expect(detail.label).toBe("Fetch");
    expect(detail.businessRole).toBe("Transforms order status");
    expect(detail.integrationSolutions).toEqual(["ETTM"]);
    expect(detail.protocols).toEqual(["REST"]);
    expect(detail.channels).toHaveLength(1);
    expect(detail.invokedDesigns).toEqual(["Writer"]);
    expect(detail.mappingComponents).toEqual(["tMap_1 (tMap)"]);
    expect(detail.mappings).toEqual(expect.arrayContaining([
      expect.objectContaining({ source: "Order.Status", target: "serviceOrders.status", mappingType: "value" }),
      expect.objectContaining({ source: "Order.Number", target: "serviceOrders.orderNo", origin: "scanner_lineage" }),
      expect.objectContaining({ source: "Order.Status", target: "serviceOrders.status", group: "Invoked job · Writer" }),
    ]));
    expect(detail.mappingAttribution).toBe("exact");
  });

  it("does not assign name-only lineage when duplicate jobs exist in different roots", () => {
    const ambiguous: ProjectLandscapeResponse = {
      ...landscape,
      topology_evidence: {
        artifacts: [
          { id: "artifact:a", name: "Fetch", job_id: "Fetch", source_root: "A", artifact_kind: "job", artifact_role: "execution" },
          { id: "artifact:b", name: "Fetch", job_id: "Fetch", source_root: "B", artifact_kind: "job", artifact_role: "execution" },
        ],
        components: [],
        lineage: [{ id: "lineage:1", job_name: "Fetch", source_table: "A", source_column: "id", source_component: "in", target_table: "B", target_column: "id", target_component: "out" }],
        relations: [],
      },
    };
    const detail = implementingDesignDetail(ambiguous, ambiguous.edges[0], ambiguous.edges[0].provenance[0]);
    expect(detail.mappings).toEqual([]);
    expect(detail.mappingAttribution).toBe("ambiguous");
  });
  /**
   * A mapping row is only readable if it says what KIND of fact it is. Grouping
   * on the transformation NAME alone put a status value table and a field
   * mapping under indistinguishable headings, which is how `GGK -> In Process`
   * came to read as a business decision.
   */
  it("carries the kind, expression, category, reach and note through, grouped by kind", () => {
    const claim = { name: "", description: "", status: "observed" as const, confidence: 0.8, evidence_ids: ["artifact:fetch"] };
    const stream = {
      id: "stream:orders", name: "Orders", description: "", status: "inferred" as const,
      confidence: 0.8, evidence_ids: ["edge:orders"], source_system_id: "system:maximo",
      destination_system_id: "application:assetcheck", business_object_ids: [],
      business_summary: "Moves orders into AssetCheck.", integration_solution_ids: [],
      protocols: ["REST"], requiredness: "not_established" as const, control_ids: [], risk_ids: [],
      unknown_ids: [], artifact_ids: ["artifact:fetch"],
    };
    const enriched: ProjectLandscapeResponse = {
      ...landscape,
      business_topology: {
        schema_version: "business-topology-v1", scan_fingerprint: "f",
        metadata: { project_id: "demo", scan_fingerprint: "f", generated_by: "test", model_name: "" },
        systems: [], business_objects: [], data_streams: [stream], delivery_paths: [], controls: [], risks: [], unknowns: [],
        runtime_observations: [], conflicts: [], component_annotations: [], lineage_annotations: [],
        integration_solutions: [], artifact_annotations: [],
        transformations: [
          {
            ...claim, id: "transform:fields", name: "Order fields", kind: "field_mapping",
            stream_ids: [stream.id], integration_solution_ids: [], artifact_ids: ["artifact:fetch"],
            mappings: [{
              source: "Order.Description", target: "serviceOrders.description", mapping_type: "derived",
              expression: "substring(DESCRIPTION, 1, 50)", category: "text", reaches_target: "partial",
              note: "Truncated at 50 characters.",
            }],
          },
          {
            ...claim, id: "transform:status", name: "Order status", kind: "value_domain",
            target_field: "serviceOrders.status",
            stream_ids: [stream.id], integration_solution_ids: [], artifact_ids: ["artifact:fetch"],
            mappings: [{ source: "GGK", target: "In Process", mapping_type: "value" }],
          },
        ],
        coverage: { total_artifacts: 1, cited_artifacts: 1, total_evidence: 1, cited_evidence: 1, coverage_ratio: 1, notes: [] },
      },
      topology_evidence: {
        artifacts: [{ id: "artifact:fetch", name: "Fetch", job_id: "Fetch", source_root: "A", artifact_kind: "route", artifact_role: "transformation" }],
        components: [], lineage: [], relations: [],
      },
    };
    const edge = { ...enriched.edges[0], businessStream: stream };
    const detail = implementingDesignDetail(enriched, edge, edge.provenance[0]);

    expect(detail.mappings).toEqual([
      expect.objectContaining({
        group: "Field mapping",
        transformationKind: "field_mapping",
        transformationName: "Order fields",
        target: "serviceOrders.description",
        expression: "substring(DESCRIPTION, 1, 50)",
        category: "text",
        reachesTarget: "partial",
        note: "Truncated at 50 characters.",
      }),
      expect.objectContaining({
        group: "Value domain",
        transformationKind: "value_domain",
        targetField: "serviceOrders.status",
        source: "GGK",
        target: "In Process",
        reachesTarget: "unknown",
      }),
    ]);
  });

  it("keeps an unclassified transformation under its own name", () => {
    const claim = { name: "", description: "", status: "observed" as const, confidence: 0.8, evidence_ids: ["artifact:fetch"] };
    const stream = {
      id: "stream:orders", name: "Orders", description: "", status: "inferred" as const,
      confidence: 0.8, evidence_ids: [], source_system_id: "system:maximo",
      destination_system_id: "application:assetcheck", business_object_ids: [],
      business_summary: "", integration_solution_ids: [], protocols: [],
      requiredness: "not_established" as const, control_ids: [], risk_ids: [],
      unknown_ids: [], artifact_ids: ["artifact:fetch"],
    };
    const legacy: ProjectLandscapeResponse = {
      ...landscape,
      business_topology: {
        schema_version: "business-topology-v1", scan_fingerprint: "f",
        metadata: { project_id: "demo", scan_fingerprint: "f", generated_by: "test", model_name: "" },
        systems: [], business_objects: [], data_streams: [stream], delivery_paths: [], controls: [], risks: [], unknowns: [],
        runtime_observations: [], conflicts: [], component_annotations: [], lineage_annotations: [],
        integration_solutions: [], artifact_annotations: [],
        transformations: [{
          ...claim, id: "transform:legacy", name: "Order mapping",
          stream_ids: [stream.id], integration_solution_ids: [], artifact_ids: ["artifact:fetch"],
          mappings: [{ source: "Order.Status", target: "serviceOrders.status", mapping_type: "value" }],
        }],
        coverage: { total_artifacts: 1, cited_artifacts: 1, total_evidence: 1, cited_evidence: 1, coverage_ratio: 1, notes: [] },
      },
      topology_evidence: {
        artifacts: [{ id: "artifact:fetch", name: "Fetch", job_id: "Fetch", source_root: "A", artifact_kind: "route", artifact_role: "transformation" }],
        components: [], lineage: [], relations: [],
      },
    };
    const edge = { ...legacy.edges[0], businessStream: stream };
    const detail = implementingDesignDetail(legacy, edge, edge.provenance[0]);

    expect(detail.mappings).toEqual([
      expect.objectContaining({ group: "Order mapping", transformationKind: "unknown", expression: "", reachesTarget: "unknown" }),
    ]);
  });
});
