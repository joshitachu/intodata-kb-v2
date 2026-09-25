import { describe, expect, it } from "vitest";
import {
  buildBusinessHealthOverlay,
  buildDesignNameIndex,
  buildStreamHealth,
  canCarryImpact,
  propagateStreamImpact,
  rollUpSystemHealth,
  streamEffectiveStatus,
  UNMEASURED_REASON_TEXT,
} from "@/lib/businessHealth";
import type { BusinessDataStream, BusinessTopology } from "@/types/landscape";
import type { RuntimeJobHealth } from "@/types/runtime";

/**
 * NOTE ON WHAT IS PINNED HERE.
 *
 * Nothing in this suite asserts an absolute green/red/grey count for a real
 * estate. Those numbers are a function of whatever the runtime mirror holds at
 * poll time and drift by the hour. What is pinned is structural: the direction
 * of propagation, that lifecycle rows read as healthy when they are up, that an
 * unmeasured thing carries a REASON and is never the same value as a healthy
 * one, and that the join resolves through the artifact annotations.
 */

function stream(overrides: Partial<BusinessDataStream> & { id: string; source_system_id: string; destination_system_id: string }): BusinessDataStream {
  return {
    name: overrides.id,
    description: "",
    business_object_ids: [],
    business_summary: "",
    integration_solution_ids: [],
    protocols: [],
    requiredness: "required",
    status: "observed",
    confidence: 0.9,
    control_ids: [],
    risk_ids: [],
    unknown_ids: [],
    evidence_ids: [],
    artifact_ids: [],
    lifecycle: "active",
    ...overrides,
  } as BusinessDataStream;
}

function topologyOf(
  systems: string[],
  streams: BusinessDataStream[],
  annotations: { artifactId: string; design: string }[] = [],
): BusinessTopology {
  return {
    schema_version: "1.0",
    scan_fingerprint: "fp",
    metadata: { project_id: "p", scan_fingerprint: "fp", generated_by: "test", model_name: "" },
    systems: systems.map((id) => ({
      id,
      name: id,
      aliases: [],
      kind: "software_system",
      description: "",
      status: "observed",
      confidence: 0.9,
      evidence_ids: [],
    })),
    integration_solutions: [],
    business_objects: [],
    data_streams: streams,
    delivery_paths: [],
    transformations: [],
    controls: [],
    risks: [],
    unknowns: [],
    artifact_annotations: annotations.map(({ artifactId, design }) => ({
      id: `ann-${design}`,
      name: design,
      description: "",
      status: "observed",
      confidence: 0.9,
      evidence_ids: [artifactId],
      artifact_id: design,
      business_role: "",
    })),
    component_annotations: [],
    lineage_annotations: [],
    coverage: {
      total_artifacts: 0,
      cited_artifacts: 0,
      total_evidence: 0,
      cited_evidence: 0,
      coverage_ratio: 0,
      notes: [],
    },
  } as BusinessTopology;
}

function health(overrides: Partial<RuntimeJobHealth> & { job_name: string }): RuntimeJobHealth {
  return {
    project_id: "p",
    source_key: "tmc-1",
    job_id: `job-${overrides.job_name}`,
    total_runs: 10,
    failed_runs: 0,
    failure_rate: 0,
    current_failure_streak: 0,
    latest_run_at: "2026-09-20T09:00:00Z",
    latest_state: "success",
    ...overrides,
  };
}

