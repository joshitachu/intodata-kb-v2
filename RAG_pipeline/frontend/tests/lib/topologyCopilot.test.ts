import { describe, expect, it } from "vitest";
import {
  buildTopologyPageScope,
  designAssistantEmphasis,
  highlightNodeIdsForTargets,
  integrationAssistantEmphasis,
  jobAssistantEmphasis,
  landscapeAssistantEmphasis,
  mappingAssistantEmphasis,
  readTopologyTargetsPayload,
  topologyCopilotScope,
  topologyCopilotSuggestions,
} from "@/lib/topologyCopilot";
import { buildTopologyGraphData, resolveTopologyTargets } from "@/lib/topologyTargets";
import type { ManifestData } from "@/lib/graphTransform";
import type { TopologyLevel } from "@/types/topology";
import type { ResolvedTopologyTarget } from "@/lib/topologyTargets";

const MANIFEST: ManifestData = {
  job_calls: { tJob_OrderSync: ["tJob_Child"] },
  job_called_by: { tJob_Child: ["tJob_OrderSync"] },
  job_connections_used: {},
  connection_used_by: {},
  job_groups: { tJob_OrderSync: "Orders", tJob_Child: "Orders" },
};

const GRAPH = buildTopologyGraphData({ manifest: MANIFEST });

describe("buildTopologyPageScope", () => {
  it("wraps the level in the surface envelope", () => {
    expect(buildTopologyPageScope("werkorder", { kind: "project" })).toEqual({
      surface: "topology",
      project_id: "werkorder",
      level: { kind: "project" },
    });
  });

  it("keeps the level's camelCase field names, unlike the rest of the API", () => {
    const scope = buildTopologyPageScope("werkorder", {
      kind: "component",
      jobName: "tJob_OrderSync",
      componentName: "tMap_1",
      sourceRoot: "werkorder",
    });

    expect(scope?.level).toEqual({
      kind: "component",
      jobName: "tJob_OrderSync",
      componentName: "tMap_1",
      sourceRoot: "werkorder",
    });
  });

  it("omits an absent sourceRoot rather than carrying the key as undefined", () => {
    // The backend and DB round-trip this byte-identically, and the wire schema
    // rejects an explicit null, so "absent" has to mean the key is not there.
    const scope = buildTopologyPageScope("alitalend", {
      kind: "job",
      jobName: "Log_Loader",
      sourceRoot: undefined,
    });

    expect(scope?.level).toEqual({ kind: "job", jobName: "Log_Loader" });
    expect(Object.keys(scope?.level ?? {})).not.toContain("sourceRoot");
    expect(JSON.parse(JSON.stringify(scope))).toEqual({
      surface: "topology",
      project_id: "alitalend",
      level: { kind: "job", jobName: "Log_Loader" },
    });
  });

  it("passes integration selection ids through untouched", () => {
    const scope = buildTopologyPageScope("werkorder", {
      kind: "integration",
      selection: { kind: "system", systemId: "landscape-system:9f2c" },
    });

    expect(scope?.level).toEqual({
      kind: "integration",
      selection: { kind: "system", systemId: "landscape-system:9f2c" },
    });
  });

  it("carries bounded visible context without inventing empty fields", () => {
    const scope = buildTopologyPageScope("werkorder", { kind: "project" }, {
      selected_entity: { kind: "system", label: "Maximo", entity_id: "system:maximo" },
      active_detail_tab: "runtime",
      filters: [{ name: "connections", value: "shown" }],
    });
    expect(scope).toMatchObject({
      selected_entity: { kind: "system", label: "Maximo", entity_id: "system:maximo" },
      active_detail_tab: "runtime",
      filters: [{ name: "connections", value: "shown" }],
    });
    expect(buildTopologyPageScope("werkorder", { kind: "project" }, { filters: [] }))
      .toEqual({ surface: "topology", project_id: "werkorder", level: { kind: "project" } });
  });

  it("drops the entire hint when visible context exceeds its contract bounds", () => {
    expect(buildTopologyPageScope("werkorder", { kind: "project" }, {
      selected_entity: { kind: "system", label: "x".repeat(201) },
    })).toBeNull();
    expect(buildTopologyPageScope("werkorder", { kind: "project" }, {
      filters: Array.from({ length: 13 }, (_, index) => ({ name: `f${index}`, value: "on" })),
    })).toBeNull();
  });

  it("returns null instead of a half-valid envelope", () => {
    expect(buildTopologyPageScope("", { kind: "project" })).toBeNull();
    // Past the 500-char token bound the strict wire schema rejects the level;
    // page_scope is only a hint, so sending nothing is the correct degradation.
    expect(
      buildTopologyPageScope("werkorder", { kind: "job", jobName: "x".repeat(501) }),
    ).toBeNull();
  });
});

