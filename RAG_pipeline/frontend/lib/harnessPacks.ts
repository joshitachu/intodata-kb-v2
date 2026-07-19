import {
  GovernedPackActivationSchema,
  GovernancePackSchema,
  GovernancePolicySchema,
  DocumentationPackCatalogSchema,
  DocumentationPackSelectionSchema,
  StylePackRequestSchema,
  StylePackSchema,
  type DocumentationPackSelection,
  type DocumentationPackCatalog,
  type GovernedPackActivation,
  type GovernedPackSelection,
  type GovernancePack,
  type GovernancePolicy,
  type StylePack,
  type StylePackRequest,
} from "@/types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { credentials: "include", ...init });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(typeof payload.error === "string" ? payload.error : "Pack request failed");
  }
  return payload as T;
}

export async function fetchGovernancePolicy(): Promise<GovernancePolicy> {
  return GovernancePolicySchema.parse(await request("/api/harness/packs/policy"));
}

export async function fetchCertifiedPackLibrary(): Promise<GovernancePack[]> {
  const response = await request<{ packs: GovernancePack[] }>("/api/harness/packs/library");
  return response.packs.map((item) => GovernancePackSchema.parse(item));
}

export async function fetchPrivateStylePacks(): Promise<StylePack[]> {
  return StylePackSchema.array().parse(await request("/api/harness/packs/styles/private"));
}

export async function fetchProjectStylePacks(projectId: string): Promise<StylePack[]> {
  return StylePackSchema.array().parse(await request(`/api/harness/packs/projects/${encodeURIComponent(projectId)}/styles`));
}

export async function createPrivateStylePack(payload: StylePackRequest): Promise<StylePack> {
  return StylePackSchema.parse(await request("/api/harness/packs/styles/private", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(StylePackRequestSchema.parse(payload)),
  }));
}

export async function savePrivateStylePackVersion(packId: string, payload: StylePackRequest): Promise<StylePack> {
  return StylePackSchema.parse(await request(`/api/harness/packs/styles/private/${encodeURIComponent(packId)}/versions`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(StylePackRequestSchema.parse(payload)),
  }));
}

export async function createProjectStylePack(projectId: string, payload: StylePackRequest): Promise<StylePack> {
  return StylePackSchema.parse(await request(`/api/harness/packs/projects/${encodeURIComponent(projectId)}/styles`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(StylePackRequestSchema.parse(payload)),
  }));
}

export async function saveProjectStylePackVersion(projectId: string, packId: string, payload: StylePackRequest): Promise<StylePack> {
  return StylePackSchema.parse(await request(`/api/harness/packs/projects/${encodeURIComponent(projectId)}/styles/${encodeURIComponent(packId)}/versions`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(StylePackRequestSchema.parse(payload)),
  }));
}

export async function fetchGovernedPackActivation(
  projectId: string,
  workflowKey: string,
): Promise<GovernedPackActivation | null> {
  const response = await fetch(`/api/harness/packs/projects/${encodeURIComponent(projectId)}/activations/${encodeURIComponent(workflowKey)}`, { credentials: "include" });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof payload.error === "string" ? payload.error : "Pack activation could not be loaded");
  return payload === null ? null : GovernedPackActivationSchema.parse(payload);
}

export async function saveGovernedPackActivation(
  projectId: string,
  workflowKey: string,
  selection: GovernedPackSelection,
): Promise<GovernedPackActivation> {
  return GovernedPackActivationSchema.parse(await request(
    `/api/harness/packs/projects/${encodeURIComponent(projectId)}/activations/${encodeURIComponent(workflowKey)}`,
    { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(selection) },
  ));
}

export async function fetchDocumentationPackSelection(projectId: string): Promise<DocumentationPackSelection> {
  return DocumentationPackSelectionSchema.parse(await request(
    `/api/harness/packs/projects/${encodeURIComponent(projectId)}/documentation-pack`,
  ));
}

export async function fetchDocumentationPacks(): Promise<DocumentationPackCatalog> {
  return DocumentationPackCatalogSchema.parse(await request(
    "/api/harness/packs/documentation-packs",
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