describe("buildDesignNameIndex", () => {
  it("resolves an artifact digest to the plain design name through the annotations", () => {
    const topology = topologyOf(["sys-a"], [], [{ artifactId: "artifact:95152b0425f1", design: "collector_edifact_sales_orders_c003" }]);
    const index = buildDesignNameIndex(topology);
    expect(index.get("artifact:95152b0425f1")).toEqual(["collector_edifact_sales_orders_c003"]);
  });

  it("resolves through the evidence catalogue when the topology has no annotations", () => {
    // Every GENERATED topology currently served carries zero artifact_annotations
    // (werkorder, schepenhuizen-talend, TEST_V2, cdc_pack), and the artifact ->
    // design bridge lives in topology_evidence.artifacts instead. Resolving only
    // through annotations renders the entire overlay grey on all of them.
    const topology = topologyOf(["sys-a"], [], []);
    const index = buildDesignNameIndex(topology, {
      artifacts: [{
        id: "artifact:c801612fe8aa", name: "AssetCheck2Vise_CreateKTAResult_SyncDate",
        job_id: "AssetCheck2Vise_CreateKTAResult_SyncDate", source_root: "", artifact_kind: "", artifact_role: "",
      }],
      components: [], lineage: [], relations: [],
    });
    expect(index.get("artifact:c801612fe8aa")).toEqual(["AssetCheck2Vise_CreateKTAResult_SyncDate"]);
  });

  it("falls back to job_id when the catalogue entry has no separate name", () => {
    const topology = topologyOf(["sys-a"], [], []);
    const index = buildDesignNameIndex(topology, {
      artifacts: [{
        id: "artifact:deadbeef0001", name: "", job_id: "j000_GetMessagesFromQueue_Cloud",
        source_root: "", artifact_kind: "", artifact_role: "",
      }],
      components: [], lineage: [], relations: [],
    });
    expect(index.get("artifact:deadbeef0001")).toEqual(["j000_GetMessagesFromQueue_Cloud"]);
  });

  it("indexes only artifact: evidence, so a channel id can never resolve to a design", () => {
    const topology = topologyOf(["sys-a"], [], []);
    topology.artifact_annotations = [{
      id: "ann-x", name: "x", description: "", status: "observed", confidence: 0.9,
      evidence_ids: ["artifact:aaa", "channel:bbb"], artifact_id: "design_x", business_role: "",
    }];
    const index = buildDesignNameIndex(topology);
    expect(index.has("artifact:aaa")).toBe(true);
    expect(index.has("channel:bbb")).toBe(false);
  });
});

