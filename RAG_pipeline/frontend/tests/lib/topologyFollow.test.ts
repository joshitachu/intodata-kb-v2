import { describe, expect, it } from "vitest";
import {
  PANEL_SUPPORTING_LIMIT,
  describeRelationship,
  findRelationshipTarget,
  pickMovementTarget,
  relationshipEndpointsFromQuery,
  relationshipEndpointsFromTargets,
  relationshipScopeEndpoints,
  relationshipShape,
  selectRelevantTargets,
  synthesizeRelationshipTarget,
} from "@/lib/topologyFollow";
import {
  buildTopologyGraphData,
  resolveTopologyTargets,
  type ResolvedTopologyTarget,
  type TopologyGraphData,
} from "@/lib/topologyTargets";
import type { ManifestData } from "@/lib/graphTransform";
import type { ProjectLandscapeResponse } from "@/types/landscape";

/**
 * Follow mode's subject selection.
 *
 * The regression under test throughout is a real one: asked "what data flows
 * from AFAS to Maximo" with the AFAS<->Maximo pair selected on the canvas, the
 * answer marked AFAS `primary`, Follow followed AFAS, and the banner claimed a
 * move that never happened. Every assertion below is about the same invariant --
 * the thing Follow moves for is the SUBJECT of the turn, and a relationship is
 * never reduced to one of its endpoints.
 */

const UNIT_SEPARATOR = String.fromCharCode(0x1f);

function stream(id: string, source: string, destination: string) {
  return {
    id,
    name: id,
    source_system_id: source,
    destination_system_id: destination,
    business_object_ids: [],
    integration_solution_ids: [],
    status: "confirmed",
    artifact_ids: [],
    evidence_ids: [],
  };
}

const MANIFEST: ManifestData = {
  job_calls: { tJob_AfasExport: ["tJob_Child"] },
  job_called_by: { tJob_Child: ["tJob_AfasExport"] },
  job_connections_used: {},
  connection_used_by: {},
  job_groups: {
    tJob_AfasExport: "Orders",
    tJob_Child: "Orders",
    tJob_Payroll: "Payroll",
    tJob_Assets: "Assets",
    tJob_Invoices: "Finance",
    tJob_Timesheets: "HR",
    tJob_Contracts: "Legal",
  },
} as unknown as ManifestData;

/**
 * A validated business overlay, so the active id space is `business-overlay`
 * and the ids under test are the ones the reported run actually carried.
 */
const LANDSCAPE = {
  project_id: "werkorder",
  applications: [],
  systems: [],
  edges: [],
  channels: [],
  interfaces: [],
  diagnostics: [],
  unmapped: [],
  summary: {},
  business_topology: {
    systems: [
      { id: "sys-afas", name: "AFAS" },
      { id: "sys-maximo", name: "Maximo" },
      { id: "sys-4ps", name: "4PS" },
      { id: "sys-lonely", name: "ForSuppliers" },
    ],
    data_streams: [
      stream("employees", "sys-afas", "sys-maximo"),
      stream("costcentres", "sys-afas", "sys-maximo"),
      stream("workorders", "sys-maximo", "sys-4ps"),
    ],
    integration_solutions: [],
  },
  topology_state: { validated: true, stale: false },
} as unknown as ProjectLandscapeResponse;

const GRAPH: TopologyGraphData = buildTopologyGraphData({
  landscape: LANDSCAPE,
  manifest: MANIFEST,
});

const AFAS_MAXIMO = { source: "sys-afas", target: "sys-maximo" };
const STREAM_KEYS = [
  `business-stream${UNIT_SEPARATOR}employees`,
  `business-stream${UNIT_SEPARATOR}costcentres`,
];

function systemTarget(
  systemId: string,
  visualRole: "primary" | "supporting" | "path",
) {
  return {
    label: systemId,
    level: { kind: "integration", selection: { kind: "system", systemId } },
    visual_role: visualRole,
  };
}

function jobTarget(jobName: string, visualRole: "primary" | "supporting" | "path" = "supporting") {
  return {
    label: jobName,
    level: { kind: "job", jobName },
    visual_role: visualRole,
  };
}

