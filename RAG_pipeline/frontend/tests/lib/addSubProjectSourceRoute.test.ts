import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { POST } from "@/app/api/source/projects/[projectId]/sources/route";

/**
 * `/api/source/projects/{id}/sources` is the additive ZIP path: it appends a
 * sub-project and leaves everything already generated in place. Kept separate
 * from `/api/source/upload-zip`, which owns a project's whole tree -- routing the
 * additive case through that one would replace the source the operator is trying
 * to extend.
 */

afterEach(() => {
  vi.unstubAllGlobals();
});

function upstreamOk(body: unknown) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    text: async () => JSON.stringify(body),
    headers: { get: () => "application/json" },
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/**
 * The request is a stub rather than a real `NextRequest`. Under jsdom the
 * multipart body built from jsdom's `FormData`/`File` is unreadable by undici's
 * `formData()` parser ("Content-Type was not one of multipart/form-data"), which
 * is an environment quirk and not the handler's behaviour. Stubbing the two
 * things the handler actually uses keeps the test about the handler.
 */
function post(projectId: string, form: FormData) {
  const request = {
    headers: new Headers({ cookie: "session=abc" }),
    formData: async () => form,
  } as unknown as NextRequest;
  return POST(request, { params: Promise.resolve({ projectId }) });
}

function zipForm(): FormData {
  const form = new FormData();
  form.append("file", new File(["zip-bytes"], "archive.zip"), "archive.zip");
  return form;
}

describe("POST /api/source/projects/{id}/sources", () => {
  it("forwards the archive to the project's own sources endpoint", async () => {
    const fetchMock = upstreamOk({
      project_id: "werkorder",
      subproject: "werkorder_archive",
      migrated_existing_source: true,
      requires_rescan: true,
      status: "added",
    });

    const response = await post("werkorder", zipForm());
    const body = await response.json();

    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/mcp/projects/werkorder/sources",
    );
    // Not /mcp/upload-zip: that one replaces the whole tree.
    expect(String(fetchMock.mock.calls[0][0])).not.toContain("upload-zip");
    expect(response.status).toBe(200);
    // Both fields survive the schema; either one missing leaves the panel unable
    // to explain what happened.
    expect(body.subproject).toBe("werkorder_archive");
    expect(body.migrated_existing_source).toBe(true);
  });

  it("rejects a malformed project id without calling upstream", async () => {
    const fetchMock = upstreamOk({});

    const response = await post("../etc", zipForm());

    expect(response.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("requires a file", async () => {
    const fetchMock = upstreamOk({});

    const response = await post("werkorder", new FormData());

    expect(response.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("passes an upstream refusal through verbatim", async () => {
    // "This project is not a ZIP project" and the size-limit message are both
    // user-facing; a generic 500 would hide the actual reason.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 400,
        text: async () => JSON.stringify({ detail: "project is not a zip project" }),
        headers: { get: () => "application/json" },
      }),
    );

    const response = await post("werkorder", zipForm());

    expect(response.status).toBe(400);
    expect(await response.text()).toContain("project is not a zip project");
  });
});
