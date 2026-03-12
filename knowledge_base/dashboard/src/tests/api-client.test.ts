import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { api } from "../api/client";

describe("API client", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("listProjects returns projects array", async () => {
    const mockData = {
      projects: [
        {
          id: "test-project",
          display_name: "Test Project",
          status: "ready",
          platform: "talend",
          job_count: 5,
          doc_count: 3,
        },
      ],
      total: 1,
    };

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(mockData), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );

    const result = await api.listProjects();
    expect(result.projects).toHaveLength(1);
    expect(result.projects[0].id).toBe("test-project");
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/mcp/projects",
      expect.objectContaining({ credentials: "include" })
    );
  });

  it("throws on non-OK response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response("Server Error", { status: 500 })
    );

    await expect(api.listProjects()).rejects.toThrow("500");
  });

  it("redirects to login on 401", async () => {
    // Mock window.location
    const originalLocation = window.location;
    Object.defineProperty(window, "location", {
      writable: true,
      value: { ...originalLocation, href: "http://localhost:5173/", hostname: "localhost", protocol: "http:" },
    });

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(null, { status: 401 })
    );

    await expect(api.listProjects()).rejects.toThrow("Not authenticated");

    // Restore
    Object.defineProperty(window, "location", {
      writable: true,
      value: originalLocation,
    });
  });

  it("scan sends correct POST body", async () => {
    const mockResponse = { status: "scan_triggered", message: "ok" };

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(mockResponse), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );

    const result = await api.scan("my-project", true);
    expect(result.status).toBe("scan_triggered");

    const fetchCall = vi.mocked(globalThis.fetch).mock.calls[0];
    const body = JSON.parse(fetchCall[1]?.body as string);
    expect(body.project_id).toBe("my-project");
    expect(body.force).toBe(true);
  });
});
