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

  it("getStarterPack returns public clone metadata", async () => {
    const mockData = {
      zip_url: "/mcp/plugins/starter.zip",
      repo_url: "https://github.com/example/swap-pack-starter",
      clone_command: "git clone https://github.com/example/swap-pack-starter",
    };

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(mockData), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );

    const result = await api.getStarterPack();
    expect(result.clone_command).toBe("git clone https://github.com/example/swap-pack-starter");
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/mcp/plugins/starter",
      expect.objectContaining({ credentials: "include" })
    );
  });

  it("discoverPluginPackBranches sends repo URL and parses branch packs", async () => {
    const mockData = {
      repo_url: "https://github.com/example/swap-pack-starter",
      branches: [
        {
          branch: "main",
          commit_sha: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
          has_pack: true,
          valid: true,
          name: "swap-pack-starter",
          version: "0.1.0",
          description: "Starter",
          registered_pack_id: null,
          error: null,
        },
      ],
    };

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(mockData), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );

    const result = await api.discoverPluginPackBranches(
      "https://github.com/example/swap-pack-starter"
    );

    expect(result.branches[0].branch).toBe("main");
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/mcp/plugins/discover",
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        body: JSON.stringify({ repo_url: "https://github.com/example/swap-pack-starter" }),
      })
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
