import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useTopologyNavigation } from "@/hooks/useTopologyNavigation";
import {
  buildTopologyGraphData,
  extractTargetsFromAgentEvent,
  navigateToTarget,
  parseTopologyTargets,
  resolveTopologyTarget,
  resolveTopologyTargets,
  toCopilotTarget,
  type TopologyGraphData,
} from "@/lib/topologyTargets";
import type { TopologyNavigation } from "@/hooks/useTopologyNavigation";
import type { ProjectLandscapeResponse } from "@/types/landscape";
import type { ManifestData } from "@/lib/graphTransform";
import type { PublicAgentEvent } from "@/types";

const US = "\u001f";

function navigationStub(): TopologyNavigation {
  return {
    stack: [{ kind: "project" }],
    current: { kind: "project" },
    depth: 1,
    descendToIntegration: vi.fn(),
    descendToGroup: vi.fn(),
    descendToJob: vi.fn(),
    descendToComponent: vi.fn(),
    ascend: vi.fn(),
    jumpTo: vi.fn(),
    reset: vi.fn(),
    restore: vi.fn(),
    replaceStack: vi.fn(),
  };
}

function scannerEdge(overrides: Record<string, unknown> = {}) {
  return {
    source: "talend-project:werkorder",
    target: "system:connection:0:sap",
    source_kind: "application",
    target_kind: "system",
    dependency_type: "data",
    mechanism: "db",
    direction: "out",
    reliability: "unknown",
    confidence: "certain",
    evidence_count: 2,
    provenance: [],
    ...overrides,
  };
}

const SCANNER_EDGE_KEY = [
  "application",
  "talend-project:werkorder",
  "system",
  "system:connection:0:sap",
  "data",
  "db",
  "out",
].join(US);

/** Landscape with a business overlay whose validation state is configurable. */
function landscape(options: {
  overlay?: { validated: boolean; stale: boolean };
} = {}): ProjectLandscapeResponse {
  const base = {
    project_id: "werkorder",
    applications: [
      {
        id: "talend-project:werkorder",
        source_root: "werkorder",
        source_roots: ["werkorder"],
        project_id: "werkorder",
        label: "Werkorder",
        description: "",
        role: "execution",
        artifact_count: 3,
        kinds_breakdown: {},
      },
    ],
    systems: [
      { id: "system:connection:0:sap", name: "SAP ERP", kind: "database", evidence: [], source_roots: [] },
    ],
    edges: [scannerEdge()],
    channels: [],
    interfaces: [],
    diagnostics: [],
    unmapped: [],
    summary: {},
  } as unknown as ProjectLandscapeResponse;

  if (!options.overlay) return base;

  return {
    ...base,
    topology_state: {
      available: true,
      validated: options.overlay.validated,
      stale: options.overlay.stale,
      source: "curated",
    },
    business_topology: {
      systems: [
        {
          id: "landscape-system:aaaa1111",
          name: "SAP ERP",
          aliases: [],
          kind: "software_system",
          description: "",
          status: "confirmed",
          confidence: 0.9,
          evidence_ids: [],
        },
        {
          id: "landscape-system:bbbb2222",
          name: "Werkorder",
          aliases: [],
          kind: "software_system",
          description: "",
          status: "confirmed",
          confidence: 0.9,
          evidence_ids: [],
        },
      ],
      integration_solutions: [],
      business_objects: [],
      data_streams: [
        {
          id: "stream-1",
          source_system_id: "landscape-system:bbbb2222",
          destination_system_id: "landscape-system:aaaa1111",
          name: "Order sync",
          description: "",
          business_object_ids: [],
          business_summary: "",
          integration_solution_ids: [],
          protocols: [],
          requiredness: "required",
          status: "confirmed",
          confidence: 0.9,
          control_ids: [],
          risk_ids: [],
          unknown_ids: [],
          evidence_ids: [],
          artifact_ids: [],
        },
      ],
      delivery_paths: [],
    },
  } as unknown as ProjectLandscapeResponse;
}

const MANIFEST: ManifestData = {
  job_calls: { tJob_OrderSync: ["tJob_Child"] },
  job_called_by: { tJob_Child: ["tJob_OrderSync"] },
  job_connections_used: {},
  connection_used_by: {},
  job_groups: { tJob_OrderSync: "Orders", tJob_Child: "Orders" },
  job_source_roots: { tJob_OrderSync: "werkorder", tJob_Child: "werkorder" },
};

function scannerGraph(): TopologyGraphData {
  return buildTopologyGraphData({ landscape: landscape(), manifest: MANIFEST });
}

