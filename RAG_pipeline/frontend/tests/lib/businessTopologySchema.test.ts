import { describe, expect, it } from "vitest";
import rawWorkorderTopology from "../fixtures/workorders_demo_org_business_topology.json";
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
  /**
   * The schema objects are `.strict()`. A slot the backend serializes but this
   * contract omits rejects the WHOLE topology, which blanks the canvas rather
   * than degrading one section -- so every structured slot on the Python model
   * has to be accepted here, with its enum members intact.
   */
  it("accepts and round-trips every additive structured slot", () => {
    const claim = { name: "Claim", description: "", status: "observed", confidence: 0.9, evidence_ids: ["evidence:1"] };
    const topology = BusinessTopologySchema.parse({
      schema_version: "business-topology-v1",
      scan_fingerprint: "scan:abc",
      metadata: { project_id: "project", scan_fingerprint: "scan:abc", generated_by: "agent", model_name: "" },
      systems: [],
      integration_solutions: [],
      business_objects: [],
      data_streams: [{
        ...claim,
        id: "stream:1",
        source_system_id: "system:1",
        destination_system_id: "system:2",
        business_summary: "Moves work orders.",
        mechanism: "tRunJob child",
        generation: "Talend 7.3",
        direction_pattern: "hybrid",
        lifecycle: "duplicate",
        contract_width: { offered: 412, mapped: 27, written: 12, note: "12 of 412 attributes persist." },
        conditions: [
          { kind: "trigger", statement: "Only when status is GEREED.", evidence_note: "tJava_2" },
          { kind: "ordering", statement: "Runs after the cost-centre load.", evidence_note: "" },
        ],
        deployment: { supplied_version: "1.4", deployed_version: "1.3", built_at: "2026-01-05", agreement: "supplied_ahead", note: "Analysed definition is newer." },
        key_form: "WO number",
        verdict: "Active, duplicated by the ESB route.",
        document_refs: ["03_INTEGRATION_CATALOG.md#workorders"],
      }],
      delivery_paths: [],
      transformations: [{
        ...claim,
        id: "transformation:1",
        kind: "value_domain",
        target_field: "serviceOrders.status",
        mappings: [{
          source: "GGK",
          target: "In Process",
          mapping_type: "value",
          expression: "substring(DESCRIPTION, 1, 50)",
          category: "status",
          reaches_target: "partial",
          note: "Truncated at 50 characters.",
        }],
      }],
      business_rules: [{
        ...claim,
        id: "rule:1",
        kind: "precedence",
        statement: "The 4FS value wins over the AssetSys value.",
        inputs: ["WORKORDER.STATUS", "serviceOrders.status"],
        output: "serviceOrders.status",
        stream_ids: ["stream:1"],
        transformation_ids: ["transformation:1"],
        owner: "Service desk",
        owner_question: "Should 4FS keep overriding the AssetSys status?",
      }],
      controls: [],
      risks: [],
      unknowns: [],
      runtime_observations: [{
        ...claim,
        id: "runtime:1",
        stream_ids: ["stream:1"],
        observed_behavior: "Runs every 30 minutes.",
        observation_window: "90 days",
        kind: "cadence",
        source: "TMC",
        metrics: [{ label: "Runs", value: "2122", unit: "runs", window: "90 days", source: "TMC PROD", note: "" }],
      }],
      conflicts: [],
      artifact_annotations: [],
      component_annotations: [],
      lineage_annotations: [],
      coverage: { total_artifacts: 1, cited_artifacts: 1, total_evidence: 1, cited_evidence: 1, coverage_ratio: 1, notes: [] },
    });

    const stream = topology.data_streams[0];
    expect(stream.mechanism).toBe("tRunJob child");
    expect(stream.generation).toBe("Talend 7.3");
    expect(stream.direction_pattern).toBe("hybrid");
    expect(stream.lifecycle).toBe("duplicate");
    expect(stream.contract_width).toEqual({ offered: 412, mapped: 27, written: 12, note: "12 of 412 attributes persist." });
    expect(stream.conditions?.map((condition) => condition.kind)).toEqual(["trigger", "ordering"]);
    expect(stream.deployment?.agreement).toBe("supplied_ahead");
    expect(stream.key_form).toBe("WO number");
    expect(stream.verdict).toContain("duplicated");
    expect(stream.document_refs).toEqual(["03_INTEGRATION_CATALOG.md#workorders"]);

    expect(topology.transformations[0].kind).toBe("value_domain");
    expect(topology.transformations[0].target_field).toBe("serviceOrders.status");
    expect(topology.transformations[0].mappings[0]).toMatchObject({
      expression: "substring(DESCRIPTION, 1, 50)",
      category: "status",
      reaches_target: "partial",
      note: "Truncated at 50 characters.",
    });

    expect(topology.business_rules?.[0]).toMatchObject({
      kind: "precedence",
      owner: "Service desk",
      owner_question: "Should 4FS keep overriding the AssetSys status?",
      inputs: ["WORKORDER.STATUS", "serviceOrders.status"],
      output: "serviceOrders.status",
    });

    expect(topology.runtime_observations?.[0].kind).toBe("cadence");
    expect(topology.runtime_observations?.[0].source).toBe("TMC");
    expect(topology.runtime_observations?.[0].metrics?.[0]).toMatchObject({ label: "Runs", value: "2122", window: "90 days" });
  });

  /**
   * Published topology files predate every slot above. Adding them must not
   * make one of those files unparseable, and must not invent a value for a fact
   * its producer never asserted.
   */
  it("still accepts a topology published before the additive slots existed", () => {
    const topology = BusinessTopologySchema.parse(rawWorkorderTopology);

    expect(topology.data_streams.length).toBeGreaterThan(0);
    const stream = topology.data_streams[0];
    expect(stream.mechanism).toBeUndefined();
    expect(stream.direction_pattern).toBeUndefined();
    expect(stream.lifecycle).toBeUndefined();
    expect(stream.contract_width).toBeUndefined();
    expect(stream.conditions).toBeUndefined();
    expect(stream.deployment).toBeUndefined();
    expect(stream.verdict).toBeUndefined();
    expect(stream.document_refs).toBeUndefined();
    expect(topology.business_rules).toBeUndefined();
  });
});
