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
          repo_url: null,
          local_path: "/data/repos/test-project",
          platform: "talend",
          doc_generation_provider: null,
          source_type: "git",
          status: "ready",
          last_scan_at: null,
          last_generate_at: null,
          last_rag_push_at: null,
          job_count: 5,
          doc_count: 3,
          error_message: null,
          last_ingested_commit_sha: null,
          auto_reingest_enabled: false,
          auto_reingest_last_checked_at: null,
          auto_reingest_error: null,
          git_update_summary: null,
          has_github_token: false,
          webhook_status: null,
          instruction_pack_dir: null,
          plugin_pack_id: null,
          created_at: null,
          updated_at: null,
        },
        {
          id: "scanned-project",
          display_name: "Scanned Project",
          repo_url: null,
          local_path: "/data/repos/scanned-project",
          platform: "talend",
          doc_generation_provider: null,
          source_type: "upload",
          status: "scanned",
          last_scan_at: "2026-08-04T10:00:00Z",
          last_generate_at: null,
          last_rag_push_at: null,
          job_count: 12,
          doc_count: 0,
          error_message: null,
          last_ingested_commit_sha: null,
          auto_reingest_enabled: false,
          auto_reingest_last_checked_at: null,
          auto_reingest_error: null,
          git_update_summary: null,
          has_github_token: false,
          webhook_status: null,
          instruction_pack_dir: null,
          plugin_pack_id: null,
          created_at: null,
          updated_at: null,
        },
        {
          id: "error-project",
          display_name: "Error Project",
          repo_url: null,
          local_path: "/data/repos/error-project",
          platform: "generic",
          doc_generation_provider: null,
          source_type: "upload",
          status: "error",
          last_scan_at: null,
          last_generate_at: null,
          last_rag_push_at: null,
          job_count: 0,
          doc_count: 0,
          error_message: "scan failed",
          last_ingested_commit_sha: null,
          auto_reingest_enabled: false,
          auto_reingest_last_checked_at: null,
          auto_reingest_error: null,
          git_update_summary: null,
          has_github_token: false,
          webhook_status: null,
          instruction_pack_dir: null,
          plugin_pack_id: null,
          created_at: null,
          updated_at: null,
        },
      ],
      total: 3,
    };

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(mockData), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );

    const result = await api.listProjects();
    expect(result.projects).toHaveLength(3);
    expect(result.projects[0].id).toBe("test-project");
    expect(result.projects.map((project) => project.status)).toEqual([
      "ready",
      "scanned",
      "error",
    ]);
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/mcp/projects",
      expect.objectContaining({ credentials: "include" })
    );
  });

  it("listPlatforms calls discovery endpoint", async () => {
    const mockData = {
      platforms: [
        {
          id: "talend",
          label: "Talend",
          unit_label: "Job",
          unit_label_plural: "Jobs",
          badge_color: "#4285F4",
          accent_color: "#4285F4",
        },
      ],
    };

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(mockData), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );

    const result = await api.listPlatforms();
    expect(result.platforms[0].id).toBe("talend");
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/mcp/platforms",
      expect.objectContaining({ credentials: "include" })
    );
  });

  it("listModels calls discovery endpoint", async () => {
    const mockData = {
      models: [
        {
          id: "moonshot/kimi-k2.6",
          provider: "moonshot",
          label: "Kimi k2.6",
          supports_analysis: true,
          supports_writing: true,
          is_default_analysis: true,
          is_default_investigation: true,
          is_default_writing: true,
          is_local: false,
        },
      ],
    };

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(mockData), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );

    const result = await api.listModels();
    expect(result.models[0].id).toBe("moonshot/kimi-k2.6");
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/mcp/models",
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

  it("loads the Nexus pack workspace through the dashboard prefix", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({
        project_id: "p1",
        organization_packs: [],
        personal_packs: [],
        selected_organization_pack_id: null,
        selected_private_version_ids: [],
      }), { status: 200, headers: { "Content-Type": "application/json" } }),
    );

    await api.getPackWorkspace("p1");

    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/nexus-api/harness/packs/projects/p1/workspace",
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("sends unified pack selections to Nexus generation", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ status: "generation_started", message: "ok" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await api.generate(
      "p1",
      true,
      "investigator",
      "analyst",
      "writer",
      10,
      [21],
      "opencode-cli",
    );

    const fetchCall = vi.mocked(globalThis.fetch).mock.calls[0];
    expect(fetchCall[0]).toBe("/nexus-api/harness/packs/projects/p1/generate");
    expect(JSON.parse(fetchCall[1]?.body as string)).toMatchObject({
      resume: true,
      investigation_model: "investigator",
      model: "analyst",
      write_model: "writer",
      doc_generation_provider: "opencode-cli",
      organization_pack_id: 10,
      private_version_ids: [21],
    });
    expect(JSON.parse(fetchCall[1]?.body as string)).not.toHaveProperty("project_id");
    expect(JSON.parse(fetchCall[1]?.body as string)).not.toHaveProperty("plugin_pack_id");
  });

});