describe("buildStreamHealth", () => {
  const topology = topologyOf(
    ["sys-a", "sys-b"],
    [stream({ id: "str-1", source_system_id: "sys-a", destination_system_id: "sys-b", artifact_ids: ["artifact:aaa", "artifact:bbb"] })],
    [
      { artifactId: "artifact:aaa", design: "design_one" },
      { artifactId: "artifact:bbb", design: "design_two" },
    ],
  );

  it("binds resolved designs to runtime rows and reports the worst bound status", () => {
    const index = buildStreamHealth(topology, [
      health({ job_name: "design_one", latest_state: "success" }),
      health({ job_name: "design_two", latest_state: "failed", failure_rate: 1, current_failure_streak: 3 }),
    ]);
    const s = index.byStream.get("str-1")!;
    expect(s.resolvedDesignCount).toBe(2);
    expect(s.boundDesignCount).toBe(2);
    expect(s.status).toBe("down");
    expect(s.measured).toBe(true);
    expect(s.unmeasuredReason).toBeNull();
    expect(s.unmeasuredDetail).toBe("");
    expect(index.measuredStreamCount).toBe(1);
  });

  it("counts a resolved design the mirror does not know as unbound, and still measures the rest", () => {
    const index = buildStreamHealth(topology, [health({ job_name: "design_one", latest_state: "success" })]);
    const s = index.byStream.get("str-1")!;
    expect(s.boundDesignCount).toBe(1);
    expect(s.unboundDesignNames).toEqual(["design_two"]);
    expect(s.measured).toBe(true);
    expect(s.status).toBe("healthy");
  });

  it("binds through artifact_name when the scheduled task name differs", () => {
    const index = buildStreamHealth(topology, [
      health({ job_name: "PRD_ONE", artifact_name: "design_one", latest_state: "success" }),
      health({ job_name: "PRD_TWO", artifact_name: "design_two", latest_state: "success" }),
    ]);
    expect(index.byStream.get("str-1")!.boundDesignCount).toBe(2);
    expect(index.byStream.get("str-1")!.status).toBe("healthy");
  });

  it("reads a lifecycle row that is up as healthy, not as unknown", () => {
    const index = buildStreamHealth(topology, [
      health({ job_name: "design_one", latest_state: "running", liveness: "lifecycle", failure_rate: 0.5, current_failure_streak: 2 }),
    ]);
    expect(index.byStream.get("str-1")!.status).toBe("healthy");
  });

  describe("unmeasured always carries a reason, and is never healthy", () => {
    it("no runtime rows at all -> no_runtime_mirror", () => {
      const index = buildStreamHealth(topology, []);
      const s = index.byStream.get("str-1")!;
      expect(s.measured).toBe(false);
      expect(s.status).toBe("unknown");
      expect(s.unmeasuredReason).toBe("no_runtime_mirror");
      expect(s.unmeasuredDetail).toBe(UNMEASURED_REASON_TEXT.no_runtime_mirror);
      expect(s.unmeasuredDetail.length).toBeGreaterThan(0);
    });

    it("rows exist but none is this stream's design -> designs_not_in_runtime", () => {
      const index = buildStreamHealth(topology, [health({ job_name: "something_else" })]);
      const s = index.byStream.get("str-1")!;
      expect(s.measured).toBe(false);
      expect(s.status).toBe("unknown");
      expect(s.unmeasuredReason).toBe("designs_not_in_runtime");
      expect(s.unmeasuredDetail).toBe(UNMEASURED_REASON_TEXT.designs_not_in_runtime);
    });

    it("a stream citing nothing -> no_artifact_citations", () => {
      const bare = topologyOf(["sys-a", "sys-b"], [stream({ id: "str-bare", source_system_id: "sys-a", destination_system_id: "sys-b" })]);
      const index = buildStreamHealth(bare, [health({ job_name: "design_one" })]);
      const s = index.byStream.get("str-bare")!;
      expect(s.unmeasuredReason).toBe("no_artifact_citations");
      expect(s.status).toBe("unknown");
    });

    it("citations that resolve to no annotation -> unresolved_artifact_citations", () => {
      const orphan = topologyOf(
        ["sys-a", "sys-b"],
        [stream({ id: "str-orphan", source_system_id: "sys-a", destination_system_id: "sys-b", artifact_ids: ["artifact:zzz"] })],
      );
      const index = buildStreamHealth(orphan, [health({ job_name: "design_one" })]);
      const s = index.byStream.get("str-orphan")!;
      expect(s.unmeasuredReason).toBe("unresolved_artifact_citations");
      expect(s.unresolvedArtifactIds).toEqual(["artifact:zzz"]);
    });

    it("every unmeasured reason resolves to a non-empty sentence", () => {
      for (const text of Object.values(UNMEASURED_REASON_TEXT)) expect(text.length).toBeGreaterThan(20);
    });
  });
});

