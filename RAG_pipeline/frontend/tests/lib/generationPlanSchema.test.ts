import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { AddSourceResponseSchema, GenerationPlanSchema } from "@/types";
import { GET } from "@/app/api/source/projects/[projectId]/generation-plan/route";

/**
 * `/api/source/projects/{id}/generation-plan` validates the upstream body through
 * `GenerationPlanSchema`, and Zod strips keys the schema does not declare. A
 * field the dialog reads but the schema omits is therefore not merely
 * undocumented -- it is permanently `undefined` in the browser, with no error
 * anywhere to say so. That is how `connection_kind` went missing on the Sources
 * page, and here the same failure would silently blank the reason an operator is
 * about to pay for six sub-projects instead of one.
 */

/** The plan exactly as the contract specifies it. */
const UPSTREAM_PLAN = {
  project_id: "werkorder",
  scan_available: true,
  composite: true,
  partition_count: 6,
  will_generate: 1,
  will_reuse: 5,
  global_invalidation: {
    reason: "shared_routine_changed",
    detail:
      "A shared Talend routine changed. Routines carry no source-root attribution, so every sub-project must be re-read.",
  },
  partitions: [
    {
      name: "werkorder_archive",
      subdir: "werkorder_archive",
      action: "generate",
      reason: "new",
      unit_count: 34,
      doc_count: 0,
      last_generated_at: null,
    },
    {
      name: "werkorder_core",
      subdir: "werkorder_core",
      action: "reuse",
      reason: "unchanged",
      unit_count: 120,
      doc_count: 41,
      last_generated_at: "2026-08-12T10:00:00Z",
    },
  ],
  notes: [
    "Cross-project references inside reused sub-projects are not rewritten. The landscape topology document is always rebuilt.",
  ],
};

describe("GenerationPlanSchema", () => {
  it("keeps every field the dialog renders a decision from", () => {
    const parsed = GenerationPlanSchema.parse(UPSTREAM_PLAN);

    // The split, i.e. the cost.
    expect(parsed.will_generate).toBe(1);
    expect(parsed.will_reuse).toBe(5);
    expect(parsed.partition_count).toBe(6);
    // The stated reason all six regenerate anyway. Losing this leaves a preview
    // that looks like a bug.
    expect(parsed.global_invalidation?.detail).toMatch(/shared Talend routine/);
    expect(parsed.global_invalidation?.reason).toBe("shared_routine_changed");
    // Per-sub-project rows.
    expect(parsed.partitions[0].subdir).toBe("werkorder_archive");
    expect(parsed.partitions[0].reason).toBe("new");
    expect(parsed.partitions[0].unit_count).toBe(34);
    expect(parsed.partitions[1].last_generated_at).toBe("2026-08-12T10:00:00Z");
    expect(parsed.partitions[1].doc_count).toBe(41);
    // Caveats about what a reused sub-project does not carry forward.
    expect(parsed.notes).toHaveLength(1);
  });

  it("survives a reason the frontend has never heard of", () => {
    // The server owns this vocabulary and may extend it. An enum here would
    // reject the whole plan and hide the generate/reuse split over a label.
    const parsed = GenerationPlanSchema.parse({
      ...UPSTREAM_PLAN,
      partitions: [{ ...UPSTREAM_PLAN.partitions[0], reason: "some_future_reason" }],
    });

    expect(parsed.partitions[0].reason).toBe("some_future_reason");
  });

  it("accepts the no-scan and single-run degradations", () => {
    const noScan = GenerationPlanSchema.parse({
      project_id: "fresh",
      scan_available: false,
      composite: true,
      partition_count: 0,
      will_generate: 0,
      will_reuse: 0,
      partitions: [],
      notes: [],
    });
    expect(noScan.scan_available).toBe(false);
    expect(noScan.global_invalidation).toBeUndefined();

    const single = GenerationPlanSchema.parse({
      project_id: "solo",
      scan_available: true,
      composite: false,
      partition_count: 0,
      will_generate: 1,
      will_reuse: 0,
      global_invalidation: null,
      partitions: [],
      notes: [],
    });
    expect(single.composite).toBe(false);
    expect(single.partitions).toEqual([]);
  });
});

describe("AddSourceResponseSchema", () => {
  it("keeps the fields the panel reports back to the operator", () => {
    const parsed = AddSourceResponseSchema.parse({
      project_id: "werkorder",
      subproject: "werkorder_archive",
      migrated_existing_source: true,
      requires_rescan: true,
      status: "added",
    });

    // Names the new row; without it the panel cannot show what was added.
    expect(parsed.subproject).toBe("werkorder_archive");
    // Explains the sub-project the operator did not create.
    expect(parsed.migrated_existing_source).toBe(true);
    expect(parsed.requires_rescan).toBe(true);
  });
});

describe("GET /api/source/projects/{id}/generation-plan", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function request(projectId: string) {
    return GET(
      new NextRequest(
        `http://localhost:3000/api/source/projects/${projectId}/generation-plan`,
      ),
      { params: Promise.resolve({ projectId }) },
    );
  }

  it("forwards to the MCP plan endpoint and returns the validated plan", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => UPSTREAM_PLAN,
    });
    vi.stubGlobal("fetch", fetchMock);

    const response = await request("werkorder");
    const body = await response.json();

    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/mcp/projects/werkorder/generation-plan",
    );
    expect(response.status).toBe(200);
    expect(body.will_generate).toBe(1);
    expect(body.global_invalidation.detail).toMatch(/shared Talend routine/);
    expect(body.partitions[1].last_generated_at).toBe("2026-08-12T10:00:00Z");
  });

  it("rejects a malformed project id without calling upstream", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await request("../etc");

    expect(response.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("reports a failure rather than a half-parsed plan", async () => {
    // The dialog treats any non-2xx as "no preview" and still generates, so a
    // shape it cannot trust must not be passed through as if it were a plan.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ project_id: "werkorder" }),
      }),
    );

    const response = await request("werkorder");

    expect(response.status).toBe(502);
  });
});
