import { describe, expect, it } from "vitest";
import {
  RuntimeSourceListResponseSchema,
  RuntimeSourceReadGrantSchema,
} from "@/types/runtime";

/**
 * The runtime-source contract, guarded at the one place a field can vanish
 * without a trace.
 *
 * Zod strips anything a schema does not declare. A stripped field is
 * permanently `undefined` in the browser with nothing logged anywhere, which is
 * how a rename lands as "the feature quietly stopped working" rather than as an
 * error. These fields are the whole point of the model change:
 *
 *   - `estate_key` is the internal row stamp, NOT a project;
 *   - `linked_project_ids` is every project attached to the source;
 *   - `is_ownerless` says whether any project has ever been attached, so no
 *     caller has to sniff the shape of the estate key to find out.
 */

const SOURCE = {
  id: 36,
  estate_key: "estate-36",
  linked_project_ids: ["werkorder", "nexus_logs"],
  is_ownerless: false,
  source_key: "tmc-36",
  source_type: "tmc",
  label: "Talend Cloud EU",
  status: "success",
  enabled: true,
  sync_interval_seconds: 300,
  ingested_at: "2026-09-01T10:00:00Z",
};

describe("RuntimeSourceListResponseSchema", () => {
  it("keeps the estate key, the attached projects and the ownerless flag", () => {
    const parsed = RuntimeSourceListResponseSchema.parse({ sources: [SOURCE], total: 1 });

    expect(parsed.sources[0].estate_key).toBe("estate-36");
    expect(parsed.sources[0].linked_project_ids).toEqual(["werkorder", "nexus_logs"]);
    expect(parsed.sources[0].is_ownerless).toBe(false);
  });

  it("carries an ownerless source with no attachments as a valid row", () => {
    const parsed = RuntimeSourceListResponseSchema.parse({
      sources: [{ ...SOURCE, estate_key: "estate:unclaimed", linked_project_ids: [], is_ownerless: true }],
      total: 1,
    });

    // An empty attachment list is a real state -- a tenant registered before
    // any project -- not a missing value to be defaulted away.
    expect(parsed.sources[0].linked_project_ids).toEqual([]);
    expect(parsed.sources[0].is_ownerless).toBe(true);
  });

  it.each(["estate_key", "linked_project_ids", "is_ownerless"])(
    "fails loudly rather than silently dropping %s",
    (field) => {
      const { [field as keyof typeof SOURCE]: _dropped, ...incomplete } = SOURCE;

      expect(() =>
        RuntimeSourceListResponseSchema.parse({ sources: [incomplete], total: 1 }),
      ).toThrow();
    },
  );

  it("no longer accepts project_id in place of the estate key", () => {
    const { estate_key: _removed, ...withoutEstate } = SOURCE;

    // The conflation this model change removed: `project_id` on a source was
    // read as "the owning project" while it was really the row stamp.
    expect(() =>
      RuntimeSourceListResponseSchema.parse({
        sources: [{ ...withoutEstate, project_id: "werkorder" }],
        total: 1,
      }),
    ).toThrow();
  });
});

describe("RuntimeSourceReadGrantSchema", () => {
  const GRANT = {
    id: 11,
    source_id: 36,
    project_id: "reader-proj",
    source_estate_key: "estate-36",
    source_label: "Talend Cloud EU",
    environment_ids: null,
    window_days: null,
    exposes_project_ids: ["estate-36"],
    created_at: "2026-09-01T10:00:00Z",
    created_by: "3",
  };

  it("reads the source's estate key under its new name", () => {
    expect(RuntimeSourceReadGrantSchema.parse(GRANT).source_estate_key).toBe("estate-36");
  });

  it("rejects the pre-rename source_project_id instead of stripping it", () => {
    const { source_estate_key: renamed, ...rest } = GRANT;

    expect(() =>
      RuntimeSourceReadGrantSchema.parse({ ...rest, source_project_id: renamed }),
    ).toThrow();
  });
});
