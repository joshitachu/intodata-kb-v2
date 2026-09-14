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
  /**
   * A field mapping, a value domain, a derived rule and a constant set are four
   * different kinds of fact. Flat-listed they are indistinguishable, which is
   * how a status value table ends up reading as a business decision, so the
   * projection -- not the renderer -- owns the partition.
   */
  it("splits a stream's transformations by kind and resolves its rules and runtime by id", () => {
    const claim = { name: "", description: "", status: "observed" as const, confidence: 0.9, evidence_ids: ["ev:1"] };
    const transformation = (id: string, kind: string, extra: Record<string, unknown> = {}) => ({
      ...claim, id, name: id, kind, stream_ids: ["ds-wo"], integration_solution_ids: [], artifact_ids: [], ...extra,
    });
    const topology = BusinessTopologySchema.parse({
      ...rawTopology,
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
        artifact_ids: [],
        control_ids: [], risk_ids: [], unknown_ids: [], conflict_ids: [],
        runtime_observation_ids: ["ro-cadence"],
        lifecycle: "active",
        direction_pattern: "push",
        contract_width: { offered: 412, mapped: 27, written: 12, note: "" },
        conditions: [{ kind: "trigger", statement: "Alleen bij status GEREED.", evidence_note: "" }],
        verdict: "Actief.",
        document_refs: ["03_INTEGRATION_CATALOG.md"],
      }],
      delivery_paths: [],
      transformations: [
        transformation("tr-fields", "field_mapping", {
          mappings: [
            { source: "WORKORDER.WONUM", target: "serviceOrders.orderNo", mapping_type: "direct", reaches_target: "yes" },
            { source: "WORKORDER.DESCRIPTION", target: "serviceOrders.description", mapping_type: "derived", expression: "substring(DESCRIPTION, 1, 50)", reaches_target: "partial" },
          ],
        }),
        transformation("tr-status", "value_domain", { target_field: "serviceOrders.status", mappings: [{ source: "GGK", target: "In Process", mapping_type: "value" }] }),
        transformation("tr-priority", "value_domain", { target_field: "serviceOrders.status", mappings: [{ source: "AFG", target: "Closed", mapping_type: "value" }] }),
        transformation("tr-derived", "derived_rule", { mappings: [{ source: "WORKORDER.SITEID", target: "serviceOrders.site", mapping_type: "derived", expression: "lookup(SITEID)" }] }),
        transformation("tr-constant", "constant_set", { mappings: [{ source: "\"MAXIMO\"", target: "serviceOrders.origin", mapping_type: "constant" }] }),
        transformation("tr-legacy", "unknown", { mappings: [{ source: "A", target: "B" }] }),
      ],
      business_rules: [
        { ...claim, id: "br-status", name: "Statusvertaling", kind: "value_mapping", statement: "GGK wordt In Process.", inputs: [], output: "", stream_ids: [], transformation_ids: ["tr-status"], owner: "Servicedesk", owner_question: "Klopt dit?" },
        { ...claim, id: "br-other", name: "Andere stroom", kind: "selection", statement: "Niet van deze stroom.", inputs: [], output: "", stream_ids: ["ds-other"], transformation_ids: [], owner: "", owner_question: "" },
      ],
      controls: [], risks: [], unknowns: [], conflicts: [],
      runtime_observations: [{
        ...claim, id: "ro-cadence", stream_ids: [], delivery_path_ids: [], observed_behavior: "Draait elk half uur.",
        observation_window: "90 dagen", kind: "cadence", source: "TMC",
        metrics: [{ label: "Runs", value: "2122", unit: "runs", window: "90 dagen", source: "TMC PROD", note: "" }],
      }],
      artifact_annotations: [], component_annotations: [], lineage_annotations: [],
    });

    const model = normalizeArchitectureL1(topology);
    const pair = architectureRelationshipPairs(model)[0];
    const [flow] = architectureFlowsForPair(topology, pair);

    expect(flow.fieldMappings.map((item) => item.id)).toEqual(["tr-fields"]);
    expect(flow.valueDomains.map((item) => item.id)).toEqual(["tr-status", "tr-priority"]);
    expect(flow.derivedRules.map((item) => item.id)).toEqual(["tr-derived"]);
    expect(flow.constantSets.map((item) => item.id)).toEqual(["tr-constant"]);
    // An unclassified transformation is NOT promoted into a classified bucket.
    expect(flow.unclassifiedTransformations.map((item) => item.id)).toEqual(["tr-legacy"]);
    expect(flow.transformations).toHaveLength(6);

    // Field rows are the mapping-level view: value domains are deliberately
    // absent from them and grouped under the field they constrain instead.
    expect(flow.fieldRows.map((row) => [row.index, row.targetField, row.sourceOrExpression])).toEqual([
      [1, "serviceOrders.orderNo", "WORKORDER.WONUM"],
      [2, "serviceOrders.description", "substring(DESCRIPTION, 1, 50)"],
      [3, "serviceOrders.site", "lookup(SITEID)"],
      [4, "serviceOrders.origin", "\"MAXIMO\""],
    ]);
    expect(flow.fieldRows.map((row) => row.reachesTarget)).toEqual(["yes", "partial", "unknown", "unknown"]);
    expect(flow.valueDomainGroups).toHaveLength(1);
    expect(flow.valueDomainGroups[0].targetField).toBe("serviceOrders.status");
    expect(flow.valueDomainGroups[0].transformations.map((item) => item.id)).toEqual(["tr-status", "tr-priority"]);

    // Rules reach a stream either directly or through one of its transformations.
    expect(flow.businessRules.map((rule) => rule.id)).toEqual(["br-status"]);
    expect(flow.runtimeObservations.map((item) => item.id)).toEqual(["ro-cadence"]);
    expect(flow.runtimeMetrics.map((metric) => [metric.label, metric.value, metric.window])).toEqual([
      ["Runs", "2122", "90 dagen"],
    ]);

    expect(flow.lifecycle).toBe("active");
    expect(flow.directionPattern).toBe("push");
    expect(flow.contractWidth).toMatchObject({ offered: 412, mapped: 27, written: 12 });
    expect(flow.conditions.map((condition) => condition.statement)).toEqual(["Alleen bij status GEREED."]);
    expect(flow.documentRefs).toEqual(["03_INTEGRATION_CATALOG.md"]);
  });

  /** An older payload resolves to definite "not established" values, never undefined. */
  it("resolves absent structured slots without inventing a value", () => {
    const model = normalizeArchitectureL1(topology);
    const pair = architectureRelationshipPairs(model).find((item) =>
      item.streamIds.some((streamId) => streamId.includes("maximo--4ps")),
    )!;
    const [flow] = architectureFlowsForPair(topology, pair);

    expect(flow.lifecycle).toBe("unknown");
    expect(flow.directionPattern).toBe("unknown");
    expect(flow.contractWidth).toBeNull();
    expect(flow.deployment).toBeNull();
    expect(flow.mechanism).toBe("");
    expect(flow.verdict).toBe("");
    expect(flow.conditions).toEqual([]);
    expect(flow.documentRefs).toEqual([]);
    expect(flow.fieldRows).toEqual([]);
    expect(flow.valueDomainGroups).toEqual([]);
    expect(flow.businessRules).toEqual([]);
    expect(flow.runtimeMetrics).toEqual([]);
  });
});

