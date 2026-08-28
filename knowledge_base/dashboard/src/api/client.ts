import { type ZodType } from "zod";
import { getLoginUrl } from "../hooks/useAuth";
import {
  AddSourceResponseSchema,
  AssignPluginPackResponseSchema,
  ConnectorCatalogResponseSchema,
  ConnectorEvidenceSummarySchema,
  ConnectorInstanceSchema,
  ConnectorListResponseSchema,
  ConnectorSourceListResponseSchema,
  KibanaDataViewListResponseSchema,
  KibanaSpaceListResponseSchema,
  ConnectorTestResultSchema,
  DocContentSchema,
  DocListResponseSchema,
  GenerateAssessmentResponseSchema,
  GenerationPlanResponseSchema,
  InstanceSettingsResponseSchema,
  GenerateRunListResponseSchema,
  InstructionPackMetadataSchema,
  LogContentSchema,
  LogFileListResponseSchema,
  ModelListResponseSchema,
  PlatformListResponseSchema,
  PluginPackBranchDiscoverySchema,
  PluginPackFileSchema,
  PluginPackFilesSchema,
  PluginPackListSchema,
  PluginPackSchema,
  ProjectListResponseSchema,
  ProjectConnectorBindingListResponseSchema,
  ProjectConnectorBindingSchema,
  ProjectAvailableConnectorListResponseSchema,
  ProjectSchema,
  ProjectUsageSchema,
  QualityHistoryResponseSchema,
  RuntimeDocReadScopeSchema,
  RuntimeSourceListResponseSchema,
  RuntimeSourceReadGrantListResponseSchema,
  RuntimeSourceReadGrantResponseSchema,
  RuntimeSourceSchema,
  RuntimeSourceScopesResponseSchema,
  RuntimeSourceSyncResponseSchema,
  RuntimeSourceTypeListResponseSchema,
  QuestionnaireResponseSchema,
  ReviewAggregateResponseSchema,
  RunComparisonSchema,
  ScanRunListResponseSchema,
  ScanStatusResponseSchema,
  StatusResponseSchema,
  StarterPackSchema,
  SubmitReviewResponseSchema,
  WebhookStatusSchema,
} from "./schemas";

function parseResponse<T>(schema: ZodType<T>, data: unknown): T {
  return schema.parse(data);
}

// The backend's CSRFMiddleware uses the double-submit cookie pattern: the
// non-httpOnly `csrf_token` cookie set at login must be echoed back in the
// X-CSRF-Token header on every state-changing request.
export function getCsrfToken(): string | undefined {
  const raw = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]*)/)?.[1];
  return raw ? decodeURIComponent(raw) : undefined;
}

export function csrfHeaders(): Record<string, string> {
  const token = getCsrfToken();
  return token ? { "X-CSRF-Token": token } : {};
}

async function request<T>(
  path: string,
  options: RequestInit = {},
  schema?: ZodType<T>
): Promise<T> {
  // Don't set Content-Type for FormData — browser sets it with the correct boundary
  const isFormData = options.body instanceof FormData;
  const res = await fetch(path, {
    ...options,
    headers: isFormData
      ? { ...csrfHeaders(), ...(options.headers as Record<string, string>) }
      : { "Content-Type": "application/json", ...csrfHeaders(), ...(options.headers as Record<string, string>) },
    credentials: "include",
  });

  if (res.status === 401) {
    window.location.href = `${getLoginUrl()}?redirect=${encodeURIComponent(window.location.href)}`;
    throw new Error("Not authenticated");
  }

  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`${res.status}: ${detail}`);
  }
  if (res.status === 204) return undefined as T;
  const data = await res.json();
  return schema ? parseResponse(schema, data) : (data as T);
}

