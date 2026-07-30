import { describe, expect, it } from "vitest";
import { integrationDetailForEdge } from "@/lib/integrationDetailTransform";
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
});
