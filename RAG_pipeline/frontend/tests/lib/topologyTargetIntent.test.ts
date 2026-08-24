import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useTopologyNavigation } from "@/hooks/useTopologyNavigation";
import {
  buildTopologyGraphData,
  isTopologyLevelResolvable,
  navigateToTarget,
  parseTopologyTargets,
  pickAutoNavigationTarget,
  pickFollowTarget,
  resolveTopologyTarget,
  resolveTopologyTargets,
  type TopologyGraphData,
} from "@/lib/topologyTargets";
import type { ManifestData } from "@/lib/graphTransform";
import type { ProjectLandscapeResponse } from "@/types/landscape";

/**
 * Navigation intent and strict component validation.
 *
 * The invariant under test throughout: a malformed or absent `intent` must
 * degrade to `reference`, never to `navigate`. A payload the backend got wrong
 * is allowed to produce a useless chip; it is not allowed to move the canvas.
 */

const MANIFEST: ManifestData = {
  job_calls: { tJob_OrderSync: ["tJob_Child"] },
  job_called_by: { tJob_Child: ["tJob_OrderSync"] },
  job_connections_used: {},
  connection_used_by: {},
  job_groups: { tJob_OrderSync: "Orders", tJob_Child: "Orders" },
} as unknown as ManifestData;

const LANDSCAPE = {
  project_id: "werkorder",
  applications: [],
  systems: [
    { id: "system:connection:0:quintiq", name: "Quintiq", kind: "app", evidence: [], source_roots: [] },
    { id: "system:connection:1:maximo", name: "Maximo", kind: "app", evidence: [], source_roots: [] },
  ],
  edges: [],
  channels: [],
  interfaces: [],
  diagnostics: [],
  unmapped: [],
  summary: {},
} as unknown as ProjectLandscapeResponse;

function graph(
  componentNamesByJob?: Record<string, readonly string[]>,
): TopologyGraphData {
  return buildTopologyGraphData({
    landscape: LANDSCAPE,
    manifest: MANIFEST,
    componentNamesByJob,
  });
}

function payload(entries: Array<Record<string, unknown>>): unknown {
  return entries;
}

const JOB_LEVEL = { kind: "job", jobName: "tJob_OrderSync" };
const CHILD_LEVEL = { kind: "job", jobName: "tJob_Child" };
const GROUP_LEVEL = { kind: "group", groupName: "Orders" };

describe("target intent parsing", () => {
  it("defaults an old target without visual_role to supporting", () => {
    const { targets, rejected } = parseTopologyTargets(
      payload([{ label: "Order sync", level: JOB_LEVEL }]),
    );
    expect(rejected).toBe(0);
    expect(targets[0].visual_role).toBe("supporting");
  });

  it("defaults to reference when the field is absent", () => {
    const { targets, rejected } = parseTopologyTargets(
      payload([{ label: "Order sync", level: JOB_LEVEL }]),
    );
    expect(rejected).toBe(0);
    expect(targets[0].intent).toBe("reference");
  });

  it("keeps an explicit navigate intent", () => {
    const { targets } = parseTopologyTargets(
      payload([{ label: "Order sync", level: JOB_LEVEL, intent: "navigate" }]),
    );
    expect(targets[0].intent).toBe("navigate");
  });

  it("falls back to reference for an unrecognised value rather than rejecting the target", () => {
    const { targets, rejected } = parseTopologyTargets(
      payload([{ label: "Order sync", level: JOB_LEVEL, intent: "teleport" }]),
    );
    // The target is still usable as a chip; only its intent is distrusted.
    expect(rejected).toBe(0);
    expect(targets[0].intent).toBe("reference");
  });

  it("falls back to reference for null, a number and an object", () => {
    for (const intent of [null, 7, { kind: "navigate" }, ["navigate"]]) {
      const { targets } = parseTopologyTargets(
        payload([{ label: "Order sync", level: JOB_LEVEL, intent }]),
      );
      expect(targets[0].intent).toBe("reference");
    }
  });

  it("never upgrades a malformed payload to navigate", () => {
    const { targets } = parseTopologyTargets(
      payload([
        { label: "A", level: JOB_LEVEL, intent: "Navigate" },
        { label: "B", level: GROUP_LEVEL, intent: "navigate " },
      ]),
    );
    expect(targets.map((target) => target.intent)).toEqual(["reference", "reference"]);
  });
});

