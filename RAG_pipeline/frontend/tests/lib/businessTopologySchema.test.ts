import { describe, expect, it } from "vitest";
import { BusinessTopologySchema } from "@/types/landscape";

/** Mirrors `BusinessTopology.model_dump()` including inherited claim fields. */
describe("BusinessTopologySchema", () => {
  it("accepts a complete backend business-topology payload", () => {
    const claim = { id: "claim:1", name: "Claim", description: "Description", status: "proposed", confidence: 0.75, evidence_ids: ["evidence:1"] };
    const topology = BusinessTopologySchema.parse({
      schema_version: "business-topology-v1",
      scan_fingerprint: "scan:abc",
      metadata: { project_id: "project", scan_fingerprint: "scan:abc", generated_by: "documentation_topology_agent", model_name: "gpt" },
      systems: [{ ...claim, id: "system:1", kind: "software_system", aliases: ["System One"], purpose: "Dispatches work", owner: "Operations" }],
      integration_solutions: [{ ...claim, id: "solution:1", platform: "talend", role: "orchestration", artifact_ids: ["artifact:1"] }],
      business_objects: [{ ...claim, id: "object:1", aliases: ["Object One"] }],
      data_streams: [{ ...claim, id: "stream:1", source_system_id: "system:1", destination_system_id: "system:2", business_object_ids: ["object:1"], business_summary: "Moves work orders.", integration_solution_ids: ["solution:1"], protocols: ["REST", "JMS"], requiredness: "not_established", delivery_path_id: "path:1", control_ids: ["control:1"], risk_ids: ["risk:1"], unknown_ids: ["unknown:1"], runtime_observation_ids: ["runtime:1"], conflict_ids: ["conflict:1"], artifact_ids: ["artifact:1"] }],
      delivery_paths: [{ ...claim, id: "path:1", stream_id: "stream:1", segments: [{ ...claim, id: "segment:1", sequence: 1, source_ref: "system:1", destination_ref: "system:2", mechanism: "rest", protocol: "REST", resource: "/workorders", component: "restClient", artifact_ids: ["artifact:1"], mode: "direct" }] }],
      transformations: [{ ...claim, id: "transformation:1", stream_ids: ["stream:1"], integration_solution_ids: ["solution:1"], artifact_ids: ["artifact:1"], mappings: [{ source: "STATUS=READY", target: "Status=In Process", mapping_type: "value" }] }],
      controls: [{ ...claim, id: "control:1", stream_ids: ["stream:1"], control_type: "validation" }],
      risks: [{ ...claim, id: "risk:1", stream_ids: ["stream:1"], severity: "high" }],
      unknowns: [{ ...claim, id: "unknown:1", stream_ids: ["stream:1"], question: "Who owns this stream?" }],
      runtime_observations: [{ ...claim, id: "runtime:1", stream_ids: ["stream:1"], delivery_path_ids: ["path:1"], observed_behavior: "Observed successful delivery", observation_window: "2026-08-01" }],
      conflicts: [{ ...claim, id: "conflict:1", conflict_type: "runtime_vs_implementation", subject_refs: ["stream:1"], implementation_evidence_ids: ["artifact:1"], supporting_evidence_ids: ["runtime:1"], resolution: "Open" }],
      artifact_annotations: [{ ...claim, id: "annotation:artifact", artifact_id: "artifact:1", business_role: "Transforms work orders" }],
      component_annotations: [{ ...claim, id: "annotation:component", component_id: "component:1", human_explanation: "Maps work order fields." }],
      lineage_annotations: [{ ...claim, id: "annotation:lineage", lineage_id: "lineage:1", business_meaning: "Work order identifier", business_object_id: "object:1" }],
      coverage: { total_artifacts: 5, cited_artifacts: 4, total_evidence: 8, cited_evidence: 7, coverage_ratio: 0.875, technical_relationships: 1, grounded_technical_relationships: 1, notes: ["Partial route coverage"] },
    });
    expect(topology.data_streams[0].destination_system_id).toBe("system:2");
    expect(topology.data_streams[0].protocols).toEqual(["REST", "JMS"]);
    expect(topology.delivery_paths[0].segments[0].resource).toBe("/workorders");
    expect(topology.transformations[0].mappings[0].mapping_type).toBe("value");
    expect(topology.unknowns[0].question).toContain("owns");
  });
});