function resolvedTarget(
  level: TopologyLevel,
  visualRole: "primary" | "supporting" | "path",
  visualDetail?: ResolvedTopologyTarget["target"]["visual_detail"],
): ResolvedTopologyTarget {
  return {
    key: JSON.stringify(level),
    label: "target",
    target: {
      label: "target",
      level,
      intent: "reference",
      visual_role: visualRole,
      ...(visualDetail ? { visual_detail: visualDetail } : {}),
    },
    status: "resolved",
    confirmed: "graph",
  };
}

describe("assistant canvas projections", () => {
  it("keeps the strongest role when multiple targets name one design node", () => {
    const emphasis = designAssistantEmphasis([
      resolvedTarget({ kind: "job", jobName: "tJob_OrderSync" }, "supporting"),
      resolvedTarget({ kind: "component", jobName: "tJob_OrderSync", componentName: "tMap_1" }, "primary"),
    ]);
    expect(emphasis.nodeRoles.get("job:tJob_OrderSync")).toBe("primary");
  });

  it("projects landscape nodes and a relationship path with their roles", () => {
    const emphasis = landscapeAssistantEmphasis([
      resolvedTarget({ kind: "integration", selection: { kind: "system", systemId: "maximo" } }, "primary"),
      resolvedTarget({ kind: "integration", selection: { kind: "relationship", source: "a", target: "b", streamKeys: [] } }, "path"),
    ]);
    expect(emphasis.nodeRoles.get("maximo")).toBe("primary");
    expect(emphasis.nodeRoles.get("a")).toBe("path");
    expect(emphasis.nodeRoles.get("b")).toBe("path");
    expect([...emphasis.edgeRoles.values()]).toContain("path");
  });

  it("emphasises both endpoints AND the connecting edge for a relationship", () => {
    // The user sees a relationship as a line between two boxes. Lighting up one
    // of the three is what made the reported run look like it had emphasised a
    // different node than the banner named.
    const emphasis = landscapeAssistantEmphasis([
      resolvedTarget(
        {
          kind: "integration",
          selection: {
            kind: "relationship",
            source: "sys-afas",
            target: "sys-maximo",
            streamKeys: ["stream-1"],
          },
        },
        "primary",
      ),
    ]);
    expect(emphasis.nodeRoles.get("sys-afas")).toBe("primary");
    expect(emphasis.nodeRoles.get("sys-maximo")).toBe("primary");
    expect(emphasis.edgeRoles.get("pair:sys-afas->sys-maximo")).toBe("primary");
    // The named streams narrow the highlight within a pair carrying many flows.
    expect(emphasis.edgeRoles.get("stream-1")).toBe("primary");
  });

  it("aliases artifact system ids onto the architecture canvas's node ids", () => {
    // `landscapeToFlow` keys nodes by the artifact id; the architecture canvas
    // keys them `system:<id>` and its edges `pair:<node><->` <node>. A non-empty
    // emphasis that matches neither dims the whole diagram, which is how a
    // correct answer ended up greying out the system it was about.
    const emphasis = landscapeAssistantEmphasis([
      resolvedTarget(
        {
          kind: "integration",
          selection: {
            kind: "relationship",
            source: "sys-afas",
            target: "sys-maximo",
            streamKeys: [],
          },
        },
        "primary",
      ),
      resolvedTarget({ kind: "integration", selection: { kind: "system", systemId: "sys-4ps" } }, "path"),
    ]);
    expect(emphasis.nodeRoles.get("system:sys-afas")).toBe("primary");
    expect(emphasis.nodeRoles.get("system:sys-maximo")).toBe("primary");
    expect(emphasis.nodeRoles.get("system:sys-4ps")).toBe("path");
    expect(emphasis.edgeRoles.get("pair:system:sys-afas<->system:sys-maximo")).toBe("primary");
    expect(emphasis.edgeRoles.get("pair:system:sys-afas->system:sys-maximo")).toBe("primary");
    // Reversed too: the canvas draws one line per unordered pair, and which
    // endpoint it calls `first` is a sort order, not a fact about the flow.
    expect(emphasis.edgeRoles.get("pair:sys-maximo->sys-afas")).toBe("primary");
  });

  it("projects artifacts and exact job components at their mounted depths", () => {
    const component = resolvedTarget(
      { kind: "component", jobName: "tJob_OrderSync", componentName: "tMap_1", sourceRoot: "src" },
      "primary",
    );
    expect(integrationAssistantEmphasis([component], [{
      artifact_id: "tJob_OrderSync",
      qualified_artifact_id: "src::tJob_OrderSync",
      source_root: "src",
    }]).nodeRoles.get("artifact:src::tJob_OrderSync")).toBe("primary");
    expect(jobAssistantEmphasis([component], "tJob_OrderSync").nodeRoles.get("comp:tMap_1"))
      .toBe("primary");
  });

  it("projects bounded mapping tables, columns and target edges", () => {
    const component = resolvedTarget(
      { kind: "component", jobName: "tJob_OrderSync", componentName: "tMap_1" },
      "path",
      {
        kind: "mapping",
        tables: ["input"],
        columns: [{ table: "output", column: "workorderid" }],
        edges: [{ target_table: "output", target_column: "status" }],
      },
    );
    const emphasis = mappingAssistantEmphasis([component], "tJob_OrderSync", "tMap_1");
    expect(emphasis.nodeRoles.get("table:input")).toBe("path");
    expect(emphasis.nodeRoles.get("table:output")).toBe("path");
    expect(emphasis.columnRoles?.get("output::workorderid")).toBe("path");
    expect(emphasis.edgeRoles.get("output::status")).toBe("path");
  });

  it("ignores assumed and unresolvable targets for every projection", () => {
    const target = resolvedTarget({ kind: "job", jobName: "tJob_OrderSync" }, "primary");
    const assumed = { ...target, confirmed: "assumed" as const };
    const missing = { ...target, status: "unresolvable" as const, confirmed: undefined };
    expect(designAssistantEmphasis([assumed, missing]).nodeRoles.size).toBe(0);
  });
});

