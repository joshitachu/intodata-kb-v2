import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import {
  GET as listReaders,
  POST as addReader,
} from "@/app/api/runtime/sources/[id]/readers/route";
import { DELETE as removeReader } from "@/app/api/runtime/sources/[id]/readers/[projectId]/route";

/**
 * Attaching a project to a runtime source crosses the BFF boundary, and the
 * field most likely to be lost there is the one that matters most:
 * `exposes_project_ids` is the attachment's real reach, and Zod silently strips
 * anything a proxy schema does not declare. A dropped field is `undefined` in
 * the browser with nothing logged, so the operator would simply never be warned
 * that an attachment also hands over a third project's runtime data.
 *
 * `source_project_id` was renamed to `source_estate_key` when runtime sources
 * stopped having an owning project. A rename is the same hazard as a drop --
 * the old key is stripped and the new one is missing -- so the whole payload is
 * asserted field by field, and every field is proven load-bearing below.
 *
 * Sibling of runtimeSourceScopeRoutes.test.ts, which guards the same seam for
 * ingest scope.
 */

const GRANT = {
  id: 11,
  source_id: 36,
  project_id: "reader-proj",
  // The source's internal row stamp, not a project.
  source_estate_key: "estate-36",
  source_label: "Talend Cloud EU",
  environment_ids: null,
  window_days: null,
  exposes_project_ids: ["estate-36", "third-proj"],
  created_at: "2026-09-01T10:00:00Z",
  created_by: "3",
};

afterEach(() => {
  vi.unstubAllGlobals();
});

function captureUpstream(payload: unknown, status = 200) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function upstream(fetchMock: ReturnType<typeof captureUpstream>) {
  const [url, init] = fetchMock.mock.calls[0];
  return { url: String(url), init: init as RequestInit };
}

describe("GET /api/runtime/sources/[id]/readers", () => {
  it("targets the source's readers path", async () => {
    const fetchMock = captureUpstream({ grants: [], total: 0 });

    await listReaders(new NextRequest("http://localhost:3000/api/runtime/sources/36/readers"), {
      params: Promise.resolve({ id: "36" }),
    });

    const { url } = upstream(fetchMock);
    expect(url).toMatch(/\/api\/v1\/runtime\/sources\/36\/readers$/);
  });

  it("carries every declared field, including the renamed estate key, to the browser", async () => {
    captureUpstream({ grants: [GRANT], total: 1 });

    const response = await listReaders(
      new NextRequest("http://localhost:3000/api/runtime/sources/36/readers"),
      { params: Promise.resolve({ id: "36" }) },
    );

    expect(response.status).toBe(200);
    const body = (await response.json()) as { grants: Array<Record<string, unknown>> };
    // Field-by-field, not a shape check: a schema that forgets one of these
    // strips it, and nothing else in the system notices.
    expect(body.grants[0]).toEqual(GRANT);
    expect(body.grants[0].source_estate_key).toBe("estate-36");
    expect(body.grants[0].exposes_project_ids).toEqual(["estate-36", "third-proj"]);
  });

  /**
   * Every field, not just the memorable ones. This is the test that fails if a
   * field is ever dropped from the proxy schema or silently renamed on either
   * side -- including the `source_project_id` -> `source_estate_key` rename
   * that prompted it.
   */
  it.each(Object.keys(GRANT))("refuses a response missing %s", async (field) => {
    const { [field as keyof typeof GRANT]: _dropped, ...incomplete } = GRANT;
    captureUpstream({ grants: [incomplete], total: 1 });

    const response = await listReaders(
      new NextRequest("http://localhost:3000/api/runtime/sources/36/readers"),
      { params: Promise.resolve({ id: "36" }) },
    );

    expect(response.status).toBe(502);
  });

  it("refuses a response still using the pre-rename source_project_id", async () => {
    const { source_estate_key: renamed, ...rest } = GRANT;
    captureUpstream({ grants: [{ ...rest, source_project_id: renamed }], total: 1 });

    const response = await listReaders(
      new NextRequest("http://localhost:3000/api/runtime/sources/36/readers"),
      { params: Promise.resolve({ id: "36" }) },
    );

    // Zod would strip the old key and leave the new one undefined. 502 instead:
    // an attachment whose estate is unknown must not render.
    expect(response.status).toBe(502);
  });

  it("keeps null and [] distinct for environment_ids", async () => {
    captureUpstream({
      grants: [
        { ...GRANT, id: 1, environment_ids: null },
        { ...GRANT, id: 2, project_id: "third-proj", environment_ids: [] },
      ],
      total: 2,
    });

    const response = await listReaders(
      new NextRequest("http://localhost:3000/api/runtime/sources/36/readers"),
      { params: Promise.resolve({ id: "36" }) },
    );

    const body = (await response.json()) as { grants: Array<Record<string, unknown>> };
    // null means "every mirrored environment"; [] is a deliberate "nothing".
    expect(body.grants[0].environment_ids).toBeNull();
    expect(body.grants[1].environment_ids).toEqual([]);
  });

  it("reports a response missing exposes_project_ids instead of serving it", async () => {
    const { exposes_project_ids: _dropped, ...withoutExposure } = GRANT;
    captureUpstream({ grants: [withoutExposure], total: 1 });

    const response = await listReaders(
      new NextRequest("http://localhost:3000/api/runtime/sources/36/readers"),
      { params: Promise.resolve({ id: "36" }) },
    );

    // 502, not a 200 carrying a grant whose blast radius is unknown.
    expect(response.status).toBe(502);
  });

  it("forwards an upstream failure with its own status", async () => {
    captureUpstream({ detail: "Runtime source not found" }, 404);

    const response = await listReaders(
      new NextRequest("http://localhost:3000/api/runtime/sources/36/readers"),
      { params: Promise.resolve({ id: "36" }) },
    );

    expect(response.status).toBe(404);
    await expect(response.json()).resolves.toMatchObject({
      detail: "Runtime source not found",
    });
  });
});

