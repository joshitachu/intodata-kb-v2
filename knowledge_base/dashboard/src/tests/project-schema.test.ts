import { describe, expect, it } from "vitest";
import { ProjectListResponseSchema, ProjectSchema } from "../api/schemas";

/**
 * A project field the dashboard has not been taught yet must never blank the page.
 *
 * `ProjectListResponseSchema` validates an ARRAY, so a single unrecognised value
 * rejects the whole response and every project disappears behind
 * "Failed to load projects: Request failed" -- with the API having answered
 * 200 the whole time. That is exactly what happened when the backend gained
 * `connection_kind: "azure_devops"` alongside "pat" and "app": one Azure DevOps
 * project hid every GitHub and ZIP project on the page.
 */

function project(overrides: Record<string, unknown> = {}) {
  return {
    id: "p1",
    display_name: "Project One",
    repo_url: null,
    local_path: "/srv/repos/p1",
    platform: "talend",
    doc_generation_provider: null,
    source_type: "git",
    status: "ready",
    last_scan_at: null,
    last_generate_at: null,
    last_rag_push_at: null,
    job_count: 0,
    doc_count: 0,
    error_message: null,
    last_ingested_commit_sha: null,
    auto_reingest_enabled: true,
    auto_reingest_last_checked_at: null,
    auto_reingest_error: null,
    git_update_summary: null,
    has_github_token: true,
    connection_kind: "pat",
    github_account_login: null,
    webhook_status: null,
    instruction_pack_dir: null,
    plugin_pack_id: null,
    created_at: null,
    updated_at: null,
    ...overrides,
  };
}

describe("ProjectSchema.connection_kind", () => {
  it.each(["pat", "app", "azure_devops"])("accepts the known kind %s", (kind) => {
    expect(ProjectSchema.parse(project({ connection_kind: kind })).connection_kind).toBe(kind);
  });

  it("accepts a kind this build has never heard of", () => {
    // The API is free to add a connection kind before the dashboard ships support
    // for it. Rendering it in the default branch is correct; refusing to parse is not.
    expect(
      ProjectSchema.parse(project({ connection_kind: "gitlab_group" })).connection_kind,
    ).toBe("gitlab_group");
  });

  it("defaults to pat when the field is absent", () => {
    const { connection_kind: _omitted, ...withoutKind } = project();
    expect(ProjectSchema.parse(withoutKind).connection_kind).toBe("pat");
  });
});

describe("ProjectListResponseSchema", () => {
  it("keeps every project when one carries an unknown connection kind", () => {
    const parsed = ProjectListResponseSchema.parse({
      projects: [
        project({ id: "github-proj", connection_kind: "app" }),
        project({ id: "azure-proj", connection_kind: "azure_devops" }),
        project({ id: "future-proj", connection_kind: "something_new" }),
      ],
      total: 3,
    });

    expect(parsed.projects.map((p) => p.id)).toEqual([
      "github-proj",
      "azure-proj",
      "future-proj",
    ]);
  });

  it("still fails closed on a genuinely malformed project", () => {
    // Permissiveness is scoped to open-ended metadata. A missing required field
    // is a real contract break and must not be silently rendered.
    expect(() =>
      ProjectListResponseSchema.parse({
        projects: [{ ...project(), id: undefined }],
        total: 1,
      }),
    ).toThrow();
  });
});