function resolutions(payload: unknown[]): ResolvedTopologyTarget[] {
  return resolveTopologyTargets(payload, GRAPH);
}

/** The reported answer: AFAS primary, Maximo path, and a lot of noise. */
const REPORTED_ANSWER = resolutions([
  systemTarget("sys-afas", "primary"),
  systemTarget("sys-maximo", "path"),
  jobTarget("tJob_AfasExport"),
  jobTarget("tJob_Payroll"),
  jobTarget("tJob_Assets"),
  jobTarget("tJob_Invoices"),
  jobTarget("tJob_Timesheets"),
  jobTarget("tJob_Contracts"),
]);

const RELATIONSHIP_SCOPE = {
  kind: "relationship",
  label: "AFAS ↔ Maximo",
  entity_id: "pair:system:sys-afas<->system:sys-maximo",
};

describe("relationshipScopeEndpoints", () => {
  it("reads the architecture canvas's prefixed, unordered pair id", () => {
    expect(relationshipScopeEndpoints(RELATIONSHIP_SCOPE, GRAPH)).toEqual(AFAS_MAXIMO);
  });

  it("reads the aggregated edge id and the recorded level key too", () => {
    for (const entityId of [
      "pair:sys-afas->sys-maximo",
      "sys-afas->sys-maximo",
      "integration:relationship:sys-afas->sys-maximo",
    ]) {
      expect(relationshipScopeEndpoints(
        { kind: "relationship", label: "x", entity_id: entityId },
        GRAPH,
      )).toEqual(AFAS_MAXIMO);
    }
  });

  it("orients the pair by the direction the graph actually carries flow in", () => {
    // Two streams AFAS->Maximo, none back, so the reversed id still reports the
    // real direction rather than the order it happened to be written in.
    expect(relationshipScopeEndpoints(
      { kind: "relationship", label: "x", entity_id: "pair:sys-maximo<->sys-afas" },
      GRAPH,
    )).toEqual(AFAS_MAXIMO);
  });

  it("ignores a selection that is not a relationship", () => {
    expect(relationshipScopeEndpoints(
      { kind: "system", label: "AFAS", entity_id: "sys-afas" },
      GRAPH,
    )).toBeNull();
  });

  it("returns null for a pair the loaded graph has no flow between", () => {
    expect(relationshipScopeEndpoints(
      { kind: "relationship", label: "x", entity_id: "pair:sys-afas<->sys-lonely" },
      GRAPH,
    )).toBeNull();
  });

  it("returns null for ids this graph does not contain", () => {
    expect(relationshipScopeEndpoints(
      { kind: "relationship", label: "x", entity_id: "pair:sys-ghost<->sys-maximo" },
      GRAPH,
    )).toBeNull();
  });

  it("does not strip a `system:` prefix that is part of a real id", () => {
    // Scanner ids ARE `system:...`. Stripping first would resolve this to a
    // different system, or to none.
    const scanner = buildTopologyGraphData({
      landscape: {
        ...LANDSCAPE,
        systems: [
          { id: "system:connection:0:afas", name: "AFAS" },
          { id: "system:connection:1:maximo", name: "Maximo" },
        ],
        edges: [
          {
            source: "system:connection:0:afas",
            target: "system:connection:1:maximo",
            source_kind: "system",
            target_kind: "system",
            dependency_type: "data",
            mechanism: "jms",
            direction: "out",
            reliability: "buffered",
            confidence: "certain",
            evidence_count: 2,
            provenance: [],
          },
        ],
        business_topology: undefined,
        topology_state: { validated: false, stale: false },
      } as unknown as ProjectLandscapeResponse,
    });
    expect(relationshipScopeEndpoints(
      {
        kind: "relationship",
        label: "x",
        entity_id: "pair:system:connection:0:afas<->system:connection:1:maximo",
      },
      scanner,
    )).toEqual({
      source: "system:connection:0:afas",
      target: "system:connection:1:maximo",
    });
  });
});