describe("topologyCopilotSuggestions", () => {
  it("asks about feeders and external dependencies at the landscape level", () => {
    const suggestions = topologyCopilotSuggestions({ kind: "project" });
    expect(suggestions.length).toBeGreaterThanOrEqual(3);
    expect(suggestions.join(" ")).toMatch(/feed/i);
    expect(suggestions.join(" ")).toMatch(/external systems/i);
  });

  it("asks about that job's dependencies and health at a job level", () => {
    const suggestions = topologyCopilotSuggestions({
      kind: "job",
      jobName: "tJob_OrderSync",
    });
    expect(suggestions.every((item) => item.includes("tJob_OrderSync"))).toBe(true);
    expect(suggestions.join(" ")).toMatch(/depend/i);
    expect(suggestions.join(" ")).toMatch(/healthy/i);
  });

  it("changes as the user drills, and never repeats the level above", () => {
    const levels: TopologyLevel[] = [
      { kind: "project" },
      { kind: "group", groupName: "Orders" },
      { kind: "job", jobName: "tJob_OrderSync" },
      { kind: "component", jobName: "tJob_OrderSync", componentName: "tMap_1" },
    ];
    const rendered = levels.map((level) => topologyCopilotSuggestions(level).join("|"));
    expect(new Set(rendered).size).toBe(levels.length);
    for (const suggestions of levels.map(topologyCopilotSuggestions)) {
      expect(suggestions.length).toBeGreaterThanOrEqual(3);
      expect(suggestions.length).toBeLessThanOrEqual(4);
    }
  });
});