describe("POST /api/runtime/sources/[id]/readers", () => {
  it("forwards the grant request verbatim and returns the grant in full", async () => {
    const fetchMock = captureUpstream({ grant: GRANT });
    const body = { project_id: "reader-proj", environment_ids: null, window_days: null };

    const response = await addReader(
      new NextRequest("http://localhost:3000/api/runtime/sources/36/readers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
      { params: Promise.resolve({ id: "36" }) },
    );

    const { url, init } = upstream(fetchMock);
    expect(url).toMatch(/\/api\/v1\/runtime\/sources\/36\/readers$/);
    expect(init.method).toBe("POST");
    // `environment_ids: null` must survive as null. Dropped or rewritten to []
    // it would flip "every environment" into "no environments".
    expect(JSON.parse(String(init.body))).toEqual(body);
    await expect(response.json()).resolves.toEqual({ grant: GRANT });
  });

  it("passes a duplicate-reader refusal through with its status and message", async () => {
    captureUpstream({ detail: "Project reader-proj already reads this source" }, 409);

    const response = await addReader(
      new NextRequest("http://localhost:3000/api/runtime/sources/36/readers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project_id: "reader-proj" }),
      }),
      { params: Promise.resolve({ id: "36" }) },
    );

    expect(response.status).toBe(409);
    await expect(response.json()).resolves.toMatchObject({
      detail: "Project reader-proj already reads this source",
    });
  });
});

describe("DELETE /api/runtime/sources/[id]/readers/[projectId]", () => {
  it("targets the reader's own path and returns an empty 204", async () => {
    const fetchMock = captureUpstream(null, 204);

    const response = await removeReader(
      new NextRequest("http://localhost:3000/api/runtime/sources/36/readers/reader%2Fproj", {
        method: "DELETE",
      }),
      { params: Promise.resolve({ id: "36", projectId: "reader/proj" }) },
    );

    const { url, init } = upstream(fetchMock);
    expect(url).toMatch(/\/api\/v1\/runtime\/sources\/36\/readers\/reader%2Fproj$/);
    expect(init.method).toBe("DELETE");
    expect(response.status).toBe(204);
    await expect(response.text()).resolves.toBe("");
  });

  it("forwards a refusal rather than reporting a revoke that did not happen", async () => {
    captureUpstream({ detail: "Not permitted" }, 403);

    const response = await removeReader(
      new NextRequest("http://localhost:3000/api/runtime/sources/36/readers/reader-proj", {
        method: "DELETE",
      }),
      { params: Promise.resolve({ id: "36", projectId: "reader-proj" }) },
    );

    expect(response.status).toBe(403);
  });
});