describe("pickFollowTarget", () => {
  it("takes only the first graph-confirmed primary target", () => {
    const resolutions = resolveTopologyTargets(
      payload([
        { label: "Supporting", level: GROUP_LEVEL, visual_role: "supporting" },
        { label: "Primary", level: JOB_LEVEL, visual_role: "primary" },
        { label: "Second", level: CHILD_LEVEL, visual_role: "primary" },
      ]),
      graph(),
    );
    expect(pickFollowTarget(resolutions)?.label).toBe("Primary");
  });

  it("does not move for legacy, path, unresolvable, or assumed targets", () => {
    const resolutions = resolveTopologyTargets(
      payload([
        { label: "Legacy", level: GROUP_LEVEL },
        { label: "Path", level: JOB_LEVEL, visual_role: "path" },
        {
          label: "Missing",
          level: { kind: "job", jobName: "not-present" },
          visual_role: "primary",
        },
        {
          label: "Unchecked component",
          level: { kind: "component", jobName: "tJob_OrderSync", componentName: "tMap_1" },
          visual_role: "primary",
        },
      ]),
      graph(),
    );
    expect(resolutions.at(-1)?.confirmed).toBe("assumed");
    expect(pickFollowTarget(resolutions)).toBeNull();
  });
});

describe("pickAutoNavigationTarget", () => {
  it("returns nothing when every target is a reference", () => {
    const resolutions = resolveTopologyTargets(
      payload([
        { label: "Order sync", level: JOB_LEVEL },
        { label: "Orders", level: GROUP_LEVEL },
      ]),
      graph(),
    );
    expect(pickAutoNavigationTarget(resolutions)).toBeNull();
  });

  it("takes the first navigate target and leaves the rest alone", () => {
    const resolutions = resolveTopologyTargets(
      payload([
        { label: "Orders", level: GROUP_LEVEL },
        { label: "Order sync", level: JOB_LEVEL, intent: "navigate" },
        { label: "Child", level: CHILD_LEVEL, intent: "navigate" },
      ]),
      graph(),
    );
    const picked = pickAutoNavigationTarget(resolutions);
    expect(picked?.label).toBe("Order sync");
    // The others remain ordinary resolvable chips -- nothing is consumed.
    expect(resolutions.filter((item) => item.status === "resolved")).toHaveLength(3);
  });

  it("skips an unresolvable navigate target rather than spending the one slot on it", () => {
    const resolutions = resolveTopologyTargets(
      payload([
        { label: "Ghost", level: { kind: "job", jobName: "tJob_NotInScan" }, intent: "navigate" },
        { label: "Order sync", level: JOB_LEVEL, intent: "navigate" },
      ]),
      graph(),
    );
    expect(pickAutoNavigationTarget(resolutions)?.label).toBe("Order sync");
  });

  it("returns nothing when the only navigate target is unresolvable", () => {
    const resolutions = resolveTopologyTargets(
      payload([
        { label: "Ghost", level: { kind: "job", jobName: "tJob_NotInScan" }, intent: "navigate" },
      ]),
      graph(),
    );
    expect(pickAutoNavigationTarget(resolutions)).toBeNull();
  });

  it("dispatches the picked target onto navigation, component through its job", () => {
    const resolutions = resolveTopologyTargets(
      payload([
        {
          label: "The mapping",
          level: { kind: "component", jobName: "tJob_OrderSync", componentName: "tMap_1" },
          intent: "navigate",
        },
      ]),
      graph({ tJob_OrderSync: ["tMap_1"] }),
    );
    const picked = pickAutoNavigationTarget(resolutions);
    expect(picked).not.toBeNull();

    const { result } = renderHook(() => useTopologyNavigation());
    act(() => {
      navigateToTarget(picked!, result.current);
    });
    expect(result.current.stack.map((level) => level.kind)).toEqual([
      "project",
      "job",
      "component",
    ]);
  });
});