describe("relationshipEndpointsFromQuery", () => {
  it("recognises the two systems the user named", () => {
    expect(relationshipEndpointsFromQuery(
      "can you show me what data flows from afas to maximo pls? So which objects",
      GRAPH,
    )).toEqual(AFAS_MAXIMO);
  });

  it("needs exactly two: one name or three is not a relationship question", () => {
    expect(relationshipEndpointsFromQuery("what does AFAS do?", GRAPH)).toBeNull();
    expect(relationshipEndpointsFromQuery("AFAS, Maximo and 4PS please", GRAPH)).toBeNull();
  });

  it("does not match a name embedded inside a longer word", () => {
    expect(relationshipEndpointsFromQuery("maximoflow versus afasx", GRAPH)).toBeNull();
  });

  it("returns null for a named pair the graph has no flow between", () => {
    expect(relationshipEndpointsFromQuery("AFAS to ForSuppliers", GRAPH)).toBeNull();
  });
});

describe("relationshipEndpointsFromTargets", () => {
  it("reads the pair out of two primary/path system targets", () => {
    expect(relationshipEndpointsFromTargets(REPORTED_ANSWER, GRAPH)).toEqual(AFAS_MAXIMO);
  });

  it("adopts a relationship target outright when the answer returned one", () => {
    const withRelationship = resolutions([
      {
        label: "AFAS ↔ Maximo",
        level: {
          kind: "integration",
          selection: {
            kind: "relationship",
            source: "sys-maximo",
            target: "sys-afas",
            streamKeys: STREAM_KEYS,
          },
        },
        visual_role: "primary",
      },
    ]);
    expect(relationshipEndpointsFromTargets(withRelationship, GRAPH)).toEqual(AFAS_MAXIMO);
  });

  it("does not invent a flow between two merely-listed systems", () => {
    // "Which systems have no runtime data" is a list, not a relationship.
    const listing = resolutions([
      systemTarget("sys-afas", "supporting"),
      systemTarget("sys-maximo", "supporting"),
    ]);
    expect(relationshipEndpointsFromTargets(listing, GRAPH)).toBeNull();
  });
});

describe("synthesizeRelationshipTarget", () => {
  it("builds a graph-confirmed primary relationship carrying its stream keys", () => {
    const target = synthesizeRelationshipTarget(AFAS_MAXIMO, GRAPH);
    expect(target?.status).toBe("resolved");
    expect(target?.confirmed).toBe("graph");
    expect(target?.label).toBe("AFAS → Maximo");
    expect(target?.target.visual_role).toBe("primary");
    // `reference`, not `navigate`: focusing is not the same as being taken.
    expect(target?.target.intent).toBe("reference");
    const level = target!.target.level;
    expect(level.kind === "integration" && level.selection).toEqual({
      kind: "relationship",
      source: "sys-afas",
      target: "sys-maximo",
      streamKeys: STREAM_KEYS,
    });
  });

  it("refuses a pair the loaded graph has no flow between", () => {
    expect(synthesizeRelationshipTarget(
      { source: "sys-afas", target: "sys-lonely" },
      GRAPH,
    )).toBeNull();
  });

  it("labels with the graph's own system names", () => {
    expect(describeRelationship(AFAS_MAXIMO, GRAPH)).toBe("AFAS → Maximo");
  });
});

describe("relationshipShape", () => {
  it("prefers the user's selection over their words and over the answer", () => {
    const shape = relationshipShape({
      resolutions: REPORTED_ANSWER,
      selectedEntity: RELATIONSHIP_SCOPE,
      query: "AFAS to 4PS",
      graph: GRAPH,
    });
    expect(shape).toEqual({ endpoints: AFAS_MAXIMO, source: "scope" });
  });

  it("falls back to the query, then to the answer's own targets", () => {
    expect(relationshipShape({
      resolutions: [],
      query: "what flows from maximo to 4ps",
      graph: GRAPH,
    })).toEqual({ endpoints: { source: "sys-maximo", target: "sys-4ps" }, source: "query" });

    expect(relationshipShape({
      resolutions: REPORTED_ANSWER,
      query: "and can you base the answer of topology pls",
      graph: GRAPH,
    })).toEqual({ endpoints: AFAS_MAXIMO, source: "answer" });
  });

  it("reports no shape for an ordinary single-entity turn", () => {
    expect(relationshipShape({
      resolutions: resolutions([systemTarget("sys-afas", "primary")]),
      selectedEntity: { kind: "system", label: "AFAS", entity_id: "sys-afas" },
      query: "what does AFAS do?",
      graph: GRAPH,
    })).toBeNull();
  });
});

