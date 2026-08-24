import { describe, expect, it } from "vitest";
import {
  readDeclaredAnswerSource,
  topologyAnswerSource,
} from "@/lib/topologyCopilot";
import type { ResolvedTopologyTarget } from "@/lib/topologyTargets";

/**
 * Source honesty. The failure this guards against: a question asked at an
 * integration depth the topology tools had nothing for was answered out of
 * generated documentation, arrived under diagram chrome, and contradicted the
 * diagram on screen. An unattributed answer must therefore never read as a
 * diagram-backed one.
 */

function resolution(label: string): ResolvedTopologyTarget {
  return {
    key: `job::${label}`,
    label,
    target: { label, level: { kind: "job", jobName: label }, intent: "reference" },
    status: "resolved",
    confirmed: "graph",
  };
}

function message(overrides: Record<string, unknown> = {}) {
  return {
    structured_answer: null,
    sources: null,
    status: "completed",
    ...overrides,
  } as Parameters<typeof topologyAnswerSource>[0];
}

describe("readDeclaredAnswerSource", () => {
  it("reads the backend's own declaration off the raw record", () => {
    expect(readDeclaredAnswerSource({ answer_source: "documents" })).toBe("documents");
    expect(readDeclaredAnswerSource({ answer_source: "topology" })).toBe("topology");
    expect(readDeclaredAnswerSource({ answer_source: "runtime" })).toBe("runtime");
    expect(readDeclaredAnswerSource({ answer_source: "none" })).toBe("none");
  });

  it("treats an unknown or malformed value as absent", () => {
    // A typo must not be able to claim the diagram vouched for an answer.
    for (const value of ["diagram", "", 3, null, {}, ["topology"]]) {
      expect(readDeclaredAnswerSource({ answer_source: value })).toBeNull();
    }
    expect(readDeclaredAnswerSource(null)).toBeNull();
    expect(readDeclaredAnswerSource("topology")).toBeNull();
  });
});

describe("topologyAnswerSource", () => {
  it("has nothing to attribute without an answer", () => {
    expect(topologyAnswerSource(null, [])).toBeNull();
  });

  it("stays quiet on a turn that failed before producing an answer", () => {
    expect(topologyAnswerSource(message({ status: "failed" }), [])).toBeNull();
  });

  it("prefers the backend's declaration over the derived guess", () => {
    // Targets present, but the backend says documents answered: the
    // declaration wins, because it is the only source that actually knows.
    const source = topologyAnswerSource(
      message({ structured_answer: { answer_source: "documents" } }),
      [resolution("tJob_OrderSync")],
    );
    expect(source?.attribution).toBe("other");
    expect(source?.label).toMatch(/not the diagram/i);
  });

  it("reports runtime data as not the diagram", () => {
    const source = topologyAnswerSource(
      message({ structured_answer: { answer_source: "runtime" } }),
      [],
    );
    expect(source?.attribution).toBe("other");
    expect(source?.label).toMatch(/runtime/i);
  });

  it("credits the diagram when the answer actually pointed at it", () => {
    const source = topologyAnswerSource(message(), [
      resolution("tJob_OrderSync"),
      resolution("tJob_Child"),
    ]);
    expect(source?.attribution).toBe("surface");
    expect(source?.detail).toBe("2 places on this page referenced");
  });

  it("names documents when there are no topology targets but there are citations", () => {
    const source = topologyAnswerSource(
      message({ sources: [{ document_id: "1" }, { document_id: "2" }] }),
      [],
    );
    expect(source?.attribution).toBe("other");
    expect(source?.label).toBe("Answered from documents, not the diagram");
    expect(source?.detail).toBe("2 documents cited");
  });

  it("says so when nothing at all backed the answer", () => {
    const source = topologyAnswerSource(message(), []);
    expect(source?.attribution).toBe("none");
    expect(source?.detail).toMatch(/verify/i);
  });

  it("does not credit the diagram for targets it could not resolve", () => {
    const unresolvable: ResolvedTopologyTarget = {
      key: "job::ghost",
      label: "Ghost",
      target: { label: "Ghost", level: { kind: "job", jobName: "ghost" }, intent: "reference" },
      status: "unresolvable",
      reason: "unknown-job",
    };
    // Targets at all means the artifact was read, so the attribution stands --
    // but the detail must not claim a place on this page was referenced when
    // none of them could be placed.
    const source = topologyAnswerSource(message(), [unresolvable]);
    expect(source?.attribution).toBe("surface");
    expect(source?.detail).toBe("Nothing it named is in the topology loaded here");
  });

  it("counts only the placeable targets in a mixed answer", () => {
    const unresolvable: ResolvedTopologyTarget = {
      key: "job::ghost",
      label: "Ghost",
      target: { label: "Ghost", level: { kind: "job", jobName: "ghost" }, intent: "reference" },
      status: "unresolvable",
      reason: "unknown-job",
    };
    const source = topologyAnswerSource(message(), [
      resolution("tJob_OrderSync"),
      unresolvable,
    ]);
    expect(source?.detail).toBe("1 place on this page referenced");
  });
});
