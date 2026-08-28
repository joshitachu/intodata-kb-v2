import { describe, expect, it } from "vitest";
import {
  AddSourceResponseSchema,
  GenerationPlanResponseSchema,
} from "../api/schemas";

/**
 * A field missing from the schema does not fail — it is STRIPPED.
 *
 * zod's `z.object` drops undeclared keys silently, so a contract field nobody
 * remembered to declare is permanently `undefined` in the browser with no error,
 * no warning and no failing request to debug from. For this plan that would be
 * worse than a crash: `global_invalidation.detail` is the only place the operator
 * is told WHY all six sub-projects are about to be charged for, and a stripped
 * `last_generated_at` turns every reused sub-project into "never generated".
 *
 * These tests therefore assert on the fields most likely to be lost, not just
 * that the payload parses.
 */

const FULL_PLAN = {
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
      doc_count: 11,
      last_generated_at: "2026-08-20T09:15:00Z",
    },
  ],
  notes: [
    "Cross-project references inside reused sub-projects are not rewritten. The landscape topology document is always rebuilt.",
  ],
};

describe("GenerationPlanResponseSchema", () => {
  it("keeps every field of the documented payload", () => {
    const parsed = GenerationPlanResponseSchema.parse(FULL_PLAN);

    expect(parsed.partition_count).toBe(6);
    expect(parsed.will_generate).toBe(1);
    expect(parsed.will_reuse).toBe(5);
    // The three fields a stripping bug would silence completely.
    expect(parsed.global_invalidation?.detail).toContain("no source-root attribution");
    expect(parsed.global_invalidation?.reason).toBe("shared_routine_changed");
    expect(parsed.partitions[1].last_generated_at).toBe("2026-08-20T09:15:00Z");
    // And the rest of a partition row, field by field.
    expect(parsed.partitions[0]).toEqual({
      name: "werkorder_archive",
      subdir: "werkorder_archive",
      action: "generate",
      reason: "new",
      unit_count: 34,
      doc_count: 0,
      last_generated_at: null,
    });
    expect(parsed.notes).toHaveLength(1);
  });

  it("accepts a reason code this build has never heard of", () => {
    // `partitions` is validated as an ARRAY, so rejecting one unfamiliar reason
    // would hide the generate/reuse split for EVERY sub-project — the same
    // failure mode as an unknown `connection_kind` blanking the project list.
    const parsed = GenerationPlanResponseSchema.parse({
      ...FULL_PLAN,
      partitions: [
        { ...FULL_PLAN.partitions[0], reason: "some_future_reason", action: "requeue" },
        FULL_PLAN.partitions[1],
      ],
    });
    expect(parsed.partitions.map((p) => p.reason)).toEqual([
      "some_future_reason",
      "unchanged",
    ]);
  });

  it("parses a single-run project and a project with no scan", () => {
    const single = GenerationPlanResponseSchema.parse({
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

    const unscanned = GenerationPlanResponseSchema.parse({
      project_id: "fresh",
      scan_available: false,
      composite: true,
      partitions: [],
    });
    expect(unscanned.scan_available).toBe(false);
    expect(unscanned.will_generate).toBe(0);
    expect(unscanned.global_invalidation).toBeNull();
  });

  it("still fails closed when the plan itself is malformed", () => {
    // Permissiveness is scoped to open-ended codes. A missing gate field is a
    // real contract break: defaulting it would decide the preview's visibility
    // on a guess.
    expect(() =>
      GenerationPlanResponseSchema.parse({ ...FULL_PLAN, composite: undefined }),
    ).toThrow();
  });
});

describe("AddSourceResponseSchema", () => {
  it("keeps the migration flag", () => {
    // The operator has to be told: a migrated project's original documentation
    // moves from {project}/docs to {project}/{subproject}/docs.
    const parsed = AddSourceResponseSchema.parse({
      project_id: "werkorder",
      subproject: "werkorder_archive",
      migrated_existing_source: true,
      requires_rescan: true,
      status: "added",
    });
    expect(parsed.subproject).toBe("werkorder_archive");
    expect(parsed.migrated_existing_source).toBe(true);
    expect(parsed.requires_rescan).toBe(true);
  });
});