describe("pickMovementTarget", () => {
  const base = { graph: GRAPH, followConversation: true };

  it("follows the relationship, not the endpoint the model called primary", () => {
    const decision = pickMovementTarget({
      ...base,
      resolutions: REPORTED_ANSWER,
      selectedEntity: RELATIONSHIP_SCOPE,
      query: "can you show me what data flows from afas to maximo pls?",
    });
    expect(decision?.reason).toBe("follow");
    expect(decision?.synthesized).toBe(true);
    expect(decision?.relationship?.source).toBe("scope");
    expect(decision?.target.label).toBe("AFAS → Maximo");
    const level = decision!.target.target.level;
    expect(level.kind === "integration" && level.selection.kind).toBe("relationship");
  });

  it("adopts the answer's own relationship target rather than rebuilding it", () => {
    const withRelationship = resolutions([
      {
        label: "AFAS to Maximo employee flow",
        level: {
          kind: "integration",
          selection: {
            kind: "relationship",
            source: "sys-afas",
            target: "sys-maximo",
            streamKeys: STREAM_KEYS,
          },
        },
        visual_role: "primary",
      },
      systemTarget("sys-afas", "primary"),
    ]);
    const decision = pickMovementTarget({
      ...base,
      resolutions: withRelationship,
      selectedEntity: RELATIONSHIP_SCOPE,
    });
    expect(decision?.synthesized).toBe(false);
    expect(decision?.target.label).toBe("AFAS to Maximo employee flow");
  });

  const UNGROUNDABLE_PAIR = {
    kind: "relationship",
    label: "AFAS ↔ ForSuppliers",
    entity_id: "pair:sys-afas<->sys-lonely",
  };

  it("moves nothing rather than following one endpoint of the pair in view", () => {
    // The mitigation that must hold even when no relationship can be grounded:
    // a lone system is never followed while the user is looking at a pair.
    expect(pickMovementTarget({
      ...base,
      resolutions: resolutions([systemTarget("sys-afas", "primary")]),
      selectedEntity: UNGROUNDABLE_PAIR,
      query: "what flows from AFAS to ForSuppliers",
    })).toBeNull();
  });

  it("refuses a lone endpoint on a two-system question even with nothing selected", () => {
    expect(pickMovementTarget({
      ...base,
      resolutions: resolutions([systemTarget("sys-afas", "primary")]),
      query: "does AFAS still talk to ForSuppliers?",
    })).toBeNull();
  });

  it("still follows a job that implements the flow when the pair is ungroundable", () => {
    // The guard is about systems, not about refusing to be useful: a job is a
    // defensible place to land for a question about a flow.
    const decision = pickMovementTarget({
      ...base,
      resolutions: resolutions([
        systemTarget("sys-afas", "primary"),
        jobTarget("tJob_AfasExport", "primary"),
      ]),
      selectedEntity: UNGROUNDABLE_PAIR,
    });
    expect(decision?.target.label).toBe("tJob_AfasExport");
  });

  it("still follows the ordinary primary target on a single-entity turn", () => {
    const decision = pickMovementTarget({
      ...base,
      resolutions: resolutions([
        jobTarget("tJob_AfasExport", "primary"),
        jobTarget("tJob_Payroll"),
      ]),
      query: "what does tJob_AfasExport do?",
    });
    expect(decision?.reason).toBe("follow");
    expect(decision?.target.label).toBe("tJob_AfasExport");
  });

  it("moves nothing at all while Stay put is selected", () => {
    expect(pickMovementTarget({
      ...base,
      followConversation: false,
      resolutions: REPORTED_ANSWER,
      selectedEntity: RELATIONSHIP_SCOPE,
    })).toBeNull();
  });

  it("honours an explicit navigate target in either preference, and over the relationship", () => {
    const withNavigate = resolutions([
      systemTarget("sys-afas", "primary"),
      systemTarget("sys-maximo", "path"),
      { ...jobTarget("tJob_AfasExport", "supporting"), intent: "navigate" },
    ]);
    for (const followConversation of [true, false]) {
      const decision = pickMovementTarget({
        ...base,
        followConversation,
        resolutions: withNavigate,
        selectedEntity: RELATIONSHIP_SCOPE,
      });
      expect(decision?.reason).toBe("explicit");
      expect(decision?.target.label).toBe("tJob_AfasExport");
    }
  });
});

