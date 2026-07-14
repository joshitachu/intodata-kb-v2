import {
  HarnessPackActivationSchema,
  HarnessPackCatalogSchema,
  HarnessPackRecommendationSchema,
  DocumentationPackSelectionSchema,
  type DocumentationPackSelection,
  type HarnessPackActivation,
  type HarnessPackCatalog,
  type HarnessPackRecommendation,
} from "@/types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { credentials: "include", ...init });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(typeof payload.error === "string" ? payload.error : "Pack request failed");
  }
  return payload as T;
}

export async function fetchPackCatalog(projectId: string, workflowKey = "default"): Promise<HarnessPackCatalog> {
  const query = new URLSearchParams({ project_id: projectId, workflow_key: workflowKey });
  return HarnessPackCatalogSchema.parse(await request(`/api/harness/packs?${query.toString()}`));
}

export async function fetchPackRecommendations(projectId: string): Promise<HarnessPackRecommendation[]> {
  const response = await request<{ recommendations: HarnessPackRecommendation[] }>(
    `/api/harness/packs/projects/${encodeURIComponent(projectId)}/recommendations`,
  );
  return response.recommendations.map((item) => HarnessPackRecommendationSchema.parse(item));
}

export async function savePackActivation(
  projectId: string,
  workflowKey: string,
  activePackIds: string[],
): Promise<HarnessPackActivation> {
  return HarnessPackActivationSchema.parse(await request(
    `/api/harness/packs/projects/${encodeURIComponent(projectId)}/activations/${encodeURIComponent(workflowKey)}`,
    { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ active_pack_ids: activePackIds }) },
  ));
}

export async function createCustomPack(projectId: string, manifest: unknown): Promise<unknown> {
  return request(`/api/harness/packs/projects/${encodeURIComponent(projectId)}/custom`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ manifest }),
  });
}

export async function saveCustomPackVersion(projectId: string, packId: string, manifest: unknown): Promise<unknown> {
  return request(`/api/harness/packs/projects/${encodeURIComponent(projectId)}/custom/${encodeURIComponent(packId)}/versions`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ manifest }),
  });
}

export async function fetchDocumentationPackSelection(projectId: string): Promise<DocumentationPackSelection> {
  return DocumentationPackSelectionSchema.parse(await request(
    `/api/harness/packs/projects/${encodeURIComponent(projectId)}/documentation-pack`,
  ));
}

export async function saveDocumentationPackSelection(
  projectId: string,
  pluginPackId: number | null,
): Promise<DocumentationPackSelection> {
  return DocumentationPackSelectionSchema.parse(await request(
    `/api/harness/packs/projects/${encodeURIComponent(projectId)}/documentation-pack`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plugin_pack_id: pluginPackId }),
    },
  ));
}
