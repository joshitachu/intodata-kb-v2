import { describe, expect, it } from "vitest";
import {
  parseTopologyConversationFromParams,
  parseTopologyLevelFromParams,
  parseTopologyProjectFromParams,
  topologyHref,
  topologyLevelToParams,
} from "@/lib/topologyUrl";
import type { TopologyLevel } from "@/types/topology";

/**
 * The deep-link contract. Two properties matter and are tested as properties
 * rather than as example strings: every level variant survives a round trip
 * byte-for-byte, and anything that does not parse yields null so the caller
 * falls back to the landscape instead of opening a plausible wrong view.
 */

const US = "\u001f";

const SCANNER_EDGE_KEY = [
  "application",
  "talend-project:werkorder",
  "system",
  "system:connection:0:sap",
  "data",
  "db",
  "out",
].join(US);

/** Every variant of the union, with and without an optional source root. */
const LEVELS: Array<[string, TopologyLevel]> = [
  ["project", { kind: "project" }],
  ["group", { kind: "group", groupName: "Orders" }],
  ["group with source root", { kind: "group", groupName: "Orders", sourceRoot: "werkorder" }],
  ["job", { kind: "job", jobName: "tJob_OrderSync" }],
  ["job with source root", { kind: "job", jobName: "tJob_OrderSync", sourceRoot: "werkorder" }],
  ["component", { kind: "component", jobName: "tJob_OrderSync", componentName: "tMap_1" }],
  [
    "component with source root",
    {
      kind: "component",
      jobName: "tJob_OrderSync",
      componentName: "tMap_1",
      sourceRoot: "werkorder",
    },
  ],
  [
    "integration application",
    { kind: "integration", selection: { kind: "application", applicationId: "talend-project:werkorder" } },
  ],
  [
    "integration system",
    { kind: "integration", selection: { kind: "system", systemId: "landscape-system:aaaa1111" } },
  ],
  [
    "integration edge",
    { kind: "integration", selection: { kind: "edge", edgeKey: SCANNER_EDGE_KEY } },
  ],
  [
    "integration relationship",
    {
      kind: "integration",
      selection: {
        kind: "relationship",
        source: "landscape-system:aaaa1111",
        target: "landscape-system:bbbb2222",
        streamKeys: [`business-stream${US}stream-1`, `business-stream${US}stream-2`],
      },
    },
  ],
  [
    "integration relationship with no streams",
    {
      kind: "integration",
      selection: {
        kind: "relationship",
        source: "landscape-system:aaaa1111",
        target: "landscape-system:bbbb2222",
        streamKeys: [],
      },
    },
  ],
];

describe("topologyUrl round trip", () => {
  for (const [name, level] of LEVELS) {
    it(`round-trips a ${name} level`, () => {
      const params = topologyLevelToParams(level);
      expect(parseTopologyLevelFromParams(params)).toEqual(level);
    });

    it(`round-trips a ${name} level through a real query string`, () => {
      // Via the string, not the object: this is what a shared link actually
      // survives, unit separators and colons included.
      const href = topologyHref({ level, projectId: "werkorder" });
      const search = new URLSearchParams(href.slice(href.indexOf("?") + 1));
      expect(parseTopologyLevelFromParams(search)).toEqual(level);
      expect(parseTopologyProjectFromParams(search)).toBe("werkorder");
    });
  }

  it("keeps an absent source root absent rather than blank", () => {
    const params = topologyLevelToParams({ kind: "job", jobName: "tJob_OrderSync" });
    expect(params.has("root")).toBe(false);
  });

  it("does not confuse two source roots that share a job name", () => {
    const a = topologyLevelToParams({ kind: "job", jobName: "Log", sourceRoot: "bundle-a" });
    const b = topologyLevelToParams({ kind: "job", jobName: "Log", sourceRoot: "bundle-b" });
    expect(parseTopologyLevelFromParams(a)).toEqual({
      kind: "job",
      jobName: "Log",
      sourceRoot: "bundle-a",
    });
    expect(parseTopologyLevelFromParams(b)).toEqual({
      kind: "job",
      jobName: "Log",
      sourceRoot: "bundle-b",
    });
  });
});

describe("topologyUrl malformed input", () => {
  const malformed: Array<[string, string]> = [
    ["no level param", "project=werkorder"],
    ["unknown level kind", "level=galaxy"],
    ["group without a name", "level=group"],
    ["job without a name", "level=job"],
    ["component without a component name", "level=component&job=tJob_OrderSync"],
    ["component without a job name", "level=component&component=tMap_1"],
    ["integration without a selection", "level=integration"],
    ["integration with an unknown selection", "level=integration&sel=vibes"],
    ["integration system without an id", "level=integration&sel=system"],
    ["relationship missing its target", "level=integration&sel=relationship&source=a"],
    ["blank job name", "level=job&job="],
    // The wire schema is strict on both sides: a snake_case key is a contract
    // mismatch, not something to normalise.
    ["snake_case field name", "level=job&job_name=tJob_OrderSync"],
  ];

  for (const [name, search] of malformed) {
    it(`returns null for ${name}`, () => {
      expect(parseTopologyLevelFromParams(new URLSearchParams(search))).toBeNull();
    });
  }

  it("rejects a name past the wire token bound instead of truncating it", () => {
    const params = new URLSearchParams({ level: "job", job: "x".repeat(501) });
    expect(parseTopologyLevelFromParams(params)).toBeNull();
  });

  it("ignores params it does not own", () => {
    const params = new URLSearchParams("level=job&job=tJob_OrderSync&utm_source=slack");
    expect(parseTopologyLevelFromParams(params)).toEqual({
      kind: "job",
      jobName: "tJob_OrderSync",
    });
  });
});

describe("topologyHref page-level params", () => {
  it("carries the project and the conversation", () => {
    const href = topologyHref({
      level: { kind: "job", jobName: "tJob_OrderSync" },
      projectId: "werkorder",
      conversationId: 42,
    });
    const search = new URLSearchParams(href.slice(href.indexOf("?") + 1));
    expect(href.startsWith("/topology?")).toBe(true);
    expect(parseTopologyProjectFromParams(search)).toBe("werkorder");
    expect(parseTopologyConversationFromParams(search)).toBe(42);
  });

  it("omits a project and conversation it was not given", () => {
    const href = topologyHref({ level: { kind: "project" } });
    const search = new URLSearchParams(href.slice(href.indexOf("?") + 1));
    expect(parseTopologyProjectFromParams(search)).toBeNull();
    expect(parseTopologyConversationFromParams(search)).toBeNull();
  });

  it("treats a non-positive or unparseable conversation id as absent", () => {
    expect(parseTopologyConversationFromParams(new URLSearchParams("c=0"))).toBeNull();
    expect(parseTopologyConversationFromParams(new URLSearchParams("c=-3"))).toBeNull();
    expect(parseTopologyConversationFromParams(new URLSearchParams("c=new"))).toBeNull();
  });
});