export const api = {
  listProjects: () =>
    request<{ projects: import("../types").Project[]; total: number }>(
      "/mcp/projects",
      {},
      ProjectListResponseSchema
    ),

  listPlatforms: () =>
    request<import("../types").PlatformListResponse>(
      "/mcp/platforms",
      {},
      PlatformListResponseSchema
    ),

  listModels: () =>
    request<import("../types").ModelListResponse>(
      "/mcp/models",
      {},
      ModelListResponseSchema
    ),

  getInstanceSettings: () =>
    request<{ settings: Record<string, string> }>(
      "/mcp/admin/instance-settings",
      {},
      InstanceSettingsResponseSchema
    ),

  updateInstanceSettings: (settings: Record<string, string | null>) =>
    request<{ settings: Record<string, string> }>(
      "/mcp/admin/instance-settings",
      {
        method: "PUT",
        body: JSON.stringify({ settings }),
      },
      InstanceSettingsResponseSchema
    ),

  // Every credential reference /mcp/add-repo accepts must be present here.
  // Omitting one does not fail loudly: the server falls back to an
  // unauthenticated clone, which a private repo rejects as "Authentication
  // failed" — a credential error for what is really a missing field.
  addRepo: (data: { url: string; project_id: string; platform?: string; display_name?: string; branch?: string; github_token?: string; installation_id?: number; azure_devops_connection_id?: string }) =>
    request<import("../types").Project>("/mcp/add-repo", {
      method: "POST",
      body: JSON.stringify(data),
    }, ProjectSchema),

  // GitHub App connect: get the install URL to redirect the operator to.
  getGithubAppInstallUrl: (projectId?: string) =>
    request<{ install_url: string }>(
      `/mcp/github/app/connect${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ""}`,
    ),

  // Every GitHub App installation this user may use. The repo-picker needs an
  // installation id before it can list anything, and an operator adding a
  // sub-project to an existing project has no post-install redirect to read one
  // from -- so it is asked for here rather than reconstructed from the URL.
  listGithubAppInstallations: () =>
    request<{ installations: import("../types").GithubAppInstallation[] }>(
      "/mcp/github/app/installations",
    ),

  // List repos an installation can access (for the repo-picker).
  listGithubAppRepos: (installationId: number) =>
    request<{
      installation_id: number;
      account_login: string | null;
      repositories: { full_name: string | null; clone_url: string | null; private: boolean | null; default_branch: string | null }[];
    }>(`/mcp/github/app/installations/${installationId}/repos`),

  // --- Azure DevOps organization connections -------------------------------
  // Azure DevOps has no GitHub-App equivalent, so a PAT is the only credential.
  // It is held once per organization, encrypted, and never returned here.
  listAzureDevOpsConnections: () =>
    request<{ connections: import("../types").AzureDevOpsConnection[] }>(
      "/mcp/azure-devops/connections",
    ),

  connectAzureDevOps: (organization_url: string, pat: string) =>
    request<import("../types").AzureDevOpsConnection>("/mcp/azure-devops/connections", {
      method: "POST",
      body: JSON.stringify({ organization_url, pat }),
    }),

  testAzureDevOpsConnection: (connectionId: string) =>
    request<import("../types").AzureDevOpsConnection>(
      `/mcp/azure-devops/connections/${encodeURIComponent(connectionId)}/test`,
      { method: "POST" },
    ),

  deleteAzureDevOpsConnection: (connectionId: string) =>
    request<void>(`/mcp/azure-devops/connections/${encodeURIComponent(connectionId)}`, {
      method: "DELETE",
    }),

  // Every repository the PAT can read, across all Azure DevOps projects.
  listAzureDevOpsRepos: (connectionId: string) =>
    request<{
      connection_id: string;
      organization_name: string;
      repositories: import("../types").AzureDevOpsRepo[];
    }>(`/mcp/azure-devops/connections/${encodeURIComponent(connectionId)}/repos`),

  // --- Multi-repo projects --------------------------------------------------
  // One project spanning N repositories: each member becomes its own
  // sub-project with its own documentation set and its own generation budget,
  // plus a shared cross-repo topology layer.
  createMultiRepoProject: (data: {
    project_id: string;
    display_name?: string;
    platform: string;
    provider: "github" | "azure_devops";
    members: {
      repo_identity: string;
      repo_name: string;
      clone_url: string;
      branch?: string;
    }[];
    installation_id?: number;
    azure_devops_connection_id?: string;
  }) =>
    request<import("../types").MultiRepoProject>("/mcp/multi-repo/projects", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  // Members of a multi-repo project. Empty for a single-repo project.
  listMultiRepoMembers: (projectId: string) =>
    request<import("../types").MultiRepoProject>(
      `/mcp/multi-repo/projects/${encodeURIComponent(projectId)}/members`,
    ),

  // Add repositories to an existing multi-repo project. Members already present
  // are ignored rather than rejected, and only the new ones are cloned, so
  // re-submitting the picker's whole selection is safe. Same request shape as
  // creation on purpose -- the credential reference travels with the members.
  addMultiRepoMembers: (
    projectId: string,
    data: {
      platform: string;
      provider: "github" | "azure_devops";
      members: {
        repo_identity: string;
        repo_name: string;
        clone_url: string;
        branch?: string;
      }[];
      installation_id?: number;
      azure_devops_connection_id?: string;
    },
  ) =>
    request<import("../types").MultiRepoProject>(
      `/mcp/multi-repo/projects/${encodeURIComponent(projectId)}/members`,
      {
        method: "POST",
        // `project_id` is in the path, but the request model still requires it
        // in the body; sending only the path form fails validation.
        body: JSON.stringify({ project_id: projectId, ...data }),
      },
    ),

  /**
   * Add one more ZIP source to an existing ZIP project.
   *
   * Additive, unlike `replace-zip`: the project's existing source trees are
   * left in place and the archive becomes a new sub-project. The server may
   * first migrate a flat single-source tree into a named sub-project directory,
   * which it reports back so the UI can say so.
   */
  addProjectZipSource: (projectId: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<import("../types").AddSourceResult>(
      `/mcp/projects/${encodeURIComponent(projectId)}/sources`,
      { method: "POST", body: form },
      AddSourceResponseSchema,
    );
  },

  removeMultiRepoMember: (projectId: string, sourceId: string) =>
    request<{ status: string; message?: string }>(
      `/mcp/multi-repo/projects/${encodeURIComponent(projectId)}/members/${encodeURIComponent(sourceId)}`,
      { method: "DELETE" },
    ),

  // Fast-forward member clones. `repoIdentity` limits it to one repository --
  // the incremental path, and the manual equivalent of the push webhook until
  // that is wired for multi-repo projects.
  syncMultiRepoProject: (projectId: string, repoIdentity?: string) =>
    request<import("../types").MultiRepoProject>(
      `/mcp/multi-repo/projects/${encodeURIComponent(projectId)}/sync${
        repoIdentity ? `?repo_identity=${encodeURIComponent(repoIdentity)}` : ""
      }`,
      { method: "POST" },
    ),

  updateGithubToken: (projectId: string, github_token: string) =>
    request<import("../types").Project>(`/mcp/projects/${projectId}/github-token`, {
      method: "PATCH",
      body: JSON.stringify({ github_token }),
    }, ProjectSchema),

  registerWebhook: (projectId: string) =>
    request<import("../types").Project>(`/mcp/projects/${projectId}/github-webhook/register`, {
      method: "POST",
    }, ProjectSchema),

  deleteWebhook: (projectId: string) =>
    request<import("../types").StatusResponse>(
      `/mcp/projects/${projectId}/github-webhook`,
      { method: "DELETE" },
      StatusResponseSchema
    ),

  getWebhookStatus: (projectId: string) =>
    request<{ project_id: string; webhook_status: string; webhook_id: number | null }>(
      `/mcp/projects/${projectId}/github-webhook/status`,
      {},
      WebhookStatusSchema
    ),

  uploadZip: (file: File, projectId: string, platform: string = "talend") => {
    const form = new FormData();
    form.append("file", file);
    form.append("project_id", projectId);
    form.append("platform", platform);
    return request<import("../types").Project>("/mcp/upload-zip", { method: "POST", body: form }, ProjectSchema);
  },

  scan: (projectId: string, force = false) =>
    request<import("../types").StatusResponse>("/mcp/scan", {
      method: "POST",
      body: JSON.stringify({ project_id: projectId, force }),
    }, StatusResponseSchema),

  pushToRag: (projectId: string) =>
    request<import("../types").StatusResponse>("/mcp/push-to-rag", {
      method: "POST",
      body: JSON.stringify({ project_id: projectId }),
    }, StatusResponseSchema),

  resetStatus: (projectId: string) =>
    request<import("../types").StatusResponse>("/mcp/reset-status", {
      method: "POST",
      body: JSON.stringify({ project_id: projectId }),
    }, StatusResponseSchema),

  repairLatestGitUpdate: (projectId: string) =>
    request<import("../types").StatusResponse>(
      `/mcp/projects/${projectId}/git-updates/repair-latest`,
      { method: "POST" },
      StatusResponseSchema
    ),

  deleteProject: (projectId: string) =>
    request<import("../types").StatusResponse>(`/mcp/projects/${projectId}`, {
      method: "DELETE",
    }, StatusResponseSchema),

  getStatus: (projectId: string) =>
    request<import("../types").ScanStatusResponse>(
      `/mcp/status/${projectId}`,
      {},
      ScanStatusResponseSchema
    ),

  listGenerateRuns: (projectId: string) =>
    request<{ runs: import("../types").GenerateRun[]; total: number }>(
      `/mcp/generate-runs/${projectId}`,
      {},
      GenerateRunListResponseSchema
    ),

  listLogFiles: (projectId: string) =>
    request<{ files: import("../types").LogFile[]; total: number }>(
      `/mcp/logs/${projectId}`,
      {},
      LogFileListResponseSchema
    ),

  getLogContent: (projectId: string, filename: string) =>
    request<{ filename: string; content: string }>(
      `/mcp/logs/${projectId}/${filename}`,
      {},
      LogContentSchema
    ),

  updateProject: (projectId: string, data: { display_name?: string; platform?: string; doc_generation_provider?: string | null; instruction_pack_dir?: string | null }) =>
    request<import("../types").Project>(`/mcp/projects/${projectId}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }, ProjectSchema),

  listPluginPacks: () =>
    request<import("../types").PluginPack[]>("/mcp/plugins", {}, PluginPackListSchema),

  getStarterPack: () =>
    request<import("../types").StarterPack>("/mcp/plugins/starter", {}, StarterPackSchema),

  discoverPluginPackBranches: (repoUrl?: string | null) =>
    request<import("../types").PluginPackBranchDiscovery>(
      "/mcp/plugins/discover",
      {
        method: "POST",
        body: JSON.stringify({ repo_url: repoUrl ?? undefined }),
      },
      PluginPackBranchDiscoverySchema
    ),

  assignPluginPack: (projectId: string, pluginPackId: number | null) =>
    request<{ project_id: string; plugin_pack_id: number | null }>(
      `/mcp/projects/${projectId}/plugin`,
      {
        method: "PATCH",
        body: JSON.stringify({ plugin_pack_id: pluginPackId }),
      },
      AssignPluginPackResponseSchema
    ),

  registerPluginPack: (body: { path?: string; repo_url?: string; repo_branch?: string }) =>
    request<import("../types").PluginPack>("/mcp/plugins", {
      method: "POST",
      body: JSON.stringify(body),
    }, PluginPackSchema),

  uploadPluginPackZip: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<import("../types").PluginPack>("/mcp/plugins/upload", {
      method: "POST",
      body: form,
    }, PluginPackSchema);
  },

  pullPluginPack: (packId: number) =>
    request<import("../types").PluginPack>(`/mcp/plugins/${packId}`, {
      method: "PATCH",
      body: JSON.stringify({ pull: true }),
    }, PluginPackSchema),

  deletePluginPack: (packId: number) =>
    request<void>(`/mcp/plugins/${packId}`, { method: "DELETE" }),

  getPluginPackFiles: (packId: number) =>
    request<string[]>(`/mcp/plugins/${packId}/files`, {}, PluginPackFilesSchema),

  getPluginPackBranchFiles: (repoUrl: string, repoBranch: string) =>
    request<string[]>("/mcp/plugins/branch-files", {
      method: "POST",
      body: JSON.stringify({ repo_url: repoUrl, repo_branch: repoBranch }),
    }, PluginPackFilesSchema),

  getPluginPackBranchFile: (repoUrl: string, repoBranch: string, filePath: string) =>
    request<import("../types").PluginPackFileContent>("/mcp/plugins/branch-file", {
      method: "POST",
      body: JSON.stringify({ repo_url: repoUrl, repo_branch: repoBranch, file_path: filePath }),
    }, PluginPackFileSchema),

  getPluginPackFile: (packId: number, filePath: string) =>
    request<{ path: string; content: string }>(
      `/mcp/plugins/${packId}/files/${filePath}`,
      {},
      PluginPackFileSchema
    ),

  savePluginPackFile: (packId: number, filePath: string, content: string) =>
    request<{ path: string; content: string }>(`/mcp/plugins/${packId}/files/${filePath}`, {
      method: "PUT",
      body: JSON.stringify({ content }),
    }, PluginPackFileSchema),

  listScanRuns: (projectId: string) =>
    request<{ runs: import("../types").ScanRun[]; total: number }>(
      `/mcp/scan-runs/${projectId}`,
      {},
      ScanRunListResponseSchema
    ),

  listDocs: (projectId: string, source: import("../types").DocSource = "published") =>
    request<{ project_id: string; docs: import("../types").DocFile[]; total: number }>(
      `/mcp/projects/${projectId}/docs?source=${source}`,
      {},
      DocListResponseSchema
    ),

  getDocContent: (projectId: string, filename: string, source: import("../types").DocSource = "published") =>
    request<{ filename: string; content: string }>(
      `/mcp/projects/${projectId}/docs/${filename}/content?source=${source}`,
      {},
      DocContentSchema
    ),

  getProjectUsage: (projectId: string) =>
    request<import("../types").ProjectUsage>(
      `/mcp/projects/${projectId}/usage`,
      {},
      ProjectUsageSchema
    ),

  compareGenerateRuns: (projectId: string, a: number, b: number) =>
    request<import("../types").RunComparison>(
      `/mcp/generate-runs/${projectId}/compare?a=${a}&b=${b}`,
      {},
      RunComparisonSchema
    ),

  getQualityHistory: (projectId: string) =>
    request<import("../types").QualityHistoryResponse>(
      `/mcp/projects/${projectId}/quality-history`,
      {},
      QualityHistoryResponseSchema
    ),

  getQuestionnaire: (projectId: string, runId: number) =>
    request<import("../types").QuestionnaireResponse>(
      `/mcp/projects/${projectId}/questionnaire?run_id=${runId}`,
      {},
      QuestionnaireResponseSchema
    ),

  submitReview: (
    projectId: string,
    data: {
      run_id: number;
      reviewer: string;
      overall: number;
      questions: import("../types").ReviewQuestion[];
      feedback: string;
      reviewed_at: string;
    },
  ) =>
    request<{ status: string; path: string }>(`/mcp/projects/${projectId}/reviews`, {
      method: "POST",
      body: JSON.stringify(data),
    }, SubmitReviewResponseSchema),

  getReviews: (projectId: string, runId?: number | null) =>
    request<import("../types").ReviewAggregateResponse>(
      `/mcp/projects/${projectId}/reviews${runId != null ? `?run_id=${runId}` : ""}`,
      {},
      ReviewAggregateResponseSchema
    ),

  getInstructionPack: () =>
    request<import("../types").InstructionPackMetadata>(
      "/mcp/instruction-pack",
      {},
      InstructionPackMetadataSchema
    ),

  generateAssessment: (projectId: string) =>
    request<{ project_id: string; status: string; html_path: string | null; error: string | null }>(
      `/mcp/projects/${projectId}/generate-assessment`,
      { method: "POST" },
      GenerateAssessmentResponseSchema
    ),

  listConnectorCatalog: () =>
    request<{ providers: import("../types").ConnectorProvider[] }>(
      "/mcp/connectors/catalog",
      {},
      ConnectorCatalogResponseSchema,
    ),

  listConnectors: () =>
    request<{ connectors: import("../types").ConnectorInstance[]; total?: number }>(
      "/mcp/connectors",
      {},
      ConnectorListResponseSchema,
    ),

  getConnector: (connectorId: string) =>
    request<import("../types").ConnectorInstance>(
      `/mcp/connectors/${encodeURIComponent(connectorId)}`,
      {},
      ConnectorInstanceSchema,
    ),

  createConnector: (data: {
    provider_id: string;
    display_name: string;
    safe_config: Record<string, unknown>;
    credential_type: "api_key" | "basic";
    credential: string;
    kibana_credential?: string;
  }) =>
    request<import("../types").ConnectorInstance>("/mcp/connectors", {
      method: "POST",
      body: JSON.stringify(data),
    }, ConnectorInstanceSchema),

  updateConnector: (connectorId: string, data: { display_name?: string; safe_config?: Record<string, unknown>; enabled?: boolean }) =>
    request<import("../types").ConnectorInstance>(
      `/mcp/connectors/${encodeURIComponent(connectorId)}`,
      { method: "PATCH", body: JSON.stringify(data) },
      ConnectorInstanceSchema,
    ),

  deleteConnector: (connectorId: string) =>
    request<void>(`/mcp/connectors/${encodeURIComponent(connectorId)}`, { method: "DELETE" }),

  testConnector: (connectorId: string) =>
    request<import("../types").ConnectorTestResult>(
      `/mcp/connectors/${encodeURIComponent(connectorId)}/test`,
      { method: "POST" },
      ConnectorTestResultSchema,
    ),

  rotateConnectorCredential: (connectorId: string, credentials: {
    credential: string;
    credential_type?: "api_key" | "basic";
    kibana_credential?: string;
    reuse_elasticsearch_credential?: boolean;
  }) =>
    request<import("../types").ConnectorInstance>(
      `/mcp/connectors/${encodeURIComponent(connectorId)}/rotate-credential`,
      { method: "POST", body: JSON.stringify(credentials) },
      ConnectorInstanceSchema,
    ),

  listConnectorSources: (connectorId: string) =>
    request<{ connector_instance_id: string; sources: import("../types").ConnectorSource[]; total?: number }>(
      `/mcp/connectors/${encodeURIComponent(connectorId)}/sources`,
      {},
      ConnectorSourceListResponseSchema,
    ),

  discoverConnectorSources: (connectorId: string, data?: { source_ids?: string[]; include_kibana?: boolean; kibana_space?: string; data_view_id?: string }) =>
    request<{ connector_instance_id: string; sources: import("../types").ConnectorSource[]; total?: number }>(
      `/mcp/connectors/${encodeURIComponent(connectorId)}/discover`,
      { method: "POST", ...(data ? { body: JSON.stringify(data) } : {}) },
      ConnectorSourceListResponseSchema,
    ),

  listKibanaSpaces: (connectorId: string) =>
    request<{ spaces: import("../types").KibanaSpace[] }>(
      `/mcp/connectors/${encodeURIComponent(connectorId)}/kibana/spaces`, {}, KibanaSpaceListResponseSchema,
    ),

  listKibanaDataViews: (connectorId: string, space: string) =>
    request<{ data_views: import("../types").KibanaDataView[] }>(
      `/mcp/connectors/${encodeURIComponent(connectorId)}/kibana/spaces/${encodeURIComponent(space)}/data-views`, {}, KibanaDataViewListResponseSchema,
    ),

  // --- Runtime sources ---
  // The MCP server proxies these to the RAG backend on the caller's own
  // session, so a runtime-managed credential can be collected here while still
  // being stored where the mirroring scheduler reads it.

  listRuntimeSourceTypes: () =>
    request<{ source_types: import("../types").RuntimeSourceType[] }>(
      "/mcp/runtime-sources/source-types",
      {},
      RuntimeSourceTypeListResponseSchema,
    ),

  /** The read scope a documentation run for this project will apply. */
  getRuntimeReadScope: (projectId: string) =>
    request<import("../types").RuntimeDocReadScope>(
      `/mcp/runtime-sources/read-scope?project_id=${encodeURIComponent(projectId)}`,
      {},
      RuntimeDocReadScopeSchema,
    ),

  listRuntimeSources: (params?: { providerId?: string; projectId?: string }) => {
    const query = new URLSearchParams();
    if (params?.providerId) query.set("provider_id", params.providerId);
    if (params?.projectId) query.set("project_id", params.projectId);
    const suffix = query.toString() ? `?${query.toString()}` : "";
    return request<{ sources: import("../types").RuntimeSource[]; total: number }>(
      `/mcp/runtime-sources${suffix}`,
      {},
      RuntimeSourceListResponseSchema,
    );
  },

  /**
   * Test unsaved credentials and list the ingest scopes they reach.
   *
   * Writes nothing on either side, so it is safe to call repeatedly while the
   * operator is still editing the form. A 200 is the connection test.
   */
  discoverRuntimeSourceScopes: (data: {
    provider_id: string;
    credentials: Record<string, string>;
  }) =>
    request<{ scopes: import("../types").RuntimeScopeOption[] }>(
      "/mcp/runtime-sources/discover",
      { method: "POST", body: JSON.stringify(data) },
      RuntimeSourceScopesResponseSchema,
    ),

  createRuntimeSource: (data: {
    // The CONNECTOR id (e.g. `talend_tmc`). The server maps it to the RAG
    // runtime provider key; the browser must never send that key itself.
    provider_id: string;
    project_id: string;
    label: string;
    // Values are strings except the ingest-scope field, which is a list of ids.
    credentials: Record<string, string | string[]>;
    sync_interval_seconds?: number;
  }) =>
    request<import("../types").RuntimeSource>(
      "/mcp/runtime-sources",
      { method: "POST", body: JSON.stringify(data) },
      RuntimeSourceSchema,
    ),

  syncRuntimeSource: (sourceId: number) =>
    request<import("../types").RuntimeSourceSyncResult>(
      `/mcp/runtime-sources/${sourceId}/sync`,
      { method: "POST" },
      RuntimeSourceSyncResponseSchema,
    ),

  deleteRuntimeSource: (sourceId: number) =>
    request<void>(`/mcp/runtime-sources/${sourceId}`, { method: "DELETE" }),

  // --- Read grants: share one mirror with a second project ---
  // The answer to "already registered". A tenant is polled once; other projects
  // documenting the same estate read that mirror instead of re-registering it.

  listRuntimeSourceReaders: (sourceId: number) =>
    request<{ grants: import("../types").RuntimeSourceReadGrant[]; total: number }>(
      `/mcp/runtime-sources/${sourceId}/readers`,
      {},
      RuntimeSourceReadGrantListResponseSchema,
    ),

  addRuntimeSourceReader: (
    sourceId: number,
    data: { project_id: string; environment_ids?: string[] | null; window_days?: number | null },
  ) =>
    request<{ grant: import("../types").RuntimeSourceReadGrant }>(
      `/mcp/runtime-sources/${sourceId}/readers`,
      { method: "POST", body: JSON.stringify(data) },
      RuntimeSourceReadGrantResponseSchema,
    ),

  removeRuntimeSourceReader: (sourceId: number, projectId: string) =>
    request<void>(
      `/mcp/runtime-sources/${sourceId}/readers/${encodeURIComponent(projectId)}`,
      { method: "DELETE" },
    ),

  listProjectConnectorBindings: (projectId: string) =>
    request<{ project_id: string; bindings: import("../types").ProjectConnectorBinding[] }>(
      `/mcp/projects/${encodeURIComponent(projectId)}/connector-bindings`,
      {},
      ProjectConnectorBindingListResponseSchema,
    ),

  listProjectAvailableConnectors: (projectId: string) =>
    request<{ connectors: import("../types").ProjectAvailableConnector[] }>(
      `/mcp/projects/${encodeURIComponent(projectId)}/available-connectors`,
      {},
      ProjectAvailableConnectorListResponseSchema,
    ),

  putProjectConnectorBinding: (
    projectId: string,
    connectorId: string,
    data: {
      source_ids: string[];
      capability_ids: string[];
      budgets?: Record<string, number>;
      max_time_window_days?: number | null;
      enabled: boolean;
    },
  ) =>
    request<import("../types").ProjectConnectorBinding>(
      `/mcp/projects/${encodeURIComponent(projectId)}/connector-bindings/${encodeURIComponent(connectorId)}`,
      { method: "PUT", body: JSON.stringify(data) },
      ProjectConnectorBindingSchema,
    ),

  deleteProjectConnectorBinding: (projectId: string, connectorId: string) =>
    request<void>(
      `/mcp/projects/${encodeURIComponent(projectId)}/connector-bindings/${encodeURIComponent(connectorId)}`,
      { method: "DELETE" },
    ),

  getRunConnectorEvidence: (runId: number) =>
    request<import("../types").ConnectorEvidenceSummary>(
      `/mcp/generate-runs/${runId}/connector-evidence`,
      {},
      ConnectorEvidenceSummarySchema,
    ),

  /**
   * Dry run of the next generation: which sub-projects regenerate, which are
   * reused, and why.
   *
   * Read-only and derived from the same helpers the orchestrator's own skip
   * predicate uses, so the dialog cannot drift into promising a cheap run the
   * orchestrator then charges full price for.
   */
  getGenerationPlan: (projectId: string) =>
    request<import("../types").GenerationPlan>(
      `/mcp/projects/${encodeURIComponent(projectId)}/generation-plan`,
      {},
      GenerationPlanResponseSchema,
    ),

  /**
   * Start a documentation run.
   *
   * An options object rather than a positional list: the sixth positional
   * argument was already one miscount away from silently swapping the write
   * model for the generation method, and `liveEvidence` would have been the
   * seventh.
   */
  generate: (options: {
    projectId: string;
    resume?: boolean;
    investigationModel?: string | null;
    model?: string | null;
    writeModel?: string | null;
    generationMethod?: "custom" | "opencode-cli" | null;
    /**
     * Whether this run may use the project's mirrored runtime evidence.
     * Omitted entirely when undefined, so the server's `project_default`
     * stands. A run can only decline the project's grant, never widen it.
     */
    liveEvidence?: boolean;
  }) =>
    request<import("../types").StatusResponse>(
      "/mcp/generate",
      {
        method: "POST",
        body: JSON.stringify({
          project_id: options.projectId,
          resume: options.resume ?? true,
          investigation_model: options.investigationModel ?? null,
          model: options.model ?? null,
          write_model: options.writeModel ?? null,
          doc_generation_provider: options.generationMethod ?? null,
          ...(options.liveEvidence === undefined
            ? {}
            : { live_evidence: options.liveEvidence ? "project_default" : "off" }),
        }),
      },
      StatusResponseSchema,
    ),
};
