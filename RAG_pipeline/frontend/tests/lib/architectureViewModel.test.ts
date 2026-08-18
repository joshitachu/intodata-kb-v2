import { describe, expect, it } from "vitest";
import rawTopology from "../fixtures/werkorder_org_business_topology.json";
import { architectureFlowsForPair, architectureRelationshipPairs, normalizeArchitectureL1, relationshipIntegrationNodes } from "@/lib/architectureViewModel";
import { BusinessTopologySchema } from "@/types/landscape";

const topology = BusinessTopologySchema.parse(rawTopology);

describe("normalizeArchitectureL1", () => {
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