describe("topologyCopilotScope", () => {
  it("omits a name at the root", () => {
    expect(topologyCopilotScope({ kind: "project" })).toEqual({ kindLabel: "Landscape" });
  });

  it("names the thing in view at a job level", () => {
    expect(topologyCopilotScope({ kind: "job", jobName: "tJob_OrderSync" })).toEqual({
      kindLabel: "Job",
      name: "tJob_OrderSync",
    });
  });

  it("shows the level kind rather than a hash when an id cannot be resolved", () => {
    const level: TopologyLevel = {
      kind: "integration",
      selection: { kind: "edge", edgeKey: "business-streamabc123" },
    };
    expect(topologyCopilotScope(level)).toEqual({ kindLabel: "Flow" });
    expect(topologyCopilotScope(level, "Maximo -> RHM")).toEqual({
      kindLabel: "Flow",
      name: "Maximo -> RHM",
    });
  });
});

describe("highlightNodeIdsForTargets", () => {
  function resolve(payload: unknown) {
    return resolveTopologyTargets(payload, GRAPH);
  }

  it("maps a job target to its design-canvas node id", () => {
    const ids = highlightNodeIdsForTargets(
      resolve([{ label: "Order sync", level: { kind: "job", jobName: "tJob_OrderSync" } }]),
    );
    expect([...ids]).toEqual(["job:tJob_OrderSync"]);
  });

  it("maps a component target to its JOB's node, the only true statement this canvas can make", () => {
    const ids = highlightNodeIdsForTargets(
      resolve([
        {
          label: "The mapping",
          level: {
            kind: "component",
            jobName: "tJob_OrderSync",
            componentName: "tMap_1",
          },
        },
      ]),
    );
    expect([...ids]).toEqual(["job:tJob_OrderSync"]);
  });

  it("maps a group target to its group node", () => {
    const ids = highlightNodeIdsForTargets(
      resolve([{ label: "Orders", level: { kind: "group", groupName: "Orders" } }]),
    );
    expect([...ids]).toEqual(["group:Orders"]);
  });

  it("contributes nothing for levels this canvas has no emphasis channel for", () => {
    const ids = highlightNodeIdsForTargets(
      resolve([
        { label: "Everything", level: { kind: "project" } },
        {
          label: "A flow",
          level: { kind: "integration", selection: { kind: "edge", edgeKey: "whatever" } },
        },
      ]),
    );
    expect(ids.size).toBe(0);
  });

  it("skips unresolvable targets, which must never light anything up", () => {
    const resolutions = resolve([
      { label: "Missing", level: { kind: "job", jobName: "tJob_NotInThisScan" } },
    ]);
    expect(resolutions[0].status).toBe("unresolvable");
    expect(highlightNodeIdsForTargets(resolutions).size).toBe(0);
  });
});

describe("readTopologyTargetsPayload", () => {
  it("reads the field off a structured answer whose schema does not declare it", () => {
    const payload = readTopologyTargetsPayload({
      response_type: "agent_response_v1",
      content: "...",
      topology_targets: [{ label: "Order sync", level: { kind: "job", jobName: "tJob_OrderSync" } }],
    });
    expect(resolveTopologyTargets(payload, GRAPH)).toHaveLength(1);
  });

  it("yields nothing for a deployment that does not emit targets yet", () => {
    expect(readTopologyTargetsPayload(null)).toBeUndefined();
    expect(readTopologyTargetsPayload(undefined)).toBeUndefined();
    expect(readTopologyTargetsPayload({ content: "..." })).toBeUndefined();
    expect(resolveTopologyTargets(readTopologyTargetsPayload("nope"), GRAPH)).toEqual([]);
  });
});
