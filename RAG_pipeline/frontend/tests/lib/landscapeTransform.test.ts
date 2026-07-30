import { describe, expect, it } from "vitest";
import { landscapeEdgeKey, landscapeToFlow } from "@/lib/landscapeTransform";
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
  it("keeps identically labelled applications distinct by scanner ID", () => {
    const result = landscapeToFlow(landscape);
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

  it("searches by label while retaining only direct scanner-computed context", () => {
    const result = landscapeToFlow(landscape, { query: "Maximo" });
    expect(result.nodes.map((node) => node.id)).toEqual(expect.arrayContaining(["system:maximo", "application:one"]));
    expect(result.nodes.map((node) => node.id)).not.toContain("application:two");
  });
});