function overlayGraph(): TopologyGraphData {
  return buildTopologyGraphData({
    landscape: landscape({ overlay: { validated: true, stale: false } }),
    manifest: MANIFEST,
  });
}

describe("buildTopologyGraphData", () => {
  it("uses the scanner id space unless a validated, non-stale overlay exists", () => {
    expect(scannerGraph().idSpace).toBe("scanner");
    expect(
      buildTopologyGraphData({ landscape: landscape({ overlay: { validated: false, stale: false } }) })
        .idSpace,
    ).toBe("scanner");
    expect(
      buildTopologyGraphData({ landscape: landscape({ overlay: { validated: true, stale: true } }) })
        .idSpace,
    ).toBe("scanner");
    expect(overlayGraph().idSpace).toBe("business-overlay");
  });

  it("keys overlay edges as business streams and scanner edges as 7-part keys", () => {
    expect([...overlayGraph().edgeKeys]).toEqual([`business-stream${US}stream-1`]);
    expect([...scannerGraph().edgeKeys]).toEqual([SCANNER_EDGE_KEY]);
  });
});

describe("resolveTopologyTarget navigation variants", () => {
  it("dispatches an integration system selection with its id intact", () => {
    const navigation = navigationStub();
    const resolution = resolveTopologyTarget(
      {
        intent: "reference" as const,
        label: "SAP ERP",
        level: { kind: "integration", selection: { kind: "system", systemId: "landscape-system:aaaa1111" } },
      },
      overlayGraph(),
    );

    expect(resolution.status).toBe("resolved");
    expect(navigateToTarget(resolution, navigation)).toBe(true);
    expect(navigation.descendToIntegration).toHaveBeenCalledWith({
      kind: "system",
      systemId: "landscape-system:aaaa1111",
    });
    expect(navigation.descendToGroup).not.toHaveBeenCalled();
    expect(navigation.descendToJob).not.toHaveBeenCalled();
  });

  it("dispatches an integration edge selection by its exact edge key", () => {
    const navigation = navigationStub();
    const resolution = resolveTopologyTarget(
      { intent: "reference" as const, label: "Werkorder to SAP", level: { kind: "integration", selection: { kind: "edge", edgeKey: SCANNER_EDGE_KEY } } },
      scannerGraph(),
    );

    expect(navigateToTarget(resolution, navigation)).toBe(true);
    expect(navigation.descendToIntegration).toHaveBeenCalledWith({
      kind: "edge",
      edgeKey: SCANNER_EDGE_KEY,
    });
  });

  it("dispatches a group by name plus sourceRoot", () => {
    const navigation = navigationStub();
    const resolution = resolveTopologyTarget(
      { intent: "reference" as const, label: "Orders", level: { kind: "group", groupName: "Orders", sourceRoot: "werkorder" } },
      scannerGraph(),
    );

    expect(navigateToTarget(resolution, navigation)).toBe(true);
    expect(navigation.descendToGroup).toHaveBeenCalledWith("Orders", "werkorder");
  });

  it("dispatches a job by name plus sourceRoot", () => {
    const navigation = navigationStub();
    const resolution = resolveTopologyTarget(
      { intent: "reference" as const, label: "tJob_OrderSync", level: { kind: "job", jobName: "tJob_OrderSync", sourceRoot: "werkorder" } },
      scannerGraph(),
    );

    expect(navigateToTarget(resolution, navigation)).toBe(true);
    expect(navigation.descendToJob).toHaveBeenCalledWith("tJob_OrderSync", "werkorder");
    expect(navigation.descendToComponent).not.toHaveBeenCalled();
  });

  it("pushes the job level before a component, because descendToComponent reads the stack top", () => {
    const navigation = navigationStub();
    const graph = buildTopologyGraphData({
      landscape: landscape(),
      manifest: MANIFEST,
      componentNamesByJob: { tJob_OrderSync: ["tMap_1"] },
    });
    const resolution = resolveTopologyTarget(
      {
        intent: "reference" as const,
        label: "tMap_1",
        level: {
          kind: "component",
          jobName: "tJob_OrderSync",
          componentName: "tMap_1",
          sourceRoot: "werkorder",
        },
      },
      graph,
    );

    expect(resolution.confirmed).toBe("graph");
    expect(navigateToTarget(resolution, navigation)).toBe(true);
    expect(navigation.descendToJob).toHaveBeenCalledWith("tJob_OrderSync", "werkorder");
    expect(navigation.descendToComponent).toHaveBeenCalledWith("tMap_1");
  });
});

