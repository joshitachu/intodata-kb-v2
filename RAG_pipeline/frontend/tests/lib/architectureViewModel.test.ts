import { describe, expect, it } from "vitest";
import rawTopology from "../fixtures/werkorder_org_business_topology.json";
import { architectureFlowsForPair, architectureRelationshipPairs, normalizeArchitectureL1, relationshipIntegrationNodes } from "@/lib/architectureViewModel";
import { BusinessTopologySchema } from "@/types/landscape";

const topology = BusinessTopologySchema.parse(rawTopology);

describe("normalizeArchitectureL1", () => {
  it("derives a non-Werkorder anchor and directional columns from business streams", () => {
    const claim = {
      aliases: [], kind: "software_system" as const, description: "", purpose: "", owner: "",
      status: "confirmed" as const, confidence: 0.9, evidence_ids: [],
    };
    const stream = (id: string, source: string, destination: string) => ({
      id, source_system_id: source, destination_system_id: destination, name: id, description: "",
      business_object_ids: [], business_summary: id, integration_solution_ids: [], protocols: [],
      requiredness: "not_established" as const, delivery_path_id: null, status: "confirmed" as const,
      confidence: 0.9, control_ids: [], risk_ids: [], unknown_ids: [], runtime_observation_ids: [],
      conflict_ids: [], artifact_ids: [], evidence_ids: [],
    });
    const scherpenhuizen = BusinessTopologySchema.parse({
      ...rawTopology,
      metadata: { ...rawTopology.metadata, project_id: "scherpenhuizen-talend" },
      systems: [
        { ...claim, id: "sys-oxin", name: "Oxin" },
        { ...claim, id: "sys-jde", name: "JD Edwards" },
        { ...claim, id: "sys-centric-edi", name: "Centric" },
        { ...claim, id: "sys-tmc", name: "Talend Cloud" },
      ],
      integration_solutions: [], business_objects: [],
      data_streams: [
        stream("oxin-to-jde-1", "sys-oxin", "sys-jde"),
        stream("oxin-to-jde-2", "sys-oxin", "sys-jde"),
        stream("jde-to-centric", "sys-jde", "sys-centric-edi"),
        stream("centric-to-tmc", "sys-centric-edi", "sys-tmc"),
      ],
      delivery_paths: [], transformations: [], controls: [], runtime_observations: [], conflicts: [], risks: [], unknowns: [],
      artifact_annotations: [], component_annotations: [], lineage_annotations: [],
    });

    const model = normalizeArchitectureL1(scherpenhuizen);

    expect(model.title).toBe("Scherpenhuizen Talend architecture");
    expect(model.anchorId).toBe("system:sys-jde");
    expect(model.nodes.find((node) => node.label === "JD Edwards")?.column).toBe("anchor");
    expect(model.nodes.find((node) => node.label === "Oxin")?.column).toBe("upstream");
    expect(model.nodes.find((node) => node.label === "Centric")?.column).toBe("downstream");
    expect(model.nodes.find((node) => node.label === "Talend Cloud")?.column).toBe("secondary");
    expect(model.relationships).toHaveLength(3);
  });

  it("keeps an evidence-bounded topology with no streams renderable", () => {
    const noStreamTopology = BusinessTopologySchema.parse({
      ...topology,
      systems: [topology.systems.find((system) => system.id === "sys-maximo")!],
      integration_solutions: [],
      data_streams: [],
    });

    const model = normalizeArchitectureL1(noStreamTopology);

    expect(model.relationships).toEqual([]);
    expect(model.nodes).toMatchObject([{ label: "Maximo", layer: "core" }]);
  });

  it("creates a bounded, traceable company architecture projection from real topology evidence", () => {
    const model = normalizeArchitectureL1(topology);

    expect(model.level).toBe("L1");
    expect(model.nodes.length).toBeLessThanOrEqual(15);
    expect(model.relationships.length).toBeLessThanOrEqual(18);
    expect(model.nodes.find((node) => node.label === "Maximo")).toMatchObject({ layer: "core" });
    expect(model.nodes.find((node) => node.label === "ETTM")).toMatchObject({ layer: "integration" });
    expect(model.nodes.find((node) => node.label === "ActiveMQ")).toMatchObject({ kind: "broker" });
    expect(model.nodes.find((node) => node.id === "group:supporting-and-unresolved")?.systemIds).toEqual(expect.arrayContaining([
      "sys-relatics", "sys-sharepoint", "sys-dds-visp-unresolved",
    ]));
  });

  it("collapses Maximo to 4PS streams into one L1 relationship without losing their ids", () => {
    const model = normalizeArchitectureL1(topology);
    const relationship = model.relationships.find((item) => item.source === "system:sys-maximo" && item.target === "system:sys-4ps");

    expect(relationship).toMatchObject({
      businessObjects: expect.arrayContaining(["WorkOrder", "Invoice", "PurchaseOrder"]),
      integrationSolutionIds: expect.arrayContaining(["int-sol-ettm"]),
    });
    expect(relationship?.streamIds).toHaveLength(6);
    expect(relationship?.streamIds.every((id) => topology.data_streams.some((stream) => stream.id === id))).toBe(true);
    expect(relationshipIntegrationNodes(relationship!)).toEqual(expect.arrayContaining(["integration:ettm"]));
  });

  it("keeps the raw graph available beyond the quiet overview", () => {
    const model = normalizeArchitectureL1(topology);
    const representedStreamIds = new Set(model.relationships.flatMap((relationship) => relationship.streamIds));
    const omittedStreamIds = new Set([...model.hiddenInternalStreamIds]);

    for (const relationshipId of model.hiddenRelationshipIds) {
      const [sourceTarget] = relationshipId.replace("relationship:", "").split("->");
      expect(sourceTarget).toBeTruthy();
    }
    expect(representedStreamIds.size + omittedStreamIds.size).toBeLessThanOrEqual(topology.data_streams.length);
    expect(model.hiddenRelationshipIds.length + model.hiddenInternalStreamIds.length).toBeGreaterThan(0);
  });

  it("collapses opposite directions into one L1 connection and restores direction in L2", () => {
    const model = normalizeArchitectureL1(topology);
    const pair = architectureRelationshipPairs(model).find((item) =>
      item.streamIds.some((streamId) => streamId.includes("maximo--4ps")),
    );

    expect(pair).toBeDefined();
    expect(pair?.directions).toHaveLength(2);
    expect(pair?.streamIds.length).toBeGreaterThan(6);
    const flows = architectureFlowsForPair(topology, pair!);
    expect(new Set(flows.map((flow) => flow.sourceSystemId))).toEqual(
      new Set(["sys-maximo", "sys-4ps"]),
    );
    expect(flows.every((flow) => flow.evidenceIds.length > 0)).toBe(true);
  });
});