describe("selectRelevantTargets", () => {
  it("puts the subject first and keeps the panel off the evidence dump", () => {
    const synthesized = synthesizeRelationshipTarget(AFAS_MAXIMO, GRAPH)!;
    const chips = selectRelevantTargets([synthesized, ...REPORTED_ANSWER]);

    expect(chips[0].label).toBe("AFAS → Maximo");
    const labels = chips.map((chip) => chip.label);
    expect(labels).toContain("sys-afas");
    expect(labels).toContain("sys-maximo");
    // Six unrelated jobs came back as `supporting`; only the cap survives.
    const jobs = chips.filter((chip) => chip.target.level.kind === "job");
    expect(jobs).toHaveLength(PANEL_SUPPORTING_LIMIT);
    expect(labels).not.toContain("tJob_Contracts");
  });

  it("never drops a primary or path target to make room for supporting ones", () => {
    const noisy = resolutions([
      jobTarget("tJob_Payroll"),
      jobTarget("tJob_Assets"),
      jobTarget("tJob_Invoices"),
      jobTarget("tJob_Timesheets"),
      jobTarget("tJob_Contracts"),
      jobTarget("tJob_AfasExport", "primary"),
      systemTarget("sys-maximo", "path"),
    ]);
    const labels = selectRelevantTargets(noisy).map((chip) => chip.label);
    expect(labels.slice(0, 2)).toEqual(["tJob_AfasExport", "sys-maximo"]);
  });

  it("keeps an unresolvable chip only behind the targets that can be placed", () => {
    const mixed = resolutions([
      jobTarget("tJob_Ghost", "primary"),
      jobTarget("tJob_AfasExport", "primary"),
    ]);
    const chips = selectRelevantTargets(mixed);
    expect(chips[0].label).toBe("tJob_AfasExport");
    expect(chips[1].status).toBe("unresolvable");
  });

  it("collapses duplicate targets that resolve to the same level", () => {
    const duplicated = resolutions([
      jobTarget("tJob_AfasExport", "primary"),
      jobTarget("tJob_AfasExport", "supporting"),
    ]);
    expect(selectRelevantTargets(duplicated)).toHaveLength(1);
  });
});

describe("findRelationshipTarget", () => {
  it("matches a pair regardless of the direction the target declared", () => {
    const reversed = resolutions([
      {
        label: "Maximo ← AFAS",
        level: {
          kind: "integration",
          selection: {
            kind: "relationship",
            source: "sys-maximo",
            target: "sys-afas",
            streamKeys: STREAM_KEYS,
          },
        },
        visual_role: "primary",
      },
    ]);
    expect(findRelationshipTarget(reversed, AFAS_MAXIMO)?.label).toBe("Maximo ← AFAS");
  });

  it("does not match a different pair", () => {
    const other = resolutions([
      {
        label: "Maximo → 4PS",
        level: {
          kind: "integration",
          selection: {
            kind: "relationship",
            source: "sys-maximo",
            target: "sys-4ps",
            streamKeys: [`business-stream${UNIT_SEPARATOR}workorders`],
          },
        },
        visual_role: "primary",
      },
    ]);
    expect(findRelationshipTarget(other, AFAS_MAXIMO)).toBeNull();
  });
});