describe("navigateToTarget against the real navigation hook", () => {
  it("lands on the component level from the landscape root", () => {
    const graph = buildTopologyGraphData({
      landscape: landscape(),
      manifest: MANIFEST,
      componentNamesByJob: { tJob_OrderSync: ["tMap_1"] },
    });
    const resolution = resolveTopologyTarget(
      {
        intent: "reference" as const,
        label: "tMap_1",
        level: {
          kind: "component",
          jobName: "tJob_OrderSync",
          componentName: "tMap_1",
          sourceRoot: "werkorder",
        },
      },
      graph,
    );

    const { result } = renderHook(() => useTopologyNavigation());
    expect(result.current.current).toEqual({ kind: "project" });

    // `descendToComponent` derives the job from the stack top, so this only
    // works because both updaters are queued in order within one act().
    act(() => {
      navigateToTarget(resolution, result.current);
    });

    expect(result.current.current).toEqual({
      kind: "component",
      jobName: "tJob_OrderSync",
      componentName: "tMap_1",
      sourceRoot: "werkorder",
    });
    expect(result.current.stack.map((level) => level.kind)).toEqual([
      "project",
      "job",
      "component",
    ]);
  });

  it("returns to the root for a project-level target", () => {
    const { result } = renderHook(() => useTopologyNavigation());
    act(() => {
      result.current.descendToJob("tJob_OrderSync", "werkorder");
    });
    expect(result.current.depth).toBe(2);

    const resolution = resolveTopologyTarget(
      { intent: "reference" as const, label: "Landscape", level: { kind: "project" } },
      scannerGraph(),
    );
    act(() => {
      navigateToTarget(resolution, result.current);
    });

    expect(result.current.current).toEqual({ kind: "project" });
  });
});

describe("resolveTopologyTarget rejections", () => {
  it("reports a target the loaded graph cannot resolve and never navigates", () => {
    const navigation = navigationStub();
    const resolution = resolveTopologyTarget(
      { intent: "reference" as const, label: "Ghost job", level: { kind: "job", jobName: "tJob_NotScanned" } },
      scannerGraph(),
    );

    expect(resolution.status).toBe("unresolvable");
    expect(resolution.reason).toBe("unknown-job");
    expect(navigateToTarget(resolution, navigation)).toBe(false);
    expect(navigation.descendToJob).not.toHaveBeenCalled();
    expect(navigation.descendToIntegration).not.toHaveBeenCalled();
    expect(navigation.reset).not.toHaveBeenCalled();
  });

  it("treats an inactive-space system id as unresolvable and leaves the id untranslated", () => {
    const resolution = resolveTopologyTarget(
      {
        intent: "reference" as const,
        label: "SAP ERP",
        level: { kind: "integration", selection: { kind: "system", systemId: "system:connection:0:sap" } },
      },
      overlayGraph(),
    );

    expect(resolution.status).toBe("unresolvable");
    expect(resolution.reason).toBe("inactive-id-space");
    // The scanner id is reported back exactly as received: there is no bridge
    // to the overlay space, so translating it would invent an identity.
    expect(resolution.target.level).toEqual({
      kind: "integration",
      selection: { kind: "system", systemId: "system:connection:0:sap" },
    });
  });

  it("treats an inactive-space edge key as unresolvable in either direction", () => {
    const overlayKeyOnScannerCanvas = resolveTopologyTarget(
      {
        intent: "reference" as const,
        label: "Order sync",
        level: {
          kind: "integration",
          selection: { kind: "edge", edgeKey: `business-stream${US}stream-1` },
        },
      },
      scannerGraph(),
    );
    expect(overlayKeyOnScannerCanvas.reason).toBe("inactive-id-space");

    const scannerKeyOnOverlayCanvas = resolveTopologyTarget(
      { intent: "reference" as const, label: "Werkorder to SAP", level: { kind: "integration", selection: { kind: "edge", edgeKey: SCANNER_EDGE_KEY } } },
      overlayGraph(),
    );
    expect(scannerKeyOnOverlayCanvas.reason).toBe("inactive-id-space");
  });

  it("rejects a declared sourceRoot the manifest does not confirm", () => {
    const resolution = resolveTopologyTarget(
      { intent: "reference" as const, label: "tJob_OrderSync", level: { kind: "job", jobName: "tJob_OrderSync", sourceRoot: "other-bundle" } },
      scannerGraph(),
    );
    expect(resolution.reason).toBe("source-root-mismatch");
  });

  it("rejects an unknown component when that job's graph is loaded", () => {
    const graph = buildTopologyGraphData({
      landscape: landscape(),
      manifest: MANIFEST,
      componentNamesByJob: { tJob_OrderSync: ["tMap_1"] },
    });
    const resolution = resolveTopologyTarget(
      { intent: "reference" as const, label: "tMap_9", level: { kind: "component", jobName: "tJob_OrderSync", componentName: "tMap_9" } },
      graph,
    );
    expect(resolution.reason).toBe("unknown-component");
  });

  it("keeps a component chip live but unconfirmed when the job graph is not loaded yet", () => {
    const resolution = resolveTopologyTarget(
      { intent: "reference" as const, label: "tMap_1", level: { kind: "component", jobName: "tJob_OrderSync", componentName: "tMap_1" } },
      scannerGraph(),
    );
    expect(resolution.status).toBe("resolved");
    expect(resolution.confirmed).toBe("assumed");
  });

  it("reports everything as unresolvable when nothing is loaded", () => {
    const resolution = resolveTopologyTarget(
      { intent: "reference" as const, label: "SAP ERP", level: { kind: "integration", selection: { kind: "system", systemId: "x" } } },
      null,
    );
    expect(resolution.reason).toBe("no-graph-data");
  });
});

