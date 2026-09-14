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
          connection_kind: "azure_devops",
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
    expect(result.projects[0].connection_kind).toBe("azure_devops");
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

  it("starts generation through the canonical MCP contract", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ status: "generation_started", message: "ok" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await api.generate({
      projectId: "p1",
      resume: true,
      investigationModel: "investigator",
      model: "analyst",
      writeModel: "writer",
      generationMethod: "opencode-cli",
    });

    const fetchCall = vi.mocked(globalThis.fetch).mock.calls[0];
    expect(fetchCall[0]).toBe("/mcp/generate");
    expect(JSON.parse(fetchCall[1]?.body as string)).toMatchObject({
      project_id: "p1",
      resume: true,
      investigation_model: "investigator",
      model: "analyst",
      write_model: "writer",
      doc_generation_provider: "opencode-cli",
    });
    // Unset means "use the project's grant"; the field is omitted, not guessed.
    expect(JSON.parse(fetchCall[1]?.body as string)).not.toHaveProperty("live_evidence");
    expect(JSON.parse(fetchCall[1]?.body as string)).not.toHaveProperty("organization_pack_id");
    expect(JSON.parse(fetchCall[1]?.body as string)).not.toHaveProperty("private_version_ids");
    expect(JSON.parse(fetchCall[1]?.body as string)).not.toHaveProperty("plugin_pack_id");
  });

  it("uses the connector catalogue endpoint and accepts safe connector metadata only", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({
        connectors: [{
          id: "con_1",
          provider_id: "elasticsearch",
          display_name: "Production logs",
          status: "connected",
          enabled: true,
        }],
        total: 1,
      }), { status: 200, headers: { "Content-Type": "application/json" } }),
    );

    const result = await api.listConnectors();

    expect(result.connectors[0]).toMatchObject({ id: "con_1", display_name: "Production logs" });
    expect(result.connectors[0]).not.toHaveProperty("credentials");
    expect(globalThis.fetch).toHaveBeenCalledWith("/mcp/connectors", expect.objectContaining({ credentials: "include" }));
  });

  it("writes Elastic credentials only in the create request and never expects them in the response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({
        id: "con_1", provider_id: "elasticsearch", display_name: "Production logs", enabled: true, status: "not_connected",
      }), { status: 200, headers: { "Content-Type": "application/json" } }),
    );

    const result = await api.createConnector({
      provider_id: "elasticsearch",
      display_name: "Production logs",
      safe_config: { endpoint: "https://elastic.example.test", auth_type: "api_key" },
      credential_type: "api_key",
      credential: "test-write-only-credential",
    });
    const body = JSON.parse(vi.mocked(globalThis.fetch).mock.calls[0][1]?.body as string);

    expect(body.credential).toBe("test-write-only-credential");
    expect(result).not.toHaveProperty("credential");
    expect(result).not.toHaveProperty("credentials");
  });

  it("loads project-scoped available connectors for binding without workspace credential access", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({
        connectors: [{
          connector: { id: "con_1", provider_id: "elasticsearch", display_name: "Production logs", enabled: true, status: "connected" },
          sources: [{ id: "src_1", connector_instance_id: "con_1", source_type: "data_stream", display_label: "Production events", enabled: true }],
        }],
      }), { status: 200, headers: { "Content-Type": "application/json" } }),
    );

    const result = await api.listProjectAvailableConnectors("p1");

    expect(result.connectors[0].sources[0].display_label).toBe("Production events");
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/mcp/projects/p1/available-connectors",
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("does not submit legacy per-run connector settings", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ status: "generation_started", message: "ok" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await api.generate({ projectId: "p1", model: "openai/gpt-5.6", generationMethod: "opencode-cli" });

    expect(JSON.parse(vi.mocked(globalThis.fetch).mock.calls[0][1]?.body as string)).toMatchObject({
      doc_generation_provider: "opencode-cli",
    });
    expect(JSON.parse(vi.mocked(globalThis.fetch).mock.calls[0][1]?.body as string)).not.toHaveProperty("use_connectors");
  });

  it("submits an explicit empty profile to activate Talend landscape v2", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ status: "generation_started", message: "ok" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await api.generate({
      projectId: "p1",
      generationMethod: "opencode-cli",
      generationProfile: {},
    });

    expect(JSON.parse(vi.mocked(globalThis.fetch).mock.calls[0][1]?.body as string)).toMatchObject({
      doc_generation_provider: "opencode-cli",
      generation_profile: {},
    });
  });

  it("submits the enterprise Talend contract verbatim", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ status: "generation_started", message: "ok" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await api.generate({
      projectId: "p1",
      generationMethod: "opencode-cli",
      generationProfile: { contract: "talend-enterprise-v1" },
    });

    expect(JSON.parse(vi.mocked(globalThis.fetch).mock.calls[0][1]?.body as string)).toMatchObject({
      doc_generation_provider: "opencode-cli",
      generation_profile: { contract: "talend-enterprise-v1" },
    });
  });

  it("declines the runtime lane with the contract's own vocabulary", async () => {
    // A fresh Response per call: a Body can only be read once.
    vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      new Response(JSON.stringify({ status: "generation_started", message: "ok" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await api.generate({ projectId: "p1", generationMethod: "custom", liveEvidence: false });
    expect(JSON.parse(vi.mocked(globalThis.fetch).mock.calls[0][1]?.body as string).live_evidence).toBe("off");

    await api.generate({ projectId: "p1", generationMethod: "custom", liveEvidence: true });
    expect(JSON.parse(vi.mocked(globalThis.fetch).mock.calls[1][1]?.body as string).live_evidence).toBe("project_default");
  });

});
