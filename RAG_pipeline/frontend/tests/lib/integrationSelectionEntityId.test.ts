import { describe, expect, it } from "vitest";
import { integrationSelectionEntityId } from "@/lib/topologyCopilot";

/** The canvas joins the parts of an edge key with US, as an escape. */
const US = "";
const EDGE_KEY = `business-stream${US}ds-ettm-maximo--4ps-invoice`;

/**
 * The identifier the copilot sends for an integration-level selection.
 *
 * Guards one specific regression: the scope used to carry
 * `topologyLevelKey(level)`, a CLIENT render key. `integration:edge:` in front
 * of an otherwise-valid edge key is what made a selected connection
 * unresolvable server-side -- the real key was inside the string the whole time,
 * one prefix away from working -- and the answer that followed was improvised
 * from the display label with nothing disclosing that the lookup had failed.
 */
describe("integrationSelectionEntityId", () => {
  it("yields the selection's own identifier, not the level render key", () => {
    expect(integrationSelectionEntityId({ kind: "edge", edgeKey: EDGE_KEY })).toBe(EDGE_KEY);
    expect(integrationSelectionEntityId({ kind: "system", systemId: "sys-maximo" })).toBe(
      "sys-maximo",
    );
    expect(
      integrationSelectionEntityId({ kind: "application", applicationId: "app-4ps" }),
    ).toBe("app-4ps");
  });

  it("passes the unit separator through untouched", () => {
    // Anything that treats US as whitespace produces a key that resolves to
    // nothing -- which is exactly what the backend's own token normaliser did,
    // because Python's str.split() counts the C0 separators as whitespace.
    expect(integrationSelectionEntityId({ kind: "edge", edgeKey: EDGE_KEY })).toContain(US);
  });

  it("yields nothing for a relationship, which names two systems rather than one", () => {
    // Half a pair is the wrong endpoint, not a partial answer. `level.selection`
    // carries both endpoints, which is what the backend reads for this case.
    expect(
      integrationSelectionEntityId({
        kind: "relationship",
        source: "sys-afas",
        target: "sys-maximo",
        streamKeys: [],
      }),
    ).toBeUndefined();
  });
});