describe("parseTopologyTargets", () => {
  it("rejects malformed entries instead of crashing, and keeps the valid ones", () => {
    const parsed = parseTopologyTargets([
      { label: "Good", level: { kind: "job", jobName: "tJob_OrderSync" } },
      { label: "Missing level" },
      { label: "Bad kind", level: { kind: "nonsense" } },
      { label: "Snake case", level: { kind: "job", jobName: "tJob_OrderSync", source_root: "werkorder" } },
      { label: "Null sourceRoot", level: { kind: "job", jobName: "tJob_OrderSync", sourceRoot: null } },
      "not an object",
    ]);

    expect(parsed.targets).toHaveLength(1);
    expect(parsed.targets[0].label).toBe("Good");
    expect(parsed.rejected).toBe(5);
  });

  it("returns nothing for a payload that is not a list", () => {
    expect(parseTopologyTargets(undefined)).toEqual({ targets: [], rejected: 0 });
    expect(parseTopologyTargets({ label: "x" })).toEqual({ targets: [], rejected: 0 });
  });

  it("defaults relationship streamKeys rather than requiring them", () => {
    const parsed = parseTopologyTargets([
      {
        label: "Werkorder to SAP",
        level: {
          kind: "integration",
          selection: { kind: "relationship", source: "a", target: "b" },
        },
      },
    ]);
    const selection = parsed.targets[0].level;
    expect(selection.kind === "integration" && selection.selection.kind === "relationship"
      ? selection.selection.streamKeys
      : null).toEqual([]);
  });
});

describe("resolveTopologyTargets and toCopilotTarget", () => {
  it("flattens resolutions into inert or clickable chip descriptors", () => {
    const chips = resolveTopologyTargets(
      [
        { label: "tJob_OrderSync", level: { kind: "job", jobName: "tJob_OrderSync" } },
        { label: "Ghost", level: { kind: "group", groupName: "Nope" } },
      ],
      scannerGraph(),
    ).map(toCopilotTarget);

    expect(chips[0]).toMatchObject({ resolvable: true, detail: "tJob_OrderSync" });
    expect(chips[0].unresolvableReason).toBeUndefined();
    expect(chips[1].resolvable).toBe(false);
    expect(chips[1].unresolvableReason).toContain("not in the diagram");
  });
});

describe("extractTargetsFromAgentEvent", () => {
  function event(overrides: Partial<PublicAgentEvent>): PublicAgentEvent {
    return {
      event_id: "evt-1",
      run_id: "run-1",
      sequence: 1,
      type: "tool.completed",
      created_at: "2026-08-20T10:00:00Z",
      public_payload: {},
      replayable: true,
      ...overrides,
    };
  }

  it("reads targets from tool events", () => {
    const targets = extractTargetsFromAgentEvent(
      event({
        public_payload: {
          tool_label: "get topology node",
          targets: [{ label: "tJob_OrderSync", level: { kind: "job", jobName: "tJob_OrderSync" } }],
        },
      }),
    );
    expect(targets).toHaveLength(1);
  });

  it("ignores events without targets and non-tool events", () => {
    expect(extractTargetsFromAgentEvent(event({ public_payload: { status: "ok" } }))).toEqual([]);
    expect(
      extractTargetsFromAgentEvent(
        event({
          type: "assistant.delta",
          public_payload: { targets: [{ label: "x", level: { kind: "project" } }] },
        }),
      ),
    ).toEqual([]);
  });
});