describe("propagateStreamImpact", () => {
  // a -> b -> c -> d, plus an optional and a reference_only branch off b.
  const chain = topologyOf(
    ["sys-a", "sys-b", "sys-c", "sys-d", "sys-opt", "sys-ref"],
    [
      stream({ id: "s-ab", source_system_id: "sys-a", destination_system_id: "sys-b", artifact_ids: ["artifact:ab"] }),
      stream({ id: "s-bc", source_system_id: "sys-b", destination_system_id: "sys-c", artifact_ids: ["artifact:bc"] }),
      stream({ id: "s-cd", source_system_id: "sys-c", destination_system_id: "sys-d", artifact_ids: ["artifact:cd"] }),
      stream({ id: "s-bopt", source_system_id: "sys-b", destination_system_id: "sys-opt", requiredness: "optional" }),
      stream({ id: "s-bref", source_system_id: "sys-b", destination_system_id: "sys-ref", lifecycle: "reference_only" }),
    ],
    [
      { artifactId: "artifact:ab", design: "design_ab" },
      { artifactId: "artifact:bc", design: "design_bc" },
      { artifactId: "artifact:cd", design: "design_cd" },
    ],
  );
  const downAb = [health({ job_name: "design_ab", latest_state: "failed", failure_rate: 1, current_failure_streak: 2 })];

  it("walks source -> destination: a broken a->b starves b, never a", () => {
    const index = buildStreamHealth(chain, downAb);
    const impact = propagateStreamImpact(chain, index.byStream);
    expect([...impact.impactedSystems.keys()]).toEqual(["sys-b"]);
    expect(impact.impactedSystems.has("sys-a")).toBe(false);
    expect(impact.seedStreamIds.has("s-ab")).toBe(true);
  });

  it("records the chain so a tooltip can say 'impacted via X -> Y'", () => {
    const index = buildStreamHealth(chain, downAb);
    const impact = propagateStreamImpact(chain, index.byStream);
    const reached = impact.impactedSystems.get("sys-b")!;
    expect(reached.systemPath).toEqual(["sys-a", "sys-b"]);
    expect(reached.streamPath).toEqual(["s-ab"]);
    expect(reached.rootStreamId).toBe("s-ab");
    expect(reached.hopDistance).toBe(1);
  });

  it("stops at one hop by default", () => {
    const index = buildStreamHealth(chain, downAb);
    const impact = propagateStreamImpact(chain, index.byStream);
    expect(impact.impactedSystems.has("sys-c")).toBe(false);
    expect(impact.impactedStreams.size).toBe(0);
  });

  it("travels exactly as far as maxHops allows, shortest-path first", () => {
    const index = buildStreamHealth(chain, downAb);
    const two = propagateStreamImpact(chain, index.byStream, { maxHops: 2 });
    expect([...two.impactedSystems.keys()].sort()).toEqual(["sys-b", "sys-c"]);
    expect([...two.impactedStreams.keys()]).toEqual(["s-bc"]);
    expect(two.impactedSystems.get("sys-c")!.systemPath).toEqual(["sys-a", "sys-b", "sys-c"]);

    const three = propagateStreamImpact(chain, index.byStream, { maxHops: 3 });
    expect([...three.impactedSystems.keys()].sort()).toEqual(["sys-b", "sys-c", "sys-d"]);
  });

  it("does not travel along an optional or a non-active stream", () => {
    const index = buildStreamHealth(chain, downAb);
    const wide = propagateStreamImpact(chain, index.byStream, { maxHops: 4 });
    expect(wide.impactedSystems.has("sys-opt")).toBe(false);
    expect(wide.impactedSystems.has("sys-ref")).toBe(false);
    expect(canCarryImpact(chain.data_streams.find((s) => s.id === "s-bopt")!)).toBe(false);
    expect(canCarryImpact(chain.data_streams.find((s) => s.id === "s-bref")!)).toBe(false);
  });

  it("treats an unset lifecycle as not traversable rather than inventing impact", () => {
    const legacy = topologyOf(
      ["sys-a", "sys-b"],
      [stream({ id: "s-ab", source_system_id: "sys-a", destination_system_id: "sys-b", artifact_ids: ["artifact:ab"], lifecycle: undefined })],
      [{ artifactId: "artifact:ab", design: "design_ab" }],
    );
    const index = buildStreamHealth(legacy, downAb);
    expect(index.byStream.get("s-ab")!.status).toBe("down");
    expect(propagateStreamImpact(legacy, index.byStream).impactedSystems.size).toBe(0);
  });

  it("is cycle-safe", () => {
    const cyclic = topologyOf(
      ["sys-a", "sys-b"],
      [
        stream({ id: "s-ab", source_system_id: "sys-a", destination_system_id: "sys-b", artifact_ids: ["artifact:ab"] }),
        stream({ id: "s-ba", source_system_id: "sys-b", destination_system_id: "sys-a" }),
      ],
      [{ artifactId: "artifact:ab", design: "design_ab" }],
    );
    const index = buildStreamHealth(cyclic, downAb);
    const impact = propagateStreamImpact(cyclic, index.byStream, { maxHops: 99 });
    expect([...impact.impactedSystems.keys()].sort()).toEqual(["sys-a", "sys-b"]);
  });

  it("only a down stream seeds impact; a degraded one does not", () => {
    const index = buildStreamHealth(chain, [
      health({ job_name: "design_ab", latest_state: "success", failure_rate: 0.5 }),
    ]);
    expect(index.byStream.get("s-ab")!.status).toBe("degraded");
    expect(propagateStreamImpact(chain, index.byStream).impactedSystems.size).toBe(0);
  });
});

