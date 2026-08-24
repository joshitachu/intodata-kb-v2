import { describe, expect, it } from "vitest";
import { stripSourceSecrets, safeUuid } from "@/app/api/source/_proxy";

describe("stripSourceSecrets", () => {
  it("removes the Azure DevOps organization PAT in both field names", () => {
    const stripped = stripSourceSecrets({
      id: "conn-1",
      organization_url: "https://dev.azure.com/acme",
      pat: "raw-token",
      pat_enc: "gAAAA...",
    }) as Record<string, unknown>;

    expect(stripped).toEqual({
      id: "conn-1",
      organization_url: "https://dev.azure.com/acme",
    });
  });

  it("still removes GitHub tokens and local paths", () => {
    const stripped = stripSourceSecrets({
      id: "proj",
      github_token: "ghp_x",
      github_token_enc: "enc",
      local_path: "/srv/repos/proj",
      repo_url: "https://github.com/acme/repo",
    }) as Record<string, unknown>;

    expect(stripped).toEqual({ id: "proj", repo_url: "https://github.com/acme/repo" });
  });

  it("strips secrets nested inside arrays and objects", () => {
    const stripped = stripSourceSecrets({
      connections: [{ id: "a", pat_enc: "secret" }],
      nested: { deeper: { pat: "secret" } },
    }) as { connections: Record<string, unknown>[]; nested: { deeper: Record<string, unknown> } };

    expect(stripped.connections[0]).toEqual({ id: "a" });
    expect(stripped.nested.deeper).toEqual({});
  });
});

describe("safeUuid", () => {
  it("accepts a UUID and rejects anything that could traverse the upstream path", () => {
    expect(safeUuid("3f2504e0-4f89-11d3-9a0c-0305e82c3301")).toBe(
      "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
    );
    expect(safeUuid(" 3f2504e0-4f89-11d3-9a0c-0305e82c3301 ")).toBe(
      "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
    );
    expect(safeUuid("../../projects")).toBeNull();
    expect(safeUuid("not-a-uuid")).toBeNull();
    expect(safeUuid("")).toBeNull();
  });
});