describe("strict component validation", () => {
  it("confirms a component against a loaded job graph", () => {
    const resolution = resolveTopologyTarget(
      {
        label: "tMap_1",
        level: { kind: "component", jobName: "tJob_OrderSync", componentName: "tMap_1" },
        intent: "reference",
      },
      graph({ tJob_OrderSync: ["tMap_1", "tFileInputDelimited_1"] }),
    );
    expect(resolution.status).toBe("resolved");
    expect(resolution.confirmed).toBe("graph");
  });

  it("rejects a component the loaded job graph does not contain", () => {
    const resolution = resolveTopologyTarget(
      {
        label: "tMap_9",
        level: { kind: "component", jobName: "tJob_OrderSync", componentName: "tMap_9" },
        intent: "reference",
      },
      graph({ tJob_OrderSync: ["tMap_1"] }),
    );
    expect(resolution.status).toBe("unresolvable");
    expect(resolution.reason).toBe("unknown-component");
  });

  it("stays assumed only when that job's graph is genuinely not loaded", () => {
    // Another job's graph being loaded must not make this one's absence look
    // like a strict negative.
    const resolution = resolveTopologyTarget(
      {
        label: "tMap_9",
        level: { kind: "component", jobName: "tJob_OrderSync", componentName: "tMap_9" },
        intent: "reference",
      },
      graph({ tJob_Child: ["tMap_1"] }),
    );
    expect(resolution.status).toBe("resolved");
    expect(resolution.confirmed).toBe("assumed");
  });

  it("does not weaken the source-root check when the job graph is loaded", () => {
    const resolution = resolveTopologyTarget(
      {
        label: "tMap_1",
        level: {
          kind: "component",
          jobName: "tJob_OrderSync",
          componentName: "tMap_1",
          sourceRoot: "some-other-bundle",
        },
        intent: "reference",
      },
      graph({ tJob_OrderSync: ["tMap_1"] }),
    );
    expect(resolution.status).toBe("unresolvable");
    expect(resolution.reason).toBe("source-root-mismatch");
  });

  it("treats a loaded job graph with no components as a strict empty set", () => {
    const resolution = resolveTopologyTarget(
      {
        label: "tMap_1",
        level: { kind: "component", jobName: "tJob_OrderSync", componentName: "tMap_1" },
        intent: "reference",
      },
      graph({ tJob_OrderSync: [] }),
    );
    expect(resolution.status).toBe("unresolvable");
    expect(resolution.reason).toBe("unknown-component");
  });
});

describe("relationship targets", () => {
  const RELATIONSHIP = {
    kind: "integration",
    selection: {
      kind: "relationship",
      source: "system:connection:0:quintiq",
      target: "system:connection:1:maximo",
      streamKeys: [] as string[],
    },
  } as const;

  it("resolves when both endpoints are on the canvas", () => {
    const resolution = resolveTopologyTarget(
      { label: "Quintiq to Maximo", level: RELATIONSHIP, intent: "navigate" },
      graph(),
    );
    expect(resolution.status).toBe("resolved");
  });

  it("navigates to the integration level with its ids intact", () => {
    const resolution = resolveTopologyTarget(
      { label: "Quintiq to Maximo", level: RELATIONSHIP, intent: "navigate" },
      graph(),
    );
    const { result } = renderHook(() => useTopologyNavigation());
    act(() => {
      navigateToTarget(resolution, result.current);
    });
    expect(result.current.current).toEqual(RELATIONSHIP);
  });

  it("reports an unknown endpoint as an unresolvable relationship", () => {
    const resolution = resolveTopologyTarget(
      {
        label: "Quintiq to Nothing",
        level: {
          kind: "integration",
          selection: {
            kind: "relationship",
            source: "system:connection:0:quintiq",
            target: "system:connection:9:ghost",
            streamKeys: [],
          },
        },
        intent: "navigate",
      },
      graph(),
    );
    expect(resolution.status).toBe("unresolvable");
    expect(resolution.reason).toBe("unknown-relationship");
  });
});

describe("isTopologyLevelResolvable", () => {
  it("accepts a level the loaded graph contains", () => {
    expect(isTopologyLevelResolvable({ kind: "project" }, graph())).toBe(true);
    expect(isTopologyLevelResolvable({ kind: "job", jobName: "tJob_OrderSync" }, graph())).toBe(true);
    expect(isTopologyLevelResolvable({ kind: "group", groupName: "Orders" }, graph())).toBe(true);
  });

  it("rejects a level naming something this scan does not have", () => {
    expect(isTopologyLevelResolvable({ kind: "job", jobName: "tJob_Ghost" }, graph())).toBe(false);
    expect(isTopologyLevelResolvable({ kind: "group", groupName: "Nope" }, graph())).toBe(false);
  });

  it("holds a link to the same source-root standard as a chip", () => {
    expect(
      isTopologyLevelResolvable(
        { kind: "job", jobName: "tJob_OrderSync", sourceRoot: "other-bundle" },
        graph(),
      ),
    ).toBe(false);
  });

  it("rejects everything when nothing is loaded", () => {
    expect(isTopologyLevelResolvable({ kind: "project" }, null)).toBe(false);
  });
});