describe("rollUpSystemHealth", () => {
  const pair = topologyOf(
    ["sys-a", "sys-b"],
    [stream({ id: "s-ab", source_system_id: "sys-a", destination_system_id: "sys-b", artifact_ids: ["artifact:ab"] })],
    [{ artifactId: "artifact:ab", design: "design_ab" }],
  );

  it("blames the delivering system and marks the receiving one as impacted", () => {
    const index = buildStreamHealth(pair, [health({ job_name: "design_ab", latest_state: "failed", failure_rate: 1, current_failure_streak: 1 })]);
    const impact = propagateStreamImpact(pair, index.byStream);
    const bySystem = rollUpSystemHealth(pair, index, impact);
    expect(bySystem.get("sys-a")!.status).toBe("down");
    expect(bySystem.get("sys-b")!.status).toBe("impacted");
    expect(bySystem.get("sys-b")!.impact!.systemPath).toEqual(["sys-a", "sys-b"]);
  });

  it("paints an unmeasured system grey with a reason, never healthy", () => {
    const index = buildStreamHealth(pair, []);
    const impact = propagateStreamImpact(pair, index.byStream);
    const bySystem = rollUpSystemHealth(pair, index, impact);
    for (const systemId of ["sys-a", "sys-b"]) {
      const system = bySystem.get(systemId)!;
      expect(system.measured).toBe(false);
      expect(system.status).toBe("unknown");
      expect(system.status).not.toBe("healthy");
      expect(system.unmeasuredDetail.length).toBeGreaterThan(0);
    }
  });

  it("reports no_streams for a system nothing flows through", () => {
    const lonely = topologyOf(["sys-a", "sys-b", "sys-lonely"], [stream({ id: "s-ab", source_system_id: "sys-a", destination_system_id: "sys-b", artifact_ids: ["artifact:ab"] })], [{ artifactId: "artifact:ab", design: "design_ab" }]);
    const index = buildStreamHealth(lonely, [health({ job_name: "design_ab" })]);
    const bySystem = rollUpSystemHealth(lonely, index, propagateStreamImpact(lonely, index.byStream));
    expect(bySystem.get("sys-lonely")!.unmeasuredReason).toBe("no_streams");
    expect(bySystem.get("sys-lonely")!.status).toBe("unknown");
  });
});

describe("streamEffectiveStatus", () => {
  const chain = topologyOf(
    ["sys-a", "sys-b", "sys-c"],
    [
      stream({ id: "s-ab", source_system_id: "sys-a", destination_system_id: "sys-b", artifact_ids: ["artifact:ab"] }),
      stream({ id: "s-bc", source_system_id: "sys-b", destination_system_id: "sys-c", artifact_ids: ["artifact:bc"] }),
    ],
    [
      { artifactId: "artifact:ab", design: "design_ab" },
      { artifactId: "artifact:bc", design: "design_bc" },
    ],
  );

  it("keeps a stream's own measured status and only upgrades an unmeasured one to impacted", () => {
    const index = buildStreamHealth(chain, [
      health({ job_name: "design_ab", latest_state: "failed", failure_rate: 1, current_failure_streak: 1 }),
    ]);
    const impact = propagateStreamImpact(chain, index.byStream, { maxHops: 2 });
    expect(streamEffectiveStatus("s-ab", index.byStream, impact)).toBe("down");
    expect(streamEffectiveStatus("s-bc", index.byStream, impact)).toBe("impacted");
  });
});

describe("buildBusinessHealthOverlay", () => {
  it("is inactive when nothing binds, so an all-grey board cannot read as an all-green one", () => {
    const topology = topologyOf(
      ["sys-a", "sys-b"],
      [stream({ id: "s-ab", source_system_id: "sys-a", destination_system_id: "sys-b", artifact_ids: ["artifact:ab"] })],
      [{ artifactId: "artifact:ab", design: "design_ab" }],
    );
    const overlay = buildBusinessHealthOverlay(topology, []);
    expect(overlay.active).toBe(false);
    expect(overlay.index.measuredStreamCount).toBe(0);
    expect(overlay.index.unmeasuredStreamCount).toBe(1);
    expect(overlay.bySystem.get("sys-a")!.status).toBe("unknown");
  });
});