describe("relationship cap", () => {
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
  // 21 systems fanning out of one hub = 20 relationships, above the default 18.
  const hub = BusinessTopologySchema.parse({
    ...rawTopology,
    metadata: { ...rawTopology.metadata, project_id: "wide-estate" },
    systems: [
      { ...claim, id: "sys-hub", name: "Hub" },
      ...Array.from({ length: 20 }, (_, index) => ({
        ...claim, id: `sys-leaf-${index}`, name: `Leaf ${index}`,
      })),
    ],
    integration_solutions: [], business_objects: [], delivery_paths: [], transformations: [],
    controls: [], risks: [], unknowns: [], artifact_annotations: [], component_annotations: [],
    lineage_annotations: [],
    data_streams: Array.from({ length: 20 }, (_, index) =>
      stream(`ds-${index}`, "sys-hub", `sys-leaf-${index}`)),
  });

  it("hides the overflow by default and reports exactly what it hid", () => {
    const model = normalizeArchitectureL1(hub);
    expect(model.relationships).toHaveLength(18);
    expect(model.hiddenRelationshipIds).toHaveLength(2);
    // The omission is only acceptable because it is reported: nothing else on
    // the model tells a renderer that two relationships exist off-screen.
    expect(new Set([...model.relationships.map((item) => item.id), ...model.hiddenRelationshipIds]).size).toBe(20);
  });

  it("draws every relationship when the caller lifts the cap", () => {
    const model = normalizeArchitectureL1(hub, { maxVisibleRelationships: Number.MAX_SAFE_INTEGER });
    expect(model.relationships).toHaveLength(20);
    expect(model.hiddenRelationshipIds).toEqual([]);
  });

  it("keeps the default when no cap is supplied", () => {
    expect(normalizeArchitectureL1(hub, {}).relationships).toHaveLength(18);
  });
});
