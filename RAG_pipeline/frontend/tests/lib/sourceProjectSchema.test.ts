import { describe, expect, it } from "vitest";
import { SourceProjectSchema } from "@/types";
import { stripSourceSecrets } from "@/app/api/source/_proxy";

/**
 * `/api/source/projects` validates every project through this schema, and Zod
 * strips keys the schema does not declare. So a field the UI reads but the schema
 * omits is not merely undocumented -- it is permanently `undefined` in the
 * browser, with no error anywhere to say so.
 *
 * That is what happened to `connection_kind`: the Sources page computed
 * `isAzureDevOpsSource = source.connection_kind === "azure_devops"`, which was
 * always false, so every Azure DevOps project was rendered with the GitHub-only
 * access-token box and webhook buttons that do not apply to it. `artifact_counts`
 * (Talend Jobs/Routes) was invisible for the same reason.
 */

/** One project row as /mcp/projects actually returns it. */
const UPSTREAM_ROW = {
  id: "acme-talend",
  display_name: "Acme Talend",
  repo_url: "https://dev.azure.com/acme/proj/_git/repo",
  platform: "talend",
  source_type: "git",
  status: "ready",
  job_count: 143,
  doc_count: 12,
  connection_kind: "azure_devops",
  github_account_login: null,
  azure_devops_connection_id: "conn-1",
  plugin_pack_id: 7,
  artifact_counts: { total_artifacts: 150, job_count: 120, route_count: 23 },
};

describe("SourceProjectSchema", () => {
  it("keeps the fields the Sources page renders decisions from", () => {
    const parsed = SourceProjectSchema.parse(UPSTREAM_ROW);

    // Drives which credential controls the row shows at all.
    expect(parsed.connection_kind).toBe("azure_devops");
    expect(parsed.azure_devops_connection_id).toBe("conn-1");
    // Preselects the pack in the generate dialog.
    expect(parsed.plugin_pack_id).toBe(7);
    // Talend Jobs / Routes breakdown.
    expect(parsed.artifact_counts?.route_count).toBe(23);
  });

  it("accepts a row that omits every optional field", async () => {
    const minimal = {
      id: "zip-only",
      display_name: "Zip only",
      platform: "generic",
      source_type: "zip",
      status: "registered",
    };
    const parsed = SourceProjectSchema.parse(minimal);

    expect(parsed.connection_kind).toBeUndefined();
    expect(parsed.plugin_pack_id).toBeUndefined();
    expect(parsed.job_count).toBe(0);
  });

  it("does not reintroduce a secret the proxy strips", () => {
    // The schema is the second gate, after stripSourceSecrets. Neither may pass
    // a credential to the browser.
    const stripped = stripSourceSecrets({
      ...UPSTREAM_ROW,
      github_token: "ghp_live",
      local_path: "/srv/checkouts/acme",
    });
    const parsed = SourceProjectSchema.parse(stripped) as Record<string, unknown>;

    expect("github_token" in parsed).toBe(false);
    expect(parsed.local_path).toBeUndefined();
  });
});
